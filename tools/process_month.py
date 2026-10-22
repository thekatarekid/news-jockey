#!/usr/bin/env python3
"""Local whole-month worker; launched in the isolated Python 3.12 NLP runtime."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sys
import time
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
os.environ.setdefault('HF_HOME',str(ROOT/'data/models/huggingface'))
os.environ.setdefault('HF_HUB_OFFLINE','1');os.environ.setdefault('TRANSFORMERS_OFFLINE','1')
from newsjockey.storage import connect,now
from newsjockey.corpus import VERSION,MODEL
from newsjockey.classification import CATEGORIES,baseline,span
from newsjockey.processing_adapters import start_embeddings,EMBEDDING_URL,EMBEDDING_MODEL,normalize
from newsjockey.analysis_store import config
from newsjockey.event_clustering import feature_record,cluster

class Stopped(Exception):pass

def write_progress(path,message):
    if Path(path).resolve()!=(ROOT/'data/newsjockey.sqlite3').resolve():return
    try:
        with (ROOT/'progress.md').open('a') as log:log.write('\n- '+now()+' — '+message+'\n')
    except OSError:pass # A documentation failure must not discard durable job work.


def check(path,rid,stage=None,processed=None):
    with connect(path) as db:
        row=db.execute('SELECT status FROM corpus_runs WHERE id=?',(rid,)).fetchone()
        if not row or row[0] not in ('running','queued'):raise Stopped()
        if stage:db.execute('UPDATE corpus_runs SET stage=?,processed=COALESCE(?,processed),updated_at=? WHERE id=?',(stage,processed,now(),rid))


def embeddings(texts):
    import requests
    r=requests.post(EMBEDDING_URL+'/api/embed',json={'model':EMBEDDING_MODEL,'input':['task: clustering | query: '+t for t in texts],
         'dimensions':512,'truncate':False,'keep_alive':'30m'},timeout=180);r.raise_for_status()
    vectors=r.json()['embeddings']
    if len(vectors)!=len(texts):raise ValueError('Embedding count mismatch')
    if any(len(v)!=512 for v in vectors):raise ValueError('Embedding dimension mismatch')
    return [normalize(v) for v in vectors]


def metadata(payload,result):
    row={**payload,'content':payload['text'][len(payload['title']):].strip(),'feed_content':'','transcript':''}
    data=baseline(row);data['entities']=[];data['locations']=[]
    seen=set()
    for kind,items in result.get('entities',{}).items():
        for item in items:
            name=item['text'];key=(kind,name.lower())
            evidence=span(payload['text'],name)
            if key in seen or not evidence or item.get('confidence',0)<.65:continue
            seen.add(key)
            data['locations' if kind=='location' else 'entities'].append(dict(name=name,type=kind,confidence=round(item['confidence'],4),evidence=evidence))
    data['categories']=[dict(name=c['label'],confidence=round(c['confidence'],4),evidence=None,basis='GLiNER2 classification of saved title and lead') for c in result.get('categories',[]) if c['label'] in CATEGORIES and c['confidence']>=.65]
    # Rule-found dates in a lead may describe background rather than this event.
    data['event_time']={'value':None,'precision':'unknown','confidence':0,'evidence':None}
    data['method']='GLiNER2 local NER/classification + publisher content labels';data['topic_hints']=[]
    data['warnings']=['Metadata inferred from title and lead; scores are model confidence, not measured accuracy.']
    data['input_text']=payload['text'];data['ner_input_text']=payload['text'][:len(payload['title'])+502];data['model']=MODEL
    return data


def run(path,rid):
    import torch
    from gliner2 import GLiNER2
    torch.set_num_threads(4)
    check(path,rid,'loading local models')
    modeldir=ROOT/'data/models/huggingface/hub/models--fastino--gliner2-base-v1'
    revision=(modeldir/'refs/main').read_text().strip()
    model=GLiNER2.from_pretrained(str(modeldir/'snapshots'/revision),local_files_only=True)
    schema=model.create_schema().entities(['person','organization','location','product']).classification('categories',CATEGORIES,multi_label=True,cls_threshold=.65)
    emb_status=start_embeddings()
    if not emb_status['available']:raise RuntimeError(emb_status['message'])
    provenance=json.dumps({'ner':MODEL,'revision':revision,'library':'gliner2 2.0.0','embedding_digest':emb_status['digest'],'dimensions':512,'pipeline':VERSION,'ner_lead_characters':500,'embedding_lead_characters':1100})
    with connect(path) as db:
        db.execute("UPDATE corpus_items SET state='pending' WHERE run_id=? AND NOT EXISTS(SELECT 1 FROM corpus_features f WHERE f.article_id=corpus_items.article_id AND f.input_hash=corpus_items.input_hash AND f.version=?)",(rid,VERSION))
        db.execute("UPDATE corpus_items SET state='done' WHERE run_id=? AND EXISTS(SELECT 1 FROM corpus_features f WHERE f.article_id=corpus_items.article_id AND f.input_hash=corpus_items.input_hash AND f.version=?)",(rid,VERSION))
        done=db.execute("SELECT COUNT(*) FROM corpus_items WHERE run_id=? AND state='done'",(rid,)).fetchone()[0]
    started=time.monotonic()
    with ThreadPoolExecutor(max_workers=1) as pool:
        while True:
            check(path,rid,'metadata and embeddings',done)
            with connect(path) as db:
                rows=db.execute("SELECT * FROM corpus_items WHERE run_id=? AND state='pending' ORDER BY article_id LIMIT 16",(rid,)).fetchall();cid,_=config(db)
            if not rows:break
            payloads=[json.loads(r['input']) for r in rows];texts=[a['text'] for a in payloads]
            batch_started=time.monotonic()
            future=pool.submit(embeddings,[a['title'] for a in payloads]+texts)
            results=model.batch_extract([a['text'][:len(a['title'])+502] for a in payloads],schema,batch_size=8,include_confidence=True,include_spans=True)
            ner_seconds=time.monotonic()-batch_started
            vec=future.result();n=len(rows)
            feature_seconds=time.monotonic()-batch_started
            values=[metadata(a,r) for a,r in zip(payloads,results)]
            check(path,rid)
            with connect(path) as db:
                for i,(row,a,data) in enumerate(zip(rows,payloads,values)):
                    stamp=now();d=json.dumps(data);v=json.dumps(vec[n+i]);tv=json.dumps(vec[i])
                    db.execute('''INSERT INTO corpus_features VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(article_id) DO UPDATE SET input_hash=excluded.input_hash,version=excluded.version,data=excluded.data,title_vector=excluded.title_vector,lead_vector=excluded.lead_vector,model_version=excluded.model_version,processed_at=excluded.processed_at''',
                       (a['id'],row['input_hash'],VERSION,d,tv,v,provenance,stamp))
                    db.execute('''INSERT INTO article_analysis(article_id,input_hash,config_id,status,data,embedding_model,embedding_dimensions,vector,chunks,input_basis,processed_at)
                       VALUES(?,?,?,'ready',?,'embeddinggemma-2',512,?,1,?,?) ON CONFLICT(article_id) DO UPDATE SET input_hash=excluded.input_hash,config_id=excluded.config_id,status=excluded.status,data=excluded.data,embedding_model=excluded.embedding_model,embedding_dimensions=excluded.embedding_dimensions,vector=excluded.vector,chunks=1,input_basis=excluded.input_basis,processed_at=excluded.processed_at''',
                       (a['id'],a['source_hash'],cid,d,v,a['basis'],stamp))
                    db.execute('DELETE FROM analysis_search WHERE rowid=?',(a['id'],))
                    db.execute('INSERT INTO analysis_search(rowid,title,terms) VALUES(?,?,?)',(a['id'],a['title'],' '.join(e['name'] for e in data['entities'])))
                    db.execute("UPDATE corpus_items SET state='done',error=NULL WHERE run_id=? AND article_id=?",(rid,a['id']))
                done+=n
                db.execute('UPDATE corpus_runs SET processed=?,updated_at=?,report=? WHERE id=?',(done,now(),json.dumps({'feature_seconds':round(time.monotonic()-started,2),'models':json.loads(provenance)}),rid))
            if done%1024==0:write_progress(path,f'October/month job {rid}: {done:,} article metadata and vector records checkpointed; whole-month matching follows feature completion.')
            print(f'{rid} metadata {done}; NER {ner_seconds:.2f}s features {feature_seconds:.2f}s total {time.monotonic()-batch_started:.2f}s',flush=True)
    del model
    check(path,rid,'loading month-wide vectors',done)
    with connect(path) as db:
        rows=db.execute('''SELECT i.input,f.*,x.manual FROM corpus_items i JOIN corpus_features f ON f.article_id=i.article_id AND f.input_hash=i.input_hash
           JOIN articles a ON a.id=i.article_id LEFT JOIN article_analysis x ON x.article_id=i.article_id WHERE i.run_id=? AND f.version=? AND a.duplicate_of IS NULL AND a.discovery_rejected=0 ORDER BY a.published_at,a.id''',(rid,VERSION)).fetchall()
    records=[feature_record(json.loads(r['input']),{**json.loads(r['data']),**json.loads(r['manual'] or '{}')}) for r in rows]
    tv=[json.loads(r['title_vector']) for r in rows];lv=[json.loads(r['lead_vector']) for r in rows]
    groups,candidates,report=cluster(records,tv,lv,lambda stage,count,total:check(path,rid,stage+' · '+str(count)+' / '+str(total)))
    check(path,rid,'saving stories')
    save_clusters(path,rid,records,groups,candidates,report)


def save_clusters(path,rid,records,groups,candidates,report):
    with connect(path) as db:
        db.execute('BEGIN IMMEDIATE')
        if db.execute('SELECT status FROM corpus_runs WHERE id=?',(rid,)).fetchone()[0] not in ('running','queued'):raise Stopped()
        # Keep reviewer's memberships; preserve old IDs as aliases when a re-run moves articles.
        from newsjockey.grouping import assign_story,new_story
        index_story={};stamp=now()
        first_build=not db.execute('SELECT 1 FROM story_members LIMIT 1').fetchone()
        for group in groups:
            anchor=records[group['anchor']]['id']
            old=None if first_build else db.execute("SELECT s.id FROM news_stories s JOIN story_members m ON m.story_id=s.id WHERE s.anchor_article_id=? AND m.article_id=? AND s.merged_into IS NULL LIMIT 1",(anchor,anchor)).fetchone()
            sid=old[0] if old else db.execute('INSERT INTO news_stories(title,anchor_article_id,created_at,updated_at) VALUES(?,?,?,?)',(records[group['anchor']]['title'],anchor,stamp,stamp)).lastrowid
            for i in group['members']:
                if first_build:
                    db.execute("INSERT INTO story_members VALUES(?,?,'primary',0,?,.8,?)",(records[i]['id'],sid,'Whole-month event clustering; representative and cohesion checks.',stamp));actual=sid
                else:actual=assign_story(db,records[i]['id'],sid,False,'Whole-month event clustering; representative and cohesion checks.',.8)
                index_story[i]=actual
        db.execute('DELETE FROM story_candidates WHERE article_id IN (SELECT article_id FROM corpus_items WHERE run_id=?)',(rid,))
        # Keep the strongest candidate per article / target story.
        chosen={}
        for pair in candidates:
            a,b=pair['left'],pair['right'];sid=index_story[b]
            if index_story[a]==sid:continue
            key=(records[a]['id'],sid)
            if key not in chosen or pair['score']>chosen[key]['score']:chosen[key]=pair
        for (aid,sid),p in chosen.items():
            db.execute('INSERT OR REPLACE INTO story_candidates VALUES(?,?,?,?,?,?,?)',(aid,sid,p['score'],'review',p['reason'],json.dumps(p),stamp))
        report.update(articles=len(records),stories=len(groups),multi_article=sum(len(g['members'])>1 for g in groups),
          multi_publisher=sum(len({records[i]['publisher_id'] for i in g['members']})>1 for g in groups),singletons=sum(len(g['members'])==1 for g in groups),
          largest=max((len(g['members']) for g in groups),default=0),quality='Provisional local clusters; review required, no measured precision claim',snapshot_at=db.execute('SELECT created_at FROM corpus_runs WHERE id=?',(rid,)).fetchone()[0])
        oldreport=json.loads(db.execute('SELECT report FROM corpus_runs WHERE id=?',(rid,)).fetchone()[0]);report={**oldreport,**report}
        db.execute("UPDATE corpus_runs SET status='complete',stage='stories ready for review',processed=total,updated_at=?,owner_pid=NULL,report=? WHERE id=?",(stamp,json.dumps(report),rid))
    write_progress(path,f"Month job {rid} finished: {report['articles']:,} articles, {report['multi_article']:,} multi-article stories, {report['multi_publisher']:,} cross-publisher stories, {report['singletons']:,} singletons. Clusters remain provisional pending review; report saved in artifacts/october-stories.")
    if Path(path).resolve()!=(ROOT/'data/newsjockey.sqlite3').resolve():return
    folder=ROOT/'artifacts/october-stories';folder.mkdir(parents=True,exist_ok=True)
    (folder/(rid+'-report.json')).write_text(json.dumps(report,indent=2))
    # Titles + matching evidence make inspection reproducible without copying full bodies.
    (folder/(rid+'-clusters.json')).write_text(json.dumps([{'anchor_id':records[g['anchor']]['id'],'articles':[{'id':records[i]['id'],'title':records[i]['title'],'publisher_id':records[i]['publisher_id']} for i in g['members']]} for g in groups],indent=2))

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--database',required=True);parser.add_argument('--job',required=True);args=parser.parse_args()
    try:run(args.database,args.job)
    except Stopped:pass
    except Exception as exc:
        import traceback;traceback.print_exc()
        with connect(args.database) as db:db.execute("UPDATE corpus_runs SET status='failed',error=?,owner_pid=NULL,updated_at=? WHERE id=?",(str(exc)[:600],now(),args.job))
    finally:
        with connect(args.database) as db:db.execute('UPDATE corpus_runs SET owner_pid=NULL WHERE id=? AND owner_pid=?',(args.job,os.getpid()))
