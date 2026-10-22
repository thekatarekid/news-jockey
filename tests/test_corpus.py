import json
import unittest
from datetime import datetime,timezone
from unittest.mock import patch
import test_analysis as fixtures
from newsjockey.corpus import create_corpus_run,prepare_input,control,overview,VERSION
from newsjockey.storage import connect
from newsjockey.windows import month_window,group_condition,date_window
from newsjockey.event_clustering import feature_record,vocabulary,compare,cluster
from newsjockey.extraction_jobs import create_job,claim_manual
from tools.process_month import metadata,save_clusters

class CorpusTests(unittest.TestCase):
 setUp=fixtures.AnalysisTests.setUp
 tearDown=fixtures.AnalysisTests.tearDown
 add=fixtures.AnalysisTests.add
 post=fixtures.AnalysisTests.post
 def test_calendar_boundaries_and_previous_excludes_unknown(self):
  w=month_window('2026-10','Asia/Calcutta')
  self.assertEqual(w['start'],'2026-09-30T18:30:00+00:00');self.assertEqual(w['end'],'2026-10-31T18:30:00+00:00')
  w=month_window('2024-02','UTC');self.assertEqual((datetime.fromisoformat(w['end'])-datetime.fromisoformat(w['start'])).days,29)
  self.assertEqual(month_window('2026-12','UTC')['end'],'2027-01-01T00:00:00+00:00')
  self.assertNotIn('IS NULL',group_condition('older',w)[0])
  for bad in ['all','2026-13','2026-1','oops']:
   with self.assertRaises(ValueError):month_window(bad,'UTC')
 def test_whole_month_snapshot_includes_headlines_without_body_and_both_boundaries(self):
  ids=[self.add(date=d,text='') for d in ['2026-09-30T18:29:59+00:00','2026-09-30T18:30:00+00:00','2026-10-31T18:29:59+00:00','2026-10-31T18:30:00+00:00',None]]
  j=create_corpus_run(self.path,'2026-10');self.assertEqual(j['total'],2)
  again=create_corpus_run(self.path,'2026-10');self.assertEqual(again['id'],j['id']);self.assertTrue(again['existing'])
  with connect(self.path) as db:self.assertEqual({r[0] for r in db.execute('SELECT article_id FROM corpus_items')},set(ids[1:3]))
  control(self.path,j['id'],'pause');self.assertEqual(overview(self.path)['runs'][0]['status'],'paused')
  control(self.path,j['id'],'resume');self.assertEqual(overview(self.path)['runs'][0]['status'],'queued')
 def test_metadata_evidence_and_unknown_event_time(self):
  a={**fixtures.article(), 'id':1,'publisher_id':1};p,h=prepare_input(a)
  data=metadata(p,{'entities':{'organization':[{'text':'Google','confidence':.99},{'text':'Invented','confidence':1}]},'categories':[{'label':'Technology','confidence':.9}]})
  self.assertEqual([e['name'] for e in data['entities']],['Google']);self.assertEqual(data['entities'][0]['evidence']['start'],0)
  self.assertIsNone(data['event_time']['value']);self.assertEqual(data['categories'][0]['name'],'Technology')
  a['last_seen']='new';self.assertEqual(prepare_input(a)[1],h)
  a['title']='Changed event';self.assertNotEqual(prepare_input(a)[1],h)
 def rec(self,i,title,action='unknown'):
  return feature_record({'id':i,'publisher_id':i,'title':title,'published_at':'2026-10-06T10:00:00+00:00'}, {'entities':[{'name':'Google','confidence':.99}],'action':action})
 def test_same_event_paraphrase_and_different_quarters(self):
  a=self.rec(1,'Google unveils new artificial intelligence software')
  b=self.rec(2,'New AI software unveiled by Google')
  self.assertEqual(compare(a,b,.94,.95,vocabulary([a,b]))['decision'],'same_event')
  c=self.rec(3,'Google announces Q1 earnings','earnings');d=self.rec(4,'Google announces Q2 earnings','earnings')
  self.assertEqual(compare(c,d,.99,.99,vocabulary([c,d]))['decision'],'reject')
 def test_clustering_searches_across_batches_and_does_not_chain_topics(self):
  import numpy as np
  records=[self.rec(i,'Google unveils new artificial intelligence software' if i in (0,130) else 'Distinct event '+str(i)) for i in range(131)]
  vectors=np.eye(131,dtype=np.float32);vectors[130]=vectors[0]
  groups,_,_=cluster(records,vectors,vectors)
  self.assertTrue(any(set(g['members'])=={0,130} for g in groups))
  # A-B and B-C similar; A-C fails representative cohesion.
  records=[self.rec(i,'Google announces artificial intelligence product '+str(i)) for i in range(3)]
  v=np.array([[1,0],[.94,.341],[.765,.644]],dtype=np.float32);v/=np.linalg.norm(v,axis=1,keepdims=True)
  with patch('newsjockey.event_clustering.compare',side_effect=lambda a,b,t,l,idf:{'decision':'same_event' if t>.9 else 'review','score':float(t),'reason':'fixture'}):
   groups,_,_=cluster(records,v,v)
  self.assertEqual(sorted(len(g['members']) for g in groups),[1,2])
 def test_save_clusters_and_pagination_preserve_raw_data_and_manual_members(self):
  ids=[self.add(),self.add(),self.add(title='Unrelated news')];j=create_corpus_run(self.path,'2026-10')
  records=[self.rec(i,'Google unveils artificial intelligence software') for i in ids]
  save_clusters(self.path,j['id'],records,[{'anchor':0,'members':[0,1]},{'anchor':2,'members':[2]}],[],{})
  response=self.client.get('/api/stories?size=2').json;self.assertEqual(response['total'],1);self.assertEqual(response['items'][0]['article_count'],2)
  self.assertEqual(self.client.get('/api/stories?size=1').json['total'],1)
  with connect(self.path) as db:
   self.assertEqual(db.execute('SELECT COUNT(*) FROM articles').fetchone()[0],3);self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])
 def test_completed_body_reuse_does_not_launch_fetch(self):
  aid=self.add()
  with connect(self.path) as db:db.execute("UPDATE articles SET extraction_status='complete',body_quality='full' WHERE id=?",(aid,))
  j=create_job(self.path,'http','2026-10');self.assertIsNone(claim_manual(self.path,'http'))
  with connect(self.path) as db:
   row=db.execute('SELECT * FROM extraction_job_items WHERE job_id=?',(j['id'],)).fetchone();self.assertEqual(row['state'],'done');self.assertTrue(json.loads(row['result'])['reused_saved_body'])
 def test_mutation_requires_token(self):
  self.assertEqual(self.client.post('/api/corpus/jobs',json={'month':'2026-10'}).status_code,403)
  self.assertEqual(self.post('/api/corpus/jobs',{'month':'bad'}).status_code,400)
 def test_distinct_puzzles_products_places_and_event_stages(self):
  examples=[('NYT Wordle hints answers October 1 2026','NYT Strands hints answers October 1 2026'),
   ('Best Prime Day iPhone deals','Best Prime Day MacBook deals'),
   ('Google MacBook Pro sale','Google MacBook Air sale'),
   ('Supreme Court allows execution of Christa Pike','Botched execution of Christa Pike'),
   ('Steelers trade Joey Porter Jr','Joey Porter Jr injury update')]
  for x,y in examples:
   a,b=self.rec(1,x),self.rec(2,y)
   self.assertEqual(compare(a,b,.99,.99,vocabulary([a,b]))['decision'],'reject',(x,y))
  a,b=self.rec(1,'Gadsden high school football scores'),self.rec(2,'Tuscaloosa high school football scores')
  a['data']['locations']=[{'name':'Gadsden','type':'location'}];b['data']['locations']=[{'name':'Tuscaloosa','type':'location'}]
  self.assertEqual(compare(a,b,.99,.99,vocabulary([a,b]))['decision'],'reject')
 def test_month_dropdown_uses_local_timezone_at_utc_boundary(self):
  self.add(date='2024-01-31T23:59:00+00:00')
  r=self.client.get('/api/extraction/months').json
  self.assertTrue(any(m['month']=='2024-02' and m['total']==1 for m in r['months']))
 def test_host_lease_exclusivity_is_atomic(self):
  from newsjockey.extraction_jobs import lease_article
  a,b=self.add(),self.add()
  with connect(self.path) as db:
   db.execute("UPDATE articles SET url_host='example.com' WHERE id IN (?,?)",(a,b))
   self.assertIsNotNone(lease_article(db,a));self.assertIsNone(lease_article(db,b))
 def test_distinct_fund_declarations_and_player_profiles(self):
  a,b=self.rec(1,'PIMCO Corporate Income Strategy Fund declares $0.1125 dividend'),self.rec(2,'PIMCO Corporate Income Opportunity Fund declares $0.1188 dividend')
  self.assertEqual(compare(a,b,.99,.99,vocabulary([a,b]))['decision'],'reject')
  a,b=self.rec(1,'Rockets jersey history John Vallely'),self.rec(2,'Rockets jersey history Jim Barrett')
  a['data']['entities'].append({'name':'John Vallely','type':'person'});b['data']['entities'].append({'name':'Jim Barrett','type':'person'})
  self.assertEqual(compare(a,b,.99,.99,vocabulary([a,b]))['decision'],'reject')
 def test_sports_match_angles_but_different_game_ids(self):
  a,b=self.rec(1,'Steelers vs. Browns final score results'),self.rec(2,'Steelers vs Browns game chat')
  self.assertEqual(compare(a,b,.97,.97,vocabulary([a,b]))['decision'],'same_event')
  a['text']='Braves win Game 2 against Phillies';b['text']='Braves Phillies preview of Game 3'
  self.assertEqual(compare(a,b,.99,.99,vocabulary([a,b]))['decision'],'reject')
 def test_administrative_leave_not_confused_with_underlying_injury(self):
  a,b=self.rec(1,'ASU Puts Hockey Coach on Leave as Legal Issues Hover Over Injury'),self.rec(2,'ASU puts hockey coach on paid leave as calls grow for investigation')
  for r in (a,b):r['entities']={'asu'};r['subject']={'asu'}
  self.assertEqual(compare(a,b,.98,.98,vocabulary([a,b]))['decision'],'same_event')
 def test_entity_boundary_differences_and_missed_headline_name(self):
  a,b=self.rec(1,'Brooke Eby, Beloved TikTok Star and ALS Activist, Dead at 37'),self.rec(2,'TikToker Brooke Eby Dies at 37 After ALS Battle')
  a['entities']={'brooke eby'};a['subject']={'brooke eby'}
  b['entities']={'tiktoker brooke eby'};b['subject']={'tiktoker brooke eby'}
  self.assertEqual(compare(a,b,.979,.978,vocabulary([a,b]))['decision'],'same_event')
  b['entities']=set();b['subject']=set()
  self.assertEqual(compare(a,b,.979,.978,vocabulary([a,b]))['decision'],'same_event')
  b=self.rec(2,'Tiktoker Jane Eby Dies After ALS Battle');b['entities']={'jane eby'};b['subject']={'jane eby'}
  self.assertNotEqual(compare(a,b,.979,.978,vocabulary([a,b]))['decision'],'same_event')
  a,b=self.rec(1,'Blazers fire play-by-play broadcaster for racist misogynistic comments made as teenager hours after hiring him'),self.rec(2,'Trail Blazers fire new play-by-play announcer hours after hiring due to racist social media posts')
  for r in (a,b):r['entities']={'braiden bell'};r['subject']=set()
  # IDF is calculated over the month, not only the two candidate headlines.
  month_vocab=vocabulary([a,b]+[{'words':{'unrelated','coverage'}} for _ in range(100)])
  self.assertEqual(compare(a,b,.9772,.9844,month_vocab)['decision'],'same_event')
 def test_whole_worker_processes_more_than_500_records_without_hosted_calls(self):
  import types
  from tools import process_month
  for i in range(505):self.add(title='Google unveils new artificial intelligence software')
  j=create_corpus_run(self.path,'2026-10')
  class FakeModel:
   def create_schema(self):return self
   def entities(self,*a,**k):return self
   def classification(self,*a,**k):return self
   def batch_extract(self,texts,*a,**k):return [{'entities':{'organization':[{'text':'Google','confidence':.99}]},'categories':[{'label':'Technology','confidence':.9}]} for _ in texts]
  modelroot=self.path.parent/'data/models/huggingface/hub/models--fastino--gliner2-base-v1';(modelroot/'refs').mkdir(parents=True);(modelroot/'refs/main').write_text('fixture')
  fake=types.SimpleNamespace(GLiNER2=types.SimpleNamespace(from_pretrained=lambda *a,**k:FakeModel()))
  vector=[1.]+[0.]*511
  with patch.dict('sys.modules',{'torch':types.SimpleNamespace(set_num_threads=lambda n:None),'gliner2':fake}),patch.object(process_month,'ROOT',self.path.parent),patch.object(process_month,'start_embeddings',return_value={'available':True,'digest':'fixture'}),patch.object(process_month,'embeddings',side_effect=lambda texts:[vector for t in texts]),patch('builtins.print'),patch('newsjockey.processing_adapters.subscription_decision',side_effect=AssertionError('No hosted inference permitted')):
   process_month.run(str(self.path),j['id'])
  result=overview(self.path);self.assertEqual(result['runs'][0]['processed'],505);self.assertEqual(result['runs'][0]['status'],'complete')
  self.assertEqual(result['stats']['articles'],505);self.assertEqual(result['stats']['multi_article'],1)
