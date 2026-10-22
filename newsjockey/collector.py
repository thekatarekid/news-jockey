import calendar
import hashlib
import json
import logging
import re
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit
from .storage import connect, now, upsert_article
from .network import fetch, parse_feed, clean_text, article_url, discover
from .metadata import feed_metadata
from .discovery import sitemap_items, expand_publisher
from .extraction import ExtractionWorker

log = logging.getLogger(__name__)

def publisher_time(entry, key):
    entry = dict(entry)
    raw = entry.get(key, '')
    parsed = entry.get(key + '_parsed')
    # Keep unzoned publisher timestamps raw instead of inventing a timezone.
    has_zone = bool(re.search(r'(Z|[+-]\d{2}:?\d{2}|GMT|UTC|[ECMP][DS]T)\s*$', raw, re.I))
    if not parsed or not has_zone:
        return None
    try:
        return datetime.fromtimestamp(calendar.timegm(parsed), timezone.utc).isoformat(timespec='seconds')
    except (ValueError, OverflowError):
        return None

def entry_record(entry, base):
    # FeedParserDict aliases missing Atom updated fields to RSS published fields.
    # A plain dict keeps absent publisher update times absent.
    enclosures = entry.get('enclosures', [])
    entry = dict(entry)
    entry['enclosures'] = enclosures
    link = entry.get('link') or next((x.get('href') or x.get('url') for x in entry.get('enclosures', []) if x.get('href') or x.get('url')), '')
    url = article_url(link, base) if link else ''
    title = clean_text(entry.get('title'))
    if not title or not url:
        return None
    chunks = [clean_text(x.get('value')) for x in entry.get('content', []) if x.get('type', '').startswith('text') or x.get('type')=='application/xhtml+xml']
    content = '\n\n'.join(x for x in chunks if x)
    kind = 'Feed content' if content else 'Feed summary'
    if not content:
        content = clean_text(entry.get('summary', ''))
    if not content:
        kind = 'Headline only'
    record = dict(entry_id=str(entry.get('id') or url), url=url, title=title,
                  author=clean_text(entry.get('author')), tags=', '.join(dict.fromkeys(clean_text(t.get('term')) for t in entry.get('tags', []) if t.get('term'))),
                  published_at=publisher_time(entry, 'published'), publisher_updated_at=publisher_time(entry, 'updated'),
                  published_raw=entry.get('published'), updated_raw=entry.get('updated'), content=content, content_kind=kind)
    raw_html = '\n'.join(x.get('value', '') for x in entry.get('content', []) if x.get('type', '').startswith('text') or x.get('type')=='application/xhtml+xml') or entry.get('summary', '')
    record['feed_html'] = raw_html
    record.update(feed_metadata(entry, url, raw_html))
    record['fingerprint'] = hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()
    return record

def stale_warning(latest):
    if latest:
        dt = datetime.fromisoformat(latest)
        if dt > datetime.now(timezone.utc) + timedelta(hours=1):
            return 'Publisher timestamp is in the future; collection delay is unreliable.'
        if dt < datetime.now(timezone.utc) - timedelta(days=3):
            return 'Newest dated item is over 3 days old. Feed may be quiet or stale.'
    return None

def collect_source(path, source_id):
    with connect(path) as db:
        source = db.execute('SELECT * FROM sources WHERE id=?', (source_id,)).fetchone()
        if not source or not source['enabled']:
            return {'source_id': source_id, 'skipped': True}
        attempted = now()
        db.execute("UPDATE sources SET last_attempt=?,status='Checking' WHERE id=?", (attempted, source_id))
    headers = {}
    if source['etag'] and source['channel_type'] == 'feed':
        headers['If-None-Match'] = source['etag']
    if source['last_modified'] and source['channel_type'] == 'feed':
        headers['If-Modified-Since'] = source['last_modified']
    try:
        status, response_headers, body, resolved = fetch(source['feed_url'], headers)
        parsed = parse_feed(body) if status != 304 and source['channel_type'] == 'feed' else None
        sitemap_entries, cursor, sitemap_warning = [], source['discovery_cursor'], None
        if status != 304 and source['channel_type'] == 'sitemap':
            sitemap_entries, cursor, sitemap_warning = sitemap_items(resolved, body, cursor)
            if 'pbs.org/newshour' in source['website']:
                sitemap_entries = [e for e in sitemap_entries if '/newshour/' in urlsplit(e['link']).path]
        entries = parsed.entries if parsed is not None else sitemap_entries
        seen = now()
        new_count = updated_count = skipped_count = 0
        latest = source['latest_published']
        feed_ttl = source['feed_ttl_minutes']
        if parsed:
            try:
                value = int(parsed.feed.get('ttl', 0))
                feed_ttl = value if 0 <= value <= 525600 else 0
            except (ValueError, TypeError):
                feed_ttl = 0
        interval = max(source['interval_minutes'], feed_ttl)
        next_check = (datetime.now(timezone.utc) + timedelta(minutes=interval)).isoformat(timespec='seconds')
        # Parse rich feeds before taking SQLite's writer lock. Large inventories
        # commit in small batches so extraction/control jobs can also make progress.
        records=[]
        for entry in entries:
            item=entry_record(entry,resolved)
            if not item:
                skipped_count+=1
                continue
            if item['published_at'] and (not latest or item['published_at']>latest): latest=item['published_at']
            records.append(item)
        with connect(path) as db:
            for index,item in enumerate(records):
                created, changed, _ = upsert_article(db, source, item, seen)
                new_count += int(created)
                updated_count += int(changed)
                if (index+1)%25==0: db.commit()
            warnings = []
            if sitemap_warning:
                warnings.append(sitemap_warning)
            if parsed and parsed.get('bozo'):
                warnings.append('Feed has formatting issues; readable entries were collected.')
            if skipped_count:
                warnings.append(f'{skipped_count} entries lacked a usable title or article URL.')
            if parsed is not None and not parsed.entries:
                warnings.append('Feed returned no entries.')
            stale = stale_warning(latest)
            if stale:
                warnings.append(stale)
            db.execute('''UPDATE sources SET last_success=?,next_check=?,status=?,error=NULL,warning=?,failures=0,
                       etag=?,last_modified=?,latest_published=?,last_item_count=?,feed_ttl_minutes=?,discovery_cursor=? WHERE id=?''',
                       (seen, next_check, 'Unchanged' if status == 304 else 'OK', ' '.join(warnings) or None,
                        response_headers.get('ETag', source['etag'] if status == 304 else None),
                        response_headers.get('Last-Modified', source['last_modified'] if status == 304 else None),
                        latest, len(entries) if status != 304 else source['last_item_count'], feed_ttl, cursor, source_id))
        return {'source_id': source_id, 'name': source['name'], 'new': new_count, 'updated': updated_count, 'status': status}
    except Exception as exc:
        failures = source['failures'] + 1
        seconds = max(source['feed_ttl_minutes'] * 60, min(86400, source['interval_minutes'] * 60 * 2 ** min(failures, 8)))
        retry = getattr(exc, 'retry_after', None)
        if retry:
            try:
                seconds = max(seconds, int(retry))
            except ValueError:
                try:
                    seconds = max(seconds, int((parsedate_to_datetime(retry) - datetime.now(timezone.utc)).total_seconds()))
                except (ValueError, TypeError):
                    pass
        due = (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat(timespec='seconds')
        error = str(exc)[:350]
        with connect(path) as db:
            db.execute("UPDATE sources SET status='Error',error=?,failures=?,next_check=? WHERE id=?", (error, failures, due, source_id))
        return {'source_id': source_id, 'name': source['name'], 'error': error}

class Collector:
    def __init__(self, path):
        self.path = path
        self.lock = threading.RLock()
        self.active = None
        self.jobs = {}
        self.stop_event = threading.Event()
        self.thread = None
        self.discovery_thread = None
        self.extraction = ExtractionWorker(path)

    def job(self, job_id):
        with self.lock:
            job = self.jobs.get(job_id)
            return json.loads(json.dumps(job)) if job else None

    def state(self):
        with self.lock:
            return self.active

    def submit(self, kind, values):
        with self.lock:
            if self.active:
                raise ValueError('A collection or discovery job is already running. Please wait for it to finish.')
            if len(self.jobs) > 60:
                oldest = next(iter(self.jobs))
                del self.jobs[oldest]
            job_id = uuid.uuid4().hex
            self.jobs[job_id] = dict(id=job_id, kind=kind, total=len(values), completed=0, results=[], status='running', started_at=now())
            self.active = job_id
            threading.Thread(target=self._run, args=(job_id, kind, values), daemon=True).start()
            return job_id

    def _run(self, job_id, kind, values):
        def task(value):
            if kind == 'collect':
                return collect_source(self.path, value)
            try:
                return {'url': value, 'feeds': discover(value)}
            except Exception as exc:
                return {'url': value, 'error': str(exc)[:350], 'feeds': []}
        try:
            with ThreadPoolExecutor(max_workers=6) as pool:
                for future in as_completed([pool.submit(task, x) for x in values]):
                    result = future.result()
                    with self.lock:
                        self.jobs[job_id]['results'].append(result)
                        self.jobs[job_id]['completed'] += 1
            with self.lock:
                self.jobs[job_id]['status'] = 'done'
        except Exception as exc:
            log.exception('Collector job failed')
            with self.lock:
                self.jobs[job_id].update(status='error', error=str(exc))
        finally:
            with self.lock:
                self.active = None

    def collect(self, source_id=None, due_only=False):
        with connect(self.path) as db:
            sql = 'SELECT id FROM sources WHERE enabled=1'
            args = []
            if source_id is not None:
                sql += ' AND id=?'
                args.append(source_id)
            if due_only:
                sql += ' AND (next_check IS NULL OR next_check<=?)'
                args.append(now())
            else:
                # Manual collection still respects publisher backoff after failures.
                sql += " AND (status!='Error' OR next_check IS NULL OR next_check<=?)"
                args.append(now())
            ids = [x['id'] for x in db.execute(sql, args)]
        return self.submit('collect', ids)

    def start(self):
        self.extraction.start()
        def poll():
            while not self.stop_event.is_set():
                try:
                    if not self.state():
                        with connect(self.path) as db:
                            due = db.execute('SELECT 1 FROM sources WHERE enabled=1 AND (next_check IS NULL OR next_check<=?) LIMIT 1', (now(),)).fetchone()
                        if due:
                            self.collect(due_only=True)
                except Exception:
                    log.exception('Scheduled collection failed')
                self.stop_event.wait(15)
        if not self.thread:
            self.thread = threading.Thread(target=poll, daemon=True)
            self.thread.start()
        if not self.discovery_thread:
            def expand():
                while not self.stop_event.is_set():
                    try:
                        with connect(self.path) as db:
                            rows = db.execute('''SELECT p.id FROM publishers p WHERE
                                (p.next_discovery IS NULL OR p.next_discovery<=?) AND
                                EXISTS(SELECT 1 FROM sources s WHERE s.publisher_id=p.id AND s.enabled=1)
                                ORDER BY p.last_discovery IS NOT NULL,p.id LIMIT 2''', (now(),)).fetchall()
                        if rows:
                            with ThreadPoolExecutor(max_workers=2) as pool:
                                list(pool.map(lambda r: expand_publisher(self.path,r['id']), rows))
                        else:
                            self.stop_event.wait(30)
                    except Exception:
                        log.exception('Publisher expansion failed')
                        self.stop_event.wait(30)
            self.discovery_thread = threading.Thread(target=expand, daemon=True)
            self.discovery_thread.start()

    def stop(self):
        self.stop_event.set()
        self.extraction.stop_event.set()
