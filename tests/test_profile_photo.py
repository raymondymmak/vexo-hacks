import sqlite3
import tempfile
import unittest

import httpx

from remember.profile_photo import MAX_PAGE_BYTES, fetch_profile_photo, valid_photo_url
from remember.store import Store
from remember.vcard import render_contact

PROFILE = 'https://www.linkedin.com/in/sam-smith'
PHOTO = 'https://media.licdn.com/dms/image/v2/ABC/profile-displayphoto-shrink_400_400/0/123?e=456&v=beta&t=signature'


class ProfilePhotoTests(unittest.TestCase):
    def fetch(self, handler, url=PROFILE):
        def html_fixture(request):
            response = handler(request)
            # httpx's text= fixture defaults to text/plain; these bodies model HTML pages.
            if response.headers.get('content-type', '').startswith('text/plain'):
                response.headers['content-type'] = 'text/html'
            return response
        with httpx.Client(transport=httpx.MockTransport(html_fixture)) as client:
            return fetch_profile_photo(url, client=client)

    def test_public_portrait_metadata_prefers_photo_and_unescapes_query(self):
        html = '<meta property="og:image" content="https://media.licdn.com/dms/image/company-logo_100_100/logo">' + \
               '<meta name="twitter:image" content="https://media.licdn.com/dms/image/other">' + \
               '<meta property="og:image" content="' + PHOTO.replace('&', '&amp;') + '">'
        seen = []
        def handler(request):
            seen.append(request)
            return httpx.Response(200, text=html, headers={'Content-Type': 'text/html'})
        self.assertEqual(self.fetch(handler, 'https://ca.linkedin.com/in/sam-smith/?trk=public'), PHOTO)
        self.assertEqual(str(seen[0].url), PROFILE)
        self.assertEqual(seen[0].extensions['timeout']['read'], 2)
        self.assertNotIn('authorization', seen[0].headers)

    def test_redirects_stay_on_linkedin_and_never_follow_login(self):
        for target in ('https://evil.example/profile', 'https://www.linkedin.com.evil.example/in/sam',
                       'http://www.linkedin.com/in/sam', 'https://www.linkedin.com:444/in/sam',
                       'https://www.linkedin.com/authwall?sessionRedirect=anything'):
            calls = []
            def handler(request):
                calls.append(request)
                return httpx.Response(302, headers={'Location': target})
            self.assertEqual(self.fetch(handler), '')
            self.assertEqual(len(calls), 1)

    def test_two_redirect_limit_and_same_service_redirect_success(self):
        calls = []
        def handler(request):
            calls.append(request)
            if len(calls) < 3:
                return httpx.Response(302, headers={'Location': '/in/sam-new-' + str(len(calls))})
            return httpx.Response(200, text='<meta property="og:image" content="' + PHOTO + '">')
        self.assertEqual(self.fetch(handler), PHOTO)
        self.assertEqual(len(calls), 3)
        calls.clear()
        def loop(request):
            calls.append(request)
            return httpx.Response(302, headers={'Location': PROFILE})
        self.assertEqual(self.fetch(loop), '')
        self.assertEqual(len(calls), 3)

    def test_private_unavailable_and_non_html_return_no_photo(self):
        for status in (401, 403, 404, 429, 500, 999):
            self.assertEqual(self.fetch(lambda request: httpx.Response(status)), '')
        self.assertEqual(self.fetch(lambda request: httpx.Response(200, content=b'not html', headers={'Content-Type': 'application/json'})), '')
        self.assertEqual(self.fetch(lambda request: httpx.Response(200, text='<meta property="og:image" content="https://static.licdn.com/sc/h/default">')), '')

    def test_image_host_and_url_constraints(self):
        for url in ('http://media.licdn.com/image', 'https://media.licdn.com.evil.example/image',
                    'https://evil.example/image', 'https://u:p@media.licdn.com/image',
                    'https://media.licdn.com:444/image', 'https://media.licdn.com:bad/image',
                    'https://licdn.com/image', 'data:image/png;base64,abc', '//media.licdn.com/image',
                    'https://media.licdn.com/space image', 'https://media.licdn.com\\@evil.example/image'):
            self.assertFalse(valid_photo_url(url), url)
            self.assertEqual(self.fetch(lambda request: httpx.Response(200, text='<meta property="og:image" content="' + url + '">')), '')
        self.assertTrue(valid_photo_url(PHOTO))
        self.assertTrue(valid_photo_url('https://media-exp1.licdn.com/dms/image/photo'))
        self.assertEqual(self.fetch(lambda request: self.fail('invalid profile fetched'), 'https://example.com/in/sam'), '')
        self.assertEqual(self.fetch(lambda request: self.fail('invalid profile fetched'), 'https://linkedin.com/company/acme'), '')

    def test_page_size_is_bounded_even_without_content_length(self):
        class LargeStream(httpx.SyncByteStream):
            def __init__(self):
                self.count = 0
                self.closed = False
            def __iter__(self):
                for _ in range(100):
                    self.count += 1
                    yield b'x' * 16384
            def close(self):
                self.closed = True
        stream = LargeStream()
        self.assertEqual(self.fetch(lambda request: httpx.Response(200, stream=stream)), '')
        self.assertLessEqual(stream.count, MAX_PAGE_BYTES // 16384 + 1)
        self.assertTrue(stream.closed)
        self.assertEqual(self.fetch(lambda request: httpx.Response(200, headers={'Content-Length': str(MAX_PAGE_BYTES + 1)}, text='small')), '')

    def test_supplied_client_credentials_are_not_used(self):
        def handler(request):
            self.assertNotIn('authorization', request.headers)
            self.assertFalse(request.headers.get('cookie'))
            return httpx.Response(200, text='<meta property="og:image" content="' + PHOTO + '">',
                                  headers={'Content-Type': 'text/html'})
        with httpx.Client(transport=httpx.MockTransport(handler), auth=('private', 'password'),
                          cookies={'session': 'private'}) as client:
            self.assertEqual(fetch_profile_photo(PROFILE, client=client), PHOTO)

    def test_network_errors_are_best_effort(self):
        def handler(request):
            raise httpx.ReadTimeout('unavailable', request=request)
        self.assertEqual(self.fetch(handler), '')


class PhotoStoreTests(unittest.TestCase):
    def test_migration_preserves_existing_data(self):
        with tempfile.TemporaryDirectory() as folder:
            path = folder + '/legacy.sqlite'
            conn = sqlite3.connect(path)
            conn.executescript('''CREATE TABLE people (
                id INTEGER PRIMARY KEY, name TEXT NOT NULL, name_key TEXT NOT NULL UNIQUE,
                working_on TEXT NOT NULL DEFAULT '', cares_about TEXT NOT NULL DEFAULT '[]',
                deadlines TEXT NOT NULL DEFAULT '[]', summary TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                INSERT INTO people VALUES(1,'Sam','sam','','[]','[]','Existing notes','start','end');''')
            conn.close()
            store = Store(path)
            try:
                person = store.get(1)
                self.assertEqual(person['photo_url'], '')
                self.assertEqual(person['summary'], 'Existing notes')
                self.assertTrue(person['contact_uid'])
            finally:
                store.close()

    def test_uid_guard_photo_validation_and_unrelated_fields(self):
        store = Store(':memory:')
        try:
            person = store.merge({'name': 'Sam Smith', 'company': 'Acme', 'summary': 'Keep my notes'}, '', [], 'start', 'end')
            store.set_delivery(person['id'], {'icloud': {'status': 'saved'}}, PROFILE,
                               contact_uid=person['contact_uid'], original_social_url='')
            before = store.get(person['id'])
            self.assertIsNone(store.set_photo(person['id'], PHOTO, 'wrong-uid'))
            updated = store.set_photo(person['id'], PHOTO, person['contact_uid'])
            self.assertEqual(updated['photo_url'], PHOTO)
            self.assertEqual({k: v for k, v in updated.items() if k != 'photo_url'},
                             {k: v for k, v in before.items() if k != 'photo_url'})
            self.assertNotIn(PHOTO, render_contact(updated))
            with self.assertRaises(ValueError):
                store.set_photo(person['id'], 'https://evil.example/track', person['contact_uid'])
            store.reset()
            replacement = store.merge({'name': 'Different person'}, '', [], 'start', 'end')
            self.assertEqual(replacement['id'], person['id'])
            self.assertIsNone(store.set_photo(person['id'], PHOTO, person['contact_uid']))
            self.assertEqual(store.get(replacement['id'])['photo_url'], '')
        finally:
            store.close()
