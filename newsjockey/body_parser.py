"""Compare local extraction candidates and retain source HTML without rewriting."""
import json
import re
from urllib.parse import urlsplit
from bs4 import BeautifulSoup
import trafilatura
from readability import Document
from .content import sanitize_html, html_text, text_html, assess_body, words
from .metadata import metadata, json_objects, article_root
from .network import clean_text
from .discovery import non_article_url
from .recipes import recipe_candidates, recipe_metadata, COMMON_REMOVE

def active_paywall(soup):
    for element in soup.select('[class*=paywall], [id*=paywall], [data-testid*=paywall]'):
        inactive=False
        for parent in [element,*element.parents]:
            markers=' '.join(parent.get('class',[]))+' '+str(parent.get('id',''))
            if (parent.has_attr('hidden') or parent.get('aria-hidden')=='true'
                or re.search(r'display\s*:\s*none|visibility\s*:\s*hidden',parent.get('style',''),re.I)
                or re.search(r'hidepaywall|paywall[-_](?:open|unlocked|disabled|inactive|hidden)',markers,re.I)):
                inactive=True;break
        prompt=element.get_text(' ',strip=True)
        if not inactive and len(prompt)<1200 and re.search(r'\b(?:subscribe|subscription|subscribers?|sign\s*in|log\s*in|unlock|membership)\b',prompt,re.I): return True
    return False

def parse_article(body,url,feed_content='',existing_media=None,existing_images=None,recipes=None):
    if isinstance(body,bytes):
        try: body=body.decode('utf-8-sig')
        except UnicodeDecodeError: pass  # Let the HTML parser detect legacy encodings.
    soup=BeautifulSoup(body,'html.parser');info=metadata(soup,url)
    declared_restricted=info.pop('paywall');gated=active_paywall(soup)
    info['publisher_access']='restricted_marker' if declared_restricted else 'public'
    root=article_root(soup)
    readable=sanitize_html(str(root or soup),url)
    has_structure=bool(soup.find('article') or info['structured_article'] or soup.select('[itemprop=articleBody], [class*=article-body]'))
    expected=0
    for obj in json_objects(soup):
        try: expected=max(expected,int(obj.get('wordCount') or 0))
        except (ValueError,TypeError): pass
    incomplete=bool(soup.select('[class*=truncated], [data-truncated=true]'))
    candidates=[]
    def add(raw,method,structured,recipe=None):
        safe=sanitize_html(raw,url);text=html_text(safe)
        if not text: return
        quality,notes=assess_body(safe,structured,expected,incomplete,feed_content)
        if recipe and len(BeautifulSoup(safe,'html.parser').find_all('p'))<recipe.get('expected_min_paragraphs',2):
            quality='partial';notes.append('Recipe paragraph check failed.')
        # Prefer a complete recipe, then generic extractors, then a broad body root.
        priority=3 if recipe else 2 if method in ('trafilatura','readability','structured public body') else 1
        link_words=sum(len(words(x.get_text())) for x in BeautifulSoup(safe,'html.parser').find_all('a'))
        clutter=link_words/max(1,len(words(text)))
        if clutter>.6: quality='partial';notes.append('Body dominated by links/navigation.')
        score=({'none':0,'partial':1,'full':2}[quality],priority,-clutter,len(words(text)))
        candidates.append(dict(content=text,body_html=safe,body_quality=quality,body_notes=' '.join(notes),
            extraction_method=method,recipe_id=recipe['id'] if recipe else None,recipe_version=recipe.get('version') if recipe else None,
            score=score,recipe=recipe))
    for recipe,fragment in recipe_candidates(soup,url,recipes or []): add(fragment,'publisher recipe',True,recipe)
    try:
        raw=trafilatura.extract(str(soup),url=url,include_comments=False,include_tables=True,include_images=True,
             include_formatting=True,include_links=True,output_format='html',favor_recall=True)
        if raw: add(raw,'trafilatura',has_structure)
    except Exception: pass
    # Try alternatives even when the first extractor returns a partial body.
    try: add(Document(str(soup)).summary(),'readability',has_structure)
    except Exception: pass
    if root:
        cleaned=BeautifulSoup(str(root),'html.parser')
        for selector in COMMON_REMOVE:
            for el in list(cleaned.select(selector)):
                if el.parent is not None: el.decompose()
        add(str(cleaned),'article body root',has_structure)
    if not gated and not declared_restricted:
        for obj in json_objects(soup):
            if isinstance(obj.get('articleBody'),str): add(text_html(obj['articleBody']),'structured public body',True)
    best=max(candidates,key=lambda c:c['score']) if candidates else dict(content='',body_html='',body_quality='none',body_notes='No readable body.',extraction_method='local parsers',recipe_id=None,recipe_version=None,recipe=None)
    # A metadata subscription marker alone does not erase a body delivered in
    # ordinary visible HTML. Hidden/restricted structured text is never used.
    gated=gated or (declared_restricted and best['body_quality']!='full')
    if declared_restricted and not gated:
        best['body_notes']+=' Publisher metadata marks access as restricted; visible HTML was delivered without unlocking. Completeness remains heuristic.'
    text=best['content'];page_text=clean_text(str(soup)).lower()
    images=info.pop('images');media=info.pop('media')
    for image in existing_images or []:
        if not any(x['url']==image['url'] for x in images): images.append(image)
    for item in existing_media or []:
        if not any(x['url']==item['url'] and x['kind']==item['kind'] for x in media): media.append(item)
    formats=['text'] if text or feed_content else []
    for item in media:
        if item['kind'] in ('video','audio') and item['kind'] not in formats: formats.append(item['kind'])
    if len(images)>1: formats.append('image_gallery')
    blocked=any(x in page_text for x in ('verify you are human','checking your browser','access denied','enable javascript and cookies to continue','please enable js and disable any ad blocker'))
    video_landing='video' in formats and '/video/' in urlsplit(url).path and not soup.select('[itemprop=articleBody], [class*=article-body], [class*=body-content]')
    if non_article_url(url) and not info['structured_article']:
        status='unsupported';text='';best.update(body_html='',body_quality='none',body_notes='Profile, directory, or other non-article page.')
        info.update(content_type='other',content_type_basis='Non-article publisher page')
    elif blocked:
        info['publisher_access']='challenge'
        status='blocked';text='';best.update(body_html='',body_quality='none',body_notes='Publisher challenge/access page.')
    elif gated:
        info['publisher_access']='restricted'
        excerpt=soup.select_one('[class*=teaser], [class*=excerpt], [itemprop=description]')
        safe=sanitize_html(str(excerpt) if excerpt else '',url);text=html_text(safe)
        status='paywalled';best.update(body_html=safe,body_quality='partial' if text else 'none',body_notes='Publisher identifies restricted content; public excerpt/feed retained.')
    elif 'video' in formats and (not text or video_landing):
        status='not_applicable';text='';best.update(body_html='',body_quality='none',body_notes='Video-only item; transcript availability is separate.')
    elif not text: status='unsupported'
    else: status='complete' if best['body_quality']=='full' else 'partial'
    if best.get('recipe') and status not in ('blocked','paywalled'):
        info.update({k:v for k,v in recipe_metadata(best['recipe'],soup).items() if v})
    transcript=info.get('transcript') or '\n\n'.join(x.get('transcript','') for x in media if x.get('transcript'))
    info.update(content=text,body_html=best['body_html'],body_quality=best['body_quality'],body_notes=best['body_notes'],
        recipe_id=best['recipe_id'],recipe_version=best['recipe_version'],extraction_status=status,
        extraction_method=best['extraction_method'],extraction_notes=best['body_notes'],
        media_formats=json.dumps(formats or ['text']),media_json=json.dumps(media),images_json=json.dumps(images),
        transcript=transcript or None,transcript_status='available' if transcript else 'referenced' if any(x['kind']=='captions' for x in media) else 'unavailable')
    info.pop('structured_article',None);info.pop('schema_types',None)
    return info
