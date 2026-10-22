"""Conservative event matching, durable memberships, and evolving topic links."""
import json
import re
from datetime import datetime, timezone
from .storage import connect, now
from .analysis_store import effective, correction
from .classification import identity, tokens, lexical_similarity, cosine, source_text, discriminators

def stamp(value):
    try:
        dt=datetime.fromisoformat(value.replace('Z','+00:00'))
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
    except (TypeError,ValueError,AttributeError):return None

def event_names(data):
    return {identity(e['name']) for e in data.get('entities',[]) if e.get('confidence',0)>=.5}

def compare_events(left,right,left_article,right_article,semantic=0):
    names=event_names(left)&event_names(right)
    lexical=lexical_similarity(left_article['title'],right_article['title'])
    reasons=[];hard=[]
    la,ra=left.get('action','unknown'),right.get('action','unknown')
    if la!='unknown' and ra!='unknown' and la!=ra:hard.append('Different event actions: '+la+' / '+ra)
    ld,rd=left.get('event_time',{}),right.get('event_time',{})
    exact=lambda d:d.get('value') and d.get('precision') in ('date','datetime') and d.get('confidence',0)>=.7
    if exact(ld) and exact(rd) and ld['value'][:10]!=rd['value'][:10]:hard.append('Different evidenced event dates.')
    # Background years, prices and historical results in the body are not event IDs.
    # Headline qualifiers are safer hard guards; evidenced event dates are checked above.
    ldetails=set(discriminators(left_article['title']));rdetails=set(discriminators(right_article['title']))
    for pattern in (r'q[1-4]|(?:first|second|third|fourth) quarter',r'game \d+',r'20\d{2}'):
        a={x for x in ldetails if re.fullmatch(pattern,x)};b={x for x in rdetails if re.fullmatch(pattern,x)}
        if a and b and a.isdisjoint(b):hard.append('Different quarter, game, or year details.')
    lt,rt=stamp(left_article['published_at']),stamp(right_article['published_at'])
    hours=abs((lt-rt).total_seconds())/3600 if lt and rt else None
    if hours is not None and hours>72 and not (exact(ld) and exact(rd) and ld['value'][:10]==rd['value'][:10]):
        reasons.append('Outside 72 hours; delayed coverage needs evidence review.')
    if la in ('earnings','match') and not (exact(ld) and exact(rd)) and ldetails!=rdetails:
        hard.append('Recurring event details differ or are incomplete.')
    location_aliases={'new york city':'new york','nyc':'new york','uk':'united kingdom','u.k.':'united kingdom','usa':'united states'}
    location_name=lambda value:location_aliases.get(identity(value),identity(value))
    loc1={location_name(x['name']) for x in left.get('locations',[])};loc2={location_name(x['name']) for x in right.get('locations',[])}
    if loc1 and loc2 and loc1.isdisjoint(loc2):reasons.append('Locations differ; review required.')
    if any(d.get('input_suspect') or d.get('input_limited') or any(w.startswith(('Suspected biography','Limited input','Multiple event dates')) for w in d.get('warnings',[])) for d in (left,right)):
        reasons.append('Limited or suspect source text; review required.')
    score=round(.48*lexical+.35*max(0,semantic)+.12*min(1,len(names)/2)+.05*(la==ra and la!='unknown'),4)
    high=(lexical>=.88 and (semantic>=.80 or lexical>=.98) and bool(names) and la==ra and la!='unknown' and not reasons and not hard)
    return {'score':score,'lexical':round(lexical,4),'semantic':round(semantic,4),'shared_entities':sorted(names),
            'hours_apart':hours,'hard_conflicts':hard,'review_reasons':reasons,
            'decision':'reject' if hard else 'same_event' if high else 'review',
            'reason':'; '.join(hard+reasons) or ('Near-identical event headline and participants.' if high else 'Semantic overlap alone is insufficient to establish the same event.')}

def story_candidates(db,article,analysis,limit=20):
    # Lexical retrieval is unbounded in time so delayed coverage can find an old event.
    query=' OR '.join('"'+t+'"' for t in dict.fromkeys(tokens(article['title'])) if len(t)>3)
    lexical_ids=[]
    if query:
        lexical_ids=[r[0] for r in db.execute('SELECT rowid FROM analysis_search WHERE analysis_search MATCH ? ORDER BY rank LIMIT 200',(query,))]
    args=[article['id']];condition=''
    if lexical_ids:condition=' OR s.anchor_article_id IN ('+','.join('?'*len(lexical_ids))+')';args+=lexical_ids
    rows=db.execute('''SELECT s.id story_id,a.id,a.title,a.published_at,x.data,x.manual,x.vector,x.embedding_model,x.embedding_dimensions
      FROM news_stories s JOIN articles a ON a.id=s.anchor_article_id JOIN article_analysis x ON x.article_id=a.id
      WHERE s.merged_into IS NULL AND EXISTS(SELECT 1 FROM story_members sm WHERE sm.story_id=s.id AND sm.article_id=s.anchor_article_id AND sm.role='primary') AND a.id!=? AND (ABS(julianday(a.published_at)-julianday((SELECT published_at FROM articles WHERE id=?)))<=3'''+condition+''')''',
      [article['id'],article['id']]+lexical_ids).fetchall()
    vector=json.loads(analysis['vector']) if analysis['vector'] else None
    scores={}
    compatible=[r for r in rows if vector and r['vector'] and r['embedding_model']==analysis['embedding_model'] and r['embedding_dimensions']==analysis['embedding_dimensions']]
    if compatible:
        try:
            import numpy as np
            sims=np.asarray([json.loads(r['vector']) for r in compatible],dtype='float32')@np.asarray(vector,dtype='float32')
            scores={r['story_id']:float(v) for r,v in zip(compatible,sims)}
        except ImportError:scores={r['story_id']:cosine(vector,json.loads(r['vector'])) for r in compatible}
    result=[];left=effective(analysis)
    for row in rows:
        decision=compare_events(left,effective(row),article,row,scores.get(row['story_id'],0))
        result.append({'story_id':row['story_id'],'anchor_article_id':row['id'],'title':row['title'],**decision})
    return sorted(result,key=lambda r:r['score'],reverse=True)[:limit]

def new_story(db,article_id):
    article=db.execute('SELECT * FROM articles WHERE id=?',(article_id,)).fetchone()
    if not article:raise ValueError('Article does not exist.')
    return db.execute('INSERT INTO news_stories(title,anchor_article_id,created_at,updated_at) VALUES(?,?,?,?)',
                      (article['title'],article_id,now(),now())).lastrowid

def assign_story(db,article_id,story_id,manual=False,reason='Initial event candidate.',confidence=1,role='primary'):
    if not db.execute('SELECT 1 FROM articles WHERE id=?',(article_id,)).fetchone():raise ValueError('Article not found.')
    if role not in ('primary','reference'):raise ValueError('Choose primary or reference membership.')
    same=db.execute('SELECT role FROM story_members WHERE article_id=? AND story_id=?',(article_id,story_id)).fetchone()
    if same and same[0]=='primary' and role=='reference':return story_id
    story=db.execute('SELECT * FROM news_stories WHERE id=? AND merged_into IS NULL',(story_id,)).fetchone()
    if not story:raise ValueError('Select an active story.')
    current=db.execute("SELECT * FROM story_members WHERE article_id=? AND role='primary'",(article_id,)).fetchone()
    if current and current['manual'] and not manual:return current['story_id']
    if role=='primary':db.execute("DELETE FROM story_members WHERE article_id=? AND role='primary'",(article_id,))
    db.execute('''INSERT INTO story_members VALUES(?,?,?,?,?,?,?) ON CONFLICT(article_id,story_id) DO UPDATE SET
       role=excluded.role,manual=MAX(story_members.manual,excluded.manual),reason=excluded.reason,confidence=excluded.confidence''',
       (article_id,story_id,role,int(manual),reason,confidence,now()))
    db.execute('UPDATE news_stories SET updated_at=? WHERE id=?',(now(),story_id))
    if manual:correction(db,'story membership',article_id,dict(current) if current else {},{'story_id':story_id,'role':role},reason)
    if current and current['story_id']!=story_id:
        # Keep the old anchor inside its own surviving cluster.
        old=current['story_id'];replacement=db.execute("SELECT article_id FROM story_members WHERE story_id=? AND role='primary' ORDER BY article_id LIMIT 1",(old,)).fetchone()
        if replacement:db.execute('UPDATE news_stories SET anchor_article_id=?,updated_at=? WHERE id=?',(replacement[0],now(),old))
        elif not db.execute('SELECT 1 FROM story_members WHERE story_id=?',(old,)).fetchone():
            # Preserve an empty former story as an alias, and its topic links, after reassignment.
            for membership in db.execute('SELECT * FROM topic_members WHERE story_id=?',(old,)).fetchall():
                db.execute('INSERT OR IGNORE INTO topic_members VALUES(?,?,?,?,?,?,?)',
                    (membership['topic_id'],story_id,membership['state'],membership['manual'],membership['confidence'],
                     'Inherited from reassigned story '+str(old)+'.',now()))
            db.execute('UPDATE news_stories SET merged_into=?,updated_at=? WHERE id=?',(story_id,now(),old))
    return story_id

def merge_stories(db,target,others):
    if target in others or not others:raise ValueError('Select different story IDs to merge.')
    for sid in [target]+others:
        if not db.execute('SELECT 1 FROM news_stories WHERE id=? AND merged_into IS NULL',(sid,)).fetchone():raise ValueError('Unknown or merged story.')
    for sid in others:
        members=db.execute('SELECT * FROM story_members WHERE story_id=?',(sid,)).fetchall()
        for m in members:assign_story(db,m['article_id'],target,True,'Manual story merge.',1,m['role'])
        db.execute('DELETE FROM story_members WHERE story_id=?',(sid,))
        for m in db.execute('SELECT * FROM topic_members WHERE story_id=?',(sid,)).fetchall():
            db.execute('INSERT OR IGNORE INTO topic_members VALUES(?,?,?,?,?,?,?)',(m['topic_id'],target,m['state'],1,m['confidence'],'Inherited manual story merge.',now()))
        db.execute('UPDATE news_stories SET merged_into=?,updated_at=? WHERE id=?',(target,now(),sid))
    correction(db,'story merge',target,{'merged_ids':others},{'target':target},'Manual merge; original story IDs preserved as aliases.')

def split_story(db,sid,article_ids):
    members={r[0] for r in db.execute("SELECT article_id FROM story_members WHERE story_id=? AND role='primary'",(sid,))}
    if not article_ids or not set(article_ids)<members:raise ValueError('Select some, but not all, primary articles to split.')
    target=new_story(db,article_ids[0])
    for aid in article_ids:assign_story(db,aid,target,True,'Manual story split.')
    correction(db,'story split',sid,{'article_ids':article_ids},{'new_story_id':target},'Manual split.')
    return target

def story_detail(db,sid):
    row=db.execute('SELECT * FROM news_stories WHERE id=?',(sid,)).fetchone()
    if not row:raise ValueError('Story not found.')
    data=dict(row);members=[];categories={};entities={};locations={};times=[];origins=set();publishers=set()
    for a in db.execute('''SELECT a.id,a.title,a.url,a.author,a.content,a.published_at,a.images_json,p.name publisher,
      m.role,m.manual membership_manual,m.reason membership_reason,m.confidence membership_confidence,x.data,x.manual
      FROM story_members m JOIN articles a ON a.id=m.article_id JOIN publishers p ON p.id=a.publisher_id
      LEFT JOIN article_analysis x ON x.article_id=a.id WHERE m.story_id=? ORDER BY a.published_at''',(sid,)):
        a=dict(a);features=effective(a) if a['data'] else {};publishers.add(a['publisher'])
        credit=a['author'] or ''
        wire=next((wire for wire in ['Associated Press','Reuters','AFP'] if wire.lower() in credit.lower()),None)
        similar=next((m for m in members if lexical_similarity(a['content'][:6000],m.get('_text',''))>.94),None)
        origin=wire or (similar['reporting_group'] if similar else ('byline:'+credit.lower() if len(credit)>5 else 'article:'+str(a['id'])))
        origins.add(origin);a['reporting_group']=origin;a['syndication']='wire/byline match' if wire else 'near-identical text' if similar else 'not established'
        for field,target in [('categories',categories),('entities',entities),('locations',locations)]:
            for e in features.get(field,[]):target.setdefault(e['name'],{**e,'article_id':a['id']})
        if features.get('event_time',{}).get('value'):times.append({**features['event_time'],'article_id':a['id']})
        a['_text']=a.pop('content');a['analysis']=features;a['images']=json.loads(a.pop('images_json') or '[]');a.pop('data');a.pop('manual');members.append(a)
    for m in members:m.pop('_text',None)
    data.update(articles=members,categories=list(categories),entities=list(entities.values()),locations=list(locations.values()),
                event_times=times,publisher_count=len(publishers),reporting_group_count=len(origins),
                reporting_count_basis='Estimated text/byline groups; independent reporting is not verified.',
                topics=[dict(r) for r in db.execute('SELECT t.id,t.title,m.state,m.manual FROM topic_members m JOIN news_topics t ON t.id=m.topic_id WHERE m.story_id=? AND t.merged_into IS NULL',(sid,))])
    return data

def topic_keywords(title,description):return list(dict.fromkeys(tokens(title+' '+description)))[:35]

def topic_match(hint,topic,semantic=0):
    left=set(topic_keywords(hint['title'],hint['description']));right=set(json.loads(topic['keywords']))
    overlap=left&right
    # At least two specific subject words: a person's name alone is insufficient.
    similarity=lexical_similarity(hint['title']+' '+hint['description'],topic['title']+' '+topic['description'])
    related=len(overlap)>=3 and (similarity>=.62 or semantic>=.88)
    return related,round(max(similarity,semantic),4),sorted(overlap)

def link_topic(db,tid,sid,manual=False,state='included',reason='Related topic description.',confidence=.7):
    if not db.execute('SELECT 1 FROM news_topics WHERE id=? AND merged_into IS NULL',(tid,)).fetchone():raise ValueError('Topic not found.')
    if not db.execute('SELECT 1 FROM news_stories WHERE id=? AND merged_into IS NULL',(sid,)).fetchone():raise ValueError('Story not found.')
    old=db.execute('SELECT * FROM topic_members WHERE topic_id=? AND story_id=?',(tid,sid)).fetchone()
    if old and old['manual'] and not manual:return
    db.execute('''INSERT INTO topic_members VALUES(?,?,?,?,?,?,?) ON CONFLICT(topic_id,story_id) DO UPDATE SET
      state=excluded.state,manual=MAX(topic_members.manual,excluded.manual),confidence=excluded.confidence,reason=excluded.reason''',
      (tid,sid,state,int(manual),confidence,reason,now()))
    db.execute('UPDATE news_topics SET updated_at=? WHERE id=?',(now(),tid))
    if manual:correction(db,'topic membership',sid,dict(old) if old else {},{'topic_id':tid,'state':state},reason)

def create_topic(db,title,description,sid=None,manual=False):
    if not title.strip() or len(title.split())>6:raise ValueError('Topic titles must contain one to six words.')
    if len(description.strip())<10:raise ValueError('Describe the specific continuing subject.')
    return db.execute('INSERT INTO news_topics(title,description,anchor_story_id,keywords,created_at,updated_at,manual) VALUES(?,?,?,?,?,?,?)',
       (title.strip(),description.strip(),sid,json.dumps(topic_keywords(title,description)),now(),now(),int(manual))).lastrowid

def topic_detail(db,tid):
    row=db.execute('SELECT * FROM news_topics WHERE id=?',(tid,)).fetchone()
    if not row:raise ValueError('Topic not found.')
    result=dict(row);timeline=[];unknown=[];categories=set();publishers=set();entities={};locations={}
    for m in db.execute('''SELECT m.* FROM topic_members m JOIN news_stories s ON s.id=m.story_id
      WHERE m.topic_id=? AND m.state='included' AND s.merged_into IS NULL''',(tid,)):
        story=story_detail(db,m['story_id']);times=story['event_times']
        distinct={t['value'][:10] for t in times}
        event=times[0] if len(distinct)==1 else {'value':None,'precision':'conflicting' if distinct else 'unknown'}
        entry={'id':story['id'],'title':story['title'],'event_time':event,'published_at':story['created_at'],
               'source_publications':[a['published_at'] for a in story['articles']],
               'sources':sorted({a['publisher'] for a in story['articles']}),'membership':dict(m)}
        (timeline if event.get('value') else unknown).append(entry)
        categories.update(story['categories']);publishers.update(entry['sources'])
        for e in story['entities']:entities.setdefault(e['name'],e)
        for e in story['locations']:locations.setdefault(e['name'],e)
    result['candidates']=[dict(r) for r in db.execute('SELECT c.*,s.title FROM topic_candidates c JOIN news_stories s ON s.id=c.story_id WHERE c.topic_id=? AND s.merged_into IS NULL ORDER BY c.score DESC LIMIT 50',(tid,))]
    result.update(timeline=sorted(timeline,key=lambda x:stamp(x['event_time']['value']) or datetime.max.replace(tzinfo=timezone.utc)),undated_stories=unknown,
                  categories=sorted(categories),sources=sorted(publishers),entities=list(entities.values()),locations=list(locations.values()))
    return result
