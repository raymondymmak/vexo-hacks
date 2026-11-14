"""Pipecat pipeline: band PCM in, Deepgram (or a mock) to text, then the card builder.

The live path uses Pipecat's DeepgramSTTService (nova-3-general, interim results,
speaker diarization, mip_opt_out). With no key, MockDeepgramSTTService sits in
the same slot and turns PCM into transcript frames without leaving the machine.
"""

import asyncio
import os
import threading
from dataclasses import dataclass

from loguru import logger

# Pipecat logs every frame at DEBUG and prints a banner on import. The page is the demo.
logger.disable("pipecat")

from pipecat.frames.frames import (
    DataFrame,
    EndFrame,
    InputAudioRawFrame,
    InterimTranscriptionFrame,
    StartFrame,
    TranscriptionFrame,
    TranscriptionSpeaker,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.transcriptions.language import Language
from pipecat.transports.base_input import BaseInputTransport
from pipecat.transports.base_transport import BaseTransport, TransportParams
from pipecat.utils.time import time_now_iso8601
from pipecat.workers.runner import WorkerRunner

SAMPLE_RATE = 16000
CHANNELS = 1

# 20 ms of 16 kHz mono 16-bit PCM, the same size as one band packet.
PCM_FRAME_BYTES = 640


@dataclass
class TapFrame(DataFrame):
    """A band tap, carried through the pipeline next to the audio."""

    kind: str


class BandAudioInput(BaseInputTransport):
    """Input transport that accepts PCM pushed in from the band thread.

    BaseInputTransport owns the audio queue. start() calls set_transport_ready,
    which is what makes push_audio_frame deliver InputAudioRawFrame downstream.
    """

    def __init__(self, params: TransportParams) -> None:
        super().__init__(params)
        self._lock = threading.Lock()
        self._loop = None
        self._early = []

    async def start(self, frame: StartFrame):
        await super().start(frame)
        await self.set_transport_ready(frame)
        loop = self.get_event_loop()
        with self._lock:
            early = list(self._early)
            self._early.clear()
            self._loop = loop
        for pcm in early:
            await self._push(pcm)

    async def _push(self, pcm: bytes) -> None:
        if not pcm:
            return
        await self.push_audio_frame(
            InputAudioRawFrame(audio=pcm, sample_rate=SAMPLE_RATE, num_channels=CHANNELS)
        )

    def feed_pcm(self, pcm: bytes) -> None:
        """Hand one chunk of PCM to the pipeline. Safe to call from any thread."""
        if not pcm:
            return
        with self._lock:
            loop = self._loop
            if loop is None:
                self._early.append(bytes(pcm))
                return
        asyncio.run_coroutine_threadsafe(self._push(bytes(pcm)), loop)


class _NullOutput(FrameProcessor):
    """The transport has no speaker. Frames that reach here are dropped onward."""

    async def process_frame(self, frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)


class BandAudioTransport(BaseTransport):
    """Pipecat transport whose microphone is the Vexo band (or a clip played as PCM)."""

    def __init__(self) -> None:
        super().__init__(name="vexo-band")
        params = TransportParams(
            audio_in_enabled=True,
            audio_in_passthrough=True,
            audio_in_sample_rate=SAMPLE_RATE,
            audio_out_enabled=False,
        )
        self._input = BandAudioInput(params)
        self._output = _NullOutput()

    def input(self) -> FrameProcessor:
        return self._input

    def output(self) -> FrameProcessor:
        return self._output

    def feed_pcm(self, pcm: bytes) -> None:
        self._input.feed_pcm(pcm)


class MockDeepgramSTTService(FrameProcessor):
    """Offline stand-in for DeepgramSTTService, in the same place in the pipeline.

    It consumes InputAudioRawFrame and, once enough PCM has arrived, emits the
    next scripted event: an interim result and a final TranscriptionFrame, or a
    tap. It does not transcribe the audio and it does not use the network.
    Frames that are not audio, including taps from the band, are passed on.
    """

    def __init__(self, lines=None, events=None, bytes_per_line: int = 16000) -> None:
        super().__init__()
        if events is None:
            events = [("text", str(line).strip()) for line in (lines or []) if str(line).strip()]
        self._events = list(events)
        self._bytes_per_line = max(1, int(bytes_per_line))
        self._seen = 0
        self._index = 0
        self._speakers = 0
        self.audio_bytes = 0

    async def process_frame(self, frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, InputAudioRawFrame) and frame.audio:
            self.audio_bytes += len(frame.audio)
            self._seen += len(frame.audio)
            while self._index < len(self._events) and self._seen >= self._bytes_per_line:
                self._seen -= self._bytes_per_line
                await self._emit(self._events[self._index])
                self._index += 1
            return
        await self.push_frame(frame, direction)

    async def _emit(self, event) -> None:
        kind = event[0]
        if kind == "text":
            line = event[1]
            # Two speakers, labelled the way Deepgram diarization labels them.
            number = (self._speakers % 2) + 1
            speaker = TranscriptionSpeaker(label=str(self._speakers % 2), number=number)
            self._speakers += 1
            stamp = time_now_iso8601()
            await self.push_frame(InterimTranscriptionFrame(line, "", stamp, speaker=speaker))
            await self.push_frame(TranscriptionFrame(line, "", stamp, speaker=speaker))
        elif kind == "tap":
            await self.push_frame(TapFrame(kind="tap"))
        elif kind == "double":
            await self.push_frame(TapFrame(kind="double-tap"))


def deepgram_key() -> str:
    """The Deepgram key, or "" when the run should stay offline. Never printed."""
    if os.environ.get("REMEMBER_STT", "").strip().lower() == "mock":
        return ""
    return os.environ.get("DEEPGRAM_API_KEY", "").strip()


def build_stt(api_key: str = "", lines=None, events=None, bytes_per_line: int = 16000):
    """DeepgramSTTService when a key is present, otherwise the in-pipeline mock."""
    if api_key:
        return DeepgramSTTService(
            api_key=api_key,
            encoding="linear16",
            channels=CHANNELS,
            sample_rate=SAMPLE_RATE,
            mip_opt_out=True,
            settings=DeepgramSTTService.Settings(
                model="nova-3-general",
                language=Language.EN,
                interim_results=True,
                diarize=True,
                punctuate=True,
                smart_format=True,
            ),
        )
    return MockDeepgramSTTService(lines=lines, events=events, bytes_per_line=bytes_per_line)


class RememberProcessor(FrameProcessor):
    """Transcript frames and taps become the open conversation and, in time, a card.

    Diarized finals keep the speaker label Deepgram (or the mock) attached to
    the frame. The card text stays the words that were said.
    """

    def __init__(self, session) -> None:
        super().__init__()
        self.session = session
        self.speakers = []
        self.control_future = None

    def _control_done(self, future):
        try:
            future.result()
        except Exception:
            print("Problem: capture could not finish; check the status on the page.")

    async def process_frame(self, frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        try:
            if isinstance(frame, InterimTranscriptionFrame):
                text = (frame.text or "").strip()
                if text:
                    await asyncio.get_running_loop().run_in_executor(None, lambda: self.session.hear(text, False, speaker=getattr(getattr(frame, "speaker", None), "label", None), audio=True))
            elif isinstance(frame, TranscriptionFrame):
                text = (frame.text or "").strip()
                speaker = getattr(frame, "speaker", None)
                if speaker is not None:
                    self.speakers.append(speaker.label)
                if text:
                    await asyncio.get_running_loop().run_in_executor(None, lambda: self.session.hear(text, True, speaker=speaker.label if speaker is not None else None, audio=True))
            elif isinstance(frame, TapFrame):
                loop = asyncio.get_running_loop()
                if frame.kind == "double-tap":
                    if self.control_future is not None and not self.control_future.done():
                        # Ignore extra stop gestures while final speech is draining.
                        # Waiting here would block the very final frames end() needs.
                        pass
                    elif self.session.mic_on:
                        self.control_future = loop.run_in_executor(None, self.session.toggle)
                        self.control_future.add_done_callback(self._control_done)
                    else:
                        await loop.run_in_executor(None, self.session.toggle)
                else:
                    await loop.run_in_executor(None, self.session.mark)
        except Exception as error:
            print("Problem: %s" % error)
        await self.push_frame(frame, direction)


class VoicePipeline:
    """One Pipecat pipeline for the life of the program."""

    def __init__(self, session, live: bool = False, lines=None, events=None, bytes_per_line: int = 16000) -> None:
        self.session = session
        key = deepgram_key() if live else ""
        self.live = bool(key)
        self.bytes_per_line = bytes_per_line
        self.transport = BandAudioTransport()
        self.stt = build_stt(
            key,
            lines=lines,
            events=events,
            bytes_per_line=bytes_per_line,
        )
        self.processor = RememberProcessor(session)
        self.pipeline = Pipeline([self.transport.input(), self.stt, self.processor])
        self.worker = PipelineWorker(
            self.pipeline,
            name="remember",
            params=PipelineParams(audio_in_sample_rate=SAMPLE_RATE),
            idle_timeout_secs=None,
            enable_rtvi=False,
        )
        # WorkerRunner asks for the running loop, so it is created on the pipeline thread.
        self.runner = None
        self.loop = None
        self._thread = None
        self._ready = threading.Event()
        self._lock = threading.Lock()
        self._started = False
        self._early_taps = []

        @self.worker.event_handler("on_pipeline_started")
        async def _on_started(_worker, _frame):
            with self._lock:
                taps = list(self._early_taps)
                self._early_taps.clear()
                self._started = True
            for kind in taps:
                await self.worker.queue_frame(TapFrame(kind=kind))
            self._ready.set()

        @self.worker.event_handler("on_pipeline_error")
        async def _on_error(_worker, _frame):
            print("Problem (speech): the transcription pipeline reported an error. "
                  "You can still type a line on the page.")
            session.progress("transcription", "error", "Speech recognition reported a connection error. Reconnect the band if words do not resume.")

    def start(self, timeout: float = 15) -> None:
        self._thread = threading.Thread(target=self._thread_main, name="remember-voice", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout):
            raise RuntimeError("The transcription pipeline did not start.")

    def _thread_main(self) -> None:
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self._run())
        finally:
            self.loop.close()

    async def _run(self) -> None:
        self.runner = WorkerRunner(handle_sigint=False)
        await self.runner.add_workers(self.worker)
        await self.runner.run()

    def feed_pcm(self, pcm: bytes) -> None:
        self.transport.feed_pcm(pcm)

    def feed_tap(self, kind: str) -> None:
        with self._lock:
            if not self._started or self.loop is None:
                self._early_taps.append(kind)
                return
            loop = self.loop
        asyncio.run_coroutine_threadsafe(self.worker.queue_frame(TapFrame(kind=kind)), loop)

    def stop(self) -> None:
        loop = self.loop
        if loop is not None and loop.is_running():
            try:
                asyncio.run_coroutine_threadsafe(self.worker.queue_frame(EndFrame()), loop).result(timeout=5)
            except Exception:
                runner = self.runner
                if runner is not None:
                    try:
                        asyncio.run_coroutine_threadsafe(runner.cancel(), loop).result(timeout=3)
                    except Exception:
                        pass
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
