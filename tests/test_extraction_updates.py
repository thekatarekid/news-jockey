import json
import threading
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch
import test_processing as fixtures
from newsjockey.storage import connect, now
from newsjockey.windows import date_window
from newsjockey.extraction import extract_article, extraction_candidates, compare_feed
from newsjockey.extraction_jobs import create_job, claim_manual, control_job, lease_article, recover_leases, finish_item
from newsjockey.content import sanitize_html, feed_matches_page
from newsjockey.network import FetchError
from newsjockey.body_parser import parse_article

class ExtractionUpdatesTests(unittest.TestCase):
    setUp=fixtures.ProcessingTests.setUp
    tearDown=fixtures.ProcessingTests.tearDown
    collect=fixtures.ProcessingTests.collect
    item=fixtures.ProcessingTests.item
    extract=fixtures.ProcessingTests.extract

    def test_public_captions_saved_and_caption_failure_does_not_lose_body(self):
        aid=self.add_item(1,now())
        captions=b'WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nThe agency approved funding.\n\n00:00:02.000 --> 00:00:04.000\nConstruction starts in November.\n'
        with patch('newsjockey.transcripts.fetch',return_value=(200,{},captions,'https://example.com/captions.vtt')):
            self.extract(aid)
        article=self.client.get(f'/api/articles/{aid}').json
        self.assertEqual(article['transcript_status'],'available')
        self.assertIn('Construction starts in November.',article['transcript'])
        self.assertEqual(article['body_quality'],'full')
        self.extract(aid)
        after=self.client.get(f'/api/articles/{aid}').json
        self.assertEqual(after['body_quality'],'full')
        self.assertEqual(after['transcript'],article['transcript'])
        stats=self.client.get('/api/extraction/metrics').json
        self.assertEqual(stats['transports'][0]['attempts'],2)

    def test_invalid_recipe_numeric_fields_return_validation_error(self):
        response=self.client.post('/api/recipes/preview',json={'definition':{'id':'test','hosts':['example.com'],'include':['article'],'priority':None}},headers=self.headers)
        self.assertEqual(response.status_code,400)

    def test_utf8_rich_text_without_charset_retains_punctuation(self):
        body=fixtures.HTML.replace(b'Construction',b'Construction \xe2\x80\x94 city')
        result=parse_article(body,'https://example.com/news?id=1')
        self.assertIn('Construction — city',result['body_html'])
        self.assertNotIn('â€',result['body_html'])

    def test_restricted_metadata_does_not_discard_delivered_visible_body(self):
        body=fixtures.HTML.replace(b'"@type": "NewsArticle"',b'"@type": "NewsArticle", "isAccessibleForFree": false')
        result=parse_article(body,'https://example.com/news')
        self.assertEqual(result['body_quality'],'full')
        self.assertEqual(result['extraction_status'],'complete')
        self.assertEqual(result['publisher_access'],'restricted_marker')
        self.assertIn('visible HTML',result['body_notes'])

    def add_item(self,index,published):
        body=fixtures.RSS.replace(b'story-1',f'story-{index}'.encode()).replace(b'id=1',f'id={index}'.encode())
        self.collect(body=body)
        with connect(self.path) as db:
            aid=db.execute('SELECT MAX(id) FROM articles').fetchone()[0]
            db.execute('UPDATE articles SET published_at=? WHERE id=?',(published,aid))
        return aid

    def test_calendar_month_uses_local_month_boundaries(self):
        at=datetime(2026,10,6,18,0,tzinfo=timezone.utc)
        with connect(self.path) as db:
            window=date_window(db,at)
        self.assertEqual(window['today'],'2026-10-06')
        self.assertEqual(window['start'],'2026-09-30T18:30:00+00:00')
        self.assertEqual(window['end'],'2026-10-31T18:30:00+00:00')

    def test_dst_window_uses_calendar_midnights(self):
        with connect(self.path) as db:
            db.execute("UPDATE settings SET value='America/New_York' WHERE key='portal_timezone'")
            window=date_window(db,datetime(2026,11,1,17,tzinfo=timezone.utc))
        start=datetime.fromisoformat(window['start']);end=datetime.fromisoformat(window['end'])
        self.assertEqual((end-start).total_seconds()/3600,721)

    def test_recent_old_undated_and_future_do_not_overlap(self):
        with connect(self.path) as db: window=date_window(db)
        recent=self.add_item(1,window['start']);older=self.add_item(2,(datetime.fromisoformat(window['start'])-timedelta(seconds=1)).isoformat())
        undated=self.add_item(3,None);future=self.add_item(4,window['end'])
        with connect(self.path) as db: self.assertEqual([r['id'] for r in extraction_candidates(db,10)],[recent])
        old_job=create_job(self.path,'http','older')
        with connect(self.path) as db:
            ids={r[0] for r in db.execute('SELECT article_id FROM extraction_job_items WHERE job_id=?',(old_job['id'],))}
        self.assertEqual(ids,{older});self.assertNotIn(future,ids)
        recent_job=create_job(self.path,'browser','recent');self.assertEqual(recent_job['total'],1)

    def test_older_job_is_global_and_duplicate_button_is_idempotent(self):
        self.add_item(1,None);self.add_item(2,'2020-01-01T00:00:00+00:00')
        first=self.client.post('/api/extraction/jobs',json={'transport':'http','scope':'older','publisher_id':999},headers=self.headers)
        second=self.client.post('/api/extraction/jobs',json={'transport':'http','scope':'older'},headers=self.headers)
        self.assertEqual(first.status_code,202);self.assertEqual(first.json['total'],1)
        self.assertEqual(second.json['id'],first.json['id']);self.assertTrue(second.json['existing'])

    def test_pause_resume_cancel_and_restart_state(self):
        self.add_item(1,'2020-01-01T00:00:00+00:00');job=create_job(self.path,'http','older')
        control_job(self.path,job['id'],'pause');self.assertIsNone(claim_manual(self.path,'http'))
        control_job(self.path,job['id'],'resume');claim=claim_manual(self.path,'http');self.assertIsNotNone(claim)
        with connect(self.path) as db:
            db.execute("UPDATE articles SET lease_until='2000-01-01' WHERE id=?",(claim['id'],))
            recover_leases(db)
            self.assertEqual(db.execute('SELECT state FROM extraction_job_items WHERE job_id=?',(job['id'],)).fetchone()[0],'pending')
        control_job(self.path,job['id'],'cancel');self.assertIsNone(claim_manual(self.path,'http'))
        self.assertEqual(self.client.get('/api/articles').json['total'],1)

    def test_two_workers_cannot_claim_same_article(self):
        aid=self.add_item(1,None);tokens=[];barrier=threading.Barrier(2)
        def take():
            barrier.wait()
            with connect(self.path) as db: tokens.append(lease_article(db,aid))
        threads=[threading.Thread(target=take) for _ in range(2)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        self.assertEqual(sum(bool(token) for token in tokens),1)

    def test_recent_workers_avoid_a_host_with_an_active_request(self):
        first=self.add_item(1,now());second=self.add_item(2,now())
        with connect(self.path) as db:
            lease_article(db,first)
            self.assertEqual(extraction_candidates(db,10),[])

    def test_chrome_job_does_not_change_publisher_toggle(self):
        aid=self.add_item(1,now());create_job(self.path,'browser','recent')
        claim=claim_manual(self.path,'browser')
        with patch('newsjockey.extraction.render_public',return_value=(fixtures.HTML,'https://example.com/news?id=1')) as browser,patch('newsjockey.extraction.fetch') as fetch,patch('newsjockey.extraction.throttle'):
            result=extract_article(self.path,aid,transport='browser',lease_token=claim['token'])
        browser.assert_called_once();fetch.assert_not_called();finish_item(self.path,claim,result)
        with connect(self.path) as db:
            self.assertEqual(db.execute('SELECT browser_rendering FROM publishers WHERE id=?',(self.pid,)).fetchone()[0],0)
            article=db.execute('SELECT * FROM articles WHERE id=?',(aid,)).fetchone()
            self.assertEqual(article['body_origin'],'browser');self.assertEqual(article['body_quality'],'full')

    def test_failed_recheck_preserves_full_html_and_quality(self):
        aid=self.add_item(1,now());self.extract(aid)
        before=self.client.get(f'/api/articles/{aid}').json
        with patch('newsjockey.extraction.fetch',side_effect=FetchError('Publisher returned HTTP 403.')),patch('newsjockey.extraction.throttle'):
            extract_article(self.path,aid)
        after=self.client.get(f'/api/articles/{aid}').json
        self.assertEqual(after['body_html'],before['body_html']);self.assertEqual(after['body_quality'],'full')
        self.assertEqual(after['fetch_outcome'],'blocked')
        self.assertEqual(self.client.get('/api/extraction/coverage?group=all').json['totals']['full'],1)

    def test_partial_recheck_cannot_replace_full_body(self):
        aid=self.add_item(1,now());self.extract(aid)
        before=self.client.get(f'/api/articles/{aid}').json
        self.extract(aid,body=b'<article><p>A short public excerpt with incomplete context.</p></article>')
        after=self.client.get(f'/api/articles/{aid}').json
        self.assertEqual(after['body_quality'],'full');self.assertEqual(after['body_html'],before['body_html'])
        self.assertEqual(after['extraction_status'],'partial')

    def test_html_preserves_rich_structure_and_removes_executable_content(self):
        raw='<script>alert(1)</script><svg onload="evil()"><text>bad</text></svg><p onclick="evil()">Report <a href="javascript:evil()">link</a></p><table><tr><td>Value</td></tr></table><figure><img data-src="/photo.jpg" onerror="evil()"><figcaption>Credit</figcaption></figure>'
        safe=sanitize_html(raw,'https://example.com/news')
        self.assertIn('<table>',safe);self.assertIn('<figcaption>Credit</figcaption>',safe)
        self.assertIn('https://example.com/photo.jpg',safe)
        for bad in ('<script','<svg','onclick','onerror','javascript:','alert(1)'): self.assertNotIn(bad,safe)

    def test_feed_body_needs_three_distinct_page_comparisons(self):
        feed_html=''.join('<p>'+fixtures.PARA+'</p>' for _ in range(4))
        result={'body_quality':'full','body_html':feed_html}
        self.assertTrue(feed_matches_page(feed_html,feed_html))
        self.assertFalse(feed_matches_page(feed_html[:100],feed_html))
        aids=[self.add_item(i,now()) for i in range(1,5)]
        with connect(self.path) as db:
            for aid in aids: db.execute('UPDATE articles SET feed_html=? WHERE id=?',(feed_html,aid))
            row=db.execute('SELECT * FROM articles WHERE id=?',(aids[0],)).fetchone()
            for _ in range(3): compare_feed(db,row,result)
            self.assertEqual(db.execute('SELECT state FROM feed_body_policies').fetchone()[0],'observed_full')
            for aid in aids[1:3]: compare_feed(db,db.execute('SELECT * FROM articles WHERE id=?',(aid,)).fetchone(),result)
        with patch('newsjockey.extraction.fetch') as fetch:
            accepted=extract_article(self.path,aids[3])
        fetch.assert_not_called();self.assertEqual(accepted['method'],'validated feed')

    def test_cached_304_reuses_html_without_new_snapshot(self):
        aid=self.add_item(1,now())
        with patch('newsjockey.extraction.fetch',return_value=(200,{'ETag':'article-v1'},fixtures.HTML,'https://example.com/news?id=1')),patch('newsjockey.extraction.throttle'):
            extract_article(self.path,aid)
        before=len(self.client.get(f'/api/articles/{aid}').json['versions'])
        with patch('newsjockey.extraction.fetch',return_value=(304,{},b'','https://example.com/news?id=1')) as fetch,patch('newsjockey.extraction.throttle'):
            result=extract_article(self.path,aid)
        self.assertEqual(fetch.call_args.args[1]['If-None-Match'],'article-v1');self.assertEqual(result['status'],'complete')
        self.assertEqual(len(self.client.get(f'/api/articles/{aid}').json['versions']),before)

    def test_recipe_validation_activation_rollback_and_separate_holdouts(self):
        aids=[self.add_item(i,now()) for i in range(1,4)]
        for aid in aids:
            with connect(self.path) as db: url=db.execute('SELECT url FROM articles WHERE id=?',(aid,)).fetchone()[0]
            body=fixtures.HTML.replace(b'https://example.com/news?id=1',url.encode())
            with patch('newsjockey.extraction.fetch',return_value=(200,{},body,url)),patch('newsjockey.extraction.throttle'): extract_article(self.path,aid)
        definition={'id':'example-body','hosts':['example.com'],'include':['article'],'remove':[]}
        payload={'definition':definition,'development_ids':[aids[0]],'validation_ids':aids[1:]}
        response=self.client.post('/api/recipes',json=payload,headers=self.headers)
        self.assertEqual(response.status_code,200,response.json)
        bad={**payload,'validation_ids':[aids[0],aids[1]]}
        self.assertEqual(self.client.post('/api/recipes',json=bad,headers=self.headers).status_code,400)
        response=self.client.post('/api/recipes',json=payload,headers=self.headers);self.assertEqual(response.json['version'],2)
        rollback=self.client.post('/api/recipes/example-body/rollback',json={},headers=self.headers)
        self.assertEqual(rollback.json['version'],1)

    def test_job_settings_and_mutations_require_valid_inputs_and_token(self):
        self.assertEqual(self.client.post('/api/extraction/jobs',json={'transport':'browser','scope':'older'}).status_code,403)
        self.assertEqual(self.client.post('/api/extraction/jobs',json={'transport':'paid','scope':'older'},headers=self.headers).status_code,400)
        self.assertEqual(self.client.patch('/api/extraction/settings',json={'timezone':'invalid/tz'},headers=self.headers).status_code,400)

    def test_feed_refresh_preserves_page_media_and_specific_editorial_label(self):
        aid=self.add_item(1,now());self.extract(aid)
        before=self.client.get(f'/api/articles/{aid}').json
        self.collect(body=fixtures.RSS.replace(b'First report',b'A new excerpt'))
        after=self.client.get(f'/api/articles/{aid}').json
        self.assertEqual(after['images_json'],before['images_json']);self.assertEqual(after['content_type'],'analysis')

if __name__=='__main__': unittest.main()
