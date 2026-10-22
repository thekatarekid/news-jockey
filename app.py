import argparse
import atexit
import secrets
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlsplit
from flask import Flask, jsonify, render_template, request
from newsjockey.storage import ROOT, CATALOG, CATEGORIES, CONTENT_TYPES, MEDIA_FORMATS, EXTRACTION_STATUSES, connect, initialize, add_source, now, snapshot
from newsjockey.collector import Collector
from newsjockey.network import public_url
from newsjockey.processing_api import register_processing_routes
from newsjockey.extraction_jobs import create_job, initialize_jobs
from newsjockey.windows import date_window, group_condition
from newsjockey.analysis_api import register_analysis_routes
from newsjockey.corpus_api import register_corpus_routes

def create_app(database=None, start_collector=False):
    app = Flask(__name__)
    app.config.update(DATABASE=str(database or ROOT / 'data' / 'newsjockey.sqlite3'),
                      MAX_CONTENT_LENGTH=128 * 1024, TRUSTED_HOSTS=['127.0.0.1', 'localhost', '[::1]'])
    initialize(app.config['DATABASE'])
    initialize_jobs(app.config['DATABASE'])
    collector = Collector(app.config['DATABASE'])
    app.extensions['collector'] = collector
    token = secrets.token_urlsafe(32)
    register_processing_routes(app)
    register_analysis_routes(app)
    register_corpus_routes(app)

    @app.before_request
    def protect_mutations():
        if request.method in ('POST', 'PATCH', 'DELETE'):
            origin = request.headers.get('Origin')
            if origin and origin != request.host_url.rstrip('/'):
                return jsonify(error='Requests must come from this local portal.'), 403
            if not secrets.compare_digest(request.headers.get('X-NewsJockey-Token', ''), token):
                return jsonify(error='Reload the portal before making changes.'), 403
            if not request.is_json:
                return jsonify(error='Expected JSON.'), 415

    @app.errorhandler(ValueError)
    def invalid(exc):
        return jsonify(error=str(exc)), 400

    @app.errorhandler(413)
    def too_large(exc):
        return jsonify(error='Request is too large. Import at most 50 URLs at a time.'), 413

    @app.get('/')
    def index():
        return render_template('index.html', token=token, categories=CATEGORIES,
                               content_types=CONTENT_TYPES, extraction_statuses=EXTRACTION_STATUSES)

    @app.get('/api/sources')
    def sources():
        with connect(app.config['DATABASE']) as db:
            rows = db.execute('''SELECT s.*,p.name publisher,p.category source_category,COUNT(c.article_id) article_count
              FROM sources s JOIN publishers p ON p.id=s.publisher_id
              LEFT JOIN article_channels c ON c.source_id=s.id GROUP BY s.id ORDER BY p.name,s.channel_type,s.name''').fetchall()
        return jsonify(sources=[dict(x) for x in rows], active_job=collector.state(), categories=CATEGORIES)

    @app.get('/api/publishers')
    def publishers():
        with connect(app.config['DATABASE']) as db:
            rows=db.execute('''SELECT p.*,
                (SELECT COUNT(*) FROM sources s WHERE s.publisher_id=p.id) channel_count,
                (SELECT COUNT(*) FROM sources s WHERE s.publisher_id=p.id AND enabled=1) enabled_channels,
                (SELECT COUNT(*) FROM articles a WHERE a.publisher_id=p.id AND duplicate_of IS NULL AND discovery_rejected=0) article_count,
                (SELECT COUNT(*) FROM articles a WHERE a.publisher_id=p.id AND duplicate_of IS NULL AND discovery_rejected=0 AND body_quality='full') complete_count
                FROM publishers p ORDER BY p.category,p.name''').fetchall()
        return jsonify(publishers=[dict(x) for x in rows], categories=CATEGORIES)

    @app.patch('/api/publishers/<int:publisher_id>')
    def edit_publisher(publisher_id):
        payload=request.get_json(); fields={}
        if 'browser_rendering' in payload:
            if type(payload['browser_rendering']) is not bool: raise ValueError('Browser rendering must be true or false.')
            fields['browser_rendering']=int(payload['browser_rendering'])
        if 'category' in payload:
            if payload['category'] not in CATEGORIES: raise ValueError('Select a valid source category.')
            fields['category']=payload['category']
        if not fields: raise ValueError('No publisher changes supplied.')
        with connect(app.config['DATABASE']) as db:
            if not db.execute('SELECT 1 FROM publishers WHERE id=?',(publisher_id,)).fetchone(): return jsonify(error='Publisher not found.'),404
            db.execute('UPDATE publishers SET '+','.join(k+'=?' for k in fields)+' WHERE id=?',list(fields.values())+[publisher_id])
            if 'category' in fields: db.execute('UPDATE sources SET category=? WHERE publisher_id=?',(fields['category'],publisher_id))
        return jsonify(ok=True)

    @app.post('/api/coverage/expand')
    def expand_coverage():
        pid=request.get_json().get('publisher_id')
        if pid is not None and type(pid) is not int: raise ValueError('Invalid publisher ID.')
        with connect(app.config['DATABASE']) as db:
            if pid is not None and not db.execute('SELECT 1 FROM publishers WHERE id=?',(pid,)).fetchone(): return jsonify(error='Publisher not found.'),404
            sql='UPDATE publishers SET next_discovery=NULL'
            db.execute(sql+(' WHERE id=?' if pid is not None else ''),[pid] if pid is not None else [])
        return jsonify(ok=True,message='Publisher coverage scans queued.'),202

    @app.get('/api/processing')
    def processing():
        with connect(app.config['DATABASE']) as db:
            counts={r['extraction_status']:r['n'] for r in db.execute('SELECT extraction_status,COUNT(*) n FROM articles WHERE duplicate_of IS NULL AND discovery_rejected=0 GROUP BY extraction_status')}
            versions=db.execute('SELECT COUNT(*) FROM article_versions').fetchone()[0]
        return jsonify(extraction=counts,versions=versions,workers=4,running=bool(collector.thread))

    @app.post('/api/extraction/retry')
    def retry_extraction():
        payload=request.get_json(); aid=payload.get('article_id'); pid=payload.get('publisher_id')
        if any(x is not None and type(x) is not int for x in (aid,pid)): raise ValueError('Invalid item/publisher ID.')
        conditions=['duplicate_of IS NULL','discovery_rejected=0','lease_token IS NULL']; args=[]
        if aid is not None:
            conditions.append('id=?');args.append(aid)
        elif pid is not None:
            conditions.append('publisher_id=?');args.append(pid)
        if aid is None: conditions.append("extraction_status IN ('partial','failed','blocked','unsupported','paywalled')")
        else: conditions.append("extraction_status!='extracting'")
        with connect(app.config['DATABASE']) as db:
            window=date_window(db)
            if aid is not None:
                item=db.execute('SELECT published_at FROM articles WHERE id=?',(aid,)).fetchone()
                if item and (not item['published_at'] or item['published_at']<window['start'] or item['published_at']>=window['end']):
                    # Explicit article action is manual; it never enables automatic historical work.
                    data=create_job(app.config['DATABASE'],'http','single',[aid])
                    return jsonify(queued=data['total'],job_id=data['id']),202
            conditions.extend(['published_at>=?','published_at<?']);args.extend([window['start'],window['end']])
            count=db.execute("UPDATE articles SET extraction_status='pending',next_extraction=NULL,extraction_failures=0 WHERE "+' AND '.join(conditions),args).rowcount
        return jsonify(queued=count),202

    @app.get('/api/catalog')
    def catalog():
        q = request.args.get('q', '').strip().lower()
        category = request.args.get('category', '')
        with connect(app.config['DATABASE']) as db:
            added = {x['feed_url'] for x in db.execute('SELECT feed_url FROM sources')}
        results = [{**x, 'added': x['feed_url'] in added} for x in CATALOG
                   if (not category or x['category'] == category)
                   and (not q or q in (x['name'] + ' ' + x['website'] + ' ' + x['description']).lower())]
        return jsonify(results=results, total=len(CATALOG))

    @app.post('/api/sources')
    def add_sources():
        payload = request.get_json()
        entries = payload.get('sources')
        if not isinstance(entries, list) or not 1 <= len(entries) <= 50:
            raise ValueError('Select between 1 and 50 sources.')
        prepared = []
        for row in entries:
            if not isinstance(row, dict):
                raise ValueError('Invalid source.')
            if row.get('catalog_id'):
                item = next((x for x in CATALOG if x['id'] == row['catalog_id']), None)
                if not item:
                    raise ValueError('Unknown catalog source.')
            else:
                category = row.get('category')
                if category not in CATEGORIES:
                    raise ValueError('Select a valid category.')
                url = public_url(str(row.get('feed_url', '')))
                website = public_url(str(row.get('website') or url))
                name = str(row.get('name', '')).strip()[:200]
                if not name:
                    raise ValueError('A source name is required.')
                item = dict(name=name, category=category, website=website, feed_url=url)
                if row.get('channel_type') not in (None, 'feed', 'sitemap'): raise ValueError('Invalid channel type.')
                if row.get('channel_type'): item['channel_type']=row['channel_type']
            prepared.append(item)
        results = []
        with connect(app.config['DATABASE']) as db:
            for item in prepared:
                source_id, added = add_source(db, item)
                results.append(dict(id=source_id, name=item['name'], added=added))
        return jsonify(results=results)

    @app.patch('/api/sources/<int:source_id>')
    def edit_source(source_id):
        payload = request.get_json()
        fields = {}
        if 'enabled' in payload:
            if type(payload['enabled']) is not bool:
                raise ValueError('Enabled must be true or false.')
            fields['enabled'] = int(payload['enabled'])
        if 'category' in payload:
            if payload['category'] not in CATEGORIES:
                raise ValueError('Select a valid category.')
            fields['category'] = payload['category']
        if 'interval_minutes' in payload:
            value = payload['interval_minutes']
            if type(value) is not int or not 1 <= value <= 1440:
                raise ValueError('Polling interval must be 1–1440 minutes.')
            fields['interval_minutes'] = value
            # Reschedule from the last successful check without bypassing error backoff.
        if not fields:
            raise ValueError('No source changes supplied.')
        with connect(app.config['DATABASE']) as db:
            source = db.execute('SELECT * FROM sources WHERE id=?', (source_id,)).fetchone()
            if not source:
                return jsonify(error='Source not found.'), 404
            if 'interval_minutes' in fields and source['status'] != 'Error':
                interval = max(fields['interval_minutes'], source['feed_ttl_minutes'])
                fields['next_check'] = (datetime.now(timezone.utc) + timedelta(minutes=interval)).isoformat(timespec='seconds')
            db.execute('UPDATE sources SET ' + ','.join(f'{k}=?' for k in fields) + ' WHERE id=?', list(fields.values()) + [source_id])
            if 'category' in fields:
                db.execute('UPDATE publishers SET category=? WHERE id=?',(fields['category'],source['publisher_id']))
                db.execute('UPDATE sources SET category=? WHERE publisher_id=?',(fields['category'],source['publisher_id']))
        return jsonify(ok=True)

    @app.post('/api/discover')
    def discover_sources():
        urls = request.get_json().get('urls')
        if not isinstance(urls, list) or not 1 <= len(urls) <= 50 or any(not isinstance(x, str) or len(x) > 2048 for x in urls):
            raise ValueError('Paste 1–50 website or RSS/Atom URLs.')
        values = list(dict.fromkeys(x.strip() for x in urls if x.strip()))
        if not values:
            raise ValueError('Paste at least one URL.')
        return jsonify(job_id=collector.submit('discover', values)), 202

    @app.post('/api/collect')
    def collect():
        source_id = request.get_json().get('source_id')
        if source_id is not None and type(source_id) is not int:
            raise ValueError('Invalid source ID.')
        return jsonify(job_id=collector.collect(source_id=source_id)), 202

    @app.get('/api/jobs/<job_id>')
    def job(job_id):
        data = collector.job(job_id)
        return (jsonify(data), 200) if data else (jsonify(error='Job not found.'), 404)

    @app.get('/api/articles')
    def articles():
        page = max(1, min(100000, int(request.args.get('page', 1))))
        size = max(10, min(100, int(request.args.get('size', 50))))
        order = {'published': 'a.published_at', 'seen': 'a.first_seen', 'changed': 'a.content_changed_at',
                 'source': 'p.name COLLATE NOCASE', 'title': 'a.title COLLATE NOCASE'}.get(request.args.get('sort'), 'a.published_at')
        direction = 'ASC' if request.args.get('direction') == 'asc' else 'DESC'
        conditions, args = ['a.duplicate_of IS NULL','a.discovery_rejected=0'], []
        if request.args.get('category'):
            conditions.append('p.category=?'); args.append(request.args['category'])
        if request.args.get('source'):
            conditions.append('EXISTS(SELECT 1 FROM article_channels c WHERE c.article_id=a.id AND c.source_id=?)'); args.append(int(request.args['source']))
        if request.args.get('publisher'):
            conditions.append('a.publisher_id=?');args.append(int(request.args['publisher']))
        for key in ('content_type','extraction_status','body_quality'):
            if request.args.get(key): conditions.append('a.'+key+'=?');args.append(request.args[key])
        if request.args.get('media_format'):
            conditions.append('EXISTS(SELECT 1 FROM json_each(a.media_formats) WHERE value=?)');args.append(request.args['media_format'])
        if request.args.get('q', '').strip():
            conditions.append('(a.title LIKE ? OR a.content LIKE ? OR a.author LIKE ?)')
            query = '%' + request.args['q'].strip()[:200] + '%'
            args.extend([query] * 3)
        if request.args.get('kind'):
            conditions.append('a.content_kind=?'); args.append(request.args['kind'])
        if request.args.get('date_group'):
            with connect(app.config['DATABASE']) as db: window=date_window(db)
            clause,values=group_condition(request.args['date_group'],window)
            conditions.append(clause);args.extend(values)
        since = request.args.get('since', '')
        if since in ('24', '168', '720'):
            conditions.append('a.first_seen>=?')
            args.append((datetime.now(timezone.utc) - timedelta(hours=int(since))).isoformat(timespec='seconds'))
        for key, op in [('from', '>='), ('to', '<')]:
            if request.args.get(key):
                date = datetime.strptime(request.args[key], '%Y-%m-%d').replace(tzinfo=timezone.utc)
                if key == 'to':
                    date += timedelta(days=1)
                conditions.append(f'a.published_at {op} ?'); args.append(date.isoformat(timespec='seconds'))
        where = ' WHERE ' + ' AND '.join(conditions) if conditions else ''
        base = ' FROM articles a JOIN sources s ON s.id=a.source_id JOIN publishers p ON p.id=a.publisher_id'
        with connect(app.config['DATABASE']) as db:
            total = db.execute('SELECT COUNT(*)' + base + where, args).fetchone()[0]
            rows = db.execute('''SELECT a.id,a.title,a.url,a.author,a.tags,a.published_at,a.publisher_updated_at,
              a.first_seen,a.last_seen,a.content_changed_at,a.content_kind,substr(a.content,1,240) preview,
              length(a.content) content_length,p.name source,p.category category,p.category source_category,s.id source_id,
              a.publisher_id,a.content_type,a.media_formats,a.extraction_status,a.extraction_method,a.extraction_error,
              a.body_quality,a.body_origin,a.fetch_outcome,a.recipe_id,a.recipe_version''' + base + where +
              f' ORDER BY {order} {direction} NULLS LAST,a.id DESC LIMIT ? OFFSET ?',
              args + [size, (page-1)*size]).fetchall()
        return jsonify(articles=[dict(x) for x in rows], total=total, page=page, size=size)

    @app.get('/api/articles/<int:article_id>')
    def article(article_id):
        with connect(app.config['DATABASE']) as db:
            row = db.execute('SELECT duplicate_of FROM articles WHERE id=?',(article_id,)).fetchone()
            if row and row['duplicate_of']: article_id=row['duplicate_of']
            row = db.execute('''SELECT a.*,p.name source,p.category,p.category source_category,s.feed_url,p.website FROM articles a
                               JOIN sources s ON s.id=a.source_id JOIN publishers p ON p.id=a.publisher_id WHERE a.id=?''', (article_id,)).fetchone()
            channels=db.execute('''SELECT s.id,s.name,s.feed_url,s.channel_type,c.first_seen,c.last_seen
                                 FROM article_channels c JOIN sources s ON s.id=c.source_id WHERE c.article_id=?''',(article_id,)).fetchall()
            versions=db.execute('SELECT id,observed_at,origin FROM article_versions WHERE article_id=? ORDER BY id DESC LIMIT 50',(article_id,)).fetchall()
        if not row:
            return jsonify(error='Article not found.'), 404
        data = dict(row)
        import json
        for key in ('media_formats','media_json','images_json'): data[key]=json.loads(data[key])
        data['channels']=[dict(x) for x in channels]
        data['versions']=[dict(x) for x in versions]
        data.pop('fingerprint')
        data['observed_delay_seconds'] = None
        if data['published_at']:
            data['observed_delay_seconds'] = int((datetime.fromisoformat(data['first_seen']) - datetime.fromisoformat(data['published_at'])).total_seconds())
        return jsonify(data)

    @app.get('/api/articles/<int:article_id>/versions/<int:version_id>')
    def article_version(article_id,version_id):
        import json
        with connect(app.config['DATABASE']) as db:
            row=db.execute('SELECT * FROM article_versions WHERE article_id=? AND id=?',(article_id,version_id)).fetchone()
        if not row: return jsonify(error='Version not found.'),404
        return jsonify(id=row['id'],observed_at=row['observed_at'],origin=row['origin'],snapshot=json.loads(row['snapshot']))

    @app.patch('/api/articles/<int:article_id>')
    def label_article(article_id):
        kind=request.get_json().get('content_type')
        if kind not in CONTENT_TYPES: raise ValueError('Select a valid content type.')
        with connect(app.config['DATABASE']) as db:
            row=db.execute('SELECT id FROM articles WHERE id=? AND duplicate_of IS NULL AND discovery_rejected=0',(article_id,)).fetchone()
            if not row: return jsonify(error='Article not found.'),404
            db.execute("UPDATE articles SET content_type=?,content_type_basis='Manual correction' WHERE id=?",(kind,article_id))
            snapshot(db,article_id,'manual label')
        return jsonify(ok=True)

    if start_collector:
        collector.start()
        app.extensions['processing_worker'].start()
        app.extensions['corpus_worker'].start()
        atexit.register(app.extensions['corpus_worker'].stop)
        atexit.register(app.extensions['processing_worker'].stop)
        atexit.register(collector.stop)
    return app

if __name__ == '__main__':
    # On-demand local diagnostics; no article text or credentials are dumped.
    import faulthandler, signal
    faulthandler.register(signal.SIGUSR1, all_threads=True)
    parser = argparse.ArgumentParser(description='Run the News Jockey portal on this Mac.')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--no-poll', action='store_true', help='Disable background collection for debugging.')
    args = parser.parse_args()
    app = create_app(start_collector=not args.no_poll)
    try:
        app.run(host='127.0.0.1', port=args.port, debug=False, use_reloader=False, threaded=True)
    finally:
        app.extensions['collector'].stop()
        app.extensions['processing_worker'].stop()
