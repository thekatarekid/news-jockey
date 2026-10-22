"""Bounded local raw HTML cache for conditional fetching and recipe repair."""
import gzip
import hashlib
import os
import time
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

MAX_CACHE_BYTES=200*1024*1024
RETENTION_DAYS=7

def cache_dir(path): return Path(path).parent/'page_cache'
def cache_file(path,article_id,transport):
    if transport not in ('http','browser'): raise ValueError('Invalid cache transport.')
    return cache_dir(path)/(str(int(article_id))+'-'+transport+'.html.gz')

def load_page(path,article_id,transport='http'):
    file=cache_file(path,article_id,transport)
    try:
        if time.time()-file.stat().st_mtime > RETENTION_DAYS*86400: return None
        with gzip.open(file,'rb') as stream:
            body=stream.read(4*1024*1024+1)
        return body if len(body)<=4*1024*1024 else None
    except (OSError,EOFError): return None

def save_page(db,path,article_id,transport,body,url,headers):
    directory=cache_dir(path);directory.mkdir(parents=True,exist_ok=True)
    compressed=gzip.compress(body)
    file=cache_file(path,article_id,transport);temporary=directory/(file.name+'.'+uuid.uuid4().hex+'.tmp')
    temporary.write_bytes(compressed);os.replace(temporary,file)
    stamp=datetime.now(timezone.utc).isoformat(timespec='seconds')
    db.execute('''INSERT INTO cached_pages VALUES(?,?,?,?,?,?,?,?)
        ON CONFLICT(article_id,transport) DO UPDATE SET resolved_url=excluded.resolved_url,etag=excluded.etag,
        last_modified=excluded.last_modified,hash=excluded.hash,stored_at=excluded.stored_at,bytes=excluded.bytes''',
        (article_id,transport,url,headers.get('ETag'),headers.get('Last-Modified'),hashlib.sha256(body).hexdigest(),stamp,len(compressed)))

def prune_cache(path):
    directory=cache_dir(path)
    if not directory.exists(): return
    files=[];total=0;cutoff=time.time()-RETENTION_DAYS*86400
    for file in directory.glob('*.html.gz'):
        try: stat=file.stat()
        except OSError: continue
        if stat.st_mtime<cutoff:
            file.unlink(missing_ok=True);continue
        files.append((stat.st_mtime,stat.st_size,file));total+=stat.st_size
    for _,size,file in sorted(files):
        if total<=MAX_CACHE_BYTES: break
        file.unlink(missing_ok=True);total-=size
