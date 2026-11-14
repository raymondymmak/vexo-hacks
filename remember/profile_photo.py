"""Best-effort public LinkedIn portrait metadata; no login or private scraping."""
from html.parser import HTMLParser
import re
from urllib.parse import urljoin, urlsplit

import httpx

from .socials import _canonical_profile

MAX_PAGE_BYTES = 512 * 1024
_REDIRECTS = {301, 302, 303, 307, 308}
_GENERIC_IMAGE = re.compile(r'(?:company[-_]logo|organization[-_]logo|logo|placeholder|default[-_]avatar|profile[-_]displaybackgroundimage)', re.I)


def _https_host(url):
    """Reject credentials, control characters, nonstandard ports and ambiguous hosts."""
    if not isinstance(url, str) or len(url) > 4096 or re.search(r'[\x00-\x20\\]', url):
        return ''
    try:
        parsed = urlsplit(url)
        if parsed.scheme != 'https' or parsed.username or parsed.password or parsed.port not in (None, 443):
            return ''
        host = (parsed.hostname or '').lower()
        if not re.fullmatch(r'[a-z0-9]+(?:[a-z0-9.-]*[a-z0-9])?', host):
            return ''
        if any(not label or label.startswith('-') or label.endswith('-') for label in host.split('.')):
            return ''
        return host
    except (ValueError, TypeError):
        return ''


def valid_photo_url(url):
    """Only LinkedIn's image CDN can be loaded by the dashboard as a portrait."""
    host = _https_host(url)
    return bool(host and host.endswith('.licdn.com') and urlsplit(url).path not in ('', '/'))


def _public_linkedin_page(url):
    host = _https_host(url)
    if not host or not (host == 'linkedin.com' or host.endswith('.linkedin.com')):
        return False
    path = urlsplit(url).path.casefold()
    return not any(part in path for part in ('/authwall', '/login', '/checkpoint', '/uas/', '/signup'))


def _portrait_score(url):
    if not valid_photo_url(url):
        return -1
    parsed = urlsplit(url)
    path = parsed.path.casefold()
    if _GENERIC_IMAGE.search(path) or (parsed.hostname or '').casefold().startswith('static.'):
        return -1
    if 'profile-displayphoto' in path or 'profile-framedphoto' in path or 'profile-picture' in path:
        return 2
    return 1


class _PhotoMetadata(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.images = []

    def handle_starttag(self, tag, attrs):
        if tag != 'meta':
            return
        attrs = {key.casefold(): value for key, value in attrs if key and value is not None}
        field = (attrs.get('property') or attrs.get('name') or '').casefold()
        if field in ('og:image', 'og:image:secure_url', 'twitter:image', 'twitter:image:src'):
            value = attrs.get('content', '').strip()
            if _portrait_score(value) > 0:
                self.images.append(value)


def fetch_profile_photo(linkedin_url, client=None):
    """Read at most 512 KiB of public HTML, with two same-service redirects.

    A blocked, login-only, rate-limited or unavailable profile simply has no photo.
    Images are returned as validated URLs; the service does not download image data.
    """
    try:
        url = _canonical_profile(linkedin_url)
    except (ValueError, TypeError, AttributeError):
        return ''
    if not url or not _public_linkedin_page(url):
        return ''
    owned = client is None
    client = client or httpx.Client(timeout=2, follow_redirects=False, trust_env=False)
    try:
        for redirect_count in range(3):
            with client.stream('GET', url, follow_redirects=False, timeout=2, auth=None,
                               headers={'Accept': 'text/html,application/xhtml+xml', 'Cookie': ''}) as response:
                if response.status_code in _REDIRECTS:
                    if redirect_count == 2:
                        return ''
                    location = response.headers.get('location')
                    if not location:
                        return ''
                    target = urljoin(url, location)
                    if not _public_linkedin_page(target):
                        return ''
                    url = target
                    continue
                if response.status_code != 200:
                    return ''
                content_type = response.headers.get('content-type', '').split(';', 1)[0].strip().casefold()
                if content_type and content_type not in ('text/html', 'application/xhtml+xml'):
                    return ''
                length = response.headers.get('content-length')
                if length is not None and (not length.isdigit() or int(length) > MAX_PAGE_BYTES):
                    return ''
                chunks, size = [], 0
                for chunk in response.iter_bytes(chunk_size=16384):
                    size += len(chunk)
                    if size > MAX_PAGE_BYTES:
                        return ''
                    chunks.append(chunk)
                parser = _PhotoMetadata()
                parser.feed(b''.join(chunks).decode(response.encoding or 'utf-8', errors='replace'))
                return max(parser.images, key=_portrait_score, default='')
    except (httpx.HTTPError, ValueError, TypeError, LookupError):
        return ''
    finally:
        if owned:
            client.close()
    return ''
