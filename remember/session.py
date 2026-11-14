"""The pipeline: words and taps in, a person card in the store, a live update out."""

import queue
import threading

from .extract import attach_moments, make_extractor
from .kitpath import plain
from .splitter import Splitter
from .store import Store


class Session:
    def __init__(self, db_path, extractor=None) -> None:
        self.store = Store(db_path)
        self.extractor = extractor if extractor is not None else make_extractor()
        self.splitter = Splitter()
        self.lock = threading.Lock()
        self.subs = []
        self.on_saved = None
        self.band_label = "Not connected"
        self.sim = False
        self.mic_on = False
        self.note = "Type a line, or play the demo."

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
        inbox = queue.Queue()
        with self.lock:
            self.subs.append(inbox)
        return inbox

    def unsubscribe(self, inbox: queue.Queue) -> None:
        with self.lock:
            if inbox in self.subs:
                self.subs.remove(inbox)

    def hear(self, text: str, final: bool = True) -> dict:
        with self.lock:
            self.splitter.hear(text, final)
            status = self._status()
        self._publish({"type": "status", "status": status})
        return status

    def mark(self) -> dict:
        with self.lock:
            self.splitter.mark()
            status = self._status()
        self._publish({"type": "status", "status": status})
        return status

    def new_person(self):
        """Double-tap: save the current person if there is one, then start the next."""
        with self.lock:
            closed = self.splitter.double_tap()
            status = self._status()
        self._publish({"type": "status", "status": status})
        if closed is None:
            return None
        return self._finish(closed)

    def end(self):
        """Close the current person and go idle."""
        with self.lock:
            closed = self.splitter.end()
            status = self._status()
        self._publish({"type": "status", "status": status})
        if closed is None:
            return None
        return self._finish(closed)

    def reset(self) -> dict:
        with self.lock:
            self.store.reset()
            self.splitter = Splitter()
            status = self._status()
        self._publish({"type": "reset", "status": status})
        return status

    def state(self, query: str = "") -> dict:
        with self.lock:
            people = self.store.search(query) if query else self.store.list_people()
            status = self._status()
        return {"people": people, "status": status}

    def _finish(self, closed):
        card = self.extractor.extract(closed.transcript, closed.moments)
        card = attach_moments(card, closed.moments)
        person = self.store.merge(
            card,
            transcript=closed.transcript,
            moments=card.get("key_moments") or closed.moments,
            started_at=closed.started_at,
            ended_at=closed.ended_at,
        )
        with self.lock:
            status = self._status()
        self._publish({"type": "card", "person": person, "status": status})
        verb = "Updated" if person["conversation_count"] > 1 else "Saved"
        print("%s %s." % (verb, plain(person["name"])))
        if self.on_saved is not None:
            self.on_saved(person)
        return person

    def _status(self) -> dict:
        current = self.splitter.open
        return {
            "band": self.band_label,
            "sim": self.sim,
            "mic_on": self.mic_on,
            "extractor": getattr(self.extractor, "label", ""),
            "note": self.note,
            "phase": "talking" if current is not None else "idle",
            "transcript": current.transcript() if current is not None else "",
            "moments": [moment.text for moment in current.moments] if current is not None else [],
            "people": self.store.count(),
        }

    def _publish(self, payload: dict) -> None:
        with self.lock:
            listeners = list(self.subs)
        for inbox in listeners:
            inbox.put(payload)
