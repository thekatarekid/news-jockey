"""Versioned, declarative publisher recipes. No executable model output."""
import fnmatch
import json
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit
from bs4 import BeautifulSoup
from lxml import etree, html as lhtml
import soupsieve

COMMON_REMOVE = ['aside', 'nav', 'footer', '.related', '.related-content', '.newsletter',
                 '.advertisement', '[data-ad]', '.social-share', '.share-tools', '.recommended']
BODY_SELECTORS = {
 '9to5mac.com': ['.post-content', '.entry-content'], 'abcnews.go.com': ['.FITT_Article_main__body', '.Article__Content', '[data-testid=prism-article-body]'],
 'arstechnica.com': ['.article-content', '.post-content', '.article-body'],
 'awfulannouncing.com': ['.entry-content'], 'billboard.com': ['.a-content', '.article-content'],
 'bloomberg.com': ['.body-content', '[data-component=body]'],
 'businessinsider.com': ['.content-lock-content', '.article-body', '.body-content'],
 'cbsnews.com': ['.content__body', '.ArticleBody', '.article__body'],
 'cbssports.com': ['.Article-bodyContent', '.Article-body'],
 'cnbc.com': ['.ArticleBody-articleBody'], 'cnet.com': ['.c-pageArticle_body', '.article-body'],
 'collider.com': ['.article-body', '.content-block-regular'], 'consequence.net': ['.entry-content'],
 'deadline.com': ['.a-content', '.entry-content'], 'engadget.com': ['.caas-body', '.article-text'],
 'entrepreneur.com': ['.art-v2-body', '.article-body', '[itemprop=articleBody]'],
 'eonline.com': ['.article-body', '.post-body'], 'espn.com': ['.article-body', '.story-body'],
 'fastcompany.com': ['.article-body', '.post-content'], 'finance.yahoo.com': ['.caas-body'],
 'forbes.com': ['.article-body', '.body-container'], 'fortune.com': ['.article-body', '.paywall-content'],
 'foxnews.com': ['.article-body'], 'gizmodo.com': ['.entry-content', '.post-content'],
 'hollywoodreporter.com': ['.a-content', '.article-content'], 'inc.com': ['.article-body', '.standard-html'],
 'kiplinger.com': ['#article-body', '.article-body'], 'marketwatch.com': ['.article__body'],
 'mashable.com': ['.article-content', '.article-body'], 'nbcnews.com': ['.article-body__content'],
 'nbcsports.com': ['.RichTextArticleBody', '.article-body'], 'npr.org': ['#storytext', '.storytext'],
 'nytimes.com': ['section[name=articleBody]', '[name=articleBody]'],
 'pbs.org': ['.body-text', '.story-body', '.entry-content'], 'politico.com': ['.story-text', '.story-text__paragraph'],
 'propublica.org': ['.article-body', '.story-body'], 'rollingstone.com': ['.a-content'],
 'sbnation.com': ['.c-entry-content', '[data-chorus-optimize-field=body]'],
 'seekingalpha.com': ['[data-test-id=article-content]', '.article-content'], 'si.com': ['.article-body', '.m-detail--body'],
 'sportico.com': ['.a-content'], 'sportingnews.com': ['.article-body', '.field--name-body'],
 'sports.yahoo.com': ['.caas-body'], 'sportsnaut.com': ['.entry-content'], 'techcrunch.com': ['.entry-content', '.wp-block-post-content'],
 'technologyreview.com': ['.article__body', '.article-body'], 'thehill.com': ['.article__text', '.article-body', '.entry-content'],
 'theregister.com': ['#body', '.article_body'], 'theverge.com': ['.duet--article--article-body-component', '.c-entry-content'],
 'thewrap.com': ['.entry-content', '.article-body'], 'tmz.com': ['.article__content', '.article-body'],
 'variety.com': ['.a-content'], 'wired.com': ['.body__inner-container', '[data-testid=BodyWrapper]'],
 'wsj.com': ['.article-content', '[data-type=article-body]'], 'zdnet.com': ['.article-body', '.story-body']}

def validate_definition(value):
    if not isinstance(value, dict): raise ValueError('Recipe must be a JSON object.')
    if not re.fullmatch(r'[a-zA-Z0-9._-]{1,100}', str(value.get('id', ''))): raise ValueError('Recipe ID must contain letters, numbers, dots, underscores, or hyphens.')
    if not isinstance(value.get('hosts'), list) or not 1 <= len(value['hosts']) <= 20:
        raise ValueError('Recipe needs 1–20 hostnames.')
    if any(not isinstance(h, str) or not re.fullmatch(r'[a-z0-9.*-]{1,253}', h) for h in value['hosts']): raise ValueError('Invalid recipe hostname.')
    result = {k: value[k] for k in ('id', 'hosts')}
    for key in ('include', 'remove', 'match_any'):
        selectors = value.get(key, [])
        if not isinstance(selectors, list) or len(selectors) > 30: raise ValueError('Use at most 30 selectors per list.')
        for selector in selectors:
            if not isinstance(selector, str) or len(selector) > 2048: raise ValueError('Invalid selector.')
            try:
                if selector.startswith('xpath:'): etree.XPath(selector[6:])
                else: soupsieve.compile(selector)
            except Exception as exc: raise ValueError('Invalid CSS/XPath selector: '+selector[:100]) from exc
        result[key] = selectors
    if not result['include']: raise ValueError('At least one body selector is required.')
    path = value.get('path', '*')
    if not isinstance(path, str) or len(path) > 500: raise ValueError('Invalid URL path pattern.')
    for field in ('priority','wait_ms','expected_min_paragraphs'):
        if field in value and type(value[field]) is not int: raise ValueError(field+' must be an integer.')
    result.update(path=path, template=str(value.get('template', 'article'))[:80], priority=int(value.get('priority', 50)),
                  provenance=str(value.get('provenance', 'Local recipe'))[:1000])
    metadata = value.get('metadata', {})
    if not isinstance(metadata, dict) or set(metadata) - {'title', 'author', 'published_raw', 'updated_raw', 'canonical_url'}:
        raise ValueError('Invalid metadata fields.')
    for field, spec in metadata.items():
        if not isinstance(spec, dict) or not isinstance(spec.get('selector'), str): raise ValueError('Metadata needs selector and optional attribute.')
        selector = spec['selector']
        if len(selector) > 2048: raise ValueError('Metadata selector too long.')
        try:
            if selector.startswith('xpath:'): etree.XPath(selector[6:])
            else: soupsieve.compile(selector)
        except Exception as exc: raise ValueError('Invalid metadata selector.') from exc
        if spec.get('attribute') and not re.fullmatch(r'[\w:-]{1,80}', str(spec['attribute'])): raise ValueError('Invalid metadata attribute.')
    result['metadata'] = metadata
    wait = value.get('wait_selector', '')
    if wait:
        if not isinstance(wait, str) or len(wait)>500: raise ValueError('Invalid browser wait selector.')
        try: soupsieve.compile(wait)
        except Exception as exc: raise ValueError('Invalid browser wait selector.') from exc
    result['wait_selector'] = wait
    result['wait_ms'] = max(0, min(5000, int(value.get('wait_ms', 1500))))
    result['expected_min_paragraphs'] = max(2, min(30, int(value.get('expected_min_paragraphs', 2))))
    alternate = value.get('public_alternate', '')
    if alternate:
        if not isinstance(alternate, str) or len(alternate)>2048 or not alternate.startswith('https://'): raise ValueError('Alternate must be a public HTTPS URL template.')
        alternate_host=urlsplit(alternate).hostname
        if not alternate_host or not any(host_matches(alternate_host,h) for h in result['hosts']): raise ValueError('Alternate must stay on a recipe hostname.')
        if urlsplit(alternate).username or urlsplit(alternate).password: raise ValueError('Alternate cannot contain credentials.')
    result['public_alternate'] = alternate
    return result

def host_matches(host, pattern):
    host=host.lower().removeprefix('www.')
    return pattern=='*' or host==pattern or (pattern.startswith('*.') and (host==pattern[2:] or host.endswith('.'+pattern[2:])))

def seed_recipes(db):
    stamp=datetime.now(timezone.utc).isoformat(timespec='seconds')
    existing={r['id'] for r in db.execute('SELECT DISTINCT id FROM extraction_recipes')}
    definitions=[dict(id=host+'-article',hosts=[host],include=selectors,remove=COMMON_REMOVE,
                      template='article',provenance='Candidate selectors; completeness checked per article. Not a measured coverage guarantee.')
                 for host,selectors in BODY_SELECTORS.items()]
    definitions.append(dict(id='generic-live-blog',hosts=['*'],path='*',include=['[itemtype*=LiveBlogPosting]', '.liveblog', '[class*=live-blog]'],
        match_any=['[itemtype*=LiveBlogPosting]', '.liveblog', '[class*=live-blog]'],remove=COMMON_REMOVE,priority=80,template='live_blog'))
    for definition in definitions:
        if definition['id']=='abcnews.go.com-article': definition['hosts'].append('abcnews.com')
        if definition['id']=='9to5mac.com-article':
            definition['hosts'].append('9to5google.com')
            definition['remove']=[*COMMON_REMOVE,'.ad-disclaimer-container','.disclaimer-affiliate']
        if definition['id'] in existing: continue
        definition=validate_definition(definition)
        db.execute('INSERT INTO extraction_recipes(id,version,definition,active,created_at,validation) VALUES(?,1,?,1,?,?)',
            (definition['id'],json.dumps(definition),stamp,json.dumps({'state':'seed','note':'Candidate; not manually validated on every current layout.'})))

def active_recipes(db):
    return [{**json.loads(r['definition']),'version':r['version']} for r in db.execute('SELECT * FROM extraction_recipes WHERE active=1')]

def select_html(soup, selector):
    if selector.startswith('xpath:'):
        tree=lhtml.fromstring(str(soup))
        return [lhtml.tostring(node,encoding='unicode') if isinstance(node,etree._Element) else str(node) for node in tree.xpath(selector[6:])]
    nodes=soup.select(selector)
    return [str(n) for n in nodes if not any(parent in nodes for parent in n.parents)]

def recipe_matches(recipe,url,soup=None):
    if not any(host_matches(urlsplit(url).hostname or '',h) for h in recipe['hosts']): return False
    if not fnmatch.fnmatch(urlsplit(url).path,recipe.get('path','*')): return False
    markers=recipe.get('match_any',[])
    return soup is None or not markers or any(select_html(soup,s) for s in markers)

def recipe_candidates(soup,url,recipes):
    for recipe in sorted(recipes,key=lambda r:r.get('priority',50),reverse=True):
        if not recipe_matches(recipe,url,soup): continue
        for selector in recipe['include']:
            fragments=select_html(soup,selector)
            if not fragments: continue
            selected=BeautifulSoup(''.join(fragments),'html.parser')
            for remove in recipe.get('remove',[]):
                if remove.startswith('xpath:'):
                    tree=lhtml.fromstring(str(selected))
                    for node in tree.xpath(remove[6:]):
                        if isinstance(node,etree._Element) and node.getparent() is not None: node.getparent().remove(node)
                    selected=BeautifulSoup(lhtml.tostring(tree,encoding='unicode'),'html.parser')
                else:
                    for node in list(selected.select(remove)):
                        if node.parent is not None: node.decompose()
            yield recipe,str(selected)
            break

def recipe_metadata(recipe,soup):
    fields={}
    for field,spec in recipe.get('metadata',{}).items():
        selector=spec['selector']
        if selector.startswith('xpath:'):
            found=select_html(soup,selector)
            if found: fields[field]=BeautifulSoup(found[0],'html.parser').get_text(' ',strip=True)
        else:
            element=soup.select_one(selector)
            if element: fields[field]=element.get(spec['attribute'],'') if spec.get('attribute') else element.get_text(' ',strip=True)
    return fields
