"""Inert rich article HTML and independent body-quality checks."""
import re
from collections import Counter
from urllib.parse import urljoin, urlsplit
import bleach
from bs4 import BeautifulSoup

TAGS = ['p', 'br', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'strong', 'b', 'em', 'i', 'u',
        's', 'blockquote', 'ul', 'ol', 'li', 'dl', 'dt', 'dd', 'pre', 'code', 'hr',
        'figure', 'figcaption', 'img', 'a', 'table', 'thead', 'tbody', 'tfoot', 'tr',
        'th', 'td', 'caption', 'sup', 'sub', 'div', 'span']
ATTRS = {'a': ['href', 'title', 'rel', 'target'], 'img': ['src', 'alt', 'title', 'loading',
         'referrerpolicy'], 'th': ['colspan', 'rowspan', 'scope'], 'td': ['colspan', 'rowspan']}

def sanitize_html(value, base=''):
    soup = BeautifulSoup(value or '', 'html.parser')
    for tag in list(soup.select('script,style,iframe,object,embed,form,input,button,svg,math,template,[hidden],[aria-hidden=true]')):
        if tag.parent is not None: tag.decompose()
    for tag in list(soup.find_all(style=True)):
        if tag.attrs and re.search(r'display\s*:\s*none|visibility\s*:\s*hidden', tag['style'], re.I):
            tag.decompose()
    for tag in soup.find_all(['a', 'img']):
        key = 'href' if tag.name == 'a' else 'src'
        raw = tag.get(key, '')
        if tag.name == 'img':
            raw = tag.get('data-src') or tag.get('data-original') or raw
            if (not raw or raw.startswith('data:')) and tag.get('srcset'):
                raw = tag['srcset'].split(',')[-1].strip().split()[0]
        url = urljoin(base, str(raw).strip())
        parts = urlsplit(url)
        if parts.scheme in ('http', 'https') and parts.hostname and not parts.username and not parts.password:
            tag[key] = url
        else:
            tag.attrs.pop(key, None)
        if tag.name == 'a': tag['target'] = '_blank'; tag['rel'] = 'noopener noreferrer'
        else: tag['loading'] = 'lazy'; tag['referrerpolicy'] = 'no-referrer'
    return bleach.clean(str(soup), tags=TAGS, attributes=ATTRS, protocols=['http', 'https'], strip=True, strip_comments=True)

def html_text(value):
    soup = BeautifulSoup(value or '', 'html.parser')
    for tag in soup.find_all(['br', 'p', 'h1', 'h2', 'h3', 'h4', 'li', 'blockquote', 'tr', 'figcaption']):
        tag.insert_after('\n\n')
    return '\n\n'.join(' '.join(x.split()) for x in soup.get_text(' ', strip=False).split('\n') if x.strip())

def text_html(text):
    from html import escape
    return ''.join('<p>' + escape(p) + '</p>' for p in (text or '').split('\n\n') if p.strip())

def words(text):
    return re.findall(r"[\w']+", (text or '').lower())

def assess_body(html, structured=False, expected=0, truncated=False, feed_text=''):
    text = html_text(html)
    soup = BeautifulSoup(html or '', 'html.parser')
    paras = [p.get_text(' ', strip=True) for p in soup.find_all('p') if len(words(p.get_text())) >= 5]
    notes = []
    if not text: return 'none', ['No readable body.']
    if truncated: notes.append('Publisher truncation marker or read-more link.')
    if expected and len(words(text)) < expected * .9: notes.append('Body falls short of publisher wordCount.')
    if feed_text and len(words(feed_text)) > len(words(text)) * 1.15: notes.append('Feed contains substantially more text.')
    if len(paras) < 2: notes.append('Insufficient paragraph structure; may be a valid short item.')
    if not structured: notes.append('Article-body structure is unconfirmed.')
    # Word count is a shortfall signal, never a sufficient completeness test.
    return ('partial' if notes else 'full'), notes or ['Paragraph/structure/truncation checks passed; not publisher-verified.']

def feed_matches_page(feed_html, page_html):
    """Require paragraph retention plus first/last anchors, not merely a long feed."""
    page = html_text(page_html); feed = html_text(feed_html)
    normalized = ' '.join(words(feed))
    paragraphs = [words(p.get_text(' ', strip=True)) for p in BeautifulSoup(page_html, 'html.parser').find_all('p')]
    paragraphs = [p for p in paragraphs if len(p) >= 5]
    page_words = words(page)
    if len(paragraphs) < 2 or not page_words or not normalized: return False
    retained = sum(' '.join(p) in normalized for p in paragraphs) / len(paragraphs)
    recall = sum((Counter(page_words) & Counter(words(feed))).values()) / len(page_words)
    return retained >= .95 and recall >= .95 and all(' '.join(p) in normalized for p in (paragraphs[0][:10], paragraphs[-1][-10:]))
