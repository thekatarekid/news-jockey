import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from app import create_app
from newsjockey.storage import connect, add_source, now
from newsjockey.analysis_store import effective, config
from newsjockey.classification import baseline, input_hash, validate_decision, source_text
from newsjockey.grouping import compare_events,new_story,assign_story,merge_stories,split_story,link_topic,create_topic,topic_detail,story_candidates
from newsjockey.analysis_pipeline import create_run,ProcessingWorker,analyze_article,group_article,organize_story
from newsjockey.processing_adapters import codex_command,normalize,embed,subscription_decision,PAIR_SCHEMA

TEXT='Google unveiled new artificial intelligence software in California on October 6, 2026. The company announced privacy controls for its software. Researchers tested the system in California.'
def article(title='Google unveils new artificial intelligence software',text=TEXT,date='2026-10-06T10:00:00+00:00'):
 return dict(title=title,content=text,feed_content=text,body_quality='full',published_at=date,content_type='reporting',content_type_basis='publisher',transcript='')

class AnalysisTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'test.sqlite'
  self.app=create_app(self.path);self.client=self.app.test_client()
  self.headers={'X-NewsJockey-Token':re.search(r'name="newsjockey-token" content="([^"]+)"',self.client.get('/').text)[1]}
  with connect(self.path) as db:
   self.sid,_=add_source(db,dict(name='Example',website='https://example.com',feed_url='https://example.com/feed',category='Politics/National'))
   self.pid=db.execute('SELECT publisher_id FROM sources WHERE id=?',(self.sid,)).fetchone()[0]
   cid,cfg=config(db);cfg['embedding']='none';db.execute('UPDATE processing_configs SET config=? WHERE id=?',(json.dumps(cfg),cid))
  self.worker=ProcessingWorker(self.path)
 def tearDown(self):self.tmp.cleanup()
 def add(self,**kwargs):
  a=article(**kwargs)
  with connect(self.path) as db:
   aid=db.execute('''INSERT INTO articles(source_id,publisher_id,entry_id,url,title,content,feed_content,body_quality,published_at,content_type,content_type_basis,first_seen,last_seen,content_changed_at,content_kind,fingerprint)
    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(self.sid,self.pid,str(db.execute('SELECT COUNT(*) FROM articles').fetchone()[0]),'https://example.com/'+str(db.execute('SELECT COUNT(*) FROM articles').fetchone()[0]),a['title'],a['content'],a['feed_content'],'full',a['published_at'],'reporting','publisher',now(),now(),now(),'Full text','test')).lastrowid
  return aid
 def run_phase(self,phase,**kwargs):
  job=create_run(self.path,phase,'all','local',100,**kwargs)
  for _ in range(120):
   if not self.worker.step():break
  with connect(self.path) as db:
   errors=[r[0] for r in db.execute("SELECT error FROM processing_run_items WHERE run_id=? AND state='failed'",(job['id'],))]
  self.assertEqual(errors,[]);return job
 def post(self,url,body):return self.client.post(url,json=body,headers=self.headers)

 def test_separate_categories_and_unknown_time(self):
  a=article(text=TEXT.replace(' on October 6, 2026',''));data=baseline(a)
  self.assertIn('Technology',[c['name'] for c in data['categories']]);self.assertIsNone(data['event_time']['value'])
  self.assertEqual(data['content_type'],'reporting')
 def test_source_biography_guard(self):
  a=article(text='This article was written by Jane Reporter, a professional author.');a['feed_content']=''
  text,basis,suspect=source_text(a);self.assertTrue(suspect);self.assertNotIn('professional author',text);self.assertTrue(baseline(a)['warnings'])
 def test_input_hash_ignores_operational_changes_but_tracks_text(self):
  a=article();original=input_hash(a);a.update(extraction_status='failed',tags='different',last_seen=now());self.assertEqual(input_hash(a),original)
  a['content']+=' New facts.';self.assertNotEqual(input_hash(a),original)
 def test_model_unsupported_quotes_discarded(self):
  data=validate_decision({'categories':[{'name':'Technology','quote':'invented text','confidence':1}],
   'entities':[{'name':'Fake Company','quote':'Fake Company','confidence':1}], 'event_time':{'value':'2026-10-06','precision':'date','quote':'made up'},'content_type':'reporting'},article())
  self.assertEqual(data['categories'],[]);self.assertEqual(data['entities'],[]);self.assertIsNone(data['event_time']['value']);self.assertTrue(data['warnings'])
 def test_different_events_never_merge_on_high_semantics(self):
  left=article('Google announces Q1 earnings');right=article('Google announces Q2 earnings')
  self.assertEqual(compare_events(baseline(left),baseline(right),left,right,.99)['decision'],'reject')
  left=article('Supreme Court ruling on merger');right=article('Supreme Court appeal on merger')
  self.assertEqual(compare_events(baseline(left),baseline(right),left,right,.99)['decision'],'reject')
  left=article();right=article(text=TEXT.replace('October 6','October 7'))
  self.assertEqual(compare_events(baseline(left),baseline(right),left,right,.99)['decision'],'reject')
 def test_delayed_coverage_needs_same_event_date(self):
  left=article();right=article(date='2026-10-20T12:00:00+00:00')
  self.assertEqual(compare_events(baseline(left),baseline(right),left,right,.99)['decision'],'same_event')
  left['content']=TEXT.replace(' on October 6, 2026','');right['content']=left['content']
  self.assertEqual(compare_events(baseline(left),baseline(right),left,right,.99)['decision'],'review')
 def test_additive_storage_and_pipeline(self):
  a=self.add();b=self.add();self.run_phase(3);self.run_phase(4);self.run_phase(5)
  with connect(self.path) as db:
   self.assertEqual(db.execute('SELECT COUNT(*) FROM articles').fetchone()[0],2)
   self.assertEqual(db.execute('SELECT COUNT(DISTINCT story_id) FROM story_members').fetchone()[0],1)
   self.assertGreater(db.execute('SELECT COUNT(*) FROM news_topics').fetchone()[0],0)
   self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])
  self.assertEqual(self.client.get('/api/analysis/articles').json['total'],2)
 def test_completed_batches_advance_and_config_can_reprocess(self):
  self.add();self.run_phase(3)
  with self.assertRaisesRegex(ValueError,'No eligible'):create_run(self.path,3,'all','local',100)
  self.add(title='Google launches second artificial intelligence product');j=self.run_phase(3);self.assertEqual(j['total'],1)
  self.run_phase(4)
  with self.assertRaisesRegex(ValueError,'No eligible'):create_run(self.path,4,'all','local',100)
 def test_manual_metadata_survives_reprocessing_and_is_validated(self):
  aid=self.add();self.run_phase(3)
  r=self.client.patch(f'/api/analysis/articles/{aid}',json={'categories':['Technology','Business & Economy'],'content_type':'analysis','event_time':None},headers=self.headers);self.assertEqual(r.status_code,200)
  self.run_phase(3,article_ids=[aid]);r=self.client.get(f'/api/analysis/articles/{aid}').json
  self.assertEqual(r['analysis']['content_type'],'analysis');self.assertEqual(len(r['analysis']['categories']),2);self.assertIsNone(r['analysis']['event_time']['value'])
  bad=self.client.patch(f'/api/analysis/articles/{aid}',json={'entities':[{'name':'Fake','evidence':{'quote':'invented'}}]},headers=self.headers);self.assertEqual(bad.status_code,400)
 def test_manual_story_split_merge_and_corrections_persist(self):
  a=self.add();b=self.add();self.run_phase(3);self.run_phase(4)
  with connect(self.path) as db:
   sid=db.execute('SELECT story_id FROM story_members WHERE article_id=?',(a,)).fetchone()[0]
   split=split_story(db,sid,[b]);self.assertNotEqual(split,sid)
   cfg=config(db)[1]
  result=group_article(self.path,b,cfg,'test','local');self.assertTrue(result['manual_preserved'])
  with connect(self.path) as db:
   merge_stories(db,sid,[split]);self.assertEqual(db.execute('SELECT COUNT(*) FROM story_members WHERE story_id=?',(sid,)).fetchone()[0],2)
   self.assertEqual(db.execute('SELECT merged_into FROM news_stories WHERE id=?',(split,)).fetchone()[0],sid)
 def test_no_transitive_chain_anchor(self):
  a=self.add();b=self.add();c=self.add(title='Google launches an unrelated chip at a conference')
  self.run_phase(3);self.run_phase(4)
  with connect(self.path) as db:
   sid=db.execute('SELECT story_id FROM story_members WHERE article_id=?',(a,)).fetchone()[0]
   self.assertEqual(db.execute('SELECT anchor_article_id FROM news_stories WHERE id=?',(sid,)).fetchone()[0],a)
   self.assertNotEqual(db.execute('SELECT story_id FROM story_members WHERE article_id=?',(c,)).fetchone()[0],sid)
 def test_manual_topic_exclusion_and_unknown_timeline(self):
  aid=self.add(text=TEXT.replace(' on October 6, 2026',''));self.run_phase(3);self.run_phase(4)
  with connect(self.path) as db:
   sid=db.execute('SELECT story_id FROM story_members').fetchone()[0];tid=create_topic(db,'Google AI software','Google artificial intelligence software releases and privacy controls')
   link_topic(db,tid,sid,True);result=topic_detail(db,tid);self.assertEqual(result['timeline'],[]);self.assertEqual(len(result['undated_stories']),1)
   link_topic(db,tid,sid,True,'excluded');link_topic(db,tid,sid);self.assertEqual(topic_detail(db,tid)['undated_stories'],[])
 def test_job_duplicate_pause_resume_restart_and_failure(self):
  self.add();j=create_run(self.path,3,'all','local',100);self.assertTrue(create_run(self.path,3,'all','local',100)['existing'])
  with connect(self.path) as db:
   db.execute("UPDATE processing_runs SET status='paused',owner_pid=99999999 WHERE id=?",(j['id'],));db.execute("UPDATE processing_run_items SET state='running' WHERE run_id=?",(j['id'],))
  self.worker.recover()
  with connect(self.path) as db:
   self.assertEqual(db.execute('SELECT status FROM processing_runs').fetchone()[0],'paused');self.assertEqual(db.execute('SELECT state FROM processing_run_items').fetchone()[0],'pending')
  r=self.client.patch('/api/analysis/jobs/'+j['id'],json={'action':'resume'},headers=self.headers);self.assertEqual(r.status_code,200);self.worker.step()
 def test_subscription_failure_pauses_no_fallback(self):
  self.add();j=create_run(self.path,3,'all','luna',1)
  with patch('newsjockey.analysis_pipeline.subscription_decision',side_effect=RuntimeError('quota unavailable')):self.worker.step()
  with connect(self.path) as db:self.assertEqual(db.execute('SELECT status FROM processing_runs').fetchone()[0],'paused')
 def test_disallowed_models_and_mutation_protection(self):
  for engine in ('deepseek','sol','astra','openrouter'):
   r=self.post('/api/analysis/jobs',{'phase':3,'engine':engine});self.assertEqual(r.status_code,400)
  self.assertEqual(self.client.post('/api/topics',json={'title':'x'}).status_code,403)
  with self.assertRaises(ValueError):codex_command('/codex','/tmp','schema','gpt-6-sol')
  cmd=' '.join(codex_command('/codex','/tmp','schema','gpt-6-luna'));self.assertIn('forced_login_method="chatgpt"',cmd);self.assertIn('features.shell_tool=false',cmd);self.assertIn('--ignore-user-config',cmd)
 def test_normalization_and_chunked_embedding_no_truncation(self):
  self.assertAlmostEqual(sum(x*x for x in normalize([3,4])),1)
  with self.assertRaises(ValueError):normalize([float('nan')])
  class Response:
   def raise_for_status(self):pass
   def json(self):return {'embeddings':[[1]*768]*3,'prompt_eval_count':20}
  with patch('newsjockey.processing_adapters.embedding_status',return_value={'available':True,'digest':'abc'}),patch('newsjockey.processing_adapters.requests.post',return_value=Response()) as post:
   vector,m=embed('x'*7000);self.assertEqual(len(vector),512);self.assertEqual(m['detail']['chunks'],3);self.assertFalse(post.call_args.kwargs['json']['truncate'])
 def test_same_second_body_change_invalidates_cache(self):
  aid=self.add();self.run_phase(3)
  with connect(self.path) as db:
   db.execute("UPDATE articles SET content=content||' Newly confirmed details.' WHERE id=?",(aid,))
   self.assertEqual(db.execute('SELECT status FROM article_analysis').fetchone()[0],'stale')
  self.assertEqual(self.run_phase(3)['total'],1)
 def test_topic_controls_preserve_manual_exclusion_and_aliases(self):
  a=self.add();b=self.add(title='Google investigates a different software issue');self.run_phase(3);self.run_phase(4)
  with connect(self.path) as db:
   stories=[r[0] for r in db.execute('SELECT id FROM news_stories')];tid=create_topic(db,'Google software privacy','Privacy and retention in Google software',manual=True)
   for sid in stories:link_topic(db,tid,sid,True)
  result=self.post(f'/api/topics/{tid}/actions',{'action':'split','story_ids':[stories[0]],'title':'Google launch privacy','Privacy controls in a new Google software launch':'unused','description':'Privacy controls in a new Google software launch'})
  self.assertEqual(result.status_code,200);new=result.json['id']
  self.assertEqual(self.post(f'/api/topics/{tid}/actions',{'action':'pin','pinned':True}).status_code,200)
  with connect(self.path) as db:
   self.assertEqual(db.execute('SELECT state FROM topic_members WHERE topic_id=? AND story_id=?',(tid,stories[0])).fetchone()[0],'excluded')
  self.assertEqual(self.post(f'/api/topics/{tid}/actions',{'action':'merge','topic_ids':[new]}).status_code,200)
  self.assertEqual(self.client.get(f'/api/topics/{new}').json['merged_into'],tid)
 def test_reference_does_not_demote_primary_and_empty_anchors_not_candidates(self):
  a=self.add();b=self.add();self.run_phase(3);self.run_phase(4)
  with connect(self.path) as db:
   sid=db.execute('SELECT story_id FROM story_members WHERE article_id=?',(a,)).fetchone()[0]
   assign_story(db,a,sid,False,role='reference');self.assertEqual(db.execute('SELECT role FROM story_members WHERE article_id=?',(a,)).fetchone()[0],'primary')
   empty=new_story(db,a);article_row=db.execute('SELECT * FROM articles WHERE id=?',(b,)).fetchone();analysis=db.execute('SELECT * FROM article_analysis WHERE article_id=?',(b,)).fetchone()
   self.assertNotIn(empty,[c['story_id'] for c in story_candidates(db,article_row,analysis)])
 def test_roundup_keeps_separate_primary(self):
  a=self.add();b=self.add(title='Daily briefing roundup: Google unveils new artificial intelligence software');self.run_phase(3);self.run_phase(4)
  with connect(self.path) as db:
   self.assertEqual(db.execute("SELECT COUNT(DISTINCT story_id) FROM story_members WHERE role='primary'").fetchone()[0],2)
 def test_typography_quote_keeps_original_offsets_and_rejects_paraphrases(self):
  from newsjockey.classification import span
  text='Google said: “This is new.”\nThe company’s software works.'
  evidence=span(text,'Google said: "This is new." The company\'s software works.')
  self.assertIsNotNone(evidence);self.assertEqual(text[evidence['start']:evidence['end']],evidence['quote'])
  self.assertIsNone(span(text,'Google said this software never fails.'))
  source='The Type 01 , a new car, has arrived.'
  self.assertEqual(span(source,'The Type 01, a new car, has arrived.')['quote'],source)
  self.assertIsNone(span(source,'The Type 02, a new car, has arrived.'))
 def test_dropped_optional_metadata_does_not_block_full_text_event_verification(self):
  a=article();left=baseline(a);right=baseline(a);left['warnings']=['Dropped entity/location with unmatched evidence.']
  self.assertEqual(compare_events(left,right,a,a,.99)['decision'],'same_event')
  left['input_limited']=True;self.assertEqual(compare_events(left,right,a,a,.99)['decision'],'review')
 def test_subscription_tool_output_rejected(self):
  from types import SimpleNamespace
  output='\n'.join([json.dumps({'item':{'type':'command_execution'}}),json.dumps({'item':{'type':'agent_message','text':'{}'}})])
  with patch('newsjockey.processing_adapters.subscription_status',return_value={'available':True}),patch('newsjockey.processing_adapters.codex_binary',return_value='/codex'),patch('newsjockey.processing_adapters.subprocess.run',return_value=SimpleNamespace(returncode=0,stdout=output,stderr='')):
   with self.assertRaisesRegex(RuntimeError,'tool activity'):subscription_decision('luna','test',{},PAIR_SCHEMA)

 def test_background_years_and_location_aliases_are_not_event_conflicts(self):
  a=article('Jaguar unveils Type 01',text='Jaguar rebranded in 2024. The new car was unveiled in New York City.')
  b=article('Jaguar unveils Type 01',text='Jaguar unveiled a new car in New York. Deliveries begin in 2027.')
  left=baseline(a);right=baseline(b)
  left['entities']=right['entities']=[{'name':'Jaguar','confidence':1}]
  left['locations']=[{'name':'New York City'}];right['locations']=[{'name':'New York'}]
  self.assertEqual(compare_events(left,right,a,b,.99)['decision'],'same_event')
  a['title']='Jaguar unveils 2026 model';b['title']='Jaguar unveils 2027 model'
  self.assertEqual(compare_events(left,right,a,b,.99)['decision'],'reject')

 def test_later_chunk_dates_are_retained_but_conflicts_stay_unknown(self):
  from copy import deepcopy
  from newsjockey.analysis_pipeline import combine_decisions
  unknown=baseline(article(text='No event date in this opening chunk.'))
  dated=baseline(article());other=baseline(article(text=TEXT.replace('October 6','October 7')))
  self.assertEqual(combine_decisions([deepcopy(unknown),deepcopy(dated)])['event_time']['value'],'2026-10-06')
  combined=combine_decisions([deepcopy(unknown),deepcopy(dated),deepcopy(other),deepcopy(dated)])
  self.assertIsNone(combined['event_time']['value']);self.assertTrue(any('Multiple event dates' in w for w in combined['warnings']))

 def test_reassigned_empty_story_becomes_alias_and_retains_topic_link(self):
  a=self.add();b=self.add(title='A different event');self.run_phase(3)
  with connect(self.path) as db:
   first=new_story(db,a);second=new_story(db,b);assign_story(db,a,first);assign_story(db,b,second)
   topic=create_topic(db,'Google software privacy','Privacy controls in Google software');link_topic(db,topic,first)
   assign_story(db,a,second)
   self.assertEqual(db.execute('SELECT merged_into FROM news_stories WHERE id=?',(first,)).fetchone()[0],second)
   self.assertEqual(db.execute('SELECT state FROM topic_members WHERE topic_id=? AND story_id=?',(topic,second)).fetchone()[0],'included')
   detail=topic_detail(db,topic);self.assertEqual(len(detail['timeline'])+len(detail['undated_stories']),1)

 def test_explicit_story_recheck_and_unchanged_body_reuses_analysis(self):
  a=self.add();b=self.add();self.run_phase(3);self.run_phase(4)
  with connect(self.path) as db:db.execute('UPDATE articles SET feed_content=? WHERE id=?',('A changed feed excerpt',a))
  job=create_run(self.path,4,'all','local',10,[a])
  self.assertEqual(job['total'],1)
  with connect(self.path) as db:self.assertEqual(db.execute('SELECT status FROM article_analysis WHERE article_id=?',(a,)).fetchone()[0],'ready')
  self.worker.step()

 def test_pair_verification_retains_original_typography_evidence(self):
  a=self.add(text='Google said: “The software is available.”');b=self.add(text='Google said: “The software is available.”');self.run_phase(3)
  with connect(self.path) as db:
   sid=new_story(db,a);assign_story(db,a,sid);cfg=config(db)[1]
  candidate={'story_id':sid,'anchor_article_id':a,'score':.8,'decision':'review','hard_conflicts':[],'review_reasons':[],'reason':'Needs verification.'}
  raw={'same_event':True,'confidence':.99,'reason':'Same software release.','left_quote':'Google said: "The software is available."','right_quote':'Google said: "The software is available."'}
  metrics={'provider':'mock','model':'mock','prompt_version':'test','elapsed_seconds':0}
  with patch('newsjockey.analysis_pipeline.story_candidates',return_value=[candidate]),patch('newsjockey.analysis_pipeline.subscription_decision',return_value=(raw,metrics)):
   result=group_article(self.path,b,cfg,'test','luna')
  self.assertTrue(result['matched']);self.assertIn('“',candidate['model_evidence']['left']['quote'])

if __name__=='__main__':unittest.main()
