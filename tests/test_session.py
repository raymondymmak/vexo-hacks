import threading
import unittest
from remember.extract import blank_card
from remember.session import Session


class Reader:
    def __init__(self):
        self.last = None
        self.fail = False

    def extract(self, transcript, moments):
        self.last = transcript
        if self.fail:
            raise RuntimeError('test failure')
        card = blank_card()
        card.update(name='Maya', summary='Discussed the project.', key_moments=list(moments))
        return card


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.reader = Reader()
        self.session = Session(':memory:', self.reader, retain_transcripts=False)

    def tearDown(self):
        self.session.close()

    def test_new_save_retains_notes_not_transcript_or_tapped_quotes(self):
        self.session.start()
        self.session.hear('Private exact words.', speaker='1', audio=True)
        self.session.mark()
        current = self.session.splitter.open
        person = self.session.end()
        self.assertIn('Private exact words.', self.reader.last)
        self.assertEqual(person['summary'], 'Discussed the project.')
        self.assertEqual(person['key_moments'], [])
        self.assertEqual(person['conversations'][0]['transcript'], '')
        row = self.session.store._conn.execute('SELECT transcript, key_moments FROM conversations').fetchone()
        self.assertEqual(tuple(row), ('', '[]'))
        self.assertEqual(self.session.speaker_lines, [])
        self.assertEqual(current.lines, [])
        self.assertEqual(current.moments, [])
        self.assertEqual(self.session.state()['status']['transcript'], '')

    def test_last_interim_is_used_by_extractor(self):
        self.session.start()
        self.session.hear('A final sentence.', speaker='1', audio=True)
        self.session.hear('The trailing partial.', False, speaker='1', audio=True)
        self.session.end()
        self.assertIn('[speaker 1] A final sentence.', self.reader.last)
        self.assertIn('[speaker 1] The trailing partial.', self.reader.last)

    def test_failed_save_can_retry_and_callback_failure_is_safe(self):
        self.session.hear('Retain me until saved.', speaker='0')
        self.reader.fail = True
        with self.assertRaises(RuntimeError):
            self.session.end()
        self.assertIn('Retain me', self.session.state()['status']['transcript'])
        self.assertIsNotNone(self.session.state()['status']['error'])
        self.reader.fail = False
        self.session.on_saved = lambda person: (_ for _ in ()).throw(RuntimeError('LED failure'))
        ready = []
        self.session.on_contact_ready = lambda person: ready.append(person['id'])
        person = self.session.end()
        self.assertEqual(ready, [person['id']])
        self.assertEqual(self.session.store.count(), 1)
        self.assertEqual(self.session.state()['status']['phase'], 'idle')

    def test_delivery_callback_failure_does_not_restore_capture(self):
        self.session.hear('Hello.')
        self.session.on_contact_ready = lambda person: (_ for _ in ()).throw(RuntimeError('Queue failure'))
        self.session.end()
        self.assertEqual(self.session.store.count(), 1)
        self.assertEqual(self.session.state()['status']['transcript'], '')
        self.assertIn('delivery', self.session.state()['status']['note'])

    def test_existing_transcript_is_not_purged_by_new_save(self):
        old = self.session.store.merge(blank_card(), 'legacy transcript', ['legacy quote'], 'start', 'end')
        self.session.hear('New conversation')
        self.session.end()
        legacy = self.session.store.get(old['id'])
        self.assertEqual(legacy['conversations'][0]['transcript'], 'legacy transcript')
        self.assertEqual(legacy['key_moments'], ['legacy quote'])

    def test_reset_waits_for_processing_then_clears_saved_card(self):
        started, release, reset_done = threading.Event(), threading.Event(), threading.Event()
        original = self.reader.extract
        def blocking_extract(transcript, moments):
            started.set()
            self.assertTrue(release.wait(2))
            return original(transcript, moments)
        self.reader.extract = blocking_extract
        self.session.hear('Hello')
        saving = threading.Thread(target=self.session.end)
        saving.start()
        self.assertTrue(started.wait(2))
        resetting = threading.Thread(target=lambda: (self.session.reset(), reset_done.set()))
        resetting.start()
        self.assertFalse(reset_done.wait(.05))
        release.set()
        saving.join(2)
        resetting.join(2)
        self.assertTrue(reset_done.is_set())
        self.assertEqual(self.session.store.count(), 0)

    def test_discard_stops_capture_and_preserves_saved_contacts(self):
        self.session.hear('Saved encounter')
        person = self.session.end()
        self.session.start()
        self.session.hear('Discard this raw speech', speaker='1')
        self.session.mark()
        current = self.session.splitter.open
        stopped = []
        self.session.on_capture = lambda active: stopped.append(active)
        self.session.discard()
        self.assertEqual(stopped, [False])
        self.assertEqual(self.session.store.count(), 1)
        self.assertEqual(self.session.store.get(person['id'])['name'], 'Maya')
        self.assertEqual(current.lines, [])
        self.assertEqual(self.session.speaker_lines, [])
        self.assertEqual(self.session.state()['status']['phase'], 'idle')

    def test_reset_hook_runs_before_database_deletion(self):
        self.session.hear('Saved encounter')
        self.session.end()
        seen = []
        self.session.on_reset = lambda: seen.append(self.session.store.count())
        self.session.reset()
        self.assertEqual(seen, [1])
        self.assertEqual(self.session.store.count(), 0)

    def test_start_ignores_processing_without_waiting_for_operation_lock(self):
        held, release, done = threading.Event(), threading.Event(), threading.Event()
        def hold_operation():
            with self.session.operation_lock:
                with self.session.lock:
                    self.session.processing = True
                held.set()
                release.wait(2)
        worker = threading.Thread(target=hold_operation)
        worker.start()
        self.assertTrue(held.wait(2))
        caller = threading.Thread(target=lambda: (self.session.start(), done.set()))
        caller.start()
        try:
            self.assertTrue(done.wait(.2))
            self.assertIsNone(self.session.splitter.open)
        finally:
            release.set()
            worker.join(2)
            caller.join(2)

    def test_subscriber_queue_is_bounded_and_save_clears_raw_status(self):
        inbox = self.session.subscribe()
        for i in range(100):
            self.session.hear('Private words %d' % i)
        self.assertEqual(inbox.qsize(), 32)
        self.session.end()
        events = []
        while not inbox.empty():
            events.append(inbox.get_nowait())
        self.assertTrue(any(e.get('type') == 'card' for e in events))
        self.assertTrue(all(not e.get('status', {}).get('transcript') for e in events))
        self.assertTrue(all(not e.get('status', {}).get('moments') for e in events))
