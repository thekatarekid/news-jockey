"""Additive, independently versioned storage for processing phases 3–5."""
import json
from .storage import connect, now

PIPELINE_VERSION = '3.3'
DEFAULT_CONFIG = {'version': PIPELINE_VERSION, 'embedding': 'embeddinggemma-2', 'dimensions': 512,
                  'decision_model': 'local-gliner2', 'automatic': False, 'batch_size': 100}

def initialize_analysis(path):
    with connect(path) as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS processing_configs (
          id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, config TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS article_analysis (
          article_id INTEGER PRIMARY KEY REFERENCES articles(id), input_hash TEXT NOT NULL,
          source_version_id INTEGER REFERENCES article_versions(id), config_id INTEGER NOT NULL,
          status TEXT NOT NULL, data TEXT NOT NULL, manual TEXT NOT NULL DEFAULT '{}',
          embedding_model TEXT, embedding_dimensions INTEGER, vector TEXT, chunks INTEGER DEFAULT 0,
          input_basis TEXT NOT NULL, processed_at TEXT NOT NULL, error TEXT);
        CREATE INDEX IF NOT EXISTS analysis_status ON article_analysis(status,processed_at);
        CREATE VIRTUAL TABLE IF NOT EXISTS analysis_search USING fts5(title,terms);
        CREATE TABLE IF NOT EXISTS processing_runs (
          id TEXT PRIMARY KEY, phase INTEGER NOT NULL, scope TEXT NOT NULL, engine TEXT NOT NULL,
          config_id INTEGER NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          error TEXT, owner_pid INTEGER);
        CREATE TABLE IF NOT EXISTS processing_run_items (
          run_id TEXT NOT NULL REFERENCES processing_runs(id), item_id INTEGER NOT NULL,
          state TEXT NOT NULL DEFAULT 'pending', result TEXT, error TEXT,
          PRIMARY KEY(run_id,item_id));
        CREATE INDEX IF NOT EXISTS processing_queue ON processing_run_items(run_id,state,item_id);
        CREATE TABLE IF NOT EXISTS processing_calls (
          id INTEGER PRIMARY KEY, run_id TEXT, item_id INTEGER, operation TEXT NOT NULL,
          provider TEXT NOT NULL, model TEXT NOT NULL, model_version TEXT, prompt_version TEXT NOT NULL,
          input_hash TEXT, elapsed_seconds REAL NOT NULL, input_tokens INTEGER, output_tokens INTEGER,
          peak_memory_mb REAL, incremental_cost REAL, status TEXT NOT NULL, error TEXT,
          created_at TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '{}');
        CREATE TABLE IF NOT EXISTS news_stories (
          id INTEGER PRIMARY KEY, title TEXT NOT NULL, anchor_article_id INTEGER NOT NULL REFERENCES articles(id),
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL, merged_into INTEGER REFERENCES news_stories(id),
          manual_title INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS story_members (
          article_id INTEGER NOT NULL REFERENCES articles(id), story_id INTEGER NOT NULL REFERENCES news_stories(id),
          role TEXT NOT NULL DEFAULT 'primary', manual INTEGER NOT NULL DEFAULT 0,
          reason TEXT NOT NULL, confidence REAL NOT NULL, created_at TEXT NOT NULL,
          PRIMARY KEY(article_id,story_id));
        CREATE UNIQUE INDEX IF NOT EXISTS one_primary_story ON story_members(article_id) WHERE role='primary';
        CREATE INDEX IF NOT EXISTS members_story ON story_members(story_id,article_id);
        CREATE TABLE IF NOT EXISTS story_candidates (
          article_id INTEGER NOT NULL, story_id INTEGER NOT NULL, score REAL NOT NULL,
          decision TEXT NOT NULL, reason TEXT NOT NULL, detail TEXT NOT NULL, updated_at TEXT NOT NULL,
          PRIMARY KEY(article_id,story_id));
        CREATE TABLE IF NOT EXISTS news_topics (
          id INTEGER PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL,
          anchor_story_id INTEGER REFERENCES news_stories(id), keywords TEXT NOT NULL,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL, pinned INTEGER NOT NULL DEFAULT 0,
          manual INTEGER NOT NULL DEFAULT 0, merged_into INTEGER REFERENCES news_topics(id),
          vector TEXT, embedding_model TEXT);
        CREATE TABLE IF NOT EXISTS topic_members (
          topic_id INTEGER NOT NULL REFERENCES news_topics(id), story_id INTEGER NOT NULL REFERENCES news_stories(id),
          state TEXT NOT NULL DEFAULT 'included', manual INTEGER NOT NULL DEFAULT 0,
          confidence REAL NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL,
          PRIMARY KEY(topic_id,story_id));
        CREATE TABLE IF NOT EXISTS processing_corrections (
          id INTEGER PRIMARY KEY, kind TEXT NOT NULL, target_id INTEGER NOT NULL,
          before_json TEXT NOT NULL, after_json TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS processing_state (
          phase INTEGER NOT NULL, item_id INTEGER NOT NULL, engine TEXT NOT NULL, config_id INTEGER NOT NULL,
          processed_at TEXT NOT NULL, PRIMARY KEY(phase,item_id,engine,config_id));
        CREATE TABLE IF NOT EXISTS topic_candidates (
          story_id INTEGER NOT NULL, topic_id INTEGER NOT NULL, score REAL NOT NULL,
          decision TEXT NOT NULL, reason TEXT NOT NULL, updated_at TEXT NOT NULL,
          PRIMARY KEY(story_id,topic_id));
        CREATE TABLE IF NOT EXISTS processing_evaluations (
          id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, config_id INTEGER NOT NULL,
          dataset_version TEXT NOT NULL, results TEXT NOT NULL);
        ''')
        db.executescript('''
        CREATE TRIGGER IF NOT EXISTS analysis_input_dirty AFTER UPDATE OF title,content,feed_content,transcript,published_at,body_quality,content_type,content_type_basis ON articles
        WHEN OLD.title IS NOT NEW.title OR OLD.content IS NOT NEW.content OR OLD.feed_content IS NOT NEW.feed_content
          OR OLD.transcript IS NOT NEW.transcript OR OLD.published_at IS NOT NEW.published_at OR OLD.body_quality IS NOT NEW.body_quality
          OR OLD.content_type IS NOT NEW.content_type OR OLD.content_type_basis IS NOT NEW.content_type_basis
        BEGIN
          DELETE FROM processing_state WHERE phase=3 AND item_id=NEW.id;
          UPDATE article_analysis SET status='stale' WHERE article_id=NEW.id;
        END;
        CREATE TRIGGER IF NOT EXISTS analysis_metadata_dirty AFTER UPDATE ON article_analysis BEGIN
          DELETE FROM processing_state WHERE phase=4 AND item_id=NEW.article_id;
          DELETE FROM processing_state WHERE phase=5 AND item_id IN (SELECT story_id FROM story_members WHERE article_id=NEW.article_id);
        END;
        CREATE TRIGGER IF NOT EXISTS story_topic_dirty AFTER UPDATE ON news_stories BEGIN
          DELETE FROM processing_state WHERE phase=5 AND item_id=NEW.id;
        END;
        ''')
        if not db.execute('SELECT 1 FROM processing_configs').fetchone():
            db.execute('INSERT INTO processing_configs(created_at,config) VALUES(?,?)', (now(),json.dumps(DEFAULT_CONFIG)))
        else:
            _,previous=config(db)
            if previous.get('version')!=PIPELINE_VERSION:
                db.execute('INSERT INTO processing_configs(created_at,config) VALUES(?,?)',
                           (now(),json.dumps({**previous,'version':PIPELINE_VERSION,'decision_model':'local-gliner2','corpus_pipeline':'october-events-2'})))

def config(db):
    row = db.execute('SELECT * FROM processing_configs ORDER BY id DESC LIMIT 1').fetchone()
    return row['id'], json.loads(row['config'])

def effective(row):
    data = json.loads(row['data'])
    data.update(json.loads(row['manual']))
    return data

def correction(db, kind, target, before, after, reason):
    db.execute('INSERT INTO processing_corrections(kind,target_id,before_json,after_json,reason,created_at) VALUES(?,?,?,?,?,?)',
               (kind,target,json.dumps(before),json.dumps(after),reason,now()))
