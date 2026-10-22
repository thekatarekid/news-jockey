"""Audit recent publisher samples using cached/public HTML; no browser or model calls.

Does not change article bodies, jobs, recipes, or publisher settings. Results are
structural checks, not independent evidence that every article is complete.
"""
import argparse
import json
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from newsjockey.storage import ROOT, connect, now
from newsjockey.windows import date_window
from newsjockey.recipes import active_recipes
from newsjockey.page_cache import load_page
from newsjockey.network import fetch
from newsjockey.body_parser import parse_article
from bs4 import BeautifulSoup

def audit(database,output,sample_size=5,cached_only=False):
    started=time.monotonic()
    prior=json.loads((output/'audit.json').read_text()) if cached_only and (output/'audit.json').exists() else None
    previous={s['id']:s for p in prior['publishers'] for s in p['samples']} if prior else {}
    with connect(database) as db:
        window=prior['window'] if prior else date_window(db);recipes=active_recipes(db)
        publishers=[dict(r) for r in db.execute('SELECT id,name,identity_key FROM publishers ORDER BY name')]
        samples={p['id']:[dict(r) for r in db.execute('''SELECT id,url,resolved_url,title,feed_content,content_type,extraction_status
            FROM articles WHERE publisher_id=? AND duplicate_of IS NULL AND discovery_rejected=0
            AND published_at>=? AND published_at<? ORDER BY published_at DESC,id DESC LIMIT ?''',
            (p['id'],window['start'],window['end'],sample_size))] for p in publishers}
        if prior:
            samples={p['id']:[dict(db.execute('SELECT id,url,resolved_url,title,feed_content,content_type,extraction_status FROM articles WHERE id=?',(s['id'],)).fetchone()) for s in p['samples']] for p in prior['publishers']}
    def inspect(publisher):
        findings=[];cooldown=None
        for article in samples[publisher['id']]:
            result={'id':article['id'],'url':article['url'],'title':article['title']};begin=time.monotonic()
            body=load_page(database,article['id'],'http');url=article['resolved_url'] or article['url']
            origin='cache' if body else 'http'
            retained=output/'pages'/(str(article['id'])+'.html.gz')
            if body is None and retained.exists():
                import gzip
                body=gzip.decompress(retained.read_bytes());origin='audit_cache'
            if body is None and article['id'] in previous and previous[article['id']]['outcome'] in ('blocked','failed'):
                findings.append({**previous[article['id']],'origin':'prior_http','note':'Retained prior request failure; no repeated request.'});continue
            if body is None and (cached_only or cooldown):
                result.update(outcome='not_sampled',reason=cooldown or 'No retained HTTP page cache.');findings.append(result);continue
            try:
                if body is None:
                    _,_,body,url=fetch(article['url'])
                    # Retain development evidence in the audit artifact, separate from production cache.
                    pages=output/'pages';pages.mkdir(parents=True,exist_ok=True)
                    import gzip
                    (pages/(str(article['id'])+'.html.gz')).write_bytes(gzip.compress(body))
                parsed=parse_article(body,url,article['feed_content'],recipes=recipes)
                text=parsed['content'];paragraphs=[x.get_text(' ',strip=True) for x in BeautifulSoup(parsed['body_html'],'html.parser').find_all('p')]
                result.update(outcome=parsed['extraction_status'],quality=parsed['body_quality'],method=parsed['extraction_method'],
                    recipe=parsed['recipe_id'],words=len(text.split()),paragraphs=len(paragraphs),notes=parsed['body_notes'],
                    content_type=parsed['content_type'],images=len(json.loads(parsed['images_json'])),
                    media=json.loads(parsed['media_formats']),origin=origin,resolved_url=url,
                    anchors=[paragraphs[i][:240] for i in sorted(set([0,len(paragraphs)//2,len(paragraphs)-1]))] if paragraphs else [])
                if result['outcome']=='blocked': cooldown='Publisher challenge; remaining uncached sample requests deferred.'
            except Exception as exc:
                error=str(exc);result.update(outcome='blocked' if any('HTTP '+str(code) in error for code in (401,403,429)) else 'failed',error=error)
                cooldown='Publisher request failed; remaining uncached sample requests deferred.'
            result['seconds']=round(time.monotonic()-begin,3);findings.append(result)
            if origin=='http' and not cooldown: time.sleep(2)
        return {**publisher,'requested':sample_size,'available_recent':len(samples[publisher['id']]),'samples':findings,
                'outcomes':dict(Counter(x['outcome'] for x in findings))}
    results=[]
    with ThreadPoolExecutor(max_workers=4) as executor:
        for future in as_completed([executor.submit(inspect,p) for p in publishers]):
            result=future.result();results.append(result)
            print(result['name'],result['outcomes'],flush=True)
    results.sort(key=lambda p:p['name'])
    totals=dict(Counter(s['outcome'] for p in results for s in p['samples']))
    report={'generated_at':now(),'window':window,'samples_per_publisher':sample_size,'totals':totals,
            'seconds':round(time.monotonic()-started,2),'publishers':results,
            'limitation':'Structural parser audit; no independent completeness rating or measured >90% guarantee. Cached pages can predate this audit.'}
    output.mkdir(parents=True,exist_ok=True);(output/'audit.json').write_text(json.dumps(report,indent=2))
    lines=['# Recent extraction sample audit','',f"- Generated: {report['generated_at']}",
        f"- Date window: {window['yesterday']} + {window['today']} ({window['timezone']}).",
        f'- Target: {sample_size} recent articles per publisher; fewer where current inventory is smaller.',
        '- Ordinary public HTTP and retained local HTML only. No Chrome, paid services, or model calls.',
        '- Failed/challenged hosts stop further uncached sample requests.',
        '- Structural results are not independently verified completeness. No >90% claim.',
        '', '| Publisher | Recent samples | Complete | Partial | Blocked | Restricted | Failed | Other / deferred |',
        '|---|---:|---:|---:|---:|---:|---:|---:|']
    for p in results:
        o=p['outcomes'];other=sum(n for k,n in o.items() if k not in ('complete','partial','blocked','paywalled','failed'))
        lines.append('| '+p['name']+' | '+ ' | '.join(str(n) for n in (p['available_recent'],o.get('complete',0),o.get('partial',0),o.get('blocked',0),o.get('paywalled',0),o.get('failed',0),other))+' |')
    lines+=['','- Totals: '+json.dumps(totals),'- Detailed URLs, provenance, paragraph anchors, and reasons: `audit.json`.']
    (output/'audit.md').write_text('\n'.join(lines)+'\n');return report

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',type=Path,default=ROOT/'data/newsjockey.sqlite3')
    parser.add_argument('--output',type=Path,default=ROOT/'artifacts/extraction-audit')
    parser.add_argument('--samples',type=int,choices=range(5,11),default=5)
    parser.add_argument('--cached-only',action='store_true')
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    audit(args.database,args.output,args.samples,args.cached_only)
