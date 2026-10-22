"""Publisher-supplied labels and media; no model-generated content."""
import json
import re
from urllib.parse import urlsplit
from bs4 import BeautifulSoup
from .network import article_url, clean_text

TYPE_ALIASES = {
    'reporting': 'reporting', 'news': 'reporting', 'news report': 'reporting', 'newsarticle': 'reporting',
    'editorial': 'editorial', 'editorials': 'editorial', 'opinion': 'opinion', 'opinions': 'opinion',
    'commentary': 'opinion', 'analysis': 'analysis', 'review': 'review', 'reviews': 'review',
    'interview': 'interview', 'interviews': 'interview', 'explainer': 'explainer', 'explainers': 'explainer',
    'live': 'live_blog', 'live blog': 'live_blog', 'liveblogposting': 'live_blog',
    'sponsored': 'sponsored', 'advertorial': 'sponsored', 'paid content': 'sponsored',
}

def label_type(labels, url='', title='', schema_types=None):
    labels = [clean_text(str(x)) for x in labels if x]
    for value in labels:
        if value.lower() in TYPE_ALIASES and TYPE_ALIASES[value.lower()]!='reporting':
            return TYPE_ALIASES[value.lower()], value, 'Publisher label'
    for part in urlsplit(url).path.lower().strip('/').split('/'):
        if part in TYPE_ALIASES and part not in ('news', 'live'):
            return TYPE_ALIASES[part], part, 'Publisher URL section'
    match = re.match(r'^(editorial|opinion|analysis|review|interview|explainer)\s*[:|—-]', title, re.I)
    if match:
        return TYPE_ALIASES[match[1].lower()], match[1], 'Publisher headline label'
    types = [str(x).lower() for x in schema_types or []]
    for special, kind in [('opinionnewsarticle','opinion'), ('analysisnewsarticle','analysis'),
                           ('reviewnewsarticle','review'), ('review','review'),
                           ('liveblogposting','live_blog'), ('newsarticle','reporting')]:
        if special in types:
            return kind, special, 'Publisher structured data'
    for value in labels:
        if value.lower() in TYPE_ALIASES:
            return TYPE_ALIASES[value.lower()], value, 'Publisher label'
    return 'unknown', ', '.join(labels) or None, 'No explicit editorial label'

def values(value):
    return value if isinstance(value, list) else [value] if value else []

def json_objects(soup):
    objects = []
    def walk(value):
        if isinstance(value, list):
            for item in value: walk(item)
        elif isinstance(value, dict):
            objects.append(value)
            for key in ('@graph', 'mainEntity', 'subjectOf', 'video', 'associatedMedia', 'image', 'author'):
                if isinstance(value.get(key), (dict, list)): walk(value[key])
    for script in soup.find_all('script', type=re.compile('ld\\+json', re.I)):
        try: walk(json.loads(script.string or script.get_text()))
        except (ValueError, TypeError): pass
    return objects

def article_root(soup):
    """Prefer the headline's article/main over an earlier related-story card."""
    headline = soup.find('h1')
    if headline:
        for parent in headline.parents:
            if parent.name in ('article', 'main'):
                return parent
    return soup.find('main') or soup.find('article')

def metadata(soup, base, feed=None):
    objects = json_objects(soup)
    meta = {}
    for m in soup.find_all('meta'):
        key = m.get('property') or m.get('name') or m.get('itemprop')
        if key and m.get('content'): meta[key.lower()] = m['content']
    article_objects = [o for o in objects if any(str(t).lower() in
        ('article','newsarticle','opinionnewsarticle','analysisnewsarticle','reviewnewsarticle','review','blogposting','liveblogposting') for t in values(o.get('@type')))]
    article = article_objects[0] if article_objects else {}
    labels = values(article.get('articleSection')) + values(article.get('genre'))
    labels += [meta.get(x) for x in ('article:section','parsely-section','content-type','article:type')]
    for el in soup.select('[class*=eyebrow], [class*=kicker], [class*=article-type], [data-testid*=article-type]')[:12]:
        label=clean_text(el.get_text(' ',strip=True))
        if label.lower() in TYPE_ALIASES: labels.append(label)
    if feed:
        labels += [x.get('term') for x in feed.get('tags', [])]
    title = clean_text(article.get('headline') or meta.get('og:title') or (soup.h1.get_text() if soup.h1 else ''))
    types = values(article.get('@type'))
    kind, raw, basis = label_type(labels, base, title, types)
    images, media = [], []
    def add_image(url, caption='', credit='', role='article', license_url=None):
        url = article_url(url, base) if isinstance(url, str) else ''
        if not url: return
        existing = next((x for x in images if x['url'] == url), None)
        image = dict(url=url, caption=clean_text(caption), credit=clean_text(credit), role=role,
                     source_url=base, license=license_url)
        if existing:
            for k,v in image.items():
                if v and not existing.get(k): existing[k] = v
        else: images.append(image)
    def add_media(url, kind, **extra):
        url = article_url(url, base)
        if not url: return
        if not any(x['url'] == url and x['kind'] == kind for x in media):
            media.append(dict(url=url, kind=kind, **extra))
    for field in ('og:image','og:image:secure_url','twitter:image','twitter:image:src'):
        if meta.get(field): add_image(meta[field], meta.get('og:image:alt',''))
    for obj in article_objects:
        for img in values(obj.get('image')):
            if isinstance(img, str): add_image(img)
            elif isinstance(img, dict):
                add_image(img.get('contentUrl') or img.get('url'), img.get('caption',''),
                          img.get('creditText') or img.get('copyrightNotice') or '', license_url=img.get('license'))
    for obj in objects:
        if any(str(t).lower() == 'imageobject' for t in values(obj.get('@type'))):
            add_image(obj.get('contentUrl') or obj.get('url'), obj.get('caption',''), obj.get('creditText') or '', license_url=obj.get('license'))
        if any(str(t).lower() in ('videoobject','audioobject') for t in values(obj.get('@type'))):
            mkind = 'video' if any(str(t).lower() == 'videoobject' for t in values(obj.get('@type'))) else 'audio'
            add_media(obj.get('contentUrl') or obj.get('embedUrl') or base, mkind,
                      embed_url=article_url(obj.get('embedUrl'), base) if obj.get('embedUrl') else None,
                      title=clean_text(obj.get('name','')), duration=obj.get('duration'),
                      description=clean_text(obj.get('description','')),
                      transcript=clean_text(obj.get('transcript','')),
                      source_url=base)
            for thumb in values(obj.get('thumbnailUrl')): add_image(thumb, role='video_thumbnail')
    root = article_root(soup) or soup
    for img in root.find_all('img'):
        parent = img.find_parent('figure')
        caption = parent.find('figcaption') if parent else None
        credit = parent.select_one('[class*=credit], [class*=copyright]') if parent else None
        src = img.get('data-src') or img.get('data-original') or img.get('src')
        if not src and img.get('srcset'): src = img['srcset'].split(',')[-1].strip().split(' ')[0]
        add_image(src, caption.get_text(' ',strip=True) if caption else img.get('alt',''), credit.get_text(' ',strip=True) if credit else '')
    for video in root.find_all(['video','audio']):
        if video.get('poster'): add_image(video['poster'], role='video_thumbnail')
        for el in [video] + video.find_all('source'):
            if el.get('src'): add_media(el['src'], video.name, source_url=base)
        for track in video.find_all('track', src=True):
            add_media(track['src'], 'captions', language=track.get('srclang'), source_url=base)
    for frame in root.find_all('iframe', src=True):
        host = urlsplit(article_url(frame['src'],base)).hostname or ''
        if any(host == h or host.endswith('.'+h) for h in ('youtube.com','youtube-nocookie.com','youtu.be','vimeo.com','dailymotion.com','jwplayer.com','brightcove.net','player.cnbc.com')):
            add_media(frame['src'], 'video', embed_url=article_url(frame['src'],base), source_url=base)
    for field in ('og:video','og:video:url','og:video:secure_url'):
        if meta.get(field): add_media(meta[field], 'video', source_url=base)
    if feed:
        for m in feed.get('media_content',[]) + feed.get('enclosures',[]):
            url = m.get('url') or m.get('href')
            typ = (m.get('type') or m.get('medium') or '').lower()
            if typ.startswith('image'): add_image(url)
            elif typ.startswith(('video','audio')):
                add_media(url,'video' if typ.startswith('video') else 'audio', duration=m.get('duration'), source_url=base)
        for img in feed.get('media_thumbnail',[]): add_image(img.get('url'), role='video_thumbnail')
        for img in feed.get('media_credit',[]):
            for row in images:
                if not row['credit']: row['credit'] = clean_text(img.get('content',''))
        for frame in media:
            if frame['kind'] in ('video','audio') and not frame.get('title'): frame['title'] = clean_text(feed.get('title',''))
    authors = []
    for a in values(article.get('author')):
        if isinstance(a,str): authors.append(a)
        elif isinstance(a,dict) and a.get('name'): authors.append(a['name'])
    author = clean_text(', '.join(authors) or meta.get('author') or meta.get('article:author') or '')
    canonical = soup.find('link', rel=lambda v: v and 'canonical' in v)
    language = soup.html.get('lang','') if soup.html else ''
    language = language or article.get('inLanguage') or meta.get('og:locale','').split('_')[0]
    transcript = '\n\n'.join(x.get('transcript','') for x in media if x.get('transcript'))
    return dict(title=title, author=author, canonical_url=article_url(canonical.get('href'),base) if canonical else base,
                content_type=kind, publisher_content_label=raw, content_type_basis=basis,
                images=images, media=media, language=str(language),
                published_raw=article.get('datePublished') or meta.get('article:published_time'),
                updated_raw=article.get('dateModified') or meta.get('article:modified_time'),
                transcript=transcript, transcript_status='available' if transcript else 'referenced' if any(x['kind']=='captions' for x in media) else 'unavailable',
                structured_article=bool(article_objects), schema_types=types,
                paywall=any(o.get('isAccessibleForFree') in (False, 'false', 'False') for o in article_objects))

def feed_metadata(entry, base, html):
    soup = BeautifulSoup(html or '', 'html.parser')
    info = metadata(soup, base, entry)
    kind, raw, basis = label_type([x.get('term') for x in entry.get('tags',[])], base, clean_text(entry.get('title','')))
    info.update(content_type=kind, publisher_content_label=raw, content_type_basis=basis)
    formats = []
    if clean_text(html): formats.append('text')
    for m in info['media']:
        if m['kind'] in ('video','audio') and m['kind'] not in formats: formats.append(m['kind'])
    if len(info['images']) > 1: formats.append('image_gallery')
    if not formats: formats=['text']
    return dict(content_type=kind, publisher_content_label=raw, content_type_basis=basis,
                media_formats=json.dumps(formats), media_json=json.dumps(info['media']), images_json=json.dumps(info['images']))
