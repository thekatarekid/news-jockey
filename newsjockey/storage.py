import hashlib
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
CATEGORIES = ['Politics/National', 'Entertainment', 'Sports', 'Tech', 'Business']
CONTENT_TYPES = ['reporting', 'editorial', 'opinion', 'analysis', 'review', 'interview', 'explainer', 'live_blog', 'sponsored', 'other', 'unknown']
MEDIA_FORMATS = ['text', 'video', 'audio', 'image_gallery']
EXTRACTION_STATUSES = ['pending', 'extracting', 'complete', 'partial', 'paywalled', 'blocked', 'failed', 'unsupported', 'not_applicable']
CATALOG = json.loads((ROOT / 'catalog.json').read_text())

def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')

@contextmanager
def connect(path):
    db = sqlite3.connect(path, timeout=30)
    db.row_factory = sqlite3.Row
    try:
        db.execute('PRAGMA foreign_keys = ON')
        with db:
            yield db
    finally:
        db.close()

def publisher_key(website):
    host = (urlsplit(website).hostname or '').lower().removeprefix('www.')
    # Preserve distinct editorial brands, including Yahoo Sports/Finance and CBS Sports.
    if host in ('rss.nytimes.com', 'nytimes.com'):
        return 'nytimes.com'
    return host

def publisher_for(db, source):
    key = publisher_key(source['website'])
    row = db.execute('SELECT id FROM publishers WHERE identity_key=?', (key,)).fetchone()
    if row:
        return row['id']
    name = 'The New York Times' if key == 'nytimes.com' else source['name']
    website = 'https://www.nytimes.com' if key == 'nytimes.com' else source['website']
    cur = db.execute('INSERT INTO publishers(identity_key,name,category,website,created_at) VALUES(?,?,?,?,?)',
                     (key, name, source['category'], website, now()))
    return cur.lastrowid

def add_columns(db, table, definitions):
    existing = {r['name'] for r in db.execute(f'PRAGMA table_info({table})')}
    for name, definition in definitions.items():
        if name not in existing:
            db.execute(f'ALTER TABLE {table} ADD COLUMN {name} {definition}')

def initialize(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with connect(path) as db:
        db.execute('PRAGMA journal_mode = WAL')
        db.executescript('''
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS publishers (
          id INTEGER PRIMARY KEY, identity_key TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
          category TEXT NOT NULL, website TEXT NOT NULL, created_at TEXT NOT NULL,
          coverage_status TEXT NOT NULL DEFAULT 'Not scanned', coverage_notes TEXT,
          last_discovery TEXT, next_discovery TEXT, discovery_failures INTEGER NOT NULL DEFAULT 0,
          browser_rendering INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS sources (
          id INTEGER PRIMARY KEY, catalog_id TEXT, name TEXT NOT NULL, category TEXT NOT NULL,
          website TEXT NOT NULL, feed_url TEXT NOT NULL UNIQUE, enabled INTEGER NOT NULL DEFAULT 1,
          interval_minutes INTEGER NOT NULL DEFAULT 5, created_at TEXT NOT NULL,
          last_attempt TEXT, last_success TEXT, next_check TEXT, status TEXT NOT NULL DEFAULT 'Pending',
          error TEXT, warning TEXT, failures INTEGER NOT NULL DEFAULT 0,
          etag TEXT, last_modified TEXT, latest_published TEXT, last_item_count INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS articles (
          id INTEGER PRIMARY KEY, source_id INTEGER NOT NULL REFERENCES sources(id),
          entry_id TEXT NOT NULL, url TEXT NOT NULL, title TEXT NOT NULL, author TEXT, tags TEXT,
          published_at TEXT, publisher_updated_at TEXT, published_raw TEXT, updated_raw TEXT,
          first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, content_changed_at TEXT NOT NULL,
          content TEXT NOT NULL, content_kind TEXT NOT NULL, fingerprint TEXT NOT NULL,
          UNIQUE(source_id, entry_id));
        CREATE TABLE IF NOT EXISTS article_entries (
          source_id INTEGER NOT NULL REFERENCES sources(id), entry_id TEXT NOT NULL,
          article_id INTEGER NOT NULL REFERENCES articles(id),
          PRIMARY KEY(source_id,entry_id));
        CREATE TABLE IF NOT EXISTS article_channels (
          article_id INTEGER NOT NULL REFERENCES articles(id), source_id INTEGER NOT NULL REFERENCES sources(id),
          first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, PRIMARY KEY(article_id,source_id));
        CREATE TABLE IF NOT EXISTS article_versions (
          id INTEGER PRIMARY KEY, article_id INTEGER NOT NULL REFERENCES articles(id),
          observed_at TEXT NOT NULL, origin TEXT NOT NULL, hash TEXT NOT NULL, snapshot TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS articles_source_url ON articles(source_id,url);
        CREATE INDEX IF NOT EXISTS articles_published ON articles(published_at);
        CREATE INDEX IF NOT EXISTS articles_first_seen ON articles(first_seen);
        CREATE INDEX IF NOT EXISTS versions_article ON article_versions(article_id,id);
        ''')
        add_columns(db, 'sources', {'feed_ttl_minutes': 'INTEGER NOT NULL DEFAULT 0',
            'publisher_id': 'INTEGER REFERENCES publishers(id)', 'channel_type': "TEXT NOT NULL DEFAULT 'feed'",
            'section': "TEXT NOT NULL DEFAULT 'Original subscription'", 'discovery_cursor': 'INTEGER NOT NULL DEFAULT 0'})
        add_columns(db, 'article_entries', {'fingerprint': 'TEXT'})
        add_columns(db, 'articles', {
            'publisher_id': 'INTEGER REFERENCES publishers(id)', 'canonical_url': 'TEXT',
            'content_type': "TEXT NOT NULL DEFAULT 'unknown'", 'publisher_content_label': 'TEXT',
            'content_type_basis': 'TEXT', 'media_formats': "TEXT NOT NULL DEFAULT '[\"text\"]'",
            'media_json': "TEXT NOT NULL DEFAULT '[]'", 'images_json': "TEXT NOT NULL DEFAULT '[]'",
            'language': 'TEXT', 'feed_content': 'TEXT', 'feed_kind': 'TEXT',
            'extraction_status': "TEXT NOT NULL DEFAULT 'pending'", 'extraction_method': 'TEXT',
            'extraction_error': 'TEXT', 'extraction_notes': 'TEXT', 'extraction_attempted_at': 'TEXT',
            'extracted_at': 'TEXT', 'next_extraction': 'TEXT', 'extraction_failures': 'INTEGER NOT NULL DEFAULT 0',
            'transcript': 'TEXT', 'transcript_status': "TEXT NOT NULL DEFAULT 'unavailable'",
            'duplicate_of': 'INTEGER REFERENCES articles(id)', 'discovery_rejected': 'INTEGER NOT NULL DEFAULT 0'})
        add_columns(db, 'articles', {
            'feed_html': 'TEXT', 'body_html': 'TEXT', 'body_origin': "TEXT NOT NULL DEFAULT 'none'",
            'body_quality': "TEXT NOT NULL DEFAULT 'none'", 'body_notes': 'TEXT', 'body_saved_at': 'TEXT',
            'recipe_id': 'TEXT', 'recipe_version': 'INTEGER', 'fetch_outcome': "TEXT NOT NULL DEFAULT 'not_attempted'",
            'http_status': 'INTEGER', 'resolved_url': 'TEXT', 'url_host': 'TEXT', 'publisher_access': "TEXT NOT NULL DEFAULT 'unknown'",
            'lease_token': 'TEXT', 'lease_until': 'TEXT', 'lease_job_id': 'TEXT'})
        db.executescript('''
        CREATE TABLE IF NOT EXISTS extraction_jobs (
          id TEXT PRIMARY KEY, transport TEXT NOT NULL, scope TEXT NOT NULL, status TEXT NOT NULL,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL, timezone TEXT NOT NULL,
          window_start TEXT NOT NULL, window_end TEXT NOT NULL, total INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS extraction_job_items (
          job_id TEXT NOT NULL REFERENCES extraction_jobs(id), article_id INTEGER NOT NULL REFERENCES articles(id),
          state TEXT NOT NULL DEFAULT 'pending', result TEXT, finished_at TEXT,
          PRIMARY KEY(job_id,article_id));
        CREATE INDEX IF NOT EXISTS extraction_job_pending ON extraction_job_items(job_id,state,article_id);
        CREATE TABLE IF NOT EXISTS extraction_results (
          article_id INTEGER NOT NULL REFERENCES articles(id), transport TEXT NOT NULL,
          fingerprint TEXT NOT NULL, state TEXT NOT NULL, result TEXT NOT NULL, finished_at TEXT NOT NULL,
          PRIMARY KEY(article_id,transport));
        CREATE TABLE IF NOT EXISTS extraction_hosts (
          host TEXT PRIMARY KEY, failures INTEGER NOT NULL DEFAULT 0, next_allowed TEXT,
          last_status TEXT, last_error TEXT);
        CREATE TABLE IF NOT EXISTS extraction_attempts (
          id INTEGER PRIMARY KEY, article_id INTEGER NOT NULL REFERENCES articles(id),
          transport TEXT NOT NULL, finished_at TEXT NOT NULL, duration_seconds REAL NOT NULL,
          fetch_outcome TEXT, body_quality TEXT, http_status INTEGER, error TEXT);
        CREATE INDEX IF NOT EXISTS attempts_time ON extraction_attempts(finished_at,transport,duration_seconds,fetch_outcome,body_quality);
        CREATE TABLE IF NOT EXISTS feed_body_policies (
          source_id INTEGER NOT NULL REFERENCES sources(id), template TEXT NOT NULL,
          state TEXT NOT NULL DEFAULT 'unknown', matches INTEGER NOT NULL DEFAULT 0,
          comparisons INTEGER NOT NULL DEFAULT 0, checked_at TEXT, next_check TEXT,
          PRIMARY KEY(source_id,template));
        CREATE TABLE IF NOT EXISTS feed_body_samples (
          source_id INTEGER NOT NULL REFERENCES sources(id), template TEXT NOT NULL,
          article_id INTEGER NOT NULL REFERENCES articles(id), matched INTEGER NOT NULL,
          checked_at TEXT NOT NULL, PRIMARY KEY(source_id,template,article_id));
        CREATE TABLE IF NOT EXISTS cached_pages (
          article_id INTEGER NOT NULL REFERENCES articles(id), transport TEXT NOT NULL,
          resolved_url TEXT NOT NULL, etag TEXT, last_modified TEXT, hash TEXT NOT NULL,
          stored_at TEXT NOT NULL, bytes INTEGER NOT NULL, PRIMARY KEY(article_id,transport));
        CREATE TABLE IF NOT EXISTS extraction_recipes (
          id TEXT NOT NULL, version INTEGER NOT NULL, definition TEXT NOT NULL,
          active INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, validation TEXT,
          PRIMARY KEY(id,version));
        CREATE UNIQUE INDEX IF NOT EXISTS one_active_recipe ON extraction_recipes(id) WHERE active=1;
        CREATE TABLE IF NOT EXISTS recipe_failures (
          publisher_id INTEGER NOT NULL REFERENCES publishers(id), template TEXT NOT NULL,
          count INTEGER NOT NULL DEFAULT 0, last_article_id INTEGER REFERENCES articles(id),
          first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, resolved_at TEXT,
          PRIMARY KEY(publisher_id,template));
        CREATE INDEX IF NOT EXISTS recent_extraction_queue ON articles(extraction_status,published_at DESC,id)
          WHERE duplicate_of IS NULL AND discovery_rejected=0;
        CREATE INDEX IF NOT EXISTS body_coverage ON articles(publisher_id,published_at,body_quality)
          WHERE duplicate_of IS NULL AND discovery_rejected=0;
        CREATE INDEX IF NOT EXISTS status_counts ON articles(extraction_status,publisher_id)
          WHERE duplicate_of IS NULL AND discovery_rejected=0;
        CREATE INDEX IF NOT EXISTS channels_source ON article_channels(source_id,article_id);
        CREATE INDEX IF NOT EXISTS active_extraction_leases ON articles(lease_until,lease_token,id) WHERE lease_token IS NOT NULL;
        CREATE INDEX IF NOT EXISTS active_extraction_hosts ON articles(url_host) WHERE lease_token IS NOT NULL;
        CREATE INDEX IF NOT EXISTS availability_stats ON articles(published_at,publisher_id,body_quality,body_origin,
          extraction_status,fetch_outcome,extraction_attempted_at,media_formats)
          WHERE duplicate_of IS NULL AND discovery_rejected=0;
        ''')
        db.execute("INSERT OR IGNORE INTO settings VALUES('portal_timezone','Asia/Calcutta')")
        db.execute('CREATE INDEX IF NOT EXISTS articles_publisher_url ON articles(publisher_id,url)')
        db.execute('CREATE INDEX IF NOT EXISTS articles_publisher_canonical ON articles(publisher_id,canonical_url)')
        if not db.execute("SELECT 1 FROM settings WHERE key='seeded'").fetchone():
            for source in CATALOG:
                if source['starter']:
                    add_source(db, source)
            db.execute("INSERT INTO settings VALUES ('seeded','1')")
        if not db.execute("SELECT 1 FROM settings WHERE key='phases_1_2_migrated'").fetchone():
            for source in db.execute('SELECT * FROM sources WHERE publisher_id IS NULL').fetchall():
                pid = publisher_for(db, dict(source))
                db.execute('UPDATE sources SET publisher_id=? WHERE id=?', (pid, source['id']))
            db.execute('''UPDATE articles SET publisher_id=(SELECT publisher_id FROM sources WHERE id=articles.source_id),
                       feed_content=content,feed_kind=content_kind,canonical_url=url''')
            # Never delete historical IDs. Hide overlapping-channel duplicates behind an alias.
            db.execute('''UPDATE articles SET duplicate_of=(SELECT MIN(b.id) FROM articles b
                       WHERE b.publisher_id=articles.publisher_id AND b.url=articles.url)
                       WHERE id!=(SELECT MIN(b.id) FROM articles b
                       WHERE b.publisher_id=articles.publisher_id AND b.url=articles.url)''')
            db.execute('''INSERT OR IGNORE INTO article_entries(source_id,entry_id,article_id,fingerprint)
                       SELECT source_id,entry_id,COALESCE(duplicate_of,id),fingerprint FROM articles''')
            db.execute('''INSERT OR IGNORE INTO article_channels
                       SELECT COALESCE(duplicate_of,id),source_id,MIN(first_seen),MAX(last_seen)
                       FROM articles GROUP BY COALESCE(duplicate_of,id),source_id''')
            for a in db.execute('SELECT id FROM articles WHERE duplicate_of IS NULL').fetchall():
                snapshot(db, a['id'], 'migration')
            db.execute("INSERT INTO settings VALUES ('phases_1_2_migrated','1')")
        db.execute('CREATE INDEX IF NOT EXISTS articles_publisher_url ON articles(publisher_id,url)')
        db.execute('CREATE INDEX IF NOT EXISTS articles_publisher_canonical ON articles(publisher_id,canonical_url)')
        db.execute('CREATE INDEX IF NOT EXISTS extraction_queue ON articles(extraction_status,next_extraction)')
        db.execute('''CREATE INDEX IF NOT EXISTS extraction_pending_order
                   ON articles(MAX(first_seen,content_changed_at) DESC,id DESC)
                   WHERE extraction_status='pending' AND duplicate_of IS NULL AND discovery_rejected=0''')
        db.execute('UPDATE sources SET category=(SELECT category FROM publishers WHERE id=sources.publisher_id)')
        if not db.execute("SELECT 1 FROM settings WHERE key='inventory_exclusions_migrated'").fetchone():
            # Apply historical exclusions once; collector/parser apply them to new items.
            db.execute('''UPDATE articles SET discovery_rejected=1 WHERE publisher_id IN
                       (SELECT id FROM publishers WHERE identity_key='pbs.org') AND url NOT LIKE '%pbs.org/newshour/%' ''')
            db.execute("UPDATE articles SET discovery_rejected=1 WHERE content_type_basis='Non-article publisher page'")
            db.execute("INSERT INTO settings VALUES('inventory_exclusions_migrated','1')")
        if not db.execute("SELECT 1 FROM settings WHERE key='rich_extraction_migrated'").fetchone():
            from .content import text_html
            for a in db.execute('SELECT id,url,content,content_kind,feed_content FROM articles').fetchall():
                # Legacy text is preserved; unavailable original markup is not invented.
                origin = 'page' if a['content_kind'] in ('Full text', 'Partial page text') else 'feed'
                db.execute('''UPDATE articles SET url_host=?,body_html=?,body_origin=?,body_quality=?,body_notes=? WHERE id=?''',
                    ((urlsplit(a['url']).hostname or '').lower(), text_html(a['content']), origin if a['content'] else 'none',
                     'unknown' if a['content'] else 'none', 'Legacy text preserved; completeness requires revalidation.', a['id']))
            db.execute("INSERT INTO settings VALUES('rich_extraction_migrated','1')")
            from .windows import date_window
            window=date_window(db)
            db.execute("UPDATE articles SET extraction_status='pending',next_extraction=NULL WHERE published_at>=? AND published_at<? AND duplicate_of IS NULL AND discovery_rejected=0",
                       (window['start'],window['end']))
        from .recipes import seed_recipes
        seed_recipes(db)


def add_source(db, source):
    existing = db.execute('SELECT id FROM sources WHERE feed_url=?', (source['feed_url'],)).fetchone()
    if existing:
        return existing['id'], False
    pid = source.get('publisher_id') or publisher_for(db, source)
    cur = db.execute('''INSERT INTO sources(catalog_id,name,category,website,feed_url,created_at,publisher_id,
                     channel_type,section,interval_minutes) VALUES (?,?,?,?,?,?,?,?,?,?)''',
                     (source.get('id'), source['name'], source['category'], source['website'], source['feed_url'],
                      now(), pid, source.get('channel_type', 'feed'), source.get('section', 'Original subscription'),
                      source.get('interval_minutes', 30 if source.get('channel_type') == 'sitemap' else 5)))
    return cur.lastrowid, True

SNAPSHOT_FIELDS = ['title', 'url', 'canonical_url', 'author', 'tags', 'published_at', 'publisher_updated_at',
                   'published_raw', 'updated_raw', 'content', 'content_kind', 'content_type',
                   'publisher_content_label', 'content_type_basis', 'media_formats', 'media_json',
                   'images_json', 'language', 'transcript', 'transcript_status', 'extraction_status',
                   'extraction_method', 'extraction_notes', 'feed_html', 'body_html', 'body_origin',
                   'body_quality', 'body_notes', 'recipe_id', 'recipe_version','publisher_access']

def snapshot(db, article_id, origin):
    row = db.execute('SELECT * FROM articles WHERE id=?', (article_id,)).fetchone()
    data = {k: row[k] for k in SNAPSHOT_FIELDS}
    payload = json.dumps(data, sort_keys=True, ensure_ascii=False)
    digest = hashlib.sha256(payload.encode()).hexdigest()
    last = db.execute('SELECT hash FROM article_versions WHERE article_id=? ORDER BY id DESC LIMIT 1', (article_id,)).fetchone()
    if not last or last['hash'] != digest:
        db.execute('INSERT INTO article_versions(article_id,observed_at,origin,hash,snapshot) VALUES(?,?,?,?,?)',
                   (article_id, now(), origin, digest, payload))
        return True
    return False


def upsert_article(db, source, item, seen):
    entry = db.execute('SELECT * FROM article_entries WHERE source_id=? AND entry_id=?',
                       (source['id'], item['entry_id'])).fetchone()
    existing = db.execute('SELECT * FROM articles WHERE id=?', (entry['article_id'],)).fetchone() if entry else None
    if not existing:
        existing = db.execute('''SELECT * FROM articles WHERE id IN (SELECT id FROM articles WHERE publisher_id=? AND url=? AND duplicate_of IS NULL UNION SELECT id FROM articles WHERE publisher_id=? AND canonical_url=? AND duplicate_of IS NULL) ORDER BY id LIMIT 1''',
                              (source['publisher_id'], item['url'], source['publisher_id'], item['url'])).fetchone()
    created = not existing
    changed = created or (entry['fingerprint'] if entry else existing['fingerprint']) != item['fingerprint']
    if created:
        from .content import sanitize_html
        fields = dict(item, source_id=source['id'], publisher_id=source['publisher_id'], canonical_url=item['url'],
                      feed_content=item['content'], feed_kind=item['content_kind'], first_seen=seen,
                      last_seen=seen, content_changed_at=seen, url_host=(urlsplit(item['url']).hostname or '').lower(),
                      body_html=sanitize_html(item.get('feed_html') or '', item['url']),
                      body_origin='feed' if item['content'] else 'none', body_quality='unknown' if item['content'] else 'none',
                      body_notes='Feed body not yet compared with an article page.')
        cols = list(fields)
        aid = db.execute(f"INSERT INTO articles({','.join(cols)}) VALUES({','.join('?' for _ in cols)})",
                         [fields[x] for x in cols]).lastrowid
    else:
        aid = existing['id']
        fields = dict(item)
        fields.pop('entry_id')  # Keep original identity; alternate channel GUIDs live in article_entries.
        fields['last_seen'] = seen
        # A sitemap discovery stub must never overwrite richer feed or extracted metadata.
        if source['channel_type'] == 'sitemap':
            fields = {'last_seen': seen}
            changed = False
        elif changed:
            fields.update(feed_content=item['content'], feed_kind=item['content_kind'])
            for key in ('images_json','media_json'):
                incoming=json.loads(item.get(key) or '[]');saved=json.loads(existing[key] or '[]')
                for candidate in incoming:
                    if not any(x.get('url')==candidate.get('url') and x.get('kind')==candidate.get('kind') for x in saved): saved.append(candidate)
                fields[key]=json.dumps(saved)
            fields['media_formats']=json.dumps(list(dict.fromkeys(json.loads(existing['media_formats'])+json.loads(item.get('media_formats') or '[]'))))
            if item.get('content_type')=='unknown' or item.get('content_type')=='reporting' and existing['content_type'] not in ('unknown','reporting'):
                for key in ('content_type','publisher_content_label','content_type_basis'): fields.pop(key,None)
            if existing['extracted_at']:
                for key in ('content', 'content_kind', 'url', 'title', 'author', 'published_at', 'published_raw'):
                    fields.pop(key, None)
            elif existing['body_quality'] != 'full':
                from .content import sanitize_html
                fields.update(body_html=sanitize_html(item.get('feed_html') or '', item['url']),
                              body_origin='feed' if item['content'] else 'none', body_quality='unknown' if item['content'] else 'none')
            if existing['content_type_basis'] == 'Manual correction':
                for key in ('content_type','publisher_content_label','content_type_basis'):
                    fields.pop(key,None)
                for key in ('images_json', 'media_json', 'media_formats', 'content_type', 'publisher_content_label', 'content_type_basis'):
                    fields.pop(key, None)
            fields['content_changed_at'] = seen
            fields['next_extraction'] = None
            fields['extraction_status'] = 'pending'
            fields['extraction_failures'] = 0
        else:
            fields = {'last_seen': seen}
        db.execute('UPDATE articles SET ' + ','.join(f'{k}=?' for k in fields) + ' WHERE id=?',
                   list(fields.values()) + [aid])
    db.execute('''INSERT INTO article_entries(source_id,entry_id,article_id,fingerprint) VALUES(?,?,?,?)
               ON CONFLICT(source_id,entry_id) DO UPDATE SET article_id=excluded.article_id,fingerprint=excluded.fingerprint''',
               (source['id'], item['entry_id'], aid, item['fingerprint']))
    db.execute('INSERT INTO article_channels VALUES(?,?,?,?) ON CONFLICT(article_id,source_id) DO UPDATE SET last_seen=excluded.last_seen',
               (aid, source['id'], seen, seen))
    if changed:
        snapshot(db, aid, 'feed' if source['channel_type'] == 'feed' else 'sitemap')
    return created, changed and not created, aid
