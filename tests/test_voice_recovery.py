import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from remember.voice import RecoveringDeepgramSTTService, speech_error_detail
from remember.session import Session
from remember.extract import HeuristicExtractor


class SpeechConnectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_healthy_connection_is_not_interrupted(self):
        service = SimpleNamespace(_connection=object(), is_usable=True, _do_reconnect=AsyncMock(), set_usable=AsyncMock())
        await RecoveringDeepgramSTTService.ensure_connected(service)
        service._do_reconnect.assert_not_awaited()

    async def test_dead_service_reconnects_before_audio(self):
        service = SimpleNamespace(_connection=None, is_usable=False)
        async def reconnect():
            service._connection = object()
        service._do_reconnect = AsyncMock(side_effect=reconnect)
        service.set_usable = AsyncMock()
        await RecoveringDeepgramSTTService.ensure_connected(service)
        service._do_reconnect.assert_awaited_once()
        service.set_usable.assert_awaited_once_with(True)

    async def test_failed_connection_is_not_marked_healthy(self):
        service = SimpleNamespace(_connection=None, is_usable=False, _do_reconnect=AsyncMock(), set_usable=AsyncMock())
        with self.assertRaises(ConnectionError):
            await RecoveringDeepgramSTTService.ensure_connected(service)
        service.set_usable.assert_not_awaited()


class SpeechStatusTests(unittest.TestCase):
    def test_provider_error_redacts_credentials(self):
        with patch.dict(os.environ, {'DEEPGRAM_API_KEY': 'private-test-secret'}):
            detail = speech_error_detail(SimpleNamespace(error='Failed private-test-secret token=another-secret'))
        self.assertNotIn('private-test-secret', detail)
        self.assertNotIn('another-secret', detail)

    def test_error_survives_capture_reset_and_clears_on_real_words(self):
        with tempfile.TemporaryDirectory() as d:
            session = Session(d+'/test.sqlite', extractor=HeuristicExtractor())
            try:
                session.speech_failed('Connection lost')
                session.start()
                self.assertEqual(session.state()['status']['speech_error'], 'Connection lost')
                session.hear('These are real words.', audio=True)
                self.assertIsNone(session.state()['status']['speech_error'])
            finally:
                session.close()
