"""Free local extraction, calendar-date workers, and explicit browser jobs."""
import json
import logging
import re
import threading
import time
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlsplit
from .network import fetch, FetchError, public_url
from .storage import connect, now, snapshot
from .body_parser import parse_article, active_paywall
from .content import sanitize_html, html_text, assess_body, feed_matches_page
from .recipes import active_recipes, recipe_matches
from .page_cache import load_page, save_page, prune_cache
from .windows import date_window, group_condition
from .extraction_jobs import lease_article, release_article, recover_leases, claim_manual, finish_item, record_outcome
from .transcripts import capture_captions

log=logging.getLogger(__name__)
_host_lock=threading.Lock();_host_due={}

def throttle(url):
    host=urlsplit(url).hostname
    with _host_lock:
        wait=max(0,_host_due.get(host,0)-time.monotonic())
        _host_due[host]=time.monotonic()+wait+2
    if wait: time.sleep(wait)

def iso_time(raw):
    if not raw: return None
    try:
        dt=datetime.fromisoformat(str(raw).replace('Z','+00:00'))
        if dt.tzinfo: return dt.astimezone(timezone.utc).isoformat(timespec='seconds')
    except (ValueError,TypeError): pass
    return None

def render_public(url,recipe=None):
    """Separate ephemeral headless process; never attach to the user's Chrome."""
    from playwright.sync_api import sync_playwright
    public_url(url)
    executable=next((str(p) for p in [Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'),
                  Path('/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge')] if p.exists()),None)
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,**({'executable_path':executable} if executable else {}))
        try:
            context=browser.new_context(accept_downloads=False,service_workers='block')
            def route(route):
                req=route.request
                if req.resource_type in ('image','media','font'): route.abort();return
                try: public_url(req.url)
                except ValueError: route.abort();return
                route.continue_()
            context.route('**/*',route)
            page=context.new_page();page.goto(url,wait_until='domcontentloaded',timeout=20000)
            if recipe and recipe.get('wait_selector'):
                try: page.locator(recipe['wait_selector']).first.wait_for(state='attached',timeout=5000)
                except Exception: pass
            page.wait_for_timeout((recipe or {}).get('wait_ms',1500))
            html=page.content().encode()
            if len(html)>4*1024*1024: raise FetchError('Rendered page exceeds 4 MB.')
            return html,page.url
        finally: browser.close()

def template_key(row):
    return (row['content_type'] or 'unknown')+':'+('video' if 'video' in json.loads(row['media_formats']) else 'text')

def feed_policy(db,row):
    return db.execute('SELECT * FROM feed_body_policies WHERE source_id=? AND template=?',(row['source_id'],template_key(row))).fetchone()

def compare_feed(db,row,result,matched=None):
    if not row['feed_html'] or result.get('body_quality')!='full': return
    old=feed_policy(db,row)
    if matched is None:matched=feed_matches_page(sanitize_html(row['feed_html'],row['url']),result['body_html'])
    seen=db.execute('SELECT 1 FROM feed_body_samples WHERE source_id=? AND template=? AND article_id=?',
                    (row['source_id'],template_key(row),row['id'])).fetchone()
    db.execute('''INSERT INTO feed_body_samples VALUES(?,?,?,?,?) ON CONFLICT(source_id,template,article_id)
        DO UPDATE SET matched=excluded.matched,checked_at=excluded.checked_at''',
        (row['source_id'],template_key(row),row['id'],int(matched),now()))
    matches=(old['matches'] if old else 0)+int(not seen) if matched else 0
    comparisons=(old['comparisons'] if old else 0)+1
    next_check=(datetime.now(timezone.utc)+timedelta(hours=6)).isoformat(timespec='seconds')
    state='validated_full' if matches>=3 else 'observed_full' if matched else 'excerpt'
    db.execute('''INSERT INTO feed_body_policies VALUES(?,?,?,?,?,?,?) ON CONFLICT(source_id,template)
       DO UPDATE SET state=excluded.state,matches=excluded.matches,comparisons=excluded.comparisons,
       checked_at=excluded.checked_at,next_check=excluded.next_check''',
       (row['source_id'],template_key(row),state,matches,comparisons,now(),next_check))

def host_result(db,url,status,error=None,retry_after=None,transport='http'):
    host=(urlsplit(url).hostname or '').lower()+('|browser' if transport=='browser' else '')
    previous=db.execute('SELECT * FROM extraction_hosts WHERE host=?',(host,)).fetchone()
    failures=(previous['failures'] if previous else 0)+1 if status in ('blocked','failed') else 0
    seconds=min(86400,60*2**min(failures,10)) if failures else 2
    if status=='blocked': seconds=max(seconds,900)
    if retry_after:
        try: seconds=max(seconds,int(retry_after))
        except (ValueError,TypeError):
            try: seconds=max(seconds,int((parsedate_to_datetime(retry_after)-datetime.now(timezone.utc)).total_seconds()))
            except (ValueError,TypeError): pass
    due=(datetime.now(timezone.utc)+timedelta(seconds=seconds)).isoformat(timespec='seconds')
    db.execute('''INSERT INTO extraction_hosts VALUES(?,?,?,?,?) ON CONFLICT(host) DO UPDATE SET
        failures=excluded.failures,next_allowed=excluded.next_allowed,last_status=excluded.last_status,last_error=excluded.last_error''',
        (host,failures,due,status,error))
    return due

def extract_article(path,article_id,transport='http',lease_token=None,force=False):
    if transport not in ('http','browser'): raise ValueError('Invalid extraction transport.')
    with connect(path) as db:
        if lease_token is None:
            lease_token=lease_article(db,article_id)
            if not lease_token: return {'id':article_id,'skipped':True,'message':'Already extracting or excluded.'}
        row=db.execute('SELECT * FROM articles WHERE id=?',(article_id,)).fetchone()
        if not row or row['lease_token']!=lease_token: return {'id':article_id,'skipped':True}
        recipes=active_recipes(db);policy=feed_policy(db,row)
        cached=db.execute('SELECT * FROM cached_pages WHERE article_id=? AND transport=?',(article_id,transport)).fetchone()
    started=time.monotonic();result=None;outcome=None;http_status=None;error=None;committed=False
    try:
        safe_feed=sanitize_html(row['feed_html'] or '',row['url'])
        feed_quality,_=assess_body(safe_feed,structured=True,truncated=bool(re.search(r'read (?:the )?(?:full|more)|continue reading',html_text(safe_feed),re.I)))
        valid_feed=(transport=='http' and not force and safe_feed and feed_quality=='full' and policy
                    and policy['state']=='validated_full' and policy['next_check']>now())
        headers={};http_status=None;resolved=row['url']
        if valid_feed:
            result=dict(content=html_text(safe_feed),body_html=safe_feed,body_quality='full',extraction_status='complete',
                 extraction_method='validated feed',body_notes='Channel/template matched at least three distinct complete page bodies; periodic spot checks enabled.',
                 extraction_notes='Accepted validated feed body; metadata supplied by the feed retained.')
            outcome='feed';origin='feed'
        else:
            throttle(row['url'])
            if transport=='browser':
                wait_recipe=next((r for r in sorted(recipes,key=lambda r:r.get('priority',50),reverse=True)
                    if r.get('wait_selector') and recipe_matches(r,row['url'])),None)
                body,resolved=render_public(row['url'],wait_recipe);outcome='browser_success';origin='browser'
            else:
                previous=load_page(path,article_id)
                condition={}
                if cached and previous:
                    if cached['etag']: condition['If-None-Match']=cached['etag']
                    if cached['last_modified']: condition['If-Modified-Since']=cached['last_modified']
                http_status,headers,body,resolved=fetch(row['url'],condition)
                outcome='not_modified' if http_status==304 else 'http_success';origin='page'
                if http_status==304:
                    if previous is None: http_status,headers,body,resolved=fetch(row['url'])
                    else:
                        body=previous
                        headers={**headers,'ETag':headers.get('ETag') or cached['etag'],'Last-Modified':headers.get('Last-Modified') or cached['last_modified']}
            content_type=headers.get('Content-Type','').lower()
            if content_type.startswith(('video/','audio/')):
                result=dict(content='',body_html='',body_quality='none',extraction_status='not_applicable',
                    extraction_method='media enclosure',body_notes='Direct audio/video item; no article body expected.',extraction_notes='Transcript availability is separate.')
            elif content_type and not any(x in content_type for x in ('html','xml','text/')):
                result=dict(content='',body_html='',body_quality='none',extraction_status='unsupported',
                    extraction_method='HTTP',body_notes='Unsupported article format.',extraction_notes='Unsupported content type: '+content_type)
            else:
                with connect(path) as db: save_page(db,path,article_id,transport,body,resolved,headers)
                result=parse_article(body,resolved,row['feed_content'],json.loads(row['media_json']),json.loads(row['images_json']),recipes)
                if transport=='browser': result['extraction_method']+=' + browser'
                if transport=='http' and result['extraction_status'] not in ('complete','paywalled','blocked','not_applicable'):
                    alternate=next((r for r in recipes if r.get('public_alternate') and recipe_matches(r,row['url'])),None)
                    if alternate:
                        try:
                            alt=alternate['public_alternate'].format(url=row['url'],path=urlsplit(row['url']).path)
                            _,_,alt_body,alt_url=fetch(alt)
                            candidate=parse_article(alt_body,alt_url,row['feed_content'],json.loads(row['media_json']),json.loads(row['images_json']),recipes)
                            if candidate['body_quality']=='full': result=candidate;resolved=alt_url;result['extraction_method']+=' + public alternate'
                        except Exception as exc: result['extraction_notes']+=' Public alternate failed: '+str(exc)[:150]
                if result['extraction_status'] in ('blocked','paywalled'): outcome=result['extraction_status']
        capture_captions(result)
        # Parsing/paragraph comparison can be expensive for full feeds. Never hold the SQLite writer lock here.
        feed_matched=feed_matches_page(safe_feed,result['body_html']) if origin=='page' and safe_feed and result.get('body_quality')=='full' else False
        stamp=now()
        allowed={'canonical_url','author','title','language','content_type','publisher_content_label','content_type_basis',
            'media_formats','media_json','images_json','transcript','transcript_status','extraction_status','extraction_method',
            'extraction_notes','published_raw','updated_raw','publisher_access'}
        fields={k:v for k,v in result.items() if k in allowed and v is not None and v!=''}
        canonical=fields.get('canonical_url')
        if canonical and urlsplit(canonical).path in ('','/') and urlsplit(row['url']).path not in ('','/'): fields.pop('canonical_url',None)
        for key in ('published','updated'):
            value=iso_time(result.get(key+'_raw'))
            if value: fields['published_at' if key=='published' else 'publisher_updated_at']=value
        fields.update(extracted_at=stamp,extraction_error=None,extraction_failures=0,next_extraction=None,
                      fetch_outcome=outcome,http_status=http_status,resolved_url=resolved)
        with connect(path) as db:
            db.execute('BEGIN IMMEDIATE')
            current=db.execute('SELECT * FROM articles WHERE id=?',(article_id,)).fetchone()
            if current['lease_token']!=lease_token: return {'id':article_id,'skipped':True,'message':'Extraction lease changed.'}
            if current['fingerprint']!=row['fingerprint']:
                db.execute("UPDATE articles SET extraction_status='pending',next_extraction=NULL WHERE id=?",(article_id,))
                return {'id':article_id,'status':'pending','message':'Feed changed during extraction.'}
            if result.get('content_type')=='unknown' or current['content_type_basis']=='Manual correction':
                for key in ('content_type','publisher_content_label','content_type_basis'): fields.pop(key,None)
            if current['transcript'] and not result.get('transcript'):
                fields['transcript_status']=current['transcript_status']
            quality=result.get('body_quality','none');text=result.get('content','')
            rank={'none':0,'unknown':1,'partial':2,'full':3}
            if text and (rank[quality]>rank[current['body_quality']] or
                rank[quality]==rank[current['body_quality']] and (quality=='full' or len(text)>=len(current['content']))):
                fields.update(content=text,body_html=result['body_html'],body_quality=quality,body_origin=origin,
                    body_notes=result['body_notes'],body_saved_at=stamp,recipe_id=result.get('recipe_id'),recipe_version=result.get('recipe_version'),
                    content_kind='Full text' if quality=='full' else 'Partial page text' if origin!='feed' else row['feed_kind'])
            db.execute('UPDATE articles SET '+','.join(k+'=?' for k in fields)+' WHERE id=?',list(fields.values())+[article_id])
            if origin=='page': compare_feed(db,row,result,feed_matched)
            due=host_result(db,row['url'],result['extraction_status'],transport=transport)
            if result['extraction_status']=='blocked': db.execute('UPDATE articles SET next_extraction=? WHERE id=?',(due,article_id))
            if result.get('content_type_basis')=='Non-article publisher page': db.execute('UPDATE articles SET discovery_rejected=1 WHERE id=?',(article_id,))
            if quality!='full' and result['extraction_status'] not in ('paywalled','blocked','not_applicable'):
                template=result.get('recipe_id') or template_key(row)
                db.execute('''INSERT INTO recipe_failures VALUES(?,?,1,?,?,?,NULL) ON CONFLICT(publisher_id,template)
                    DO UPDATE SET count=count+1,last_article_id=excluded.last_article_id,last_seen=excluded.last_seen,resolved_at=NULL''',
                    (row['publisher_id'],template,article_id,stamp,stamp))
            if snapshot(db,article_id,'browser extraction' if transport=='browser' else 'extraction'):
                db.execute('UPDATE articles SET content_changed_at=? WHERE id=?',(stamp,article_id))
            canonical=fields.get('canonical_url')
            if canonical:
                other=db.execute('SELECT id FROM articles WHERE id!=? AND duplicate_of IS NULL AND id IN (SELECT id FROM articles WHERE publisher_id=? AND url=? UNION SELECT id FROM articles WHERE publisher_id=? AND canonical_url=?) ORDER BY id LIMIT 1',
                    (article_id,row['publisher_id'],canonical,row['publisher_id'],canonical)).fetchone()
                if other:
                    keep,alias=sorted([article_id,other['id']]);kept=db.execute('SELECT body_quality FROM articles WHERE id=?',(keep,)).fetchone()
                    if alias==article_id and rank[kept['body_quality']]<rank[quality] and text:
                        db.execute('UPDATE articles SET '+','.join(k+'=?' for k in fields)+' WHERE id=?',list(fields.values())+[keep]);snapshot(db,keep,'canonical merge')
                    db.execute('UPDATE articles SET duplicate_of=? WHERE id=?',(keep,alias));db.execute('UPDATE articles SET duplicate_of=? WHERE duplicate_of=?',(keep,alias))
                    db.execute('UPDATE article_entries SET article_id=? WHERE article_id=?',(keep,alias))
                    db.execute('INSERT OR IGNORE INTO article_channels SELECT ?,source_id,first_seen,last_seen FROM article_channels WHERE article_id=?',(keep,alias))
        committed=True
        return {'id':article_id,'status':result['extraction_status'],'body_quality':quality,'method':result['extraction_method']}
    except Exception as exc:
        error=str(exc)[:500];match=re.search(r'HTTP (\d{3})',error);code=int(match[1]) if match else None
        status='blocked' if code in (401,403,429) else 'failed'
        with connect(path) as db:
            due=host_result(db,row['url'],status,error,getattr(exc,'retry_after',None),transport)
            db.execute('''UPDATE articles SET extraction_status=?,fetch_outcome=?,http_status=?,extraction_error=?,extraction_failures=extraction_failures+1,
                          next_extraction=? WHERE id=? AND lease_token=?''',(status,status,code,error,due,article_id,lease_token))
        outcome=status;http_status=code;committed=True
        return {'id':article_id,'status':status,'error':error}
    finally:
        try:
            if committed:
                with connect(path) as db:
                    db.execute('INSERT INTO extraction_attempts(article_id,transport,finished_at,duration_seconds,fetch_outcome,body_quality,http_status,error) VALUES(?,?,?,?,?,?,?,?)',
                        (article_id,transport,now(),round(time.monotonic()-started,3),outcome,
                         result.get('body_quality') if result else None,http_status,error))
                    saved={'id':article_id,'status':status,'error':error} if error else {'id':article_id,'status':result['extraction_status'],'body_quality':result.get('body_quality'),'method':result.get('extraction_method')}
                    record_outcome(db,article_id,transport,row['fingerprint'],saved)
        except Exception: log.exception('Could not record extraction attempt outcome')
        finally:release_article(path,article_id,lease_token)

def extraction_candidates(db,limit=2):
    window=date_window(db);condition,args=group_condition('recent',window)
    return db.execute('''SELECT a.id FROM articles a LEFT JOIN extraction_hosts h ON h.host=a.url_host
        WHERE a.duplicate_of IS NULL AND a.discovery_rejected=0 AND a.lease_token IS NULL AND '''+condition+'''
        AND (a.extraction_status='pending' OR (a.extraction_status IN ('failed','blocked') AND a.next_extraction<=?))
        AND (h.next_allowed IS NULL OR h.next_allowed<=?)
        AND NOT EXISTS(SELECT 1 FROM articles busy WHERE busy.url_host=a.url_host AND busy.lease_token IS NOT NULL)
        AND EXISTS(SELECT 1 FROM article_channels c JOIN sources s ON s.id=c.source_id WHERE c.article_id=a.id AND s.enabled=1)
        ORDER BY COALESCE(h.next_allowed,'') ASC,a.published_at DESC,a.id DESC LIMIT ?''',args+[now(),now(),limit]).fetchall()

class ExtractionWorker:
    def __init__(self,path):
        self.path=path;self.stop_event=threading.Event();self.thread=None;self.threads=[]
    def start(self):
        if self.thread: return
        with connect(self.path) as db: recover_leases(db)
        def run(mode,maintenance=False):
            last_cleanup=time.monotonic()
            while not self.stop_event.is_set():
                try:
                    if maintenance and time.monotonic()-last_cleanup>3600:
                        with connect(self.path) as db: recover_leases(db)
                        prune_cache(self.path);last_cleanup=time.monotonic()
                    if mode=='auto':
                        with connect(self.path) as db:
                            rows=extraction_candidates(db,1)
                            token=lease_article(db,rows[0]['id'],automatic=True) if rows else None
                        claim={'id':rows[0]['id'],'token':token} if rows and token else None
                    else: claim=claim_manual(self.path,mode)
                    if not claim: self.stop_event.wait(2);continue
                    result=extract_article(self.path,claim['id'],transport='browser' if mode=='browser' else 'http',lease_token=claim['token'],force=claim.get('force',False))
                    if mode!='auto': finish_item(self.path,claim,result)
                except Exception:
                    log.exception('Extraction worker failed');self.stop_event.wait(3)
        for index,mode in enumerate(('auto',)*4+('http',)*4+('browser',)):
            thread=threading.Thread(target=run,args=(mode,index==0),daemon=True,name='extraction-'+mode)
            self.threads.append(thread);thread.start()
        self.thread=self.threads[0]
