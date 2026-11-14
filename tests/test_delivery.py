import asyncio
import os
import unittest
from unittest.mock import patch

import httpx

from remember.delivery import DeliveryService

UID = '12345678-abcd-1234-abcd-123456789012'
PERSON = {'contact_uid': UID}
VCARD = 'BEGIN:VCARD\r\nVERSION:3.0\r\nUID:' + UID + '\r\nFN:Private Person\r\nEND:VCARD\r\n'
BOOK = 'https://p01-contacts.icloud.com/account/card/'


def discovery(prop, href='/'):
    return '<d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:carddav"><d:response><d:href>' + href + '</d:href><d:propstat><d:prop>' + prop + '</d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response></d:multistatus>'


class DeliveryTests(unittest.TestCase):
    def service(self, handler, **kwargs):
        return DeliveryService(enabled=True, username='test@example.com', password='SECRET',
            transport=httpx.MockTransport(handler), **kwargs)

    def test_live_progress_preserves_icloud_failure_and_notification_success(self):
        events = []
        def handler(request):
            events.append(('request', request.method))
            return httpx.Response(401 if request.method == 'PUT' else 200)
        service = self.service(handler, addressbook_url=BOOK, ntfy_url='https://ntfy.sh/test')
        service.deliver(PERSON, VCARD, progress=lambda stage, result: events.append((stage, result['status'])))
        self.assertEqual(events, [('icloud', 'active'), ('request', 'PUT'), ('icloud', 'error'),
                                  ('ntfy', 'active'), ('request', 'POST'), ('ntfy', 'sent')])

    def test_disabled_and_construction_are_offline(self):
        with patch.dict(os.environ, {'REMEMBER_AUTO_DELIVER': '0', 'ICLOUD_USERNAME': 'secret'}, clear=True):
            service = DeliveryService.from_env()
            self.assertEqual(service.deliver(PERSON, VCARD)['icloud']['status'], 'disabled')

    def test_discovery_put_and_generic_notification(self):
        calls = []
        def handler(request):
            calls.append(request)
            if request.method == 'PROPFIND':
                if request.url.host == 'contacts.icloud.com':
                    return httpx.Response(207, text=discovery('<d:current-user-principal><d:href>https://p01-contacts.icloud.com/account/principal/</d:href></d:current-user-principal>'))
                if request.url.path.endswith('/principal/'):
                    return httpx.Response(207, text=discovery('<c:addressbook-home-set><d:href>/account/</d:href></c:addressbook-home-set>'))
                self.assertEqual(request.headers['Depth'], '1')
                return httpx.Response(207, text=discovery('<d:resourcetype><d:collection/><c:addressbook/></d:resourcetype>', '/account/card/'))
            if request.method == 'PUT':
                self.assertEqual(request.headers['If-None-Match'], '*')
                self.assertEqual(request.content, VCARD.encode())
                return httpx.Response(201)
            self.assertEqual(request.headers['Authorization'], 'Bearer TOPIC_TOKEN')
            self.assertNotIn('Private Person', request.content.decode())
            self.assertNotIn('SECRET', request.headers.get('Authorization', ''))
            self.assertNotIn('Click', request.headers)
            return httpx.Response(200)
        service = self.service(handler, ntfy_url='https://ntfy.sh/private-topic', ntfy_token='TOPIC_TOKEN')
        result = service.deliver(PERSON, VCARD, 'https://remember.example/contact')
        self.assertEqual(result['icloud']['status'], 'saved')
        self.assertEqual(result['notification']['status'], 'sent')
        self.assertEqual(len(calls), 5)
        self.assertEqual(service.addressbook_url, BOOK)

    def test_same_uid_retry_is_idempotent(self):
        calls = []
        def handler(request):
            calls.append(request)
            return httpx.Response(412) if request.method == 'PUT' else httpx.Response(200, text=VCARD)
        result = self.service(handler, addressbook_url=BOOK).deliver(PERSON, VCARD)
        self.assertEqual(result['icloud']['status'], 'saved')
        self.assertEqual([r.method for r in calls], ['PUT', 'GET'])

    def test_existing_contact_update_uses_etag(self):
        calls = []
        def handler(request):
            calls.append(request)
            if request.method == 'GET':
                return httpx.Response(200, text=VCARD.replace('Private Person', 'Old Name'), headers={'ETag': '"v1"'})
            if len(calls) == 1:
                return httpx.Response(412)
            self.assertEqual(request.headers['If-Match'], '"v1"')
            return httpx.Response(204)
        self.assertEqual(self.service(handler, addressbook_url=BOOK).deliver(PERSON, VCARD)['icloud']['status'], 'saved')

    def test_unrelated_contact_is_never_overwritten(self):
        calls = []
        def handler(request):
            calls.append(request)
            return httpx.Response(412) if request.method == 'PUT' else httpx.Response(200, text=VCARD.replace(UID, 'unrelated'), headers={'ETag': '"v1"'})
        result = self.service(handler, addressbook_url=BOOK).deliver(PERSON, VCARD)
        self.assertEqual(result['icloud']['status'], 'error')
        self.assertEqual(len(calls), 2)

    def test_failed_put_retries_once_and_notifies_local_fallback(self):
        calls = []
        def handler(request):
            calls.append(request)
            if request.method == 'PUT':
                return httpx.Response(503, text='SECRET leaked by provider')
            self.assertIn('ready in Remember', request.content.decode())
            self.assertEqual(request.headers['Click'], 'https://remember.example/contact')
            return httpx.Response(200)
        result = self.service(handler, addressbook_url=BOOK, ntfy_url='https://ntfy.sh/topic').deliver(PERSON, VCARD, 'https://remember.example/contact')
        self.assertEqual(len(calls), 3)
        self.assertEqual(result['icloud']['status'], 'error')
        self.assertEqual(result['notification']['status'], 'sent')
        self.assertNotIn('SECRET', str(result))

    def test_icloud_redirect_cannot_leak_credentials(self):
        calls = []
        def handler(request):
            calls.append(request)
            return httpx.Response(307, headers={'Location': 'https://attacker.example/'})
        result = self.service(handler).deliver(PERSON, VCARD)
        self.assertEqual(result['icloud']['status'], 'error')
        self.assertEqual(len(calls), 1)

    def test_async_deadline_bounds_channel(self):
        async def handler(request):
            await asyncio.sleep(1)
            return httpx.Response(201)
        service = self.service(handler, addressbook_url=BOOK)
        service.ICLOUD_BUDGET = 0.01
        result = service.deliver(PERSON, VCARD)
        self.assertEqual(result['icloud']['status'], 'error')
        self.assertIn('timed out', result['icloud']['error'])

    def test_notification_failure_sanitized_no_retry(self):
        calls = []
        def handler(request):
            calls.append(request)
            return httpx.Response(403, text='TOPIC_TOKEN secret')
        service = DeliveryService(enabled=True, ntfy_url='https://ntfy.sh/topic', ntfy_token='TOPIC_TOKEN', transport=httpx.MockTransport(handler))
        result = service.deliver(PERSON, VCARD)
        self.assertEqual(result['notification']['status'], 'error')
        self.assertEqual(len(calls), 1)
        self.assertNotIn('TOPIC_TOKEN', str(result))
