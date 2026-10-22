"""Durable whole-month local metadata and same-event processing.

Jobs snapshot their inputs. Extraction and collection can continue independently;
rerunning a month reuses unchanged features and includes newly collected records.
"""
import hashlib
import json
import os
import re
import subprocess
import threading
import uuid
from .storage import ROOT, connect, now
from .windows import month_window, date_window
from .classification import source_text

VERSION='october-events-2'
MODEL='fastino/gliner2-base-v1'


def initialize_corpus(path):
    with connect(path) as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS corpus_runs (
          id TEXT PRIMARY KEY, month TEXT NOT NULL, timezone TEXT NOT NULL,
          status TEXT NOT NULL, stage TEXT NOT NULL, total INTEGER NOT NULL,
          processed INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          owner_pid INTEGER, error TEXT, report TEXT NOT NULL DEFAULT '{}');
        CREATE TABLE IF NOT EXISTS corpus_items (
          run_id TEXT NOT NULL REFERENCES corpus_runs(id), article_id INTEGER NOT NULL REFERENCES articles(id),
          input_hash TEXT NOT NULL, input TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending', error TEXT,
          PRIMARY KEY(run_id,article_id));
        CREATE TABLE IF NOT EXISTS corpus_features (
          article_id INTEGER PRIMARY KEY REFERENCES articles(id), input_hash TEXT NOT NULL,
          version TEXT NOT NULL, data TEXT NOT NULL, title_vector TEXT NOT NULL, lead_vector TEXT NOT NULL,
          model_version TEXT NOT NULL, processed_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS corpus_queue ON corpus_items(run_id,state,article_id);
        ''')


def prepare_input(article):
    a=dict(article);text,basis,suspect=source_text(a)
    # Keep the event-bearing lead; embedding an entire long article can dilute it
    # with background, links and unrelated events. Exact submitted text is saved.
    body=text[len(a['title']):].strip()
    body=re.sub(r'\s+',' ',body).strip()
    title=re.sub(r'\s+',' ',a['title']).strip()
    text=title+'\n\n'+body[:1100]
    payload=dict(id=a['id'],title=title,text=text,basis=basis,suspect=suspect,
                 published_at=a['published_at'],publisher_id=a['publisher_id'],
                 content_type=a.get('content_type','unknown'),content_type_basis=a.get('content_type_basis'),
                 body_quality=a.get('body_quality'),source_hash=__import__('newsjockey.classification',fromlist=['input_hash']).input_hash(a))
    digest=hashlib.sha256(json.dumps([VERSION,text,payload['published_at'],payload['content_type'],basis],sort_keys=True).encode()).hexdigest()
    return payload,digest


def create_corpus_run(path,month):
    initialize_corpus(path)
    with connect(path) as db:
        w=month_window(month,date_window(db)['timezone'])
        active=db.execute("SELECT * FROM corpus_runs WHERE month=? AND status IN ('queued','running','paused') ORDER BY created_at DESC LIMIT 1",(month,)).fetchone()
        if active:return {**dict(active),'existing':True}
        # Read large bodies without holding a writer transaction.
        rows=db.execute('''SELECT * FROM articles WHERE published_at>=? AND published_at<?
             AND duplicate_of IS NULL AND discovery_rejected=0 ORDER BY published_at,id''',(w['start'],w['end'])).fetchall()
    inputs=[(a['id'],*prepare_input(a)) for a in rows]
    rid=uuid.uuid4().hex;stamp=now()
    with connect(path) as db:
        db.execute('BEGIN IMMEDIATE')
        active=db.execute("SELECT * FROM corpus_runs WHERE month=? AND status IN ('queued','running','paused') LIMIT 1",(month,)).fetchone()
        if active:return {**dict(active),'existing':True}
        db.execute('INSERT INTO corpus_runs(id,month,timezone,status,stage,total,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)',
                   (rid,month,w['timezone'],'queued','metadata and embeddings',len(inputs),stamp,stamp))
        db.executemany('INSERT INTO corpus_items(run_id,article_id,input_hash,input) VALUES(?,?,?,?)',
                       ((rid,aid,h,json.dumps(a)) for aid,a,h in inputs))
    return dict(id=rid,total=len(inputs),month=month,status='queued',existing=False)


def overview(path):
    with connect(path) as db:
        runs=[]
        for row in db.execute('SELECT * FROM corpus_runs ORDER BY created_at DESC LIMIT 12'):
            item=dict(row);item['report']=json.loads(item['report']);runs.append(item)
        stats=db.execute('''SELECT COUNT(*) stories,SUM(n>1) multi_article,SUM(p>1) multi_publisher,SUM(n=1) singletons,SUM(n) articles
          FROM (SELECT COUNT(*) n,COUNT(DISTINCT a.publisher_id) p FROM story_members m
          JOIN articles a ON a.id=m.article_id JOIN news_stories s ON s.id=m.story_id
          WHERE s.merged_into IS NULL AND m.role='primary' GROUP BY s.id)''').fetchone()
    return dict(runs=runs,stats=dict(stats),version=VERSION,metadata_model=MODEL,embedding_model='embeddinggemma-2',topics_deferred=True)


def control(path,rid,action):
    if action not in ('pause','resume','cancel'):raise ValueError('Choose pause, resume or cancel.')
    with connect(path) as db:
        row=db.execute('SELECT * FROM corpus_runs WHERE id=?',(rid,)).fetchone()
        if not row or row['status'] in ('complete','cancelled'):raise ValueError('This job is not active.')
        if action=='resume' and row['status'] not in ('paused','failed'):raise ValueError('Only paused/failed jobs can resume.')
        db.execute('UPDATE corpus_runs SET status=?,error=NULL,updated_at=? WHERE id=?',
                   ({'pause':'paused','resume':'queued','cancel':'cancelled'}[action],now(),rid))


class CorpusWorker:
    def __init__(self,path):self.path=str(path);self.thread=None;self.stopped=threading.Event()
    def start(self):
        if self.thread:return
        def loop():
            while not self.stopped.is_set():
                try:self.step()
                except Exception:
                    import logging;logging.exception('Whole-month scheduler error')
                self.stopped.wait(3)
        self.thread=threading.Thread(target=loop,daemon=True,name='month-processing');self.thread.start()
    def stop(self):self.stopped.set() # Detached child checks durable pause/cancel state.
    def step(self):
        with connect(self.path) as db:
            rows=db.execute("SELECT * FROM corpus_runs WHERE status IN ('queued','running') ORDER BY created_at").fetchall()
            for r in rows:
                if r['owner_pid']:
                    try:os.kill(r['owner_pid'],0);return
                    except ProcessLookupError:pass
                binary=ROOT/'data/runtime/nlp-env/bin/python'
                if not binary.exists():
                    db.execute("UPDATE corpus_runs SET status='failed',error='Local NLP runtime missing; run tools/setup_nlp.py.' WHERE id=?",(r['id'],));return
                log=ROOT/'data/logs';log.mkdir(parents=True,exist_ok=True)
                env=dict(os.environ,HF_HOME=str(ROOT/'data/models/huggingface'),HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',TOKENIZERS_PARALLELISM='false',OPENBLAS_NUM_THREADS='4')
                with (log/'corpus.log').open('ab') as out:
                    p=subprocess.Popen([str(binary),str(ROOT/'tools/process_month.py'),'--database',self.path,'--job',r['id']],cwd=ROOT,env=env,stdout=out,stderr=out,start_new_session=True)
                db.execute("UPDATE corpus_runs SET status='running',owner_pid=?,updated_at=? WHERE id=?",(p.pid,now(),r['id']));return
