"""Inspection and correction endpoints for each processing phase."""
import json
from flask import Blueprint, jsonify, request
from .storage import connect, now, CONTENT_TYPES
from .analysis_store import initialize_analysis, config, effective, correction
from .analysis_pipeline import ProcessingWorker, create_run, run_detail
from .classification import CATEGORIES, SCOPES, input_hash
from .processing_adapters import subscription_status, embedding_status, start_embeddings
from .grouping import (story_detail, topic_detail, assign_story, merge_stories, split_story,
                      create_topic, link_topic, topic_keywords)

def register_analysis_routes(app):
    bp=Blueprint('analysis',__name__)
    path=app.config['DATABASE'];initialize_analysis(path);worker=ProcessingWorker(path)
    app.extensions['processing_worker']=worker

    @bp.get('/api/analysis/overview')
    def overview():
        with connect(path) as db:
            cid,cfg=config(db)
            counts={r['status']:r['n'] for r in db.execute('SELECT status,COUNT(*) n FROM article_analysis GROUP BY status')}
            stories=db.execute('SELECT COUNT(*) FROM news_stories s WHERE merged_into IS NULL AND EXISTS(SELECT 1 FROM story_members m WHERE m.story_id=s.id)').fetchone()[0]
            topics=db.execute('SELECT COUNT(*) FROM news_topics WHERE merged_into IS NULL').fetchone()[0]
            calls=[dict(r) for r in db.execute('SELECT operation,provider,model,COUNT(*) calls,ROUND(AVG(elapsed_seconds),3) seconds,SUM(input_tokens) input_tokens,SUM(output_tokens) output_tokens,SUM(incremental_cost) incremental_cost FROM processing_calls GROUP BY operation,provider,model')]
            errors=[dict(r) for r in db.execute("SELECT * FROM processing_calls WHERE status!='ok' ORDER BY id DESC LIMIT 10")]
            runs=[run_detail(db,r[0]) for r in db.execute('SELECT id FROM processing_runs ORDER BY created_at DESC LIMIT 20')]
        return jsonify(config_id=cid,config=cfg,analysis=counts,stories=stories,topics=topics,calls=calls,errors=errors,runs=runs,categories=CATEGORIES,scopes=SCOPES)

    @bp.get('/api/analysis/adapters')
    def adapters():return jsonify(subscription=subscription_status(),embedding=embedding_status(),allowed_models={'Luna':'gpt-6-luna','Terra':'gpt-5.6-terra'})

    @bp.post('/api/analysis/embeddings/start')
    def embeddings_start():
        try:return jsonify(start_embeddings())
        except Exception as exc:return jsonify(error=str(exc)),503

    @bp.patch('/api/analysis/config')
    def settings():
        body=request.get_json()
        with connect(path) as db:
            _,cfg=config(db)
            if body.get('embedding',cfg['embedding']) not in ('none','embeddinggemma-2'):raise ValueError('Only local EmbeddingGemma 2 or lexical-only processing is supported.')
            if body.get('dimensions',cfg['dimensions']) not in (128,256,512,768):raise ValueError('Choose 128, 256, 512, or 768 dimensions.')
            for key in ('embedding','dimensions'):cfg[key]=body.get(key,cfg[key])
            cid=db.execute('INSERT INTO processing_configs(created_at,config) VALUES(?,?)',(now(),json.dumps(cfg))).lastrowid
        return jsonify(id=cid,config=cfg)

    @bp.post('/api/analysis/jobs')
    def jobs_create():
        body=request.get_json();return jsonify(create_run(path,body.get('phase'),body.get('scope','recent'),body.get('engine','local'),body.get('limit',100),body.get('article_ids'))),202

    @bp.get('/api/analysis/jobs/<rid>')
    def jobs_detail(rid):
        with connect(path) as db:
            result=run_detail(db,rid);result['items']=[dict(r) for r in db.execute('SELECT * FROM processing_run_items WHERE run_id=? ORDER BY item_id LIMIT 500',(rid,))]
        return jsonify(result)

    @bp.patch('/api/analysis/jobs/<rid>')
    def jobs_update(rid):
        action=request.get_json().get('action')
        if action not in ('pause','resume','cancel'):raise ValueError('Invalid job action.')
        with connect(path) as db:
            row=run_detail(db,rid)
            if row['status'] in ('complete','cancelled'):raise ValueError('This job has finished.')
            if action=='resume':
                if row['status']!='paused':raise ValueError('Only paused jobs can resume.')
                db.execute("UPDATE processing_run_items SET state='pending',error=NULL WHERE run_id=? AND state='failed'",(rid,))
            db.execute('UPDATE processing_runs SET status=?,error=NULL,updated_at=? WHERE id=?',({'pause':'paused','resume':'queued','cancel':'cancelled'}[action],now(),rid))
        return jsonify(ok=True)

    @bp.get('/api/analysis/articles')
    def analysis_list():
        page=max(1,request.args.get('page',1,type=int));size=50
        conditions=['a.duplicate_of IS NULL','a.discovery_rejected=0'];args=[]
        if request.args.get('q'):conditions.append('(a.title LIKE ? OR p.name LIKE ?)');args += ['%'+request.args['q']+'%']*2
        if request.args.get('status'):conditions.append('x.status=?');args.append(request.args['status'])
        if request.args.get('category'):
            conditions.append("EXISTS(SELECT 1 FROM json_each(COALESCE(json_extract(x.manual,'$.categories'),json_extract(x.data,'$.categories'))) j WHERE json_extract(j.value,'$.name')=?)");args.append(request.args['category'])
        base=' FROM article_analysis x JOIN articles a ON a.id=x.article_id JOIN publishers p ON p.id=a.publisher_id WHERE '+' AND '.join(conditions)
        with connect(path) as db:
            total=db.execute('SELECT COUNT(*)'+base,args).fetchone()[0]
            rows=db.execute('SELECT x.*,a.title,a.url,a.published_at,p.name publisher'+base+' ORDER BY x.processed_at DESC LIMIT ? OFFSET ?',args+[size,(page-1)*size]).fetchall()
        items=[]
        for r in rows:
            item=dict(r);item['analysis']=effective(r);item['manual_fields']=list(json.loads(item.pop('manual')));item.pop('vector');item.pop('data');items.append(item)
        return jsonify(items=items,total=total,page=page,pages=max(1,(total+size-1)//size))

    @bp.get('/api/analysis/articles/<int:aid>')
    def analysis_detail(aid):
        with connect(path) as db:
            row=db.execute('SELECT * FROM article_analysis WHERE article_id=?',(aid,)).fetchone()
            article=db.execute('SELECT * FROM articles WHERE id=?',(aid,)).fetchone()
            if not row or not article:raise ValueError('Run Phase 3 for this article first.')
            result=dict(row);result['analysis']=effective(row);result['manual']=json.loads(result['manual']);result.pop('vector');result.pop('data')
            from .classification import source_text
            result.update(title=article['title'],url=article['url'],input_text=result['analysis'].get('input_text') or source_text(article)[0],stale=input_hash(article)!=row['input_hash'])
            result['calls']=[dict(r) for r in db.execute('SELECT c.* FROM processing_calls c JOIN processing_runs r ON r.id=c.run_id WHERE c.item_id=? AND r.phase IN (3,4) ORDER BY c.id DESC LIMIT 20',(aid,))]
        return jsonify(result)

    @bp.patch('/api/analysis/articles/<int:aid>')
    def analysis_edit(aid):
        body=request.get_json();patch={}
        if 'categories' in body:
            if not isinstance(body['categories'],list) or any(c not in CATEGORIES for c in body['categories']):raise ValueError('Choose valid news categories.')
            patch['categories']=[{'name':c,'confidence':1,'evidence':None,'manual':True} for c in dict.fromkeys(body['categories'])]
        for field in ('entities','locations'):
            if field in body:
                values=body[field]
                if not isinstance(values,list) or len(values)>50:raise ValueError('Use up to 50 evidence-linked values.')
                with connect(path) as db:article=db.execute('SELECT * FROM articles WHERE id=?',(aid,)).fetchone()
                if not article:raise ValueError('Article not found.')
                from .classification import source_text, span
                text=source_text(article)[0];clean=[]
                for value in values:
                    if not isinstance(value,dict) or not isinstance(value.get('name'),str) or not value['name'].strip():raise ValueError('Each value needs a name.')
                    quote=(value.get('evidence') or {}).get('quote','');evidence=span(text,quote)
                    if not evidence:raise ValueError('Each entity/location needs an exact supporting quote from the input text.')
                    clean.append({'name':value['name'][:160],'type':value.get('type','location' if field=='locations' else 'unknown'),
                                  'confidence':1,'manual':True,'evidence':evidence})
                patch[field]=clean
        if 'content_type' in body:
            if body['content_type'] not in CONTENT_TYPES:raise ValueError('Invalid content type.')
            patch['content_type']=body['content_type'];patch['content_type_confidence']=1
        if 'geographic_scope' in body:
            if body['geographic_scope'] not in SCOPES:raise ValueError('Invalid geographic scope.')
            patch['geographic_scope']=body['geographic_scope']
        if 'event_time' in body:
            if body.get('event_precision','date') not in ('date','datetime','approximate','unknown'):raise ValueError('Invalid time precision.')
            value=body['event_time'];evidence=body.get('event_evidence','')
            from .grouping import stamp
            if value and not stamp(value):raise ValueError('Use an ISO date or timestamp, or leave it empty for unknown.')
            if value and not evidence.strip():raise ValueError('Explain the supporting evidence for a corrected event date.')
            patch['event_time']={'value':value or None,'precision':body.get('event_precision','date') if value else 'unknown','confidence':1 if value else 0,'evidence':{'quote':evidence,'manual':True} if value else None}
        if not patch:raise ValueError('No correction supplied.')
        with connect(path) as db:
            row=db.execute('SELECT * FROM article_analysis WHERE article_id=?',(aid,)).fetchone()
            if not row:raise ValueError('Analysis not found.')
            before=json.loads(row['manual']);after={**before,**patch}
            db.execute('UPDATE article_analysis SET manual=?,processed_at=? WHERE article_id=?',(json.dumps(after),now(),aid));correction(db,'article metadata',aid,before,after,body.get('reason','Manual correction.'))
        return jsonify(ok=True)

    @bp.get('/api/stories')
    def stories_list():
        from .windows import date_window,group_condition
        page=max(1,request.args.get('page',1,type=int));size=50
        month=request.args.get('month','all');mode=request.args.get('size','0');q='%'+request.args.get('q','')+'%'
        with connect(path) as db:
            condition,args=group_condition(month,date_window(db),'dated')
            base=""" FROM news_stories s JOIN story_members m ON m.story_id=s.id JOIN articles a ON a.id=m.article_id
              WHERE s.merged_into IS NULL AND m.role='primary'
              AND EXISTS(SELECT 1 FROM story_members sm JOIN articles dated ON dated.id=sm.article_id WHERE sm.story_id=s.id AND """+condition+""")
              AND (s.title LIKE ? OR EXISTS(SELECT 1 FROM story_members sm JOIN articles found ON found.id=sm.article_id WHERE sm.story_id=s.id AND found.title LIKE ?))
              GROUP BY s.id """
            having="HAVING COUNT(*)>=2" if mode=='2' else "HAVING COUNT(*)=1" if mode=='1' else ''
            params=args+[q,q]
            total=db.execute('SELECT COUNT(*) FROM (SELECT s.id'+base+having+')',params).fetchone()[0]
            rows=db.execute('SELECT s.*,COUNT(DISTINCT m.article_id) article_count,COUNT(DISTINCT a.publisher_id) publisher_count'+base+having+
                ' ORDER BY article_count DESC,publisher_count DESC,s.updated_at DESC,s.id DESC LIMIT ? OFFSET ?',params+[size,(page-1)*size]).fetchall()
            review=db.execute("""SELECT COUNT(*) FROM story_candidates c JOIN news_stories s ON s.id=c.story_id
              WHERE c.decision='review' AND c.score>=.45 AND s.merged_into IS NULL
              AND NOT EXISTS(SELECT 1 FROM story_members m WHERE m.article_id=c.article_id AND m.story_id=c.story_id AND m.role='primary')""").fetchone()[0]
        return jsonify(items=[dict(r) for r in rows],review_count=review,total=total,page=page,pages=max(1,(total+size-1)//size))

    @bp.get('/api/stories/<int:sid>')
    def stories_detail(sid):
        with connect(path) as db:
            data=story_detail(db,sid);data['candidates']=[dict(r) for r in db.execute('''SELECT c.*,a.title FROM story_candidates c JOIN articles a ON a.id=c.article_id
              WHERE c.story_id=? OR c.article_id IN (SELECT article_id FROM story_members WHERE story_id=?) ORDER BY score DESC LIMIT 60''',(sid,sid))]
        return jsonify(data)

    @bp.post('/api/stories/<int:sid>/actions')
    def stories_action(sid):
        body=request.get_json();action=body.get('action')
        with connect(path) as db:
            db.execute('BEGIN IMMEDIATE')
            if action=='merge':merge_stories(db,sid,body.get('story_ids',[]))
            elif action=='split':return jsonify(id=split_story(db,sid,body.get('article_ids',[])))
            elif action=='assign':assign_story(db,int(body['article_id']),sid,True,'Manual reassignment.',1,body.get('role','primary'))
            elif action=='reject':
                db.execute("UPDATE story_candidates SET decision='manual_reject',reason='Rejected by reviewer.' WHERE article_id=? AND story_id=?",(body['article_id'],sid))
                correction(db,'story rejection',body['article_id'],{}, {'story_id':sid},'Manual rejection.')
            else:raise ValueError('Choose merge, split, assign, or reject.')
        return jsonify(ok=True)

    @bp.get('/api/topics')
    def topics_list():
        with connect(path) as db:
            rows=db.execute('''SELECT t.*,COUNT(DISTINCT CASE WHEN m.state='included' AND s.merged_into IS NULL THEN m.story_id END) story_count
             FROM news_topics t LEFT JOIN topic_members m ON m.topic_id=t.id LEFT JOIN news_stories s ON s.id=m.story_id
             WHERE t.merged_into IS NULL AND (t.title LIKE ? OR t.description LIKE ?)
             GROUP BY t.id ORDER BY t.pinned DESC,t.updated_at DESC LIMIT 200''',('%'+request.args.get('q','')+'%',)*2).fetchall()
        result=[]
        for row in rows:
            r=dict(row);r.pop('vector',None);result.append(r)
        return jsonify(items=result)

    @bp.post('/api/topics')
    def topics_create():
        body=request.get_json()
        with connect(path) as db:tid=create_topic(db,body.get('title',''),body.get('description',''),manual=True)
        return jsonify(id=tid),201

    @bp.get('/api/topics/<int:tid>')
    def topics_detail(tid):
        with connect(path) as db:result=topic_detail(db,tid);result.pop('vector',None)
        return jsonify(result)

    @bp.post('/api/topics/<int:tid>/actions')
    def topics_action(tid):
        body=request.get_json();action=body.get('action')
        with connect(path) as db:
            db.execute('BEGIN IMMEDIATE');old=db.execute('SELECT * FROM news_topics WHERE id=? AND merged_into IS NULL',(tid,)).fetchone()
            if not old:raise ValueError('Topic not found.')
            if action=='rename':
                title=body.get('title','').strip();description=body.get('description',old['description']).strip()
                if not title or len(title.split())>6 or len(description)<10:raise ValueError('Use a title up to six words and a specific description.')
                db.execute('UPDATE news_topics SET title=?,description=?,keywords=?,manual=1,vector=NULL,updated_at=? WHERE id=?',(title,description,json.dumps(topic_keywords(title,description)),now(),tid))
            elif action=='pin':db.execute('UPDATE news_topics SET pinned=?,manual=1,updated_at=? WHERE id=?',(int(bool(body.get('pinned'))),now(),tid))
            elif action in ('add','remove'):link_topic(db,tid,int(body['story_id']),True,'included' if action=='add' else 'excluded','Manual membership correction.',1)
            elif action=='merge':
                other_ids=body.get('topic_ids',[])
                if not other_ids or tid in other_ids:raise ValueError('Select different topics to merge.')
                for oid in other_ids:
                    if not db.execute('SELECT 1 FROM news_topics WHERE id=? AND merged_into IS NULL',(oid,)).fetchone():raise ValueError('Unknown topic.')
                    for m in db.execute('SELECT * FROM topic_members WHERE topic_id=?',(oid,)).fetchall():
                        if db.execute('SELECT 1 FROM news_stories WHERE id=? AND merged_into IS NULL',(m['story_id'],)).fetchone():
                            existing=db.execute('SELECT state FROM topic_members WHERE topic_id=? AND story_id=?',(tid,m['story_id'])).fetchone()
                            if existing and existing['state']=='included' and m['state']=='excluded':continue
                            link_topic(db,tid,m['story_id'],True,m['state'],'Manual topic merge.',1)
                    db.execute('UPDATE news_topics SET merged_into=?,updated_at=? WHERE id=?',(tid,now(),oid))
            elif action=='split':
                ids=body.get('story_ids',[]);members={r[0] for r in db.execute("SELECT story_id FROM topic_members WHERE topic_id=? AND state='included'",(tid,))}
                if not ids or not set(ids)<members:raise ValueError('Select some, but not all, included stories.')
                new=create_topic(db,body.get('title',''),body.get('description',''),manual=True)
                for sid in ids:
                    link_topic(db,tid,sid,True,'excluded','Manual topic split.',1);link_topic(db,new,sid,True,'included','Manual topic split.',1)
                correction(db,'topic split',tid,{'story_ids':ids},{'new_topic_id':new},'Manual split.');return jsonify(id=new)
            else:raise ValueError('Unknown topic action.')
            correction(db,'topic '+action,tid,{'title':old['title'],'description':old['description']},body,'Manual topic correction.')
        return jsonify(ok=True)

    @bp.get('/api/analysis/evaluations')
    def evaluations():
        with connect(path) as db:rows=[dict(r) for r in db.execute('SELECT * FROM processing_evaluations ORDER BY id DESC LIMIT 10')]
        for r in rows:r['results']=json.loads(r['results'])
        return jsonify(items=rows)

    app.register_blueprint(bp)
