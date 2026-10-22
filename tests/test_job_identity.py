import json
import threading
import unittest
from unittest.mock import patch
from newsjockey.storage import connect,now
from newsjockey.windows import date_window,month_window
from newsjockey.extraction_jobs import (create_job,claim_manual,finish_item,control_job,
 initialize_jobs,record_outcome,release_article,lease_article,job_data)
import test_extraction_updates as fixtures

class JobIdentityTests(unittest.TestCase):
 setUp=fixtures.ExtractionUpdatesTests.setUp
 tearDown=fixtures.ExtractionUpdatesTests.tearDown
 collect=fixtures.ExtractionUpdatesTests.collect
 add_item=fixtures.ExtractionUpdatesTests.add_item
 def window(self):
  with connect(self.path) as db:return date_window(db)
 def finish(self,job,aid,status='partial'):
  with connect(self.path) as db:
   fp=db.execute('select fingerprint from articles where id=?',(aid,)).fetchone()[0]
   db.execute('update articles set extraction_status=? where id=?',(status,aid))
   record_outcome(db,aid,job['transport'],fp,{'id':aid,'status':status})
 def test_aliases_append_new_items_preserve_pause_and_same_id(self):
  w=self.window();self.add_item(1,w['start'])
  j=create_job(self.path,'http','recent');control_job(self.path,j['id'],'pause')
  self.add_item(2,w['start']);again=create_job(self.path,'http',w['month'])
  self.assertEqual(again['id'],j['id']);self.assertEqual(again['added'],1)
  self.assertEqual(again['total'],2);self.assertEqual(again['status'],'paused')
  self.assertEqual(create_job(self.path,'http','current')['added'],0)
 def test_completed_backfill_does_not_repeat_failed_or_partial_work(self):
  a=self.add_item(1,'2020-01-01T00:00:00+00:00');j=create_job(self.path,'http','older')
  self.finish(j,a,'paywalled');again=create_job(self.path,'http','older')
  self.assertEqual(again['id'],j['id']);self.assertEqual(again['status'],'completed');self.assertIsNone(claim_manual(self.path,'http'))
  selected=create_job(self.path,'http','2020-01')
  self.assertEqual(selected['status'],'completed');self.assertEqual(selected['reused_results'],1)
  b=self.add_item(2,'2020-01-02T00:00:00+00:00');extended=create_job(self.path,'http','older')
  self.assertEqual(extended['id'],j['id']);self.assertEqual(extended['added'],1);self.assertEqual(extended['remaining'],1)
  self.assertEqual(claim_manual(self.path,'http')['id'],b)
 def test_overlapping_queues_share_auto_outcome_but_transport_separate(self):
  a=self.add_item(1,'2020-01-01T00:00:00+00:00')
  j=create_job(self.path,'http','older');month=create_job(self.path,'http','2020-01');chrome=create_job(self.path,'browser','2020-01')
  self.finish(j,a,'partial')
  with connect(self.path) as db:
   self.assertEqual(job_data(db,month['id'])['status'],'completed')
   self.assertEqual(job_data(db,chrome['id'])['remaining'],1)
  self.assertIsNone(claim_manual(self.path,'http'));self.assertIsNotNone(claim_manual(self.path,'browser'))
 def test_completed_month_is_reused_and_rollover_creates_distinct_job(self):
  oct=month_window('2026-10','UTC');nov=month_window('2026-11','UTC')
  with patch('newsjockey.extraction_jobs.date_window',return_value=oct):j=create_job(self.path,'http','recent')
  with patch('newsjockey.extraction_jobs.date_window',return_value=nov):
   next_job=create_job(self.path,'http','recent');old=create_job(self.path,'http','2026-10')
  self.assertNotEqual(next_job['id'],j['id']);self.assertEqual(old['id'],j['id'])
 def test_concurrent_alias_requests_create_one_job(self):
  w=self.window();self.add_item(1,w['start']);results=[];errors=[];barrier=threading.Barrier(2)
  def make(scope):
   try:barrier.wait();results.append(create_job(self.path,'http',scope))
   except Exception as exc:errors.append(exc)
  threads=[threading.Thread(target=make,args=(s,)) for s in ('recent',w['month'])]
  for t in threads:t.start()
  for t in threads:t.join()
  self.assertFalse(errors);self.assertEqual(len({r['id'] for r in results}),1)
 def test_changed_input_does_not_reuse_old_result_and_explicit_retry_works(self):
  a=self.add_item(1,'2020-01-01T00:00:00+00:00');j=create_job(self.path,'http','older');self.finish(j,a,'paywalled')
  retry=create_job(self.path,'http','single',[a]);claim=claim_manual(self.path,'http')
  self.assertEqual(claim['job_id'],retry['id']);self.assertTrue(claim['force'])
  release_article(self.path,a,claim['token']);control_job(self.path,retry['id'],'cancel')
  with connect(self.path) as db:db.execute("update articles set fingerprint='changed',extraction_status='pending' where id=?",(a,))
  changed=create_job(self.path,'http','2020-01');self.assertEqual(changed['remaining'],1)
  self.assertIsNotNone(claim_manual(self.path,'http'))
 def test_automatic_stale_candidate_cannot_refetch_completed_article(self):
  a=self.add_item(1,self.window()['start'])
  with connect(self.path) as db:
   db.execute("update articles set extraction_status='complete' where id=?",(a,))
   self.assertIsNone(lease_article(db,a,automatic=True))
   db.execute("update articles set extraction_status='blocked',next_extraction='2999-01-01' where id=?",(a,))
   self.assertIsNone(lease_article(db,a,automatic=True))
 def test_reused_result_count_does_not_double_count_saved_body(self):
  a=self.add_item(1,'2020-01-01T00:00:00+00:00');j=create_job(self.path,'http','older')
  with connect(self.path) as db:
   db.execute("UPDATE extraction_job_items SET state='done',result=? WHERE job_id=? AND article_id=?",(json.dumps({'status':'complete','reused_attempt':True,'reused_saved_body':True}),j['id'],a))
   result=job_data(db,j['id']);self.assertEqual(result['reused_results'],1)
 def test_input_changed_during_request_stays_pending(self):
  a=self.add_item(1,'2020-01-01T00:00:00+00:00');j=create_job(self.path,'http','older');claim=claim_manual(self.path,'http')
  with connect(self.path) as db:db.execute("update articles set fingerprint='newer' where id=?",(a,))
  finish_item(self.path,claim,{'status':'pending'});release_article(self.path,a,claim['token'])
  with connect(self.path) as db:
   self.assertEqual(job_data(db,j['id'])['remaining'],1)
   self.assertEqual(db.execute('select count(*) from extraction_results').fetchone()[0],0)
 def test_cancelled_inflight_request_records_result_without_reopening_job(self):
  a=self.add_item(1,'2020-01-01T00:00:00+00:00');j=create_job(self.path,'http','older');claim=claim_manual(self.path,'http')
  control_job(self.path,j['id'],'cancel');finish_item(self.path,claim,{'status':'partial'})
  release_article(self.path,a,claim['token'])
  with connect(self.path) as db:
   result=job_data(db,j['id']);self.assertEqual(result['status'],'cancelled');self.assertEqual(result['in_flight'],0);self.assertEqual(result['counts']['done'],1)
 def test_legacy_duplicate_reconciliation_preserves_union_and_results(self):
  w=self.window();a=self.add_item(1,w['start']);master=create_job(self.path,'http',w['month']);b=self.add_item(2,w['start'])
  with connect(self.path) as db:
   db.execute("DELETE FROM settings WHERE key='extraction_jobs_v2'")
   db.execute("UPDATE extraction_jobs SET selection_key=NULL WHERE id=?",(master['id'],))
   db.execute("INSERT INTO extraction_jobs(id,transport,scope,status,created_at,updated_at,timezone,window_start,window_end,total) VALUES('legacy','http','recent','running',?,?,?,?,?,2)",(now()+'z',now(),w['timezone'],w['start'],w['end']))
   for aid in (a,b):
    db.execute("INSERT INTO extraction_job_items(job_id,article_id,state,result,finished_at) VALUES('legacy',?,'done',?,?)",(aid,json.dumps({'status':'partial'}),now()))
    db.execute("UPDATE articles SET extraction_status='partial' WHERE id=?",(aid,))
  initialize_jobs(self.path)
  with connect(self.path) as db:
   merged=dict(db.execute("SELECT * FROM extraction_jobs WHERE id='legacy'").fetchone());self.assertEqual(merged['status'],'merged');self.assertEqual(merged['merged_into'],master['id'])
   result=job_data(db,master['id']);self.assertEqual(result['total'],2);self.assertEqual(result['status'],'completed');self.assertEqual(result['counts']['done'],2)
   self.assertEqual(db.execute('SELECT COUNT(*) FROM articles').fetchone()[0],2)
   self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])
  self.assertEqual(len(self.client.get('/api/extraction/jobs').json['jobs']),1)
  initialize_jobs(self.path) # restart is idempotent
