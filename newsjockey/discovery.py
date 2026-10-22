"""Bounded broad feed/sitemap discovery, with explicit coverage gaps."""
import gzip
import hashlib
import json
import re
from datetime import datetime, timezone, timedelta
from urllib.parse import urljoin, urlsplit
from bs4 import BeautifulSoup
from defusedxml import ElementTree as ET
from .network import fetch, parse_feed, clean_text, article_url
from .storage import connect, add_source, now, upsert_article

# Candidates are validated on the publisher before subscription; these are discovery hints, not promises.
FEED_HINTS = {
 'nytimes.com': [('Top stories','https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml')] +
     [(x,'https://rss.nytimes.com/services/xml/rss/nyt/'+x+'.xml') for x in
      ('World','US','Politics','Business','Technology','Science','Health','Sports','Arts','Movies','Television','Music','Books','FashionandStyle','Travel','Food','RealEstate','Education','Opinion','Climate','SundayReview','Automobiles')],
 'cbsnews.com': [(x,'https://www.cbsnews.com/latest/rss/'+('main' if x=='All news' else x.lower().replace(' ','-'))) for x in
     ('All news','US','World','Politics','Health','Moneywatch','Science','Technology','Entertainment','Sports')],
 'abcnews.go.com': [(x,'https://abcnews.go.com/abcnews/'+y) for x,y in
     [('Top stories','topstories'),('US','usheadlines'),('International','internationalheadlines'),('Business','moneyheadlines'),('Entertainment','entertainmentheadlines'),('Health','healthheadlines'),('Technology','technologyheadlines'),('Sports','sportsheadlines')]],
 'foxnews.com': [(x,'https://moxie.foxnews.com/google-publisher/'+y+'.xml') for x,y in
     [('Latest','latest'),('US','us'),('World','world'),('Politics','politics'),('Science','science'),('Tech','tech'),('Health','health'),('Entertainment','entertainment'),('Sports','sports'),('Opinion','opinion')]],
 'nbcnews.com': [(x,'https://feeds.nbcnews.com/nbcnews/public/'+y) for x,y in
     [('Top stories','news'),('US','us-news'),('World','world'),('Politics','politics'),('Business','business'),('Technology','tech'),('Health','health'),('Science','science'),('Culture','pop-culture')]],
 'npr.org': [(x,'https://feeds.npr.org/'+str(y)+'/rss.xml') for x,y in
     [('News',1001),('World',1004),('US',1003),('Politics',1014),('Business',1006),('Science',1007),('Health',1128),('Technology',1019),('Arts',1008),('Music',1039),('Sports',1055)]],
 'pbs.org': [(x,'https://www.pbs.org/newshour/feeds/rss/'+y) for x,y in
     [('Headlines','headlines'),('Politics','politics'),('World','world'),('Nation','nation'),('Economy','economy'),('Science','science'),('Health','health'),('Arts','arts')]],
 'politico.com': [(x,'https://rss.politico.com/'+y+'.xml') for x,y in
     [('Politics','politics-news'),('Congress','congress'),('White House','whitehouse'),('Economy','economy'),('Defense','defense'),('Energy','energy'),('Technology','technology')]],
 'wsj.com': [(x,'https://feeds.content.dowjones.io/public/rss/'+y) for x,y in
     [('World','WSJcomWorldNews'),('US','WSJcomUSBusiness'),('Markets','WSJcomMarkets'),('Tech','WSJcomTech'),('Lifestyle','WSJcomLifestyle')]],
 'bloomberg.com': [(x,'https://feeds.bloomberg.com/'+y+'/news.rss') for x,y in
     [('Markets','markets'),('Politics','politics'),('Technology','technology'),('Economics','economics'),('Business','business'),('Industries','industries')]],
 'cnbc.com': [('Top news','https://www.cnbc.com/id/100003114/device/rss/rss.html'),('World','https://www.cnbc.com/id/100727362/device/rss/rss.html')],
 'forbes.com': [('All articles','https://www.forbes.com/real-time/feed/')],
 'businessinsider.com': [('All articles','https://www.businessinsider.com/rss')],
 'washingtonpost.com': [('Top stories','https://feeds.washingtonpost.com/rss/world')],
}

def non_article_url(url):
    path=urlsplit(url).path.lower()
    return bool(re.search(r'/(authors?|contributors?|tags?|category|categories|sections?|players?|teams?|standings|schedules|scores|stats|account|subscribe|subscription|privacy|terms|contact)(/|$)',path))

def loc(element):
    return next((x.text.strip() for x in element if x.tag.rsplit('}',1)[-1]=='loc' and x.text), '')

def sitemap_tree(body):
    if body.startswith(b'\x1f\x8b'):
        import io
        with gzip.GzipFile(fileobj=io.BytesIO(body)) as f: body=f.read(4*1024*1024+1)
        if len(body)>4*1024*1024: raise ValueError('Expanded sitemap exceeds 4 MB.')
    root=ET.fromstring(body)
    if root.tag.rsplit('}',1)[-1] not in ('urlset','sitemapindex'):
        raise ValueError('Not a sitemap.')
    return root

def sitemap_items(url, body, cursor=0, depth=0, visited=None, budget=None):
    visited=visited if visited is not None else set()
    budget=budget if budget is not None else {'remaining': 20}
    if url in visited:
        return [],cursor+1,'Sitemap cycle skipped.'
    visited.add(url)
    root=sitemap_tree(body)
    warnings=[]
    nested_results=[]
    if root.tag.rsplit('}',1)[-1]=='sitemapindex':
        children=[loc(x) for x in root if loc(x)]
        # Rotate bounded work through every child instead of silently ignoring sections.
        selected=[children[(cursor*4+i)%len(children)] for i in range(min(4,len(children)))] if children else []
        entries=[]
        for child in selected:
            try:
                if budget['remaining']<=0:
                    warnings.append('Sitemap traversal reached the 20-file request limit; remaining inventories rotate on later checks.')
                    break
                budget['remaining']-=1
                _,_,data,_=fetch(child)
                tree=sitemap_tree(data)
                if tree.tag.rsplit('}',1)[-1]=='sitemapindex':
                    if depth>=2:
                        warnings.append('Deep sitemap index requires a separate discovery channel: '+child)
                        continue
                    cycles=max(1,(len(children)+3)//4)
                    nested,_,warning=sitemap_items(child,data,cursor//cycles,depth+1,visited,budget)
                    nested_results.extend(nested)
                    if warning: warnings.append(warning)
                    continue
                entries.extend(list(tree))
            except Exception as exc: warnings.append(str(exc)[:160])
        if len(children)>4: warnings.append(f'Rotating through {len(children)} sitemap files, four per check; coverage is not instantaneous.')
        next_cursor=cursor+1
    else:
        entries=list(root); next_cursor=0
    result=[(item.get('published',''),item) for item in nested_results]
    cutoff=(datetime.now(timezone.utc)-timedelta(days=7)).date().isoformat()
    for entry in entries:
        url_value=article_url(loc(entry),url)
        if not url_value or non_article_url(url_value): continue
        tags={x.tag.rsplit('}',1)[-1]:x.text for x in entry.iter() if x.text}
        language=tags.get('language','en')
        if language and not language.lower().startswith('en'): continue
        date=tags.get('publication_date') or tags.get('lastmod') or ''
        if date and date[:10]<cutoff: continue
        title=clean_text(tags.get('title')) or urlsplit(url_value).path.strip('/').split('/')[-1].replace('-',' ') or url_value
        result.append((date,dict(link=url_value,id=url_value,title=title,published=tags.get('publication_date',''))))
    result.sort(key=lambda x:x[0],reverse=True)
    if len(result)>1000: warnings.append(f'{len(result)} recent/undated URLs found; capped at 1,000 this check. Inventory may be incomplete.')
    return [x[1] for x in result[:1000]],next_cursor,' '.join(warnings) or None

def expand_publisher(path, publisher_id):
    with connect(path) as db:
        publisher=db.execute('SELECT * FROM publishers WHERE id=?',(publisher_id,)).fetchone()
        if not publisher: return {'publisher_id':publisher_id,'error':'Publisher not found.'}
        existing={x['feed_url'] for x in db.execute('SELECT feed_url FROM sources WHERE publisher_id=?',(publisher_id,))}
        db.execute("UPDATE publishers SET coverage_status='Scanning' WHERE id=?",(publisher_id,))
    candidates=list(FEED_HINTS.get(publisher['identity_key'],[]))
    sitemaps=[]; errors=[]
    origin=urlsplit(publisher['website'])
    root=f'{origin.scheme}://{origin.netloc}/'
    try:
        _,_,body,resolved=fetch(publisher['website'])
        soup=BeautifulSoup(body,'html.parser')
        for tag in soup.find_all('link',href=True):
            if tag.get('type','').lower() in ('application/rss+xml','application/atom+xml','application/rdf+xml'):
                candidates.append((tag.get('title') or 'Advertised feed',urljoin(resolved,tag['href'])))
        # Publisher feed-directory links often expose additional section feeds.
        directory=next((urljoin(resolved,a['href']) for a in soup.find_all('a',href=True)
                        if re.search(r'/(rss|feeds)(/|\.|$)',a['href'],re.I)),None)
        if directory and directory!=publisher['website']:
            try:
                _,_,directory_body,directory_url=fetch(directory)
                dsoup=BeautifulSoup(directory_body,'html.parser')
                for a in dsoup.find_all(['a','link'],href=True):
                    if re.search(r'(rss|atom|feed|\.xml)',a['href'],re.I):
                        candidates.append((clean_text(a.get_text()) or a.get('title') or 'Section feed',urljoin(directory_url,a['href'])))
            except Exception as exc: errors.append('Feed directory: '+str(exc)[:100])
    except Exception as exc: errors.append('Homepage: '+str(exc)[:100])
    try:
        _,_,robots,_=fetch(urljoin(root,'robots.txt'))
        sitemaps=re.findall(r'^Sitemap:\s*(https?://\S+)',robots.decode('utf-8','replace'),re.I|re.M)
    except Exception as exc: errors.append('Sitemap discovery: '+str(exc)[:100])
    if not candidates:
        candidates=[('General feed candidate',urljoin(root,p)) for p in ('feed/','rss.xml','rss')]
    if not sitemaps: sitemaps=[urljoin(root,'sitemap.xml')]
    # News/current maps first; preserve and expose bounded inventory rather than claiming completeness.
    sitemaps.sort(key=lambda x:('news' not in x.lower(),x))
    hints=list(dict((url,(section,url)) for section,url in candidates).values())
    if len(hints)>40: errors.append('Feed candidate scan capped at 40; additional channels can be imported.')
    added=[]
    for section,url in hints[:40]:
        if url in existing: continue
        try:
            _,_,data,resolved=fetch(url)
            parsed=parse_feed(data)
            if not parsed.entries: continue
            # Directory links to articles/HTML must not become fake feed subscriptions.
            with connect(path) as db:
                sid,created=add_source(db,dict(name=publisher['name']+' — '+section,category=publisher['category'],
                    website=publisher['website'],feed_url=resolved,publisher_id=publisher_id,section=section))
            if created: added.append(sid)
        except Exception as exc: errors.append(section+': '+str(exc)[:90])
    if len(sitemaps)>8: errors.append('Sitemap discovery capped at eight channels; additional advertised inventories need review.')
    for url in list(dict.fromkeys(sitemaps))[:8]:
        if url in existing: continue
        try:
            _,_,body,resolved=fetch(url)
            sitemap_tree(body)
            with connect(path) as db:
                sid,created=add_source(db,dict(name=publisher['name']+' — Sitemap',category=publisher['category'],
                    website=publisher['website'],feed_url=resolved,publisher_id=publisher_id,channel_type='sitemap',
                    section='All sections / recent inventory',interval_minutes=30))
            if created: added.append(sid)
        except Exception as exc: errors.append('Sitemap: '+str(exc)[:100])
    with connect(path) as db:
        channels=db.execute('SELECT COUNT(*) FROM sources WHERE publisher_id=?',(publisher_id,)).fetchone()[0]
        notes=f'{channels} discovery channels. Full publisher coverage cannot be verified; sitemap scans cover recent seven-day/undated inventory. '
        if errors: notes+='Gaps: '+'; '.join(errors)[:1800]
        db.execute('UPDATE publishers SET coverage_status=?,coverage_notes=?,last_discovery=?,next_discovery=? WHERE id=?',
            ('Partial coverage' if errors else 'Channels discovered',notes,now(),
             (datetime.now(timezone.utc)+timedelta(days=1)).isoformat(timespec='seconds'),publisher_id))
    return dict(publisher_id=publisher_id,name=publisher['name'],added=len(added),channels=channels,gaps=len(errors))
