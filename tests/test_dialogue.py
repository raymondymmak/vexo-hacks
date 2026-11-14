import unittest
from remember.session import Session
from remember.extract import HeuristicExtractor

class DialogueTests(unittest.TestCase):
    def test_speakers_and_interim_replacement(self):
        session=Session(':memory:',HeuristicExtractor())
        try:
            session.start()
            session.hear('Hello',speaker='0')
            session.hear('Hi I am',False,speaker='1')
            session.hear('Hi I am Maya',False,speaker='1')
            status=session.state()['status']
            self.assertEqual(status['speakers'],['0','1'])
            self.assertEqual(status['utterances'],[
                {'speaker':'0','text':'Hello','final':True},
                {'speaker':'1','text':'Hi I am Maya','final':False}])
            session.hear('Hi I am Maya.',True,speaker='1')
            session.set_wearer('0')
            status=session.state()['status']
            self.assertEqual(len(status['utterances']),2)
            self.assertTrue(status['utterances'][1]['final'])
            self.assertEqual(status['wearer_speaker'],'0')
            session.end()
            self.assertEqual(session.state()['status']['utterances'],[])
        finally:
            session.close()
    def test_unknown_speaker_not_assigned_to_wearer(self):
        session=Session(':memory:',HeuristicExtractor())
        try:
            session.hear('Unknown voice')
            session.set_wearer('0')
            self.assertIsNone(session.state()['status']['utterances'][0]['speaker'])
            session.discard()
            self.assertEqual(session.state()['status']['utterances'],[])
        finally:
            session.close()

    def test_saved_transcript_survives_restart_and_is_not_in_vcard(self):
        import tempfile
        from remember.vcard import render_contact
        with tempfile.NamedTemporaryFile(suffix='.sqlite') as file:
            session=Session(file.name,HeuristicExtractor())
            session.start()
            session.hear("I'm Maya Chen.",speaker='1')
            session.hear('A unique sentence for the full transcript.',speaker='0')
            session.set_wearer('0')
            person=session.end()
            session.close()
            reopened=Session(file.name,HeuristicExtractor())
            try:
                saved=reopened.store.get(person['id'])
                dialogue=saved['conversations'][0]['utterances']
                self.assertEqual(dialogue[0]['speaker'],'1')
                self.assertEqual(dialogue[1]['role'],'wearer')
                self.assertIn('unique sentence',saved['conversations'][0]['transcript'])
                self.assertNotIn('unique sentence',render_contact(saved))
                self.assertEqual(reopened.state()['status']['utterances'],[])
            finally:
                reopened.close()
