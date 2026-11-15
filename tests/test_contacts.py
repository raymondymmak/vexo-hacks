import sqlite3
import tempfile
import unittest

from remember.extract import HeuristicExtractor, OpenAIExtractor
from remember.store import Store
from remember.vcard import render_contact


class ContactTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(':memory:')

    def tearDown(self):
        self.store.close()

    def save(self, **fields):
        return self.store.merge({'name': 'Sam', **fields}, 'hello', [], 's', 'e')

    def test_same_name_does_not_merge(self):
        first = self.save(email='one@example.com')
        second = self.save(email='two@example.com')
        self.assertNotEqual(first['id'], second['id'])
        self.assertNotEqual(first['contact_uid'], second['contact_uid'])

    def test_review_and_edit(self):
        contact = self.save()
        self.assertIn("BEGIN:VCARD", render_contact(contact))
        reviewed = self.store.update_person(contact['id'], {'email': 'sam@example.com', 'reviewed': True,
            'promises': [{'what': 'Send notes', 'by_when': 'Friday'}]})
        self.assertIn('EMAIL;TYPE=INTERNET:sam@example.com', render_contact(reviewed))
        self.assertIn('You promised: Send notes', render_contact(reviewed))
        changed = self.store.update_person(contact['id'], {'role': 'Engineer'})
        self.assertFalse(changed['reviewed'])
        self.assertEqual(changed['contact_uid'], contact['contact_uid'])

    def test_vcard_escaping_and_utf8_folding(self):
        person = self.save(company='A;B,C\\D', summary='界' * 80)
        person = self.store.update_person(person['id'], {'reviewed': True})
        value = render_contact(person)
        self.assertIn('ORG:A\\;B\\,C\\\\D', value)
        self.assertTrue(value.endswith('END:VCARD\r\n'))
        self.assertTrue(all(len(line.encode('utf-8')) <= 75 for line in value.split('\r\n')))
        self.assertNotIn('\n', value.replace('\r\n', ''))

    def test_only_wearer_promises(self):
        card = HeuristicExtractor().extract("[speaker 1] I'm Maya Chen. I'll send a contract.\n[wearer] I'll send notes by Friday.")
        self.assertEqual(card['name'], 'Maya Chen')
        self.assertEqual(len(card['promises']), 1)
        self.assertIn('notes', card['promises'][0]['what'])
        card = OpenAIExtractor(lambda *args: {'promises': [{'what': 'invented'}]}).extract('[unknown] hello')
        self.assertEqual(card['promises'], [])

    def test_explicit_contact_details(self):
        card = HeuristicExtractor().extract("[speaker 1] I'm Maya Chen. I'm an engineer at Acme. My email is maya@example.com. My phone is +1 555-123-4567.")
        self.assertEqual(card['company'], 'Acme')
        self.assertEqual(card['role'], 'engineer')
        self.assertEqual(card['email'], 'maya@example.com')
        self.assertEqual(card['phone'], '+1 555-123-4567')

    def test_additive_legacy_migration(self):
        with tempfile.NamedTemporaryFile(suffix='.sqlite') as file:
            conn = sqlite3.connect(file.name)
            conn.execute("CREATE TABLE people (id INTEGER PRIMARY KEY, name TEXT NOT NULL, name_key TEXT NOT NULL UNIQUE, working_on TEXT NOT NULL DEFAULT '', cares_about TEXT NOT NULL DEFAULT '[]', deadlines TEXT NOT NULL DEFAULT '[]', summary TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
            conn.execute("INSERT INTO people (name,name_key,created_at,updated_at) VALUES ('Sam','sam','s','e')")
            conn.commit()
            conn.close()
            store = Store(file.name)
            person = store.get(1)
            self.assertEqual(person['name'], 'Sam')
            self.assertFalse(person['reviewed'])
            uid = person['contact_uid']
            store.close()
            store = Store(file.name)
            self.assertEqual(store.get(1)['contact_uid'], uid)
            store.close()
