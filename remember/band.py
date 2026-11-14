"""Connect to one Vexo Open band, and flash green when a card is saved.

The band number comes from the command line. This file never picks a number and
never scans for whatever band is nearby. Pass --sim to use the pretend band.

Microphone audio is decoded to PCM and handed to the Pipecat pipeline. Taps go
in as TapFrame. The LED cannot change while the microphone is on (the kit
answers "denied"), so a saved card pauses the microphone, sets the light, then
turns the microphone back on.
"""

import threading

from .kitpath import ensure_kit

_LED_LOCK = threading.Lock()


def connect_band(number, sim: bool, replay: str = ""):
    """Connect to the band with this number, or to the pretend band when sim is set.

    number is required for a real band. For the pretend band it is optional: when
    you pass it, the pretend band reports that number and the library checks it.
    """
    ensure_kit()
    from vexo_open.sync import Band
    from vexo_open.transports.sim import SimTransport

    packets = _voice_packets(replay) if sim and replay else None
    if sim:
        # auto_tap is off so the pretend band does not invent taps every few seconds.
        if number is None:
            transport = SimTransport(auto_tap=False, audio_packets=packets)
            band = Band.connect(None, transport=transport)
        else:
            transport = SimTransport(number, auto_tap=False, audio_packets=packets)
            band = Band.connect(number, transport=transport)
    else:
        if number is None:
            raise RuntimeError("A real band needs the number on its bag.")
        band = Band.connect(number)
    if number is not None and band.number != number:
        band.disconnect()
        raise RuntimeError("The band that answered was not the one you asked for. Disconnected.")
    return band


def _voice_packets(replay: str):
    if not replay:
        return None
    ensure_kit()
    from vexo_open import audio
    return audio.ogg_packets(replay)


def flash_saved(band, seconds: float = 0.8) -> bool:
    """Turn the LED green for a moment. Returns True if the light accepted the colour.

    Pauses the microphone first. If the band still refuses (code "denied"), the
    card stays saved and this returns False. A disconnect while the light is
    changing is ignored: the card is already saved.
    """
    try:
        return _flash_saved(band, seconds)
    except BaseException:
        return False


def _streams_on(band) -> tuple:
    """Whether audio and mic-level are actually on. The streams dict always has the keys."""
    streams = getattr(band, "streams", None) or {}
    if isinstance(streams, dict):
        return bool(streams.get("audio")), bool(streams.get("mic_level") or streams.get("mic-level"))
    names = list(streams)
    return "audio" in names, ("mic-level" in names or "mic_level" in names)


def _flash_saved(band, seconds: float) -> bool:
    if band is None or not getattr(band, "connected", False):
        return False
    ensure_kit()
    from vexo_open.sync import VexoError

    with _LED_LOCK:
        audio_on, level_on = _streams_on(band)
        lit = False
        try:
            if audio_on or level_on:
                band.configure(audio=False, mic_level=False)
            band.set_led("#00ff00")
            lit = True
            if seconds > 0:
                band.wait(seconds)
            band.led_off()
        except VexoError as error:
            code = getattr(error, "code", None)
            if code == "denied":
                print("The band left its light alone (the microphone is on). The card is saved.")
            elif code != "not-connected":
                print("Problem (%s): %s" % (error.code, error))
            lit = False
        finally:
            if (audio_on or level_on) and getattr(band, "connected", False):
                try:
                    band.configure(audio=audio_on, mic_level=level_on)
                except VexoError as error:
                    if getattr(error, "code", None) != "not-connected":
                        print("Problem (%s): %s" % (error.code, error))
        return lit


class BandSession:
    """Own the band. Tap handlers only enqueue a frame; the pipeline does the work."""

    def __init__(self, session, number, sim: bool, pipeline=None, replay: str = "", listen: bool = False) -> None:
        self.session = session
        self.number = number
        self.sim = sim
        self.pipeline = pipeline
        self.replay = replay
        self.listen = listen
        self.band = None
        self.decoder = None

    def start(self):
        self.band = connect_band(self.number, self.sim, self.replay if self.listen else "")
        self.band.on("tap", lambda _event: self._gesture("tap"))
        self.band.on("double-tap", lambda _event: self._gesture("double-tap"))
        self.band.configure(events=True)
        mic_on = False
        if self.listen:
            mic_on = self._start_mic()
        if mic_on:
            note = "Listening through Pipecat and Deepgram. The blue light means the microphone is on."
        elif self.pipeline is not None and not self.listen:
            note = "Pipecat is running offline. Nothing is sent to Deepgram."
        else:
            note = "Double-tap starts a person. A single tap marks a moment."
        where = "Pretend band" if self.sim else "Band"
        self.session.set_band("%s %s" % (where, self.band.name), self.sim, mic_on, note)
        self.session.on_saved = self._flash
        print("Connected to %s. %s" % (self.band.name, note))
        return self.band

    def _gesture(self, kind: str) -> None:
        if self.pipeline is not None:
            self.pipeline.feed_tap(kind)
            return
        if kind == "double-tap":
            self.session.new_person()
        else:
            self.session.mark()

    def _flash(self, _person) -> None:
        threading.Thread(target=flash_saved, args=(self.band,), daemon=True).start()

    def _start_mic(self) -> bool:
        """Decode the band's Opus packets to PCM and feed the Pipecat pipeline."""
        ensure_kit()
        from vexo_open.audio import PcmDecoder
        from vexo_open.sync import VexoError

        print("Loading the sound decoder (a few seconds, the first time)...")
        self.decoder = PcmDecoder()
        print("Note: while this runs, everything the band's microphone hears is sent over "
              "the internet to Deepgram (deepgram.com) through Pipecat. Tell the people near the band.")

        def on_audio(event):
            pcm = self.decoder.feed(event)
            if pcm and self.pipeline is not None:
                self.pipeline.feed_pcm(pcm)

        self.band.on("audio", on_audio)
        try:
            self.band.configure(audio=True)
        except VexoError as error:
            print("Problem (%s): %s" % (error.code, error))
            return False
        return True

    def shutdown(self) -> None:
        if self.band is not None:
            try:
                self.band.disconnect()
            except Exception:
                pass
            self.band = None
