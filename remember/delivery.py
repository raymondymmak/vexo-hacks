"""Opt-in iCloud contact delivery and generic ntfy alerts.

Discovery follows DAV current-user-principal -> CardDAV addressbook-home-set ->
addressbook collection. Credentials never leave HTTPS iCloud hosts. Constructing
this service does no I/O; call deliver from a worker thread after local saving.
"""

import asyncio
import os
import re
import threading
from urllib.parse import quote, urljoin, urlsplit
from xml.etree import ElementTree as ET

import httpx

DAV = '{DAV:}'
CARD = '{urn:ietf:params:xml:ns:carddav}'


class DeliveryError(Exception):
    """A deliberately public, credential-free error."""


def _result(status, error=None):
    return {'status': status, 'error': error}


def _icloud_url(url):
    parts = urlsplit(url)
    host = (parts.hostname or '').lower()
    if (parts.scheme != 'https' or not (host == 'icloud.com' or host.endswith('.icloud.com'))
            or parts.username or parts.password or parts.port not in (None, 443)):
        raise DeliveryError('Invalid iCloud address book URL.')
    return url


def _uid(text):
    unfolded = re.sub(r'\r?\n[ \t]', '', text)
    matches = re.findall(r'^UID:(.+)$', unfolded, re.MULTILINE | re.IGNORECASE)
    return matches[0].rstrip('\r') if len(matches) == 1 else None


class DeliveryService:
    ICLOUD_BUDGET = 7.0
    NOTIFICATION_BUDGET = 2.0

    def __init__(self, *, enabled=False, username='', password='', addressbook_url='',
                 ntfy_url='', ntfy_token='', transport=None):
        self.enabled = enabled
        self.username = username
        self.password = password
        self.addressbook_url = addressbook_url
        self.ntfy_url = ntfy_url
        self.ntfy_token = ntfy_token
        self.transport = transport
        self._lock = threading.Lock()

    @classmethod
    def from_env(cls):
        return cls(enabled=os.getenv('REMEMBER_AUTO_DELIVER') == '1',
                   username=os.getenv('ICLOUD_USERNAME', '').strip(),
                   password=os.getenv('ICLOUD_APP_PASSWORD', '').strip(),
                   addressbook_url=os.getenv('ICLOUD_ADDRESSBOOK_URL', '').strip(),
                   ntfy_url=os.getenv('NTFY_URL', '').strip(),
                   ntfy_token=os.getenv('NTFY_TOKEN', '').strip())

    def deliver(self, person, vcard_text, fallback_url=None, progress=None):
        """Return per-channel status; never propagate provider responses or secrets.

        Runs synchronously and serializes sends. Use a worker thread when calling
        from asyncio. Each channel has a hard async deadline; no notification
        retry (which could duplicate alerts), and at most two CardDAV PUT attempts.
        """
        if not self.enabled:
            return {'icloud': _result('disabled'), 'notification': _result('disabled')}
        with self._lock:
            return asyncio.run(self._deliver(person, vcard_text, fallback_url, progress))

    async def _deliver(self, person, vcard_text, fallback_url, progress=None):
        result = {'icloud': _result('not_configured'), 'notification': _result('not_configured')}
        async with httpx.AsyncClient(transport=self.transport, timeout=2.0, follow_redirects=False,
                                     trust_env=False) as client:
            if self.username and self.password:
                if progress:
                    progress("icloud", _result("active"))
                try:
                    async with asyncio.timeout(self.ICLOUD_BUDGET):
                        await self._save(client, person, vcard_text)
                    result['icloud'] = _result('saved')
                except Exception as error:
                    result['icloud'] = _result('error', self._safe_error(error))
            elif self.username or self.password:
                result['icloud'] = _result('error', 'Both iCloud username and app-specific password are required.')
            if progress:
                progress("icloud", result["icloud"])
            if self.ntfy_url:
                if progress:
                    progress("ntfy", _result("active"))
                try:
                    async with asyncio.timeout(self.NOTIFICATION_BUDGET):
                        await self._notify(client, result['icloud']['status'] == 'saved', fallback_url)
                    result['notification'] = _result('sent')
                except Exception as error:
                    result['notification'] = _result('error', self._safe_error(error))
            if progress:
                progress("ntfy", result["notification"])
        return result

    @staticmethod
    def _safe_error(error):
        if isinstance(error, DeliveryError):
            return str(error)
        if isinstance(error, (TimeoutError, httpx.TimeoutException)):
            return 'Delivery timed out. Your contact remains saved locally.'
        if isinstance(error, httpx.HTTPStatusError):
            return 'Provider returned HTTP %s.' % error.response.status_code
        if isinstance(error, httpx.TransportError):
            return 'Could not reach the delivery provider.'
        return 'Delivery failed. Your contact remains saved locally.'

    async def _dav(self, client, method, url, **kwargs):
        # iCloud may redirect discovery to an account-specific cluster. Validate
        # every redirect before attaching the Apple credentials again.
        for _ in range(4):
            _icloud_url(url)
            response = await client.request(method, url, auth=(self.username, self.password), **kwargs)
            if response.status_code in (301, 302, 307, 308):
                location = response.headers.get('location')
                if not location:
                    raise DeliveryError('iCloud returned an invalid redirect.')
                url = urljoin(str(response.url), location)
                continue
            return response
        raise DeliveryError('iCloud discovery redirected too many times.')

    async def _propfind(self, client, url, properties, depth='0'):
        body = ('<?xml version="1.0" encoding="utf-8"?>'
                '<d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:carddav">'
                '<d:prop>' + properties + '</d:prop></d:propfind>')
        response = await self._dav(client, 'PROPFIND', url, content=body.encode(),
                                   headers={'Depth': depth, 'Content-Type': 'application/xml; charset=utf-8'})
        response.raise_for_status()
        if len(response.content) > 1024 * 1024 or b'<!DOCTYPE' in response.content.upper():
            raise DeliveryError('iCloud returned an invalid discovery document.')
        try:
            root = ET.fromstring(response.content)
        except ET.ParseError:
            raise DeliveryError('iCloud returned an invalid discovery document.') from None
        return root, str(response.url)

    @staticmethod
    def _properties(root):
        for response in root.findall(DAV + 'response'):
            for propstat in response.findall(DAV + 'propstat'):
                status = propstat.findtext(DAV + 'status', '')
                if ' 200 ' in status:
                    prop = propstat.find(DAV + 'prop')
                    if prop is not None:
                        yield response, prop

    async def _discover(self, client):
        root, base = await self._propfind(client, 'https://contacts.icloud.com/', '<d:current-user-principal/>')
        principal = next((prop.findtext(DAV + 'current-user-principal/' + DAV + 'href')
                          for _, prop in self._properties(root)
                          if prop.find(DAV + 'current-user-principal/' + DAV + 'href') is not None), None)
        if not principal:
            raise DeliveryError('iCloud did not return a contact principal.')
        root, base = await self._propfind(client, urljoin(base, principal), '<c:addressbook-home-set/>')
        home = next((prop.findtext(CARD + 'addressbook-home-set/' + DAV + 'href')
                     for _, prop in self._properties(root)
                     if prop.find(CARD + 'addressbook-home-set/' + DAV + 'href') is not None), None)
        if not home:
            raise DeliveryError('iCloud did not return an address book home.')
        root, base = await self._propfind(client, urljoin(base, home), '<d:resourcetype/>', depth='1')
        books = [urljoin(base, response.findtext(DAV + 'href', ''))
                 for response, prop in self._properties(root)
                 if prop.find(DAV + 'resourcetype/' + CARD + 'addressbook') is not None
                 and response.findtext(DAV + 'href')]
        if len(books) != 1:
            raise DeliveryError('Select an iCloud address book with ICLOUD_ADDRESSBOOK_URL.')
        self.addressbook_url = _icloud_url(books[0])
        return self.addressbook_url

    async def _save(self, client, person, vcard_text):
        uid = str(person.get('contact_uid') or '')
        if not re.fullmatch(r'[A-Za-z0-9-]{1,128}', uid) or _uid(vcard_text) != uid:
            raise DeliveryError('Contact identifier is missing or inconsistent.')
        book = self.addressbook_url or await self._discover(client)
        url = _icloud_url(book.rstrip('/') + '/' + quote(uid, safe='') + '.vcf')
        body = vcard_text.encode('utf-8')
        for attempt in range(2):
            try:
                response = await self._dav(client, 'PUT', url, content=body,
                    headers={'Content-Type': 'text/vcard; charset=utf-8', 'If-None-Match': '*'})
                if response.status_code in (412, 403):
                    # Some CardDAV servers reject create-if-absent on an existing
                    # resource with 403. Read it and require UID + strong ETag
                    # before attempting the same safe update used for 412.
                    current = await self._dav(client, 'GET', url)
                    if response.status_code == 403 and current.status_code != 200:
                        response.raise_for_status()
                    current.raise_for_status()
                    if _uid(current.text) != uid:
                        raise DeliveryError('A different contact already exists at that address.')
                    if current.content == body:
                        return
                    etag = current.headers.get('etag')
                    if not etag or etag.startswith('W/'):
                        raise DeliveryError('iCloud did not provide a safe contact update version.')
                    response = await self._dav(client, 'PUT', url, content=body,
                        headers={'Content-Type': 'text/vcard; charset=utf-8', 'If-Match': etag})
                if response.status_code >= 500 and attempt == 0:
                    continue
                response.raise_for_status()
                return
            except httpx.TransportError:
                if attempt:
                    raise
        raise DeliveryError('iCloud could not save this contact.')

    async def _notify(self, client, saved, fallback_url):
        parts = urlsplit(self.ntfy_url)
        if parts.scheme != 'https' or not parts.hostname or parts.username or parts.password:
            raise DeliveryError('NTFY_URL must be an HTTPS topic URL.')
        headers = {'Title': 'Remember', 'Content-Type': 'text/plain; charset=utf-8'}
        if self.ntfy_token:
            headers['Authorization'] = 'Bearer ' + self.ntfy_token
        if fallback_url and not saved:
            target = urlsplit(fallback_url)
            if target.scheme == 'https' and target.hostname and '\n' not in fallback_url and '\r' not in fallback_url:
                headers['Click'] = fallback_url
        message = 'Your new contact was saved to iCloud.' if saved else 'Your new contact is ready in Remember.'
        response = await client.post(self.ntfy_url, headers=headers, content=message.encode())
        response.raise_for_status()
