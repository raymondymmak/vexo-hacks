"""The pipeline: words and taps in, a person card in the store, a live update out."""

import queue
import threading
import time

from .extract import attach_moments, make_extractor
from .kitpath import plain
from .splitter import Splitter, OpenConversation
from .store import Store


class Session:
    def __init__(self, db_path, extractor=None, retain_transcripts=True) -> None:
        self.store = Store(db_path)
        self.retain_transcripts = retain_transcripts
        self.extractor = extractor if extractor is not None else make_extractor()
        self.splitter = Splitter()
        self.lock = threading.Lock()
        self.subs = []
        self.on_saved = None
        self.on_contact_ready = None
        self.on_reset = None
        self.on_capture = None
        self.processing = False
        self.pipeline = {}
        self.room = {"status": "disabled", "alerts": []}
        self.last_audio = 0
        self.partial_speaker = None
        self.error = None
        self.wearer_speaker = None
        self.speaker_lines = []
        self.operation_lock = threading.RLock()
        self.band_label = "Not connected"
        self.sim = False
        self.mic_on = False
        self.note = "Type a line, or play the demo."

    def progress(self, stage, state, detail, **metadata):
        with self.lock:
            self.pipeline[stage] = {"status": state, "detail": detail, "updated_at": time.time(), **metadata}
            status = self._status()
        self._publish({"type": "status", "status": status})

    def close(self) -> None:
        self.store.close()

    def set_band(self, label: str, sim: bool, mic_on: bool, note: str) -> None:
        with self.lock:
            self.band_label = label
            self.sim = sim
            self.mic_on = mic_on
            self.note = note
            status = self._status()
        self._publish({"type": "status", "status": status})

    def subscribe(self) -> queue.Queue:
        inbox = queue.Queue(maxsize=32)
        with self.lock:
            self.subs.append(inbox)
        return inbox

    def unsubscribe(self, inbox: queue.Queue) -> None:
        with self.lock:
            if inbox in self.subs:
                self.subs.remove(inbox)

    def hear(self, text: str, final: bool = True, speaker=None, audio=False) -> dict:
        with self.lock:
            if self.processing or (audio and self.splitter.open is None):
                return self._status()
            if audio:
                self.last_audio = time.monotonic()
            self.splitter.hear(text, final)
            self.partial_speaker = None if final else (str(speaker) if speaker is not None else None)
            if final and text.strip():
                self.speaker_lines.append((str(speaker) if speaker is not None else None, text.strip()))
            status = self._status()
        self._publish({"type": "status", "status": status})
        return status

    def mark(self) -> dict:
        with self.lock:
            self.splitter.mark()
            status = self._status()
        self._publish({"type": "status", "status": status})
        return status

    def start(self):
        # Motion callbacks must not wait for a model call to finish and then
        # accidentally start the next conversation from an old handshake.
        with self.lock:
            if self.processing:
                return self._status()
        with self.operation_lock:
            with self.lock:
                if self.processing or self.splitter.open is not None:
                    return self._status()
                self.pipeline = {}
                self.splitter.open = OpenConversation()
                self.speaker_lines = []
                self.wearer_speaker = None
                self.partial_speaker = None
                self.error = None
            try:
                if self.on_capture:
                    self.on_capture(True)
            except Exception:
                with self.lock:
                    self.splitter.open = None
                    self.note = "Could not start microphone. Reconnect and try again."
                    self.error = self.note
                    status = self._status()
                self._publish({"type": "status", "status": status})
                raise
            self.progress("capture", "active", "Listening for speech; speaker-labelled dialogue appears below.")
            status = self.state()["status"]
            self._publish({"type": "status", "status": status})
            return status

    def set_wearer(self, speaker):
        with self.lock:
            self.wearer_speaker = speaker or None
            status = self._status()
        self._publish({"type": "status", "status": status})
        return status

    def toggle(self):
        with self.lock:
            if self.processing:
                return self._status()
        with self.operation_lock:
            if self.splitter.open is None:
                return self.start()
            return self.end()

    def new_person(self):
        return self.toggle()

    def end(self, person_id=None):
        with self.operation_lock:
            finished_at = time.monotonic()
            was_listening = self.mic_on
            try:
                if self.on_capture:
                    self.on_capture(False)
            except Exception:
                with self.lock:
                    self.error = "Could not stop microphone. Reconnect the band and try again."
                    status = self._status()
                self._publish({"type": "status", "status": status})
                raise
            if self.splitter.open is not None:
                self.progress("capture", "done", "Microphone stopped.")
                self.progress("transcription", "active", "Waiting for the final words from transcription.")
            if was_listening:
                # Let queued audio/STT finals arrive after the physical microphone stops.
                # This bounded grace period also preserves the last interim if the
                # service never emits a final. It is not a network flush guarantee.
                start = time.monotonic()
                deadline = start + 2.5
                while time.monotonic() < deadline:
                    now = time.monotonic()
                    if now - start >= 1.0 and now - self.last_audio >= 0.5:
                        break
                    time.sleep(0.05)
            with self.lock:
                current = self.splitter.open
                lines = list(self.speaker_lines)
                if current is not None and current.partial.strip():
                    lines.append((self.partial_speaker, current.partial.strip()))
                closed = self.splitter.end()
                wearer = self.wearer_speaker
                self.speaker_lines = []
                self.processing = closed is not None
                self.error = None
                status = self._status()
            self._publish({"type": "status", "status": status})
            if closed is None:
                return None
            self.progress("transcription", "done", "Final dialogue assembled.")
            if lines:
                closed.transcript = "\n".join(
                    "[%s] %s" % ("wearer" if wearer is not None and who == wearer
                                  else "speaker " + who if who is not None else "unknown", text)
                    for who, text in lines)
            try:
                closed.utterances = [{"speaker": who, "text": text, "final": True,
                                      "role": "wearer" if wearer is not None and who == wearer else "other" if who is not None else "unknown"}
                                     for who, text in lines]
                closed.finished_at = finished_at
                person = self._finish(closed, person_id=person_id)
                with self.lock:
                    self.speaker_lines = []
                    self.partial_speaker = None
                    self.wearer_speaker = None
                # Drop transient raw capture after successful persistence.
                if current is not None:
                    current.lines.clear()
                    current.partial = ""
                    current.moments.clear()
                lines.clear()
                closed.transcript = ""
                closed.moments.clear()
                return person
            except Exception:
                failed = "save" if self.pipeline.get("save", {}).get("status") == "active" else "extraction"
                self.progress(failed, "error", "Could not finish. Transcript retained; use End to retry.")
                # Keep the original capture available for review or an explicit retry.
                with self.lock:
                    self.splitter.open = current
                    self.speaker_lines = list(lines[:-1] if current and current.partial.strip() else lines)
                    self.error = "Could not save this conversation. Your transcript is retained; try End again."
                raise
            finally:
                with self.lock:
                    self.processing = False
                    status = self._status()
                self._publish({"type": "status", "status": status})

    def discard(self) -> dict:
        """Stop capture and forget its transient words without deleting contacts."""
        with self.operation_lock:
            if self.on_capture:
                self.on_capture(False)
            with self.lock:
                current = self.splitter.open
                if current is not None:
                    current.lines.clear()
                    current.partial = ""
                    current.moments.clear()
                self.pipeline = {}
                self.splitter = Splitter()
                self.speaker_lines = []
                self.wearer_speaker = None
                self.partial_speaker = None
                self.error = None
                self.mic_on = False
                status = self._status()
            self._clear_transient_events()
            self._publish({"type": "status", "status": status})
            return status

    def reset(self) -> dict:
        # Wait for any in-flight extraction so it cannot recreate a card after reset.
        with self.operation_lock:
            if self.on_capture:
                self.on_capture(False)
            if self.on_reset:
                self.on_reset()
            with self.lock:
                self.speaker_lines = []
                self.wearer_speaker = None
                self.partial_speaker = None
                self.error = None
                self.store.reset()
                self.pipeline = {}
                self.splitter = Splitter()
                status = self._status()
            self._clear_transient_events()
            self._publish({"type": "reset", "status": status})
            return status

    def state(self, query: str = "") -> dict:
        with self.lock:
            people = self.store.search(query) if query else self.store.list_people()
            status = self._status()
        return {"people": people, "status": status}

    def _finish(self, closed, person_id=None):
        self.progress("extraction", "active", "Extracting contact details, notes, and promises.")
        card = self.extractor.extract(closed.transcript, closed.moments)
        self.progress("extraction", "done", "AI unavailable; used the built-in extractor. Review contact details." if getattr(self.extractor, "used_fallback", False) else "Contact details extracted.")
        card = attach_moments(card, closed.moments)
        # Keep transcript history locally; contact summaries and exports omit it.
        card["key_moments"] = []
        self.progress("save", "active", "Saving the contact and conversation locally.")
        person = self.store.merge(
            card,
            transcript=closed.transcript if self.retain_transcripts else "",
            utterances=getattr(closed, "utterances", []) if self.retain_transcripts else [],
            moments=[],
            started_at=closed.started_at,
            ended_at=closed.ended_at,
            person_id=person_id,
        )
        self.progress("save", "done", "Saved on this computer.", person_id=person["id"])
        with self.lock:
            status = self._status()
        self._clear_transient_events()
        self._publish({"type": "card", "person": person, "status": status})
        verb = "Updated" if person["conversation_count"] > 1 else "Saved"
        print("%s %s." % (verb, plain(person["name"])))
        if self.on_saved is not None:
            try:
                self.on_saved(person)
            except Exception:
                # Peripheral feedback must not turn a committed card into a failed save.
                with self.lock:
                    self.note = "Contact saved; band feedback was unavailable."
        if self.on_contact_ready is not None:
            try:
                self.on_contact_ready(dict(person, _finished_at=getattr(closed, "finished_at", time.monotonic()), _marked=bool(closed.moments)))
            except Exception:
                with self.lock:
                    self.note = "Contact saved; delivery could not be queued."
        return person

    def _status(self) -> dict:
        current = self.splitter.open
        utterances = []
        if current is not None:
            utterances = [{"speaker": who, "text": text, "final": True} for who, text in self.speaker_lines]
            if current.partial.strip():
                utterances.append({"speaker": self.partial_speaker, "text": current.partial.strip(), "final": False})
        return {
            "room": dict(self.room),
            "pipeline": {key: dict(value) for key, value in self.pipeline.items()},
            "band": self.band_label,
            "sim": self.sim,
            "mic_on": self.mic_on,
            "extractor": getattr(self.extractor, "label", ""),
            "note": self.error or self.note,
            "error": self.error,
            "phase": "processing" if self.processing else "talking" if current is not None else "idle",
            "speakers": sorted({turn["speaker"] for turn in utterances if turn["speaker"] is not None}),
            "utterances": utterances,
            "wearer_speaker": self.wearer_speaker,
            "transcript": current.transcript() if current is not None else "",
            "moments": [moment.text for moment in current.moments] if current is not None else [],
            "people": self.store.count(),
        }

    def _clear_transient_events(self):
        """Discard queued live-transcript status snapshots after save/discard/reset."""
        with self.lock:
            listeners = list(self.subs)
        for inbox in listeners:
            with inbox.mutex:
                retained = [event for event in inbox.queue
                            if not isinstance(event, dict) or event.get("type") != "status"]
                removed = len(inbox.queue) - len(retained)
                inbox.queue.clear()
                inbox.queue.extend(retained)
                inbox.unfinished_tasks = max(0, inbox.unfinished_tasks - removed)
                inbox.not_full.notify_all()

    def _publish(self, payload: dict) -> None:
        with self.lock:
            listeners = list(self.subs)
        for inbox in listeners:
            try:
                inbox.put_nowait(payload)
            except queue.Full:
                try:
                    inbox.get_nowait()
                    inbox.task_done()
                except queue.Empty:
                    pass
                try:
                    inbox.put_nowait(payload)
                except queue.Full:
                    # Another producer won the race. The client can refresh state.
                    pass
