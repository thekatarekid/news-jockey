"""Persistent manual extraction jobs and mutually exclusive article leases."""
import json
import uuid
import os
import logging
from datetime import datetime, timezone, timedelta
from .storage import connect, now
from .windows import date_window, group_condition, month_window

ACTIVE_STATUSES=('queued','running','paused')

def job_data(db,job_id):
    row=db.execute('SELECT * FROM extraction_jobs WHERE id=?',(job_id,)).fetchone()
    if not row: return None
    counts={r['state']:r['n'] for r in db.execute('SELECT state,COUNT(*) n FROM extraction_job_items WHERE job_id=? GROUP BY state',(job_id,))}
    reused=db.execute("SELECT COUNT(*) FROM extraction_job_items WHERE job_id=? AND state='done' AND CASE WHEN json_valid(result) THEN json_extract(result,'$.reused_saved_body') ELSE 0 END=1",(job_id,)).fetchone()[0]
    shared=db.execute("SELECT COUNT(*) FROM extraction_job_items WHERE job_id=? AND CASE WHEN json_valid(result) THEN json_extract(result,'$.reused_attempt')=1 AND json_extract(result,'$.reused_saved_body') IS NOT 1 ELSE 0 END",(job_id,)).fetchone()[0]
    waiting=db.execute("""SELECT COUNT(*) FROM extraction_job_items i JOIN articles a INDEXED BY extraction_article_work ON a.id=i.article_id
      JOIN extraction_hosts h ON h.host=a.url_host || ? WHERE i.job_id=? AND i.state='pending' AND h.next_allowed>?""",
      ('|browser' if row['transport']=='browser' else '',job_id,now())).fetchone()[0]
    return {**dict(row),'counts':counts,'reused_complete':reused,'reused_results':reused+shared,'waiting_for_host':waiting,'processed':sum(counts.get(k,0) for k in ('done','failed','skipped')),
            'remaining':counts.get('pending',0),'in_flight':counts.get('running',0)}

def selection(transport,scope,window):
    """Identity is the effective interval, never a relative UI label."""
    if scope=='single':return None,scope
    if scope in ('older','previous'):bounds=('before',None,window['start']);label='older'
    elif scope=='undated':bounds=('undated',None,None);label=scope
    elif scope=='all':bounds=('all',None,None);label=scope
    else:
        bounds=('month',window['start'],window['end'])
        from zoneinfo import ZoneInfo
        label=datetime.fromisoformat(window['start']).astimezone(ZoneInfo(window['timezone'])).strftime('%Y-%m')
    return json.dumps([transport,*bounds],separators=(',',':')),label


def complete_if_empty(db,jid):
    db.execute("""UPDATE extraction_jobs SET status='completed',updated_at=? WHERE id=?
      AND status IN ('queued','running','paused') AND NOT EXISTS
      (SELECT 1 FROM extraction_job_items WHERE job_id=? AND state IN ('pending','running'))""",(now(),jid,jid))


def settle_saved(db,jid,transport,reconcile=False):
    # Completed attempts are shared across overlapping broad selections, including
    # failures/partial bodies. A backfill is not an implicit retry command.
    db.execute("""UPDATE extraction_job_items AS i SET
      state=(SELECT r.state FROM extraction_results r WHERE r.article_id=i.article_id AND r.transport=?),
      result=(SELECT json_set(r.result,'$.reused_attempt',1) FROM extraction_results r WHERE r.article_id=i.article_id AND r.transport=?),
      finished_at=(SELECT r.finished_at FROM extraction_results r WHERE r.article_id=i.article_id AND r.transport=?)
      WHERE i.job_id=? AND (i.state='pending' OR (? AND i.state IN ('done','failed'))) AND EXISTS
      (SELECT 1 FROM extraction_results r JOIN articles a INDEXED BY extraction_article_work ON a.id=r.article_id
       WHERE r.article_id=i.article_id AND r.transport=? AND r.fingerprint=a.fingerprint
       AND a.lease_token IS NULL AND a.extraction_status!='pending')""",(transport,transport,transport,jid,reconcile,transport))
    if transport=='http':
        db.execute("""UPDATE extraction_job_items SET state='done',result='{"status":"complete","reused_saved_body":true}',finished_at=?
          WHERE job_id=? AND state='pending' AND article_id IN
          (SELECT a.id FROM articles a INDEXED BY extraction_article_work JOIN extraction_job_items i ON i.article_id=a.id
           WHERE i.job_id=? AND a.body_quality='full' AND a.extraction_status='complete' AND substr(a.content,1,40)!=''
           AND substr(a.content,1,40) NOT LIKE 'This article was written by%' AND a.lease_token IS NULL)""",(now(),jid,jid))


def initialize_jobs(path):
    """One-time reconciliation before extraction workers start; retain job history."""
    from .storage import add_columns
    with connect(path) as db:
        add_columns(db,'extraction_jobs',{'selection_key':'TEXT','merged_into':'TEXT REFERENCES extraction_jobs(id)'})
        add_columns(db,'extraction_job_items',{'input_fingerprint':'TEXT'})
        db.execute('CREATE INDEX IF NOT EXISTS extraction_item_article ON extraction_job_items(article_id,state)')
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='extraction_article_work'").fetchone():
            logging.getLogger(__name__).warning('Preparing compact extraction queue index')
        db.execute('CREATE INDEX IF NOT EXISTS extraction_article_work ON articles(id,fingerprint,extraction_status,lease_token,body_quality,substr(content,1,40),duplicate_of,discovery_rejected,published_at,url_host)')
        db.execute('DROP INDEX IF EXISTS extraction_article_identity')
        if db.execute("SELECT 1 FROM settings WHERE key='extraction_jobs_v2'").fetchone():return
        # Article rows contain large bodies. Use a narrow covering index for the
        # historical import instead of repeatedly reading body overflow pages.
        db.execute('PRAGMA cache_size=-65536')
        db.execute('BEGIN IMMEDIATE')
        recover_leases(db)
        if db.execute('SELECT 1 FROM articles WHERE lease_token IS NOT NULL LIMIT 1').fetchone():
            raise RuntimeError('Stop extraction workers before reconciling legacy jobs.')
        db.execute('UPDATE extraction_job_items SET input_fingerprint=(SELECT fingerprint FROM articles INDEXED BY extraction_article_work WHERE id=article_id) WHERE input_fingerprint IS NULL')
        # Import only outcomes still consistent with the current article. Feed
        # updates already marked pending must not inherit an obsolete attempt.
        logging.getLogger(__name__).warning('Reconciling extraction queues: importing saved outcomes')
        for r in db.execute("""SELECT i.*,j.transport,a.fingerprint,a.extraction_status FROM extraction_job_items i
          JOIN extraction_jobs j ON j.id=i.job_id JOIN articles a INDEXED BY extraction_article_work ON a.id=i.article_id
          WHERE i.state IN ('done','failed') AND json_valid(i.result) ORDER BY i.finished_at""").fetchall():
            result=json.loads(r['result'])
            if result.get('status')!=r['extraction_status']:continue
            db.execute('INSERT OR REPLACE INTO extraction_results VALUES(?,?,?,?,?,?)',
                       (r['article_id'],r['transport'],r['fingerprint'],r['state'],r['result'],r['finished_at'] or now()))
        logging.getLogger(__name__).warning('Reconciling extraction queues: merging equivalent selections')
        keep={}
        for j in db.execute('SELECT * FROM extraction_jobs ORDER BY created_at,id').fetchall():
            key,label=selection(j['transport'],j['scope'],dict(start=j['window_start'],end=j['window_end'],timezone=j['timezone']))
            if not key or j['status']=='cancelled':continue
            if key not in keep:
                keep[key]=j['id'];db.execute('UPDATE extraction_jobs SET selection_key=?,scope=? WHERE id=?',(key,label,j['id']));continue
            master=keep[key]
            db.execute("""INSERT OR IGNORE INTO extraction_job_items(job_id,article_id,state,result,finished_at,input_fingerprint)
              SELECT ?,article_id,state,result,finished_at,input_fingerprint FROM extraction_job_items WHERE job_id=?""",(master,j['id']))
            db.execute("UPDATE extraction_jobs SET status='merged',merged_into=?,selection_key=?,updated_at=? WHERE id=?",(master,key,now(),j['id']))
            db.execute("UPDATE extraction_job_items SET state='merged' WHERE job_id=? AND state IN ('pending','running')",(j['id'],))
        for jid in keep.values():
            j=db.execute('SELECT * FROM extraction_jobs WHERE id=?',(jid,)).fetchone()
            settle_saved(db,jid,j['transport'],reconcile=True)
            db.execute("UPDATE extraction_job_items SET state='skipped',result='Canonical duplicate or excluded inventory' WHERE job_id=? AND state='pending' AND EXISTS(SELECT 1 FROM articles a INDEXED BY extraction_article_work WHERE a.id=article_id AND (a.duplicate_of IS NOT NULL OR a.discovery_rejected=1))",(jid,))
            db.execute('UPDATE extraction_jobs SET total=(SELECT COUNT(*) FROM extraction_job_items WHERE job_id=?) WHERE id=?',(jid,jid))
            if j['status']=='completed' and db.execute("SELECT 1 FROM extraction_job_items WHERE job_id=? AND state='pending'",(jid,)).fetchone():
                db.execute("UPDATE extraction_jobs SET status='queued' WHERE id=?",(jid,))
            complete_if_empty(db,jid)
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS extraction_selection_unique ON extraction_jobs(selection_key) WHERE merged_into IS NULL AND status IN ('queued','running','paused','completed')")
        db.execute("INSERT INTO settings(key,value) VALUES('extraction_jobs_v2',?)",(now(),))
        logging.getLogger(__name__).warning('Extraction queue reconciliation complete')


def create_job(path,transport,scope,article_ids=None):
    if transport not in ('http','browser'): raise ValueError('Invalid extraction job.')
    if scope=='single' and not article_ids: raise ValueError('Select an article for this job.')
    if article_ids and (len(article_ids)>50 or any(type(a) is not int for a in article_ids)): raise ValueError('Select at most 50 article IDs.')
    with connect(path) as db:
        db.execute('BEGIN IMMEDIATE')
        window=date_window(db)
        condition,args=group_condition(scope,window) if scope!='single' else ('1=1',[])
        if scope not in ('recent','current','older','previous','undated','all','single'):window=month_window(scope,window['timezone'])
        key,label=selection(transport,scope,window)
        if article_ids:key=None;label='single'
        old=db.execute("SELECT * FROM extraction_jobs WHERE selection_key=? AND merged_into IS NULL AND status IN ('queued','running','paused','completed')",(key,)).fetchone() if key else None
        jid=old['id'] if old else uuid.uuid4().hex;stamp=now()
        if not old:
            db.execute('INSERT INTO extraction_jobs(id,transport,scope,status,created_at,updated_at,timezone,window_start,window_end,selection_key) VALUES(?,?,?,?,?,?,?,?,?,?)',
                       (jid,transport,label,'queued',stamp,stamp,window['timezone'],window['start'],window['end'],key))
        if article_ids:
            condition+=' AND a.id IN ('+','.join('?' for _ in article_ids)+')';args.extend(article_ids)
        added=db.execute('''INSERT OR IGNORE INTO extraction_job_items(job_id,article_id,input_fingerprint)
          SELECT ?,a.id,a.fingerprint FROM articles a INDEXED BY extraction_article_work WHERE a.duplicate_of IS NULL AND a.discovery_rejected=0 AND '''+condition,[jid]+args).rowcount
        if label!='single':settle_saved(db,jid,transport)
        count=db.execute('SELECT COUNT(*) FROM extraction_job_items WHERE job_id=?',(jid,)).fetchone()[0]
        db.execute('UPDATE extraction_jobs SET total=?,updated_at=? WHERE id=?',(count,stamp,jid))
        if old and old['status']=='completed' and db.execute("SELECT 1 FROM extraction_job_items WHERE job_id=? AND state='pending'",(jid,)).fetchone():
            db.execute("UPDATE extraction_jobs SET status='queued' WHERE id=?",(jid,))
        complete_if_empty(db,jid)
        return {**job_data(db,jid),'existing':bool(old),'added':added}


def record_outcome(db,aid,transport,fingerprint,result):
    """One attempt resolves all overlapping broad queues for this input version."""
    if result.get('skipped') or result.get('status') in (None,'pending','extracting'):return
    state='failed' if result['status'] in ('failed','blocked','paywalled','unsupported') else 'done'
    stamp=now();encoded=json.dumps(result)
    db.execute('INSERT OR REPLACE INTO extraction_results VALUES(?,?,?,?,?,?)',(aid,transport,fingerprint,state,encoded,stamp))
    if db.execute('SELECT fingerprint FROM articles WHERE id=?',(aid,)).fetchone()[0]!=fingerprint:return
    jobs=[r[0] for r in db.execute("""SELECT j.id FROM extraction_job_items i JOIN extraction_jobs j ON j.id=i.job_id
       WHERE i.article_id=? AND (i.state='pending' OR (i.state='running' AND i.input_fingerprint=?))
       AND j.transport=? AND j.scope!='single' AND j.status IN ('queued','running','paused')""",(aid,fingerprint,transport))]
    for jid in jobs:
        db.execute("UPDATE extraction_job_items SET state=?,result=?,finished_at=?,input_fingerprint=? WHERE job_id=? AND article_id=?",(state,encoded,stamp,fingerprint,jid,aid))
        complete_if_empty(db,jid)


def control_job(path,jid,action):
    if action not in ('pause','resume','cancel'): raise ValueError('Choose pause, resume, or cancel.')
    with connect(path) as db:
        db.execute('BEGIN IMMEDIATE')
        job=job_data(db,jid)
        if not job: raise ValueError('Extraction job not found.')
        if job['status'] not in ACTIVE_STATUSES: raise ValueError('This job has already finished.')
        status={'pause':'paused','resume':'queued','cancel':'cancelled'}[action]
        db.execute('UPDATE extraction_jobs SET status=?,updated_at=? WHERE id=?',(status,now(),jid))
        if action=='cancel': db.execute("UPDATE extraction_job_items SET state='cancelled' WHERE job_id=? AND state='pending'",(jid,))
        return job_data(db,jid)

def lease_article(db,aid,job_id=None,token=None,automatic=False):
    token=token or str(os.getpid())+':'+uuid.uuid4().hex
    expiry=(datetime.now(timezone.utc)+timedelta(minutes=10)).isoformat(timespec='seconds')
    changed=db.execute('''UPDATE articles SET lease_token=?,lease_until=?,lease_job_id=?,extraction_status='extracting',extraction_attempted_at=?
       WHERE id=? AND duplicate_of IS NULL AND discovery_rejected=0 AND (lease_token IS NULL OR lease_until<?)
       AND NOT EXISTS(SELECT 1 FROM articles busy WHERE busy.url_host=articles.url_host AND busy.id!=articles.id AND busy.lease_token IS NOT NULL AND busy.lease_until>=?)
       AND (?=0 OR ((extraction_status='pending' OR (extraction_status IN ('failed','blocked') AND next_extraction<=?))
         AND NOT EXISTS(SELECT 1 FROM extraction_hosts h WHERE h.host=articles.url_host AND h.next_allowed>?)))''',
       (token,expiry,job_id,now(),aid,now(),now(),int(automatic),now(),now())).rowcount
    return token if changed else None

def release_article(path,aid,token):
    with connect(path) as db:
        db.execute("UPDATE articles SET extraction_status=CASE WHEN extraction_status='extracting' THEN 'pending' ELSE extraction_status END,lease_token=NULL,lease_until=NULL,lease_job_id=NULL WHERE id=? AND lease_token=?",(aid,token))

def recover_leases(db):
    stamp=now()
    db.execute("UPDATE articles SET extraction_status='pending',lease_token=NULL,lease_until=NULL,lease_job_id=NULL WHERE lease_token IS NOT NULL AND lease_until<?",(stamp,))
    db.execute("UPDATE articles SET extraction_status='pending' WHERE extraction_status='extracting' AND lease_token IS NULL")
    for row in db.execute('SELECT id,lease_token FROM articles WHERE lease_token IS NOT NULL').fetchall():
        try:
            pid=int(row['lease_token'].split(':',1)[0]);os.kill(pid,0)
        except ProcessLookupError:
            db.execute("UPDATE articles SET extraction_status='pending',lease_token=NULL,lease_until=NULL,lease_job_id=NULL WHERE id=?",(row['id'],))
        except (ValueError,PermissionError): pass
    db.execute("""UPDATE extraction_job_items SET state='pending' WHERE state='running' AND NOT EXISTS
       (SELECT 1 FROM articles a WHERE a.id=article_id AND a.lease_job_id=job_id AND a.lease_token IS NOT NULL)""")

def claim_manual(path,transport):
    # Candidate scans are read-only. Reserve the article in a short transaction;
    # lease_article atomically enforces article AND host exclusivity.
    with connect(path) as db:
        jobs=db.execute("SELECT * FROM extraction_jobs WHERE transport=? AND status IN ('queued','running') ORDER BY created_at LIMIT 20",(transport,)).fetchall()
    for job in jobs:
        with connect(path) as db:
            row=db.execute('''SELECT a.id,a.body_quality,a.extraction_status,substr(a.content,1,40) content_start
              FROM extraction_job_items i JOIN articles a ON a.id=i.article_id
              LEFT JOIN extraction_hosts h ON h.host=(a.url_host || ?)
              WHERE i.job_id=? AND i.state='pending' AND a.duplicate_of IS NULL AND a.discovery_rejected=0 AND a.lease_token IS NULL
              AND (h.next_allowed IS NULL OR h.next_allowed<=?)
              AND NOT EXISTS(SELECT 1 FROM articles busy WHERE busy.url_host=a.url_host AND busy.lease_token IS NOT NULL)
              ORDER BY (a.body_quality!='full') DESC,a.published_at DESC,a.id DESC LIMIT 1''',
              ('|browser' if transport=='browser' else '',job['id'],now())).fetchone()
        with connect(path) as db:
            db.execute('BEGIN IMMEDIATE')
            active=db.execute("SELECT 1 FROM extraction_jobs WHERE id=? AND status IN ('queued','running')",(job['id'],)).fetchone()
            if not active:continue
            if not row:
                db.execute('''UPDATE extraction_job_items SET state='skipped',result='Canonical duplicate or excluded inventory'
                  WHERE job_id=? AND state='pending' AND EXISTS(SELECT 1 FROM articles a WHERE a.id=article_id AND (a.duplicate_of IS NOT NULL OR a.discovery_rejected=1))''',(job['id'],))
                if not db.execute("SELECT 1 FROM extraction_job_items WHERE job_id=? AND state IN ('pending','running') LIMIT 1",(job['id'],)).fetchone():
                    db.execute("UPDATE extraction_jobs SET status='completed',updated_at=? WHERE id=?",(now(),job['id']))
                continue
            if not db.execute("SELECT 1 FROM extraction_job_items WHERE job_id=? AND article_id=? AND state='pending'",(job['id'],row['id'])).fetchone():continue
            fresh=db.execute('SELECT fingerprint,body_quality,extraction_status,substr(content,1,40) content_start FROM articles WHERE id=?',(row['id'],)).fetchone()
            previous=db.execute('SELECT * FROM extraction_results WHERE article_id=? AND transport=? AND fingerprint=?',(row['id'],transport,fresh['fingerprint'])).fetchone()
            if job['scope']!='single' and previous and fresh['extraction_status']!='pending':
                result={**json.loads(previous['result']),'reused_attempt':True}
                db.execute('UPDATE extraction_job_items SET state=?,result=?,finished_at=?,input_fingerprint=? WHERE job_id=? AND article_id=?',(previous['state'],json.dumps(result),previous['finished_at'],fresh['fingerprint'],job['id'],row['id']))
                complete_if_empty(db,job['id']);continue
            if job['scope']!='single' and transport=='http' and fresh['body_quality']=='full' and fresh['extraction_status']=='complete' and fresh['content_start'] and not fresh['content_start'].startswith('This article was written by'):
                db.execute("UPDATE extraction_job_items SET state='done',result=?,finished_at=? WHERE job_id=? AND article_id=?",(json.dumps({'status':'complete','reused_saved_body':True}),now(),job['id'],row['id']))
                complete_if_empty(db,job['id'])
                continue
            token=lease_article(db,row['id'],job['id'])
            if not token:continue
            db.execute("UPDATE extraction_job_items SET state='running',input_fingerprint=? WHERE job_id=? AND article_id=?",(fresh['fingerprint'],job['id'],row['id']))
            db.execute("UPDATE extraction_jobs SET status='running',updated_at=? WHERE id=?",(now(),job['id']))
            return dict(id=row['id'],token=token,job_id=job['id'],fingerprint=fresh['fingerprint'],force=job['scope']=='single')
    return None

def finish_item(path,claim,result):
    with connect(path) as db:
        db.execute('BEGIN IMMEDIATE')
        item=db.execute('SELECT i.*,j.transport,j.status job_status FROM extraction_job_items i JOIN extraction_jobs j ON j.id=i.job_id WHERE job_id=? AND article_id=?',(claim['job_id'],claim['id'])).fetchone()
        if not item or item['job_status']=='merged' or item['state']!='running':return
        if result.get('skipped') or result.get('status')=='pending':
            db.execute("UPDATE extraction_job_items SET state=? WHERE job_id=? AND article_id=?",('cancelled' if item['job_status']=='cancelled' else 'pending',claim['job_id'],claim['id']));return
        state='failed' if result.get('status') in ('failed','blocked','paywalled','unsupported') else 'skipped' if result.get('skipped') else 'done'
        db.execute('UPDATE extraction_job_items SET state=?,result=?,finished_at=? WHERE job_id=? AND article_id=?',
                   (state,json.dumps(result),now(),claim['job_id'],claim['id']))
        record_outcome(db,claim['id'],item['transport'],item['input_fingerprint'],result)
        complete_if_empty(db,claim['job_id'])
