import json
import re
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from datetime import datetime, timezone, timedelta
import feedparser
from app import create_app
from newsjockey.storage import connect, initialize, now
from newsjockey.collector import collect_source, entry_record, publisher_time, Collector
from newsjockey.network import discover, FetchError, public_url, article_url, parse_feed

RSS = b'''<?xml version="1.0"?><rss version="2.0"><channel><title>Example News</title><link>https://example.com</link><description>News</description><item><guid isPermaLink="false">story-1</guid><title>Election update</title><link>https://example.com/news?utm_source=rss&amp;id=1</link><pubDate>Thu, 01 Oct 2026 15:00:00 GMT</pubDate><description>&lt;p&gt;First report&lt;/p&gt;</description></item></channel></rss>'''
ATOM = b'''<feed xmlns="http://www.w3.org/2005/Atom"><title>News</title><id>https://example.com</id><updated>2026-10-01T16:00:00Z</updated><entry><id>atom-1</id><title>Tech launch</title><link href="https://example.com/tech"/><published>2026-10-01T15:00:00Z</published><updated>2026-10-01T16:00:00Z</updated><author><name>Reporter</name></author><content type="html">&lt;p&gt;Details&lt;/p&gt;&lt;script&gt;evil()&lt;/script&gt;</content></entry></feed>'''

class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)/'test.sqlite'
        self.app = create_app(self.path)
        self.client = self.app.test_client()
        html = self.client.get('/').get_data(as_text=True)
        self.token = re.search(r'name="newsjockey-token" content="([^"]+)"', html).group(1)
        self.headers = {'X-NewsJockey-Token': self.token}
        with connect(self.path) as db:
            self.source_id = db.execute('SELECT id FROM sources ORDER BY id LIMIT 1').fetchone()[0]

    def tearDown(self):
        self.tmp.cleanup()

    def fetch(self, content=RSS, status=200, headers=None):
        return patch('newsjockey.collector.fetch', return_value=(status, headers or {}, content, 'https://example.com/feed'))

    def collect(self, content=RSS):
        with self.fetch(content):
            result = collect_source(self.path, self.source_id)
        self.assertNotIn('error', result)
        return result

    def test_seed_is_exactly_fifty_and_idempotent(self):
        initialize(self.path)
        sources = self.client.get('/api/sources').json['sources']
        self.assertEqual(len(sources), 50)
        from collections import Counter
        self.assertEqual(set(Counter(s['category'] for s in sources).values()), {10})
        catalog = self.client.get('/api/catalog?q=pitchfork').json
        self.assertEqual(catalog['results'][0]['name'], 'Pitchfork')
        self.assertFalse(catalog['results'][0]['added'])

    def test_repeated_collection_deduplicates_and_detects_change(self):
        first = self.collect()
        with connect(self.path) as db:
            original = dict(db.execute('SELECT * FROM articles').fetchone())
            db.execute("UPDATE articles SET first_seen='2020-01-01T00:00:00+00:00',content_changed_at='2020-01-01T00:00:00+00:00'")
        second = self.collect()
        self.assertEqual((first['new'],second['new'],second['updated']),(1,0,0))
        third = self.collect(RSS.replace(b'First report',b'Updated report'))
        self.assertEqual((third['new'],third['updated']),(0,1))
        with connect(self.path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM articles').fetchone()[0],1)
            row = dict(db.execute('SELECT * FROM articles').fetchone())
        self.assertEqual(row['first_seen'],'2020-01-01T00:00:00+00:00')
        self.assertNotEqual(row['content_changed_at'],'2020-01-01T00:00:00+00:00')
        self.assertEqual(row['url'],'https://example.com/news?id=1')
        self.assertEqual(row['content'],'Updated report')
        self.assertIsNone(row['publisher_updated_at'])
        self.assertIsNone(row['updated_raw'])

    def test_url_fallback_handles_changed_guid(self):
        self.collect()
        result = self.collect(RSS.replace(b'story-1',b'renamed-id'))
        self.assertEqual(result['new'],0)
        with connect(self.path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM articles').fetchone()[0],1)

    def test_atom_content_is_safe_and_keeps_update_timestamp(self):
        self.collect(ATOM)
        article = self.client.get('/api/articles').json['articles'][0]
        detail = self.client.get(f"/api/articles/{article['id']}").json
        self.assertEqual(detail['content'],'Details')
        self.assertEqual(detail['content_kind'],'Feed content')
        self.assertEqual(detail['author'],'Reporter')
        self.assertEqual(detail['publisher_updated_at'],'2026-10-01T16:00:00+00:00')
        self.assertIsNotNone(detail['observed_delay_seconds'])

    def test_missing_and_unzoned_dates_are_not_invented(self):
        entry = feedparser.parse(RSS).entries[0]
        entry['published']='2026-10-01T15:00:00'
        self.assertIsNone(publisher_time(entry,'published'))
        del entry['published'];del entry['published_parsed']
        self.assertIsNone(entry_record(entry,'https://example.com')['published_at'])

    def test_filters_search_sort_and_pagination(self):
        self.collect()
        self.collect(ATOM)
        self.assertEqual(self.client.get('/api/articles?q=election').json['total'],1)
        self.assertEqual(self.client.get('/api/articles?kind=Feed%20content').json['total'],1)
        self.assertEqual(self.client.get('/api/articles?category=Entertainment').json['total'],0)
        self.assertEqual(self.client.get('/api/articles?from=2026-10-02').json['total'],0)
        result=self.client.get('/api/articles?sort=title&direction=asc&size=10').json
        self.assertEqual([x['title'] for x in result['articles']],['Election update','Tech launch'])
        self.assertEqual(self.client.get('/api/articles?page=2&size=10').json['articles'],[])
        self.assertEqual(self.client.get('/api/articles?from=wrong').status_code,400)

    def test_disabled_source_does_not_fetch(self):
        response=self.client.patch(f'/api/sources/{self.source_id}',json={'enabled':False},headers=self.headers)
        self.assertEqual(response.status_code,200)
        with patch('newsjockey.collector.fetch') as fetch:
            self.assertTrue(collect_source(self.path,self.source_id)['skipped'])
            fetch.assert_not_called()

    def test_304_preserves_articles_and_caching_headers(self):
        with self.fetch(headers={'ETag':'abc','Last-Modified':'Thu, 01 Oct 2026 15:00:00 GMT'}):
            collect_source(self.path,self.source_id)
        with self.fetch(status=304) as fetch:
            result=collect_source(self.path,self.source_id)
            self.assertEqual(fetch.call_args.args[1]['If-None-Match'],'abc')
        self.assertEqual(result['new'],0)
        with connect(self.path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM articles').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT etag FROM sources WHERE id=?',(self.source_id,)).fetchone()[0],'abc')

    def test_failure_keeps_existing_articles_and_respects_retry_after(self):
        self.collect()
        with patch('newsjockey.collector.fetch',side_effect=FetchError('HTTP 429',retry_after='7200')):
            result=collect_source(self.path,self.source_id)
        self.assertIn('error',result)
        with connect(self.path) as db:
            row=db.execute('SELECT * FROM sources WHERE id=?',(self.source_id,)).fetchone()
            self.assertEqual(row['status'],'Error')
            self.assertGreater((datetime.fromisoformat(row['next_check'])-datetime.now(timezone.utc)).total_seconds(),7100)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM articles').fetchone()[0],1)
        collector=Collector(self.path)
        with patch.object(collector,'submit',return_value='job') as submit:
            collector.collect(source_id=self.source_id)
            self.assertEqual(submit.call_args.args[1],[])

    def test_feed_ttl_is_respected_after_a_cached_response(self):
        feed = RSS.replace(b'<description>News</description>', b'<description>News</description><ttl>30</ttl>')
        self.collect(feed)
        with self.fetch(status=304):
            collect_source(self.path, self.source_id)
        with connect(self.path) as db:
            row = db.execute('SELECT * FROM sources WHERE id=?', (self.source_id,)).fetchone()
            self.assertEqual(row['feed_ttl_minutes'], 30)
            self.assertGreater((datetime.fromisoformat(row['next_check'])-datetime.now(timezone.utc)).total_seconds(), 1790)

    def test_source_addition_is_idempotent(self):
        body={'sources':[{'catalog_id':'pitchfork'}]}
        first=self.client.post('/api/sources',json=body,headers=self.headers)
        second=self.client.post('/api/sources',json=body,headers=self.headers)
        self.assertTrue(first.json['results'][0]['added'])
        self.assertFalse(second.json['results'][0]['added'])
        self.assertEqual(len(self.client.get('/api/sources').json['sources']),51)

    def test_bulk_discovery_reports_success_and_failure_per_url(self):
        collector=self.app.extensions['collector']
        with patch('newsjockey.collector.discover',side_effect=lambda u: [{'name':'Test','feed_url':u,'website':u,'entries':1}] if u.endswith('good') else (_ for _ in ()).throw(FetchError('No feed'))):
            response=self.client.post('/api/discover',json={'urls':['https://example.com/good','https://example.com/bad']},headers=self.headers)
            self.assertEqual(response.status_code,202)
            for _ in range(100):
                job=collector.job(response.json['job_id'])
                if job['status']!='running':break
                time.sleep(.01)
        self.assertEqual(job['completed'],2)
        self.assertEqual(sum(bool(x.get('error')) for x in job['results']),1)
        self.assertEqual(self.client.post('/api/discover',json={'urls':['x']*51},headers=self.headers).status_code,400)

    def test_discover_advertised_feed_and_direct_atom(self):
        page=b'<html><head><link rel="alternate" type="application/rss+xml" href="/feed"/></head></html>'
        with patch('newsjockey.network.fetch',side_effect=[(200,{},page,'https://example.com'),(200,{},RSS,'https://example.com/feed')]):
            feeds=discover('https://example.com')
            self.assertEqual(feeds[0]['feed_url'],'https://example.com/feed')
        with patch('newsjockey.network.fetch',return_value=(200,{},ATOM,'https://example.com/atom.xml')):
            self.assertEqual(len(discover('https://example.com/atom.xml')),1)

    def test_local_request_protection(self):
        self.assertEqual(self.client.post('/api/collect',json={}).status_code,403)
        self.assertEqual(self.client.post('/api/collect',json={},headers={**self.headers,'Origin':'https://evil.example'}).status_code,403)
        self.assertEqual(self.client.get('/api/sources',headers={'Host':'evil.example'}).status_code,400)

    def test_untrusted_urls_and_html_are_rejected(self):
        with self.assertRaises(FetchError): public_url('file:///etc/passwd')
        with self.assertRaises(FetchError): public_url('http://127.0.0.1/feed')
        with self.assertRaises(FetchError): public_url('http://localhost/feed')
        self.assertEqual(article_url('javascript:alert(1)','https://example.com'),'')
        with self.assertRaises(FetchError):parse_feed(b'<html>not a feed</html>')

if __name__=='__main__':
    unittest.main()
