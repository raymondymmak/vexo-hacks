import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from remember import kitpath

class ConfigTests(unittest.TestCase):
    def test_delivery_settings_load_without_overriding_environment(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            (root/'.env').write_text('NTFY_URL=https://ntfy.example/test\nREMEMBER_AUTO_DELIVER=1\nICLOUD_USERNAME=test@example.com\nICLOUD_APP_PASSWORD=test-secret\nREMEMBER_SOCIAL_LOOKUP=0\nREMEMBER_PAIRING_TOKEN=random-test-token\nUNRELATED=ignore\n')
            with patch.object(kitpath,'ROOT',root), patch.object(Path,'cwd',return_value=root), patch.dict(os.environ, {'NTFY_URL':'https://ntfy.example/existing'},clear=True):
                kitpath.load_dotenv()
                self.assertEqual(os.environ['NTFY_URL'],'https://ntfy.example/existing')
                self.assertEqual(os.environ['ICLOUD_APP_PASSWORD'],'test-secret')
                self.assertEqual(os.environ['REMEMBER_AUTO_DELIVER'],'1')
                self.assertEqual(os.environ['REMEMBER_SOCIAL_LOOKUP'],'0')
                self.assertEqual(os.environ['REMEMBER_PAIRING_TOKEN'],'random-test-token')
                self.assertNotIn('UNRELATED',os.environ)
