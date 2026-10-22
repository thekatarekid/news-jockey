import json
import re
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from app import create_app
from newsjockey.storage import connect, initialize, add_source, now
from newsjockey.collector import collect_source, entry_record
from newsjockey.extraction import extract_article, parse_article, extraction_candidates
from newsjockey.discovery import expand_publisher, sitemap_items
from newsjockey.metadata import label_type
from test_collector import RSS

PARA=' '.join(['A public agency announced new funding for the city transportation project.']*8)
HTML=('''<html lang="en"><head><link rel="canonical" href="https://example.com/news?id=1">
<meta property="article:section" content="Analysis"><script type="application/ld+json">'''+json.dumps({
 '@type':'NewsArticle','headline':'Project funding announced','author':{'@type':'Person','name':'Jane Reporter'},
 'datePublished':'2026-10-01T15:00:00Z','dateModified':'2026-10-02T15:00:00Z','image':{
 'url':'https://example.com/hero.jpg','creditText':'Photo Agency','caption':'City project'}})+'''
</script></head><body><article><h1>Project funding announced</h1>'''+''.join('<p>'+PARA+'</p>' for _ in range(4))+'''
<figure><img src="/photo.jpg" alt="Construction"><figcaption>Project site <span class="credit">Jane Photographer</span></figcaption></figure>
<video poster="/poster.jpg"><source src="/report.mp4" type="video/mp4"><track src="/captions.vtt" srclang="en"></video>
</article></body></html>''').encode()

class ProcessingTests(unittest.TestCase):
 def setUp(self):
  self.caption_mock=patch('newsjockey.transcripts.fetch',side_effect=ValueError('No caption fixture supplied.'))
  self.caption_mock.start();self.addCleanup(self.caption_mock.stop)
  self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'test.sqlite'
  self.app=create_app(self.path);self.client=self.app.test_client()
  self.headers={'X-NewsJockey-Token':re.search(r'name="newsjockey-token" content="([^"]+)"',self.client.get('/').text)[1]}
  with connect(self.path) as db:
   self.sid,_=add_source(db,dict(name='Example',website='https://example.com',feed_url='https://example.com/feed',category='Politics/National'))
   self.pid=db.execute('SELECT publisher_id FROM sources WHERE id=?',(self.sid,)).fetchone()[0]
 def tearDown(self):self.tmp.cleanup()
 def collect(self,sid=None,body=RSS):
  with patch('newsjockey.collector.fetch',return_value=(200,{},body,'https://example.com/feed')):
   return collect_source(self.path,sid or self.sid)
 def item(self):return self.client.get('/api/articles').json['articles'][0]
 def extract(self,aid=None,body=HTML):
  aid=aid or self.item()['id']
  with patch('newsjockey.extraction.throttle'),patch('newsjockey.extraction.fetch',return_value=(200,{'Content-Type':'text/html'},body,'https://example.com/news?id=1')):
   return extract_article(self.path,aid)

 def test_overlap_channels_deduplicate_but_independent_publishers_do_not(self):
  self.collect()
  with connect(self.path) as db:
   second,_=add_source(db,dict(name='Example Business',website='https://example.com/business',feed_url='https://example.com/business/feed',category='Business'))
   independent,_=add_source(db,dict(name='Another publisher',website='https://another.example',feed_url='https://another.example/feed',category='Business'))
  self.assertEqual(self.collect(second)['new'],0)
  self.assertEqual(len(self.client.get('/api/articles/'+str(self.item()['id'])).json['channels']),2)
  self.assertEqual(self.collect(independent)['new'],1)
  self.assertEqual(self.client.get('/api/articles').json['total'],2)

 def test_alternate_channel_fingerprints_do_not_cause_repeated_updates(self):
  self.collect()
  with connect(self.path) as db:
   second,_=add_source(db,dict(name='Other section',website='https://example.com',feed_url='https://example.com/other/feed',category='Business'))
  other=RSS.replace(b'First report',b'A different feed excerpt')
  self.collect(second,other);self.collect()
  self.assertEqual(self.collect(second,other)['updated'],0)
  self.assertEqual(self.collect()['updated'],0)

 def test_content_type_is_separate_from_source_category(self):
  self.collect(body=RSS.replace(b'<pubDate>',b'<category>Opinion</category><pubDate>'))
  a=self.item();self.assertEqual(a['content_type'],'opinion');self.assertEqual(a['source_category'],'Politics/National')
  self.assertEqual(self.client.get('/api/articles?content_type=opinion').json['total'],1)
  self.assertEqual(self.client.get('/api/articles?content_type=reporting').json['total'],0)

 def test_all_editorial_labels_and_unknown(self):
  for label,expected in [('Editorial','editorial'),('Reporting','reporting'),('Opinion','opinion'),('Analysis','analysis'),('Reviews','review')]:
   self.assertEqual(label_type([label])[0],expected)
  self.assertEqual(label_type(['Sports'])[0],'unknown')
  self.assertEqual(label_type(['News','Opinion'])[0],'opinion')
  self.assertEqual(label_type(['News'],'https://example.com/reviews/test')[0],'review')

 def test_video_enclosure_without_article_link_is_retained(self):
  import feedparser
  feed=RSS.replace(b'<link>https://example.com/news?utm_source=rss&amp;id=1</link>',b'<enclosure url="https://example.com/report.mp4" type="video/mp4" length="20"/>')
  a=entry_record(feedparser.parse(feed).entries[0],'https://example.com/feed')
  self.assertIn('video',json.loads(a['media_formats']))
  self.assertEqual(a['url'],'https://example.com/report.mp4')

 def test_full_text_images_metadata_and_versions(self):
  self.collect();aid=self.item()['id'];self.assertEqual(self.extract()['status'],'complete')
  a=self.client.get(f'/api/articles/{aid}').json
  self.assertEqual(a['content_kind'],'Full text');self.assertGreater(len(a['content']),500)
  self.assertEqual(a['feed_content'],'First report');self.assertEqual(a['content_type'],'analysis')
  self.assertEqual(a['author'],'Jane Reporter');self.assertEqual(a['published_at'],'2026-10-01T15:00:00+00:00')
  self.assertEqual(a['transcript_status'],'referenced');self.assertIn('video',a['media_formats'])
  self.assertGreaterEqual(len(a['images_json']),3);self.assertTrue(all(i['source_url'] for i in a['images_json']))
  self.assertTrue(any(i['credit']=='Photo Agency' for i in a['images_json']))
  self.assertEqual(len(a['versions']),2)
  self.extract();self.assertEqual(len(self.client.get(f'/api/articles/{aid}').json['versions']),2)

 def test_changed_page_preserves_previous_version(self):
  self.collect();self.extract();aid=self.item()['id']
  self.extract(body=HTML.replace(b'new funding',b'additional funding'))
  a=self.client.get(f'/api/articles/{aid}').json
  self.assertEqual(len(a['versions']),3)
  old=self.client.get(f"/api/articles/{aid}/versions/{a['versions'][1]['id']}").json
  self.assertIn('new funding',old['snapshot']['content'])
  self.assertIn('additional funding',a['content'])

 def test_feed_refresh_does_not_replace_full_text(self):
  self.collect();self.extract();aid=self.item()['id']
  self.collect(body=RSS.replace(b'First report',b'Updated excerpt'))
  a=self.client.get(f'/api/articles/{aid}').json
  self.assertEqual(a['content_kind'],'Full text');self.assertIn('funding',a['content'])
  self.assertEqual(a['feed_content'],'Updated excerpt');self.assertEqual(a['extraction_status'],'pending')

 def test_manual_label_survives_extraction_and_feed_update(self):
  self.collect();aid=self.item()['id']
  self.assertEqual(self.client.patch(f'/api/articles/{aid}',json={'content_type':'editorial'},headers=self.headers).status_code,200)
  self.extract();self.collect(body=RSS.replace(b'First report',b'Updated excerpt'))
  self.assertEqual(self.client.get(f'/api/articles/{aid}').json['content_type'],'editorial')

 def test_paywall_is_not_complete(self):
  a=parse_article(HTML.replace(b'<article>',b'<article><div class="paywall">Subscribe</div>'),'https://example.com/news')
  self.assertEqual(a['extraction_status'],'paywalled')
  self.assertTrue(a['images_json'])
  self.assertEqual(a['content'],'')

 def test_open_hidden_or_empty_paywall_hooks_do_not_restrict_public_article(self):
  for marker in [b'<div class="paywall-open">'+PARA.encode()+b'</div>',
                 b'<div class="paywall-position-hidePaywallLabel"></div>',
                 b'<div id="piano-paywall"></div>',
                 b'<div hidden class="paywall">Subscribe to continue</div>']:
   with self.subTest(marker=marker[:60]):
    a=parse_article(HTML.replace(b'<article>',b'<article>'+marker),'https://example.com/news')
    self.assertEqual(a['extraction_status'],'complete')

 def test_sidebar_article_does_not_replace_main_body_or_images(self):
  body=HTML.replace(b'<body><article>',b'<body><aside><article><h2>Related story</h2><img src="/unrelated.jpg"></article></aside><main><h1>Project funding announced</h1>')
  body=body.replace(b'</article></body>',b'</main></body>')
  a=parse_article(body,'https://example.com/news')
  self.assertEqual(a['extraction_status'],'complete')
  self.assertIn('funding',a['content'])
  self.assertNotIn('/unrelated.jpg',a['images_json'])

 def test_hidden_structured_body_is_not_extracted(self):
  payload={'@type':'NewsArticle','isAccessibleForFree':False,'articleBody':PARA*4}
  body=('<html><script type="application/ld+json">'+json.dumps(payload)+'</script><article><p>Subscribe for details.</p></article></html>').encode()
  a=parse_article(body,'https://example.com/paywalled')
  self.assertEqual(a['extraction_status'],'paywalled');self.assertEqual(a['content'],'')

 def test_nested_hidden_elements_do_not_break_public_text_extraction(self):
  hidden=b'<div style="display:none"><p style="visibility:hidden">Hidden article text</p></div>'
  a=parse_article(HTML.replace(b'<article>',b'<article>'+hidden),'https://example.com/news')
  self.assertEqual(a['extraction_status'],'complete')
  self.assertNotIn('Hidden article text',a['content'])

 def test_short_single_paragraph_is_partial_not_length_only_complete(self):
  a=parse_article(b'<html><article><p>Small report with limited available public details.</p></article></html>','https://example.com/news')
  self.assertNotEqual(a['extraction_status'],'complete')

 def test_video_only_body_is_not_applicable_and_transcript_separate(self):
  body=b'<html><head><script type="application/ld+json">{"@type":"VideoObject","name":"Report","contentUrl":"https://example.com/a.mp4","transcript":"Publisher transcript"}</script></head><body><video src="/a.mp4"></video></body></html>'
  a=parse_article(body,'https://example.com/video')
  self.assertEqual(a['extraction_status'],'not_applicable');self.assertEqual(a['transcript_status'],'available')
  self.assertEqual(a['transcript'],'Publisher transcript')

 def test_blocked_retry_retains_feed_and_respects_retry_after(self):
  from newsjockey.network import FetchError
  self.collect();aid=self.item()['id']
  with patch('newsjockey.extraction.throttle'),patch('newsjockey.extraction.fetch',side_effect=FetchError('Publisher returned HTTP 429.',retry_after='7200')):
   extract_article(self.path,aid)
  a=self.client.get(f'/api/articles/{aid}').json
  self.assertEqual(a['extraction_status'],'blocked');self.assertEqual(a['content'],'First report')
  self.assertTrue(a['next_extraction']);self.assertEqual(a['extraction_failures'],1)
  retry=self.client.post('/api/extraction/retry',json={'article_id':aid},headers=self.headers)
  self.assertEqual(retry.json, {'queued': 1}) # Current-month retry uses automatic HTTP queue.
  self.assertEqual(self.item()['extraction_status'],'pending')

 def test_browser_requires_explicit_transport_even_when_old_publisher_toggle_is_on(self):
  self.collect();aid=self.item()['id']
  self.client.patch(f'/api/publishers/{self.pid}',json={'browser_rendering':True},headers=self.headers)
  with patch('newsjockey.extraction.render_public',return_value=(HTML,'https://example.com/news?id=1')) as render:
   self.extract(body=b'<html><script src="/bundle.js"></script></html>')
   render.assert_not_called()
   extract_article(self.path,aid,transport='browser')
   render.assert_called_once()
  self.assertEqual(self.item()['extraction_status'],'complete')

 def test_sitemap_discovery_uses_recent_inventory_and_english_language(self):
  date=now()
  xml=f'''<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:news="http://www.google.com/schemas/sitemap-news/0.9"><url><loc>https://example.com/new</loc><lastmod>{date}</lastmod><news:news><news:publication><news:language>en</news:language></news:publication><news:title>New report</news:title></news:news></url><url><loc>https://example.com/old</loc><lastmod>2020-01-01</lastmod></url><url><loc>https://example.com/french</loc><news:news><news:publication><news:language>fr</news:language></news:publication></news:news></url></urlset>'''.encode()
  items,_,_=sitemap_items('https://example.com/sitemap.xml',xml)
  self.assertEqual([i['link'] for i in items],['https://example.com/new'])

 def test_sitemap_indices_rotate_all_sections(self):
  index=b'<sitemapindex>'+b''.join(f'<sitemap><loc>https://example.com/{i}.xml</loc></sitemap>'.encode() for i in range(6))+b'</sitemapindex>'
  xml=b'<urlset><url><loc>https://example.com/a</loc></url></urlset>'
  with patch('newsjockey.discovery.fetch',return_value=(200,{},xml,'https://example.com/a.xml')) as fetch:
   _,cursor,warning=sitemap_items('https://example.com/index.xml',index)
   self.assertEqual(cursor,1);self.assertIn('Rotating',warning)
   sitemap_items('https://example.com/index.xml',index,cursor)
   self.assertIn('https://example.com/5.xml',[c.args[0] for c in fetch.call_args_list])

 def test_xml_entities_are_rejected(self):
  from defusedxml.common import DefusedXmlException
  with self.assertRaises(DefusedXmlException):
   sitemap_items('https://example.com/map',b'<!DOCTYPE x [<!ENTITY y SYSTEM "file:///etc/passwd">]><urlset><url><loc>&y;</loc></url></urlset>')

 def test_publisher_expansion_adds_verified_channels_and_visible_gaps(self):
  def fetch(url,headers=None):
   if url.endswith('robots.txt'):return 200,{},b'Sitemap: https://example.com/map.xml','https://example.com/robots.txt'
   if url.endswith('map.xml'):return 200,{},b'<urlset></urlset>',url
   if url.endswith('/new-feed'):return 200,{},RSS,url
   return 200,{},b'<html><link type="application/rss+xml" href="/new-feed"></html>',url
  with patch('newsjockey.discovery.fetch',side_effect=fetch):
   result=expand_publisher(self.path,self.pid)
  self.assertEqual(result['added'],2)
  channels=[s for s in self.client.get('/api/sources').json['sources'] if s['publisher_id']==self.pid]
  self.assertEqual(len(channels),3);self.assertIn('sitemap',[s['channel_type'] for s in channels])
  self.assertEqual(next(s for s in channels if s['channel_type']=='sitemap')['interval_minutes'],30)

 def test_sitemap_stub_does_not_overwrite_full_text_or_title(self):
  self.collect();self.extract();aid=self.item()['id']
  with connect(self.path) as db:
   sid,_=add_source(db,dict(name='Sitemap',website='https://example.com',feed_url='https://example.com/map.xml',category='Politics/National',channel_type='sitemap'))
  xml=b'<urlset><url><loc>https://example.com/news?id=1</loc></url></urlset>'
  self.collect(sid,xml)
  a=self.client.get(f'/api/articles/{aid}').json
  self.assertEqual(a['title'],'Project funding announced');self.assertEqual(a['content_kind'],'Full text')

 def test_source_filter_matches_all_discovery_channels(self):
  self.collect()
  with connect(self.path) as db:
   sid,_=add_source(db,dict(name='Other',website='https://example.com',feed_url='https://example.com/other.xml',category='Politics/National'))
  self.collect(sid)
  self.assertEqual(self.client.get(f'/api/articles?source={sid}').json['total'],1)
  self.assertEqual(self.client.get(f'/api/articles?publisher={self.pid}').json['total'],1)

 def test_invalid_api_labels_are_rejected(self):
  self.collect();aid=self.item()['id']
  self.assertEqual(self.client.patch(f'/api/articles/{aid}',json={'content_type':'fake'},headers=self.headers).status_code,400)
  self.assertEqual(self.client.patch(f'/api/publishers/{self.pid}',json={'browser_rendering':'yes'},headers=self.headers).status_code,400)

 def test_nested_sitemap_inventory_is_discovered(self):
  index=b'<sitemapindex><sitemap><loc>https://example.com/nested.xml</loc></sitemap></sitemapindex>'
  nested=b'<sitemapindex><sitemap><loc>https://example.com/leaf.xml</loc></sitemap></sitemapindex>'
  leaf=b'<urlset><url><loc>https://example.com/story</loc></url></urlset>'
  with patch('newsjockey.discovery.fetch',side_effect=[(200,{},nested,'https://example.com/nested.xml'),(200,{},leaf,'https://example.com/leaf.xml')]):
   entries,_,_=sitemap_items('https://example.com/root.xml',index)
  self.assertEqual(entries[0]['link'],'https://example.com/story')

 def test_profile_urls_do_not_enter_sitemap_article_inventory(self):
  xml=b'<urlset><url><loc>https://example.com/author/jane</loc></url><url><loc>https://example.com/player/one</loc></url><url><loc>https://example.com/news/story</loc></url></urlset>'
  entries,_,_=sitemap_items('https://example.com/map.xml',xml)
  self.assertEqual([x['link'] for x in entries],['https://example.com/news/story'])

 def test_video_landing_description_is_not_a_full_article(self):
  body=HTML.replace(b'<article>',b'<main>').replace(b'</article>',b'</main>')
  a=parse_article(body,'https://example.com/video/report')
  self.assertEqual(a['extraction_status'],'not_applicable')
  self.assertEqual(a['content'],'')

 def test_expired_extraction_lease_resumes_after_restart(self):
  import threading
  from newsjockey.extraction import ExtractionWorker
  self.collect();aid=self.item()['id']
  with connect(self.path) as db:
   db.execute("UPDATE articles SET published_at=?,extraction_status='extracting',extraction_attempted_at='2020-01-01T00:00:00+00:00' WHERE id=?",(now(),aid))
  worker=ExtractionWorker(self.path);finished=threading.Event()
  def fake_extract(path,item_id,**kwargs):
   with connect(path) as db:db.execute("UPDATE articles SET extraction_status='complete' WHERE id=?",(item_id,))
   worker.stop_event.set();finished.set();return {'id':item_id}
  with patch('newsjockey.extraction.extract_article',side_effect=fake_extract):
   worker.start()
   try:self.assertTrue(finished.wait(2))
   finally:
    worker.stop_event.set()
    for thread in worker.threads: thread.join(2)
  self.assertEqual(self.item()['extraction_status'],'complete')

 def test_automatic_queue_excludes_older_and_undated_inventory(self):
  self.collect();feed_id=self.item()['id']
  with connect(self.path) as db:
   sid,_=add_source(db,dict(name='Sitemap',website='https://example.com',feed_url='https://example.com/map.xml',category='Politics/National',channel_type='sitemap'))
  self.collect(sid,b'<urlset><url><loc>https://example.com/archive</loc></url></urlset>')
  with connect(self.path) as db:
   archive=db.execute('SELECT MAX(id) FROM articles').fetchone()[0]
   db.execute('UPDATE articles SET published_at=? WHERE id=?',(now(),feed_id))
   db.execute("UPDATE articles SET first_seen='2099-01-01',content_changed_at='2099-01-01' WHERE id=?",(archive,))
   self.assertEqual([r['id'] for r in extraction_candidates(db)],[feed_id])
   db.execute('UPDATE sources SET enabled=0 WHERE id=?',(self.sid,))
   self.assertEqual([r['id'] for r in extraction_candidates(db)],[])
   db.execute("UPDATE articles SET extraction_status='blocked',next_extraction='2099-01-01' WHERE id=?",(archive,))
   self.assertEqual(extraction_candidates(db),[])
   db.execute("UPDATE articles SET next_extraction='2020-01-01' WHERE id=?",(archive,))
   self.assertEqual([r['id'] for r in extraction_candidates(db)],[])

 def test_canonical_merge_preserves_ids_and_best_body(self):
  self.collect();older=self.item()['id']
  second=RSS.replace(b'story-1',b'story-2').replace(b'/news?utm_source=rss&amp;id=1',b'/duplicate')
  self.collect(body=second)
  with connect(self.path) as db:newer=db.execute('SELECT MAX(id) FROM articles').fetchone()[0]
  self.extract(aid=newer)
  self.assertEqual(self.client.get('/api/articles').json['total'],1)
  a=self.client.get(f'/api/articles/{newer}').json
  self.assertEqual(a['id'],older);self.assertEqual(a['extraction_status'],'complete')
  self.assertIn('funding',a['content'])
  with connect(self.path) as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM articles').fetchone()[0],2)

class LegacyMigrationTests(unittest.TestCase):
 def test_legacy_articles_ids_and_times_are_preserved_and_overlap_is_aliased(self):
  with tempfile.TemporaryDirectory() as tmp:
   path=Path(tmp)/'legacy.sqlite'
   with sqlite3.connect(path) as db:
    db.executescript('''CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);INSERT INTO settings VALUES('seeded','1');
     CREATE TABLE sources(id INTEGER PRIMARY KEY,catalog_id TEXT,name TEXT NOT NULL,category TEXT NOT NULL,website TEXT NOT NULL,feed_url TEXT UNIQUE NOT NULL,enabled INTEGER DEFAULT 1,interval_minutes INTEGER DEFAULT 5,created_at TEXT,last_attempt TEXT,last_success TEXT,next_check TEXT,status TEXT DEFAULT 'Pending',error TEXT,warning TEXT,failures INTEGER DEFAULT 0,etag TEXT,last_modified TEXT,latest_published TEXT,last_item_count INTEGER DEFAULT 0);
     CREATE TABLE articles(id INTEGER PRIMARY KEY,source_id INTEGER,entry_id TEXT,url TEXT,title TEXT,author TEXT,tags TEXT,published_at TEXT,publisher_updated_at TEXT,published_raw TEXT,updated_raw TEXT,first_seen TEXT,last_seen TEXT,content_changed_at TEXT,content TEXT,content_kind TEXT,fingerprint TEXT,UNIQUE(source_id,entry_id));''')
    for sid in (3,9):
     db.execute('INSERT INTO sources(id,name,category,website,feed_url,created_at) VALUES(?,?,?,?,?,?)',(sid,'NYT','Politics/National','https://www.nytimes.com','https://rss.nytimes.com/'+str(sid),now()))
     db.execute('INSERT INTO articles(id,source_id,entry_id,url,title,first_seen,last_seen,content_changed_at,content,content_kind,fingerprint) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(sid,sid,'a','https://nytimes.com/a','Original title','2020-01-01',now(),now(),'Original text','Feed content','hash'))
   db.close()
   initialize(path);initialize(path)
   with connect(path) as db:
    self.assertEqual(db.execute('SELECT COUNT(*) FROM articles').fetchone()[0],2)
    self.assertEqual(db.execute('SELECT COUNT(*) FROM publishers').fetchone()[0],1)
    a=db.execute('SELECT * FROM articles WHERE id=9').fetchone()
    self.assertEqual(a['first_seen'],'2020-01-01');self.assertEqual(a['duplicate_of'],3)
    self.assertEqual(a['feed_content'],'Original text')
    self.assertEqual(db.execute('SELECT COUNT(*) FROM article_channels WHERE article_id=3').fetchone()[0],2)
    self.assertEqual(db.execute('SELECT COUNT(*) FROM article_versions').fetchone()[0],1)

if __name__=='__main__':unittest.main()
