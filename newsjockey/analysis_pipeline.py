"""Durable bounded processing jobs; model calls never hold a database transaction."""
import json
import logging
import os
import re
import threading
import time
import uuid
from .storage import connect, now
from .windows import date_window, group_condition
from .analysis_store import config, effective
from .classification import baseline, source_text, input_hash, validate_decision, tokens, cosine, span
from .processing_adapters import embed, subscription_decision, ANALYSIS_SCHEMA, PAIR_SCHEMA, TOPIC_SCHEMA
from .grouping import story_candidates, new_story, assign_story, create_topic, topic_match, link_topic

log=logging.getLogger(__name__)

def combine_decisions(decisions):
    data=decisions[0]
    conflict=False
    for extra in decisions[1:]:
        for key in ('categories','entities','locations','topic_hints'):
            identity='title' if key=='topic_hints' else 'name'
            for value in extra[key]:
                if not any(e[identity]==value[identity] for e in data[key]):data[key].append(value)
        data['warnings']=list(dict.fromkeys(data['warnings']+extra['warnings']))
        left=data['event_time'].get('value');right=extra['event_time'].get('value')
        if left and right and left!=right:
            conflict=True
            data['warnings'].append('Multiple event dates across chunks; review required.')
        if conflict:data['event_time']={'value':None,'precision':'unknown','confidence':0,'evidence':None}
        elif not left and right:data['event_time']=extra['event_time']
    return data

def record_call(path,run_id,item_id,operation,metrics,digest=None,status='ok',error=None):
    with connect(path) as db:
        fields=dict(run_id=run_id,item_id=item_id,operation=operation,input_hash=digest,status=status,error=error,created_at=now(),**metrics)
        fields['detail']=json.dumps(fields.get('detail',{}))
        db.execute('INSERT INTO processing_calls('+','.join(fields)+') VALUES('+','.join('?'*len(fields))+')',list(fields.values()))

def create_run(path,phase,scope='recent',engine='local',limit=100,article_ids=None):
    if phase not in (3,4,5):raise ValueError('Select Phase 3, 4, or 5.')
    # Date groups, including explicit YYYY-MM values, are validated by group_condition.
    if engine not in ('local','luna','terra'):raise ValueError('Only local processing and subscription Luna/Terra are enabled.')
    if type(limit) is not int or not 1<=limit<=500:raise ValueError('Batch size must be between 1 and 500.')
    if article_ids is not None and (not isinstance(article_ids,list) or not article_ids or len(article_ids)>500 or not all(type(i) is int and i>0 for i in article_ids)):
        raise ValueError('Provide 1–500 valid article IDs.')
    with connect(path) as db:
        db.execute('BEGIN IMMEDIATE')
        # Conservative SQL invalidation also sees feed changes that do not alter a full body.
        # Restore those rows using the exact processing hash, without another model call.
        for stale in db.execute("SELECT a.*,x.input_hash old_hash FROM article_analysis x JOIN articles a ON a.id=x.article_id WHERE x.status='stale' LIMIT 1000").fetchall():
            if input_hash(stale)==stale['old_hash']:
                text,basis,suspect=source_text(stale)
                status='held' if suspect and len(text)<180 else 'ready' if basis in ('article body','publisher transcript') else 'limited'
                db.execute('UPDATE article_analysis SET status=? WHERE article_id=?',(status,stale['id']))
        cid,cfg=config(db)
        existing=db.execute("SELECT id FROM processing_runs WHERE phase=? AND status IN ('queued','running','paused')",(phase,)).fetchone()
        if existing:return {'id':existing['id'],'existing':True}
        condition,args=group_condition(scope,date_window(db))
        if phase==3:
            sql='''SELECT a.id FROM articles a WHERE a.duplicate_of IS NULL AND a.discovery_rejected=0
                   AND length(a.title)>5 AND (length(a.content)>0 OR length(a.transcript)>0) AND '''+condition
            if article_ids:
                if not all(type(i) is int and i>0 for i in article_ids):raise ValueError('Invalid article IDs.')
                sql+=' AND a.id IN ('+','.join('?'*len(article_ids))+')';args+=article_ids
            # Prefer unprocessed work and good bodies. Explicit reprocessing remains possible by IDs.
            if not article_ids:
                sql+=' AND NOT EXISTS(SELECT 1 FROM processing_state z WHERE z.phase=3 AND z.item_id=a.id AND z.engine=? AND z.config_id=? AND z.processed_at>=a.content_changed_at)';args += [engine,cid]
            sql+=" ORDER BY (a.body_quality='full') DESC,a.published_at DESC,a.id DESC LIMIT ?";args.append(limit)
        elif phase==4:
            sql='''SELECT a.id FROM article_analysis x JOIN articles a ON a.id=x.article_id
                   WHERE x.status IN ('ready','limited') AND a.duplicate_of IS NULL AND a.discovery_rejected=0 AND '''+condition+''' AND NOT EXISTS(SELECT 1 FROM processing_state z WHERE z.phase=4 AND z.item_id=a.id AND z.engine=? AND z.config_id=? AND z.processed_at>=x.processed_at) ORDER BY a.published_at,a.id LIMIT ?''';args += [engine,cid,limit]
            if article_ids:
                sql='''SELECT a.id FROM article_analysis x JOIN articles a ON a.id=x.article_id
                       WHERE x.status IN ('ready','limited') AND a.duplicate_of IS NULL AND a.discovery_rejected=0 AND '''+condition+' AND a.id IN ('+','.join('?'*len(article_ids))+') ORDER BY a.published_at,a.id LIMIT ?'
                args=group_condition(scope,date_window(db))[1]+article_ids+[limit]
        else:
            sql='''SELECT s.id FROM news_stories s JOIN articles a ON a.id=s.anchor_article_id WHERE s.merged_into IS NULL
              AND EXISTS(SELECT 1 FROM story_members m WHERE m.story_id=s.id) AND '''+condition+''' AND NOT EXISTS(SELECT 1 FROM processing_state z WHERE z.phase=5 AND z.item_id=s.id AND z.engine=? AND z.config_id=? AND z.processed_at>=s.updated_at) ORDER BY s.updated_at DESC LIMIT ?''';args += [engine,cid,limit]
        ids=[r[0] for r in db.execute(sql,args)]
        if not ids:raise ValueError('No eligible items. Run the preceding phase or select another date group.')
        rid=uuid.uuid4().hex
        db.execute('INSERT INTO processing_runs VALUES(?,?,?,?,?,?,?,?,?,NULL)',(rid,phase,scope,engine,cid,'queued',now(),now(),None))
        db.executemany('INSERT INTO processing_run_items(run_id,item_id) VALUES(?,?)',[(rid,i) for i in ids])
    return {'id':rid,'total':len(ids),'existing':False}

def analyze_article(path,article_id,cfg,run_id,engine):
    with connect(path) as db:
        article=db.execute('SELECT * FROM articles WHERE id=?',(article_id,)).fetchone()
        if not article or article['duplicate_of'] or article['discovery_rejected']:return {'skipped':'Excluded article.'}
        previous=db.execute('SELECT * FROM article_analysis WHERE article_id=?',(article_id,)).fetchone()
        version=db.execute('SELECT MAX(id) FROM article_versions WHERE article_id=?',(article_id,)).fetchone()[0]
        cid=db.execute('SELECT config_id FROM processing_runs WHERE id=?',(run_id,)).fetchone()[0]
    article=dict(article);digest=input_hash(article);text,basis,suspected=source_text(article)
    started=time.monotonic();data=baseline(article)
    if engine!='local':
        # Overlapping chunks preserve long input without silently losing the article end.
        chunks=[text[i:i+18000] for i in range(0,len(text),17000)] or [text]
        decisions=[]
        for index,chunk in enumerate(chunks):
            raw,metrics=subscription_decision(engine,
                'Classify this news item. Return multi-label categories, editorial content type, entities, locations and verbatim supporting quotes. '
                'Describe the specific event action in 1–4 words. Event time is when the event happened, never merely publication time; use null/unknown without evidence. '
                'Resolve relative dates only when the text and publication timestamp justify them, and mark approximate. '
                'Suggest up to three specific continuing topic titles (max 6 words), with descriptions and evidence; a person/company name alone is too broad. '
                'Use source spellings for entity/location names: do not expand abbreviations or add words absent from the quoted passage. '
                'Copy quotes exactly, without ellipses or paraphrasing. Event dates must be ISO 8601. '
                'Do not summarize or invent missing reporting. Confidence is 0–1. If a chunk lacks evidence, return empty lists.',
                {'title':article['title'],'publisher_published_at':article['published_at'],'input_basis':basis,'chunk':index+1,'chunks':len(chunks),'text':chunk},ANALYSIS_SCHEMA)
            metrics.setdefault('detail',{})['raw_decision']=raw
            record_call(path,run_id,article_id,'classification',metrics,digest)
            decisions.append(validate_decision(raw,article))
        data=combine_decisions(decisions)
        data['method']='Codex subscription '+engine
    else:
        record_call(path,run_id,article_id,'classification',{'provider':'local','model':'rules-v1','model_version':'1','prompt_version':'none',
                    'elapsed_seconds':round(time.monotonic()-started,3),'input_tokens':None,'output_tokens':None,'peak_memory_mb':None,'incremental_cost':0},digest)
    vector=None;model=None;chunks=0;error=None
    if cfg['embedding']=='embeddinggemma-2':
        try:
            vector,metrics=embed(text,cfg['dimensions']);model=metrics['model']+'@'+str(metrics['model_version']);chunks=metrics['detail']['chunks']
            record_call(path,run_id,article_id,'embedding',metrics,digest)
        except Exception as exc:
            error=str(exc)[:350];data['warnings'].append('Embeddings unavailable; lexical retrieval only: '+error)
            record_call(path,run_id,article_id,'embedding',{'provider':'local-ollama','model':'embeddinggemma-2','model_version':None,'prompt_version':'clustering-v1',
              'elapsed_seconds':0,'input_tokens':None,'output_tokens':None,'peak_memory_mb':None,'incremental_cost':0},digest,'error',error)
    state='held' if suspected and len(text)<180 else 'limited' if basis not in ('article body','publisher transcript') else 'ready'
    with connect(path) as db:
        current=db.execute('SELECT * FROM articles WHERE id=?',(article_id,)).fetchone()
        if input_hash(current)!=digest:return {'stale':True,'reason':'Article changed during processing; run it again.'}
        db.execute('''INSERT INTO article_analysis(article_id,input_hash,source_version_id,config_id,status,data,embedding_model,
          embedding_dimensions,vector,chunks,input_basis,processed_at,error) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
          ON CONFLICT(article_id) DO UPDATE SET input_hash=excluded.input_hash,source_version_id=excluded.source_version_id,
          config_id=excluded.config_id,status=excluded.status,data=excluded.data,embedding_model=excluded.embedding_model,
          embedding_dimensions=excluded.embedding_dimensions,vector=excluded.vector,chunks=excluded.chunks,
          input_basis=excluded.input_basis,processed_at=excluded.processed_at,error=excluded.error''',
          (article_id,digest,version,cid,state,json.dumps(data),model,len(vector) if vector else None,json.dumps(vector) if vector else None,chunks,basis,now(),error))
        db.execute('DELETE FROM analysis_search WHERE rowid=?',(article_id,))
        terms=' '.join([e['name'] for e in data['entities']]+[e['name'] for e in data['categories']]+tokens(text[:6000]))
        db.execute('INSERT INTO analysis_search(rowid,title,terms) VALUES(?,?,?)',(article_id,article['title'],terms))
    return {'article_id':article_id,'status':state,'categories':[e['name'] for e in data['categories']],'embedding':bool(vector)}

def group_article(path,aid,cfg,run_id,engine):
    with connect(path) as db:
        article=db.execute('SELECT * FROM articles WHERE id=?',(aid,)).fetchone()
        analysis=db.execute('SELECT * FROM article_analysis WHERE article_id=?',(aid,)).fetchone()
        if not analysis:return {'skipped':'Run Phase 3 first.'}
        if input_hash(article)!=analysis['input_hash']:return {'skipped':'Analysis is stale; rerun Phase 3.'}
        existing=db.execute("SELECT * FROM story_members WHERE article_id=? AND role='primary'",(aid,)).fetchone()
        if existing and existing['manual']:return {'story_id':existing['story_id'],'manual_preserved':True}
        candidates=story_candidates(db,article,analysis)
    roundup=bool(re.search(r'\b(?:roundup|round-up|daily briefing|news digest)\b',article['title'],re.I))
    if engine!='local' and not roundup:
        for candidate in candidates[:3]:
            if candidate['decision']=='reject' or candidate['score']<.45:continue
            with connect(path) as db:
                anchor=db.execute('SELECT * FROM articles WHERE id=?',(candidate['anchor_article_id'],)).fetchone()
            left=source_text(article)[0];right=source_text(anchor)[0]
            raw,metrics=subscription_decision(engine,
               'Decide if both articles report exactly the same event, not merely related events or the same person/topic. '
               'Check participants, action, event date, location, quarter/game and distinguishing details. A ruling and later appeal differ. '
               'If either excerpt is insufficient, same_event=false. Return one verbatim quote from each excerpt supporting the decision.',
               {'left':left[:16000],'right':right[:16000],'left_published':article['published_at'],'right_published':anchor['published_at'],
                'local_checks':candidate},PAIR_SCHEMA)
            record_call(path,run_id,aid,'same-event verification',metrics,analysis['input_hash'])
            evidence_left=span(left[:16000],raw.get('left_quote'));evidence_right=span(right[:16000],raw.get('right_quote'))
            evidence=bool(evidence_left and evidence_right)
            accepted=raw.get('same_event') is True and raw.get('confidence',0)>=.98 and evidence and not candidate['hard_conflicts'] and not candidate['review_reasons']
            candidate.update(decision='same_event' if accepted else 'review',reason=str(raw.get('reason',''))[:1000],model_decision=raw,
                             model_evidence={'left':evidence_left,'right':evidence_right})
    chosen=next((c for c in candidates if c['decision']=='same_event'),None) if not roundup else None
    with connect(path) as db:
        if input_hash(db.execute('SELECT * FROM articles WHERE id=?',(aid,)).fetchone())!=analysis['input_hash']:return {'stale':True}
        for c in candidates:
            old=db.execute('SELECT decision FROM story_candidates WHERE article_id=? AND story_id=?',(aid,c['story_id'])).fetchone()
            if old and old[0]=='manual_reject':
                if chosen and chosen['story_id']==c['story_id']:chosen=None
                continue
            db.execute('INSERT OR REPLACE INTO story_candidates VALUES(?,?,?,?,?,?,?)',(aid,c['story_id'],c['score'],c['decision'],c['reason'],json.dumps(c),now()))
        target=chosen['story_id'] if chosen else existing['story_id'] if existing else new_story(db,aid)
        target=assign_story(db,aid,target,False,chosen['reason'] if chosen else 'Separate event; candidates require review.' if candidates else 'First coverage of this event.',chosen['score'] if chosen else 1)
        if roundup:
            for c in candidates:
                if c['score']>=.6:assign_story(db,aid,c['story_id'],False,'Roundup reference; events remain separate.',c['score'],'reference')
    return {'article_id':aid,'story_id':target,'candidates':len(candidates),'matched':bool(chosen),'roundup':roundup}

def organize_story(path,sid,cfg,run_id,engine):
    with connect(path) as db:
        story=db.execute('SELECT * FROM news_stories WHERE id=? AND merged_into IS NULL',(sid,)).fetchone()
        if not story:return {'skipped':'Story was merged.'}
        analysis=db.execute('SELECT * FROM article_analysis WHERE article_id=?',(story['anchor_article_id'],)).fetchone()
        article=db.execute('SELECT * FROM articles WHERE id=?',(story['anchor_article_id'],)).fetchone()
        data=effective(analysis);hints=data.get('topic_hints',[])
        topics=[dict(r) for r in db.execute('SELECT * FROM news_topics WHERE merged_into IS NULL')]
    if engine!='local' and not hints:
        # Same evidence schema; only topic suggestions are consumed here.
        raw,metrics=subscription_decision(engine,'Extract supported news metadata and suggest up to three specific continuing topics, each with title of at most six words, description and verbatim quote. Do not make a topic just a person or organization. Unknown event time must remain null.',
              {'title':article['title'],'text':source_text(article)[0][:18000],'input_basis':analysis['input_basis']},ANALYSIS_SCHEMA)
        record_call(path,run_id,sid,'topic discovery',metrics,analysis['input_hash'])
        hints=validate_decision(raw,article)['topic_hints']
    if not hints:
        # Conservative local seeds: specific title words, rather than a broad person/category bucket.
        title_words=list(dict.fromkeys(tokens(article['title'])))[:6]
        if len(title_words)<3:return {'skipped':'Not enough evidence for a specific topic.'}
        hints=[{'title':' '.join(title_words),'description':article['title']}]
    linked=[]
    for hint in hints[:3]:
        vector=None;model=None
        if cfg['embedding']=='embeddinggemma-2':
            vector,metrics=embed(hint['title']+' '+hint['description'],cfg['dimensions'])
            model=metrics['model']+'@'+str(metrics['model_version'])
            record_call(path,run_id,sid,'topic embedding',metrics,analysis['input_hash'])
        matches=[];candidates=[]
        for topic in topics:
            # Edited/manual topics also need semantic retrieval. Rebuild only incompatible/missing vectors.
            if vector and (not topic.get('vector') or topic.get('embedding_model')!=model or len(json.loads(topic['vector']))!=len(vector)):
                topic_vector,topic_metrics=embed(topic['title']+' '+topic['description'],cfg['dimensions'])
                record_call(path,run_id,sid,'topic descriptor embedding',topic_metrics,analysis['input_hash'])
                topic['vector']=json.dumps(topic_vector);topic['embedding_model']=model
                with connect(path) as db:
                    db.execute('UPDATE news_topics SET vector=?,embedding_model=? WHERE id=? AND title=? AND description=?',
                        (topic['vector'],model,topic['id'],topic['title'],topic['description']))
            semantic=cosine(vector,json.loads(topic['vector'])) if vector and topic.get('vector') and topic.get('embedding_model')==model else 0
            accepted,score,overlap=topic_match(hint,topic,semantic)
            candidates.append({'topic_id':topic['id'],'score':score,'decision':'related' if accepted else 'review',
                               'reason':'Descriptor overlap: '+', '.join(overlap)+'; semantic '+str(round(semantic,3))})
            if accepted:matches.append((score,topic))
        if engine!='local' and not matches:
            for candidate in sorted(candidates,key=lambda c:c['score'],reverse=True)[:3]:
                if candidate['score']<.55:continue
                topic=next(t for t in topics if t['id']==candidate['topic_id'])
                raw,metrics=subscription_decision(engine,
                    'Decide whether this story belongs to the SAME specific continuing topic. Related events can differ in time and action, '
                    'but sharing only a person, organization or category is insufficient. Do not broaden the existing topic. '
                    'Return related=false when evidence is insufficient.',
                    {'story_topic':hint,'existing_topic':{'title':topic['title'],'description':topic['description']}},TOPIC_SCHEMA)
                record_call(path,run_id,sid,'topic verification',metrics,analysis['input_hash'])
                accepted=raw.get('related') is True and raw.get('confidence',0)>=.95
                candidate.update(decision='related' if accepted else 'review',reason=str(raw.get('reason',''))[:1000])
                if accepted:matches.append((raw['confidence'],topic));break
        with connect(path) as db:
            for candidate in sorted(candidates,key=lambda c:c['score'],reverse=True)[:20]:
                db.execute('INSERT OR REPLACE INTO topic_candidates VALUES(?,?,?,?,?,?)',(sid,candidate['topic_id'],candidate['score'],candidate['decision'],candidate['reason'],now()))
            if matches:
                score,topic=max(matches,key=lambda p:p[0]);tid=topic['id'];reason=next(c['reason'] for c in candidates if c['topic_id']==tid)
            else:
                tid=create_topic(db,hint['title'],hint['description'],sid);score=.55 if engine=='local' else .8;reason='New topic seed; review specificity.'
                db.execute('UPDATE news_topics SET vector=?,embedding_model=? WHERE id=?',(json.dumps(vector) if vector else None,model,tid))
                topics.append(dict(db.execute('SELECT * FROM news_topics WHERE id=?',(tid,)).fetchone()))
            link_topic(db,tid,sid,reason=reason,confidence=score);linked.append(tid)
    return {'story_id':sid,'topic_ids':linked}

def run_detail(db,rid):
    row=db.execute('SELECT * FROM processing_runs WHERE id=?',(rid,)).fetchone()
    if not row:raise ValueError('Run not found.')
    result=dict(row);counts={r['state']:r['n'] for r in db.execute('SELECT state,COUNT(*) n FROM processing_run_items WHERE run_id=? GROUP BY state',(rid,))}
    result.update(counts=counts,total=sum(counts.values()),processed=counts.get('done',0)+counts.get('failed',0))
    return result

class ProcessingWorker:
    def __init__(self,path):self.path=path;self.stop_event=threading.Event();self.thread=None
    def recover(self):
        with connect(self.path) as db:
            for r in db.execute("SELECT id,owner_pid,status FROM processing_runs WHERE status IN ('running','paused')").fetchall():
                try:
                    if r['owner_pid']:os.kill(r['owner_pid'],0);continue
                except ProcessLookupError:pass
                except PermissionError:continue
                db.execute("UPDATE processing_runs SET status=?,owner_pid=NULL WHERE id=?",('paused' if r['status']=='paused' else 'queued',r['id']))
                db.execute("UPDATE processing_run_items SET state='pending' WHERE run_id=? AND state='running'",(r['id'],))
    def step(self):
        with connect(self.path) as db:
            db.execute('BEGIN IMMEDIATE')
            run=db.execute("SELECT * FROM processing_runs WHERE status IN ('queued','running') AND (owner_pid IS NULL OR owner_pid=?) ORDER BY created_at LIMIT 1",(os.getpid(),)).fetchone()
            if not run:return False
            item=db.execute("SELECT item_id FROM processing_run_items WHERE run_id=? AND state='pending' ORDER BY item_id LIMIT 1",(run['id'],)).fetchone()
            if not item:
                db.execute("UPDATE processing_runs SET status='complete',updated_at=?,owner_pid=NULL WHERE id=?",(now(),run['id']));return True
            db.execute("UPDATE processing_runs SET status='running',owner_pid=?,updated_at=? WHERE id=?",(os.getpid(),now(),run['id']))
            db.execute("UPDATE processing_run_items SET state='running' WHERE run_id=? AND item_id=?",(run['id'],item[0]))
            cfg=json.loads(db.execute('SELECT config FROM processing_configs WHERE id=?',(run['config_id'],)).fetchone()[0])
        started=time.monotonic()
        try:
            result={3:analyze_article,4:group_article,5:organize_story}[run['phase']](self.path,item[0],cfg,run['id'],run['engine'])
            state='done';error=None
        except Exception as exc:
            log.exception('Phase %s processing failed',run['phase']);result={};state='failed';error=str(exc)[:1000]
            record_call(self.path,run['id'],item[0],'phase '+str(run['phase'])+' failed',
                {'provider':'codex-chatgpt' if run['engine']!='local' else 'local',
                 'model':{'luna':'gpt-6-luna','terra':'gpt-5.6-terra','local':'rules-and-local-embeddings'}[run['engine']],
                 'model_version':None,'prompt_version':'news-analysis-3.1','elapsed_seconds':round(time.monotonic()-started,3),
                 'input_tokens':None,'output_tokens':None,'peak_memory_mb':None,'incremental_cost':0},status='error',error=error)
        with connect(self.path) as db:
            if not error and not result.get('stale') and not result.get('skipped'):
                db.execute('INSERT OR REPLACE INTO processing_state VALUES(?,?,?,?,?)',(run['phase'],item[0],run['engine'],run['config_id'],now()))
            db.execute('UPDATE processing_run_items SET state=?,result=?,error=? WHERE run_id=? AND item_id=?',(state,json.dumps(result),error,run['id'],item[0]))
            db.execute('UPDATE processing_runs SET updated_at=? WHERE id=?',(now(),run['id']))
            if error and run['engine']!='local':
                db.execute("UPDATE processing_runs SET status='paused',error=? WHERE id=? AND status!='cancelled'",(error,run['id']))
        return True
    def start(self):
        if self.thread:return
        self.recover()
        def loop():
            while not self.stop_event.is_set():
                try:worked=self.step()
                except Exception:log.exception('Processing scheduler error');worked=False
                self.stop_event.wait(.1 if worked else 2)
        self.thread=threading.Thread(target=loop,daemon=True,name='news-processing');self.thread.start()
    def stop(self):self.stop_event.set()
