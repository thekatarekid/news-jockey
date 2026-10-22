"""Bounded requests to public HTTP(S) URLs for feeds, inventories, and article pages."""
import ipaddress
import socket
import time
import html
from urllib.parse import urlsplit, urljoin, urlunsplit, parse_qsl, urlencode
import requests
from bs4 import BeautifulSoup
import feedparser

USER_AGENT = 'NewsJockey/0.2 (local personal news reader)'
MAX_BYTES = 4 * 1024 * 1024

class FetchError(ValueError):
    def __init__(self, message, retry_after=None):
        super().__init__(message)
        self.retry_after = retry_after

def public_url(url):
    url = url.strip()
    parts = urlsplit(url)
    if parts.scheme not in ('http', 'https') or not parts.hostname or parts.username or parts.password:
        raise FetchError('Enter a public http:// or https:// URL without credentials.')
    if parts.port not in (None, 80, 443):
        raise FetchError('Only standard website ports (80 and 443) are supported.')
    try:
        addresses = socket.getaddrinfo(parts.hostname, parts.port or (443 if parts.scheme == 'https' else 80), type=socket.SOCK_STREAM)
    except OSError as exc:
        raise FetchError('Could not resolve this website.') from exc
    if not addresses or any(not ipaddress.ip_address(x[4][0]).is_global for x in addresses):
        raise FetchError('Private network and local URLs are not supported as news sources.')
    return url

def fetch(url, headers=None):
    deadline = time.monotonic() + 35
    with requests.Session() as session:
        session.trust_env = False
        for _ in range(6):
            public_url(url)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise FetchError('Request took too long.')
            with session.get(url, headers={'User-Agent': USER_AGENT, **(headers or {})},
                             timeout=(min(8, remaining), min(15, remaining)), stream=True,
                             allow_redirects=False) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    location = response.headers.get('Location')
                    if not location:
                        raise FetchError('Redirect has no destination.')
                    url = urljoin(url, location)
                    continue
                if response.status_code == 304:
                    return response.status_code, response.headers, b'', url
                if response.status_code >= 400:
                    raise FetchError(f'Publisher returned HTTP {response.status_code}.', response.headers.get('Retry-After'))
                if response.headers.get('Content-Type', '').lower().startswith(('video/', 'audio/')):
                    # Media metadata is collected; avoid downloading entire video/audio enclosures.
                    return response.status_code, response.headers, b'', url
                body = bytearray()
                for chunk in response.iter_content(65536):
                    body.extend(chunk)
                    if len(body) > MAX_BYTES:
                        raise FetchError('Feed or page exceeds the 4 MB limit.')
                    if time.monotonic() > deadline:
                        raise FetchError('Request took too long.')
                return response.status_code, response.headers, bytes(body), url
    raise FetchError('Too many redirects.')

def parse_feed(body):
    # Preserve supplied markup as data; the article renderer uses a separate sanitizer.
    parsed = feedparser.parse(body, sanitize_html=False, resolve_relative_uris=False)
    if not parsed.get('version'):
        raise FetchError('This URL did not return an RSS or Atom feed.')
    if parsed.get('bozo') and not parsed.entries:
        raise FetchError('The feed is malformed and could not be read.')
    return parsed

def clean_text(value):
    if not isinstance(value, str):
        value = str(value or '')
    if '<' not in value:
        return ' '.join(html.unescape(value).split())
    soup = BeautifulSoup(value or '', 'html.parser')
    for tag in soup(['script', 'style', 'iframe']):
        tag.decompose()
    return ' '.join(soup.get_text(' ', strip=True).split())

def article_url(value, base):
    url = urljoin(base, value or '')
    parts = urlsplit(url)
    if parts.scheme not in ('http', 'https') or not parts.hostname or parts.username or parts.password:
        return ''
    # Remove only known tracking parameters, preserving publisher identifiers.
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if not k.lower().startswith('utm_') and k.lower() not in ('fbclid', 'gclid')]
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, urlencode(query), ''))

def discover(url):
    if not urlsplit(url).scheme:
        url = 'https://' + url
    _, _, body, resolved = fetch(url)
    try:
        parsed = parse_feed(body)
        return [{'name': clean_text(parsed.feed.get('title')) or urlsplit(resolved).hostname,
                 'website': article_url(parsed.feed.get('link'), resolved) or resolved,
                 'feed_url': resolved, 'entries': len(parsed.entries)}]
    except FetchError:
        pass
    soup = BeautifulSoup(body, 'html.parser')
    candidates = []
    for link in soup.find_all('link', href=True):
        if link.get('type', '').lower() in ('application/rss+xml', 'application/atom+xml', 'application/rdf+xml'):
            candidates.append(urljoin(resolved, link['href']))
    if not candidates:
        origin = urlunsplit((*urlsplit(resolved)[:2], '/', '', ''))
        candidates = [urljoin(origin, x) for x in ('feed/', 'rss', 'rss.xml', 'atom.xml')]
    found = []
    for candidate in list(dict.fromkeys(candidates))[:8]:
        try:
            _, _, feed_body, feed_url = fetch(candidate)
            parsed = parse_feed(feed_body)
            if any(x['feed_url'] == feed_url for x in found):
                continue
            found.append({'name': clean_text(parsed.feed.get('title')) or urlsplit(resolved).hostname,
                          'website': resolved, 'feed_url': feed_url, 'entries': len(parsed.entries)})
        except (ValueError, requests.RequestException):
            continue
    if not found:
        raise FetchError('No working feed found. This publisher may need an official API or a specific feed URL.')
    return found
