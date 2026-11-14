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
