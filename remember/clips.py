"""Load a recording as 16 kHz mono PCM for the Pipecat pipeline.

Opus files are decoded with the kit. Wav files must already be 16 kHz mono
16-bit PCM, which is what the band's microphone decodes to.
"""

import wave


def load_pcm(path: str) -> bytes:
    """Return raw PCM bytes for a .wav or a kit .opus/.ogg recording."""
    lower = path.lower()
    if lower.endswith(".wav"):
        return _wav(path)
    if lower.endswith(".opus") or lower.endswith(".ogg"):
        return _opus(path)
    raise RuntimeError("A clip should be a .wav, .ogg, or .opus file.")


def _wav(path: str) -> bytes:
    with wave.open(path, "rb") as handle:
        channels = handle.getnchannels()
        width = handle.getsampwidth()
        rate = handle.getframerate()
        if channels != 1 or width != 2 or rate != 16000:
            raise RuntimeError(
                "This wav is %d Hz, %d channel(s), %d bytes per sample. "
                "The pipeline wants 16 kHz mono 16-bit PCM." % (rate, channels, width)
            )
        return handle.readframes(handle.getnframes())


def _opus(path: str) -> bytes:
    from .kitpath import ensure_kit
    ensure_kit()
    from vexo_open import audio
    from vexo_open.audio import PcmDecoder

    packets = audio.ogg_packets(path)
    decoder = PcmDecoder()
    parts = [decoder.feed(packet, index) for index, packet in enumerate(packets)]
    tail = decoder.flush() if hasattr(decoder, "flush") else b""
    return b"".join(parts) + (tail or b"")


def play_pcm(pipeline, pcm: bytes, chunk: int, pace: float = 0.0, sleep=None) -> None:
    """Push PCM through the pipeline in chunks. pace is the pause between chunks."""
    import time

    wait = sleep or time.sleep
    step = max(1, int(chunk))
    for start in range(0, len(pcm), step):
        pipeline.feed_pcm(pcm[start:start + step])
        if pace > 0:
            wait(pace)
