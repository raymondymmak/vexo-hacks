"""Turn a stream of words and taps into one conversation at a time.

Double-tap closes the person you are with and opens the next one.
A single tap marks a moment, with the words just before it and the next line.
"""

from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class Moment:
    def __init__(self, text: str, at: str, pending: bool) -> None:
        self.text = text
        self.at = at
        self.pending = pending


class OpenConversation:
    def __init__(self) -> None:
        self.started_at = _now()
        self.lines = []
        self.partial = ""
        self.moments = []

    def transcript(self) -> str:
        parts = list(self.lines)
        if self.partial:
            parts.append(self.partial)
        return " ".join(part.strip() for part in parts if part and part.strip()).strip()

    def snippet(self, words: int = 40) -> str:
        return " ".join(self.transcript().split()[-words:])

    def has_content(self) -> bool:
        if self.lines or self.partial.strip():
            return True
        return any(moment.pending or moment.text.strip() for moment in self.moments)


class ClosedConversation:
    def __init__(self, transcript: str, moments: list, started_at: str, ended_at: str) -> None:
        self.transcript = transcript
        self.moments = moments
        self.started_at = started_at
        self.ended_at = ended_at


class Splitter:
    """One open conversation, or none."""

    def __init__(self) -> None:
        self.open = None

    def hear(self, text: str, final: bool) -> None:
        """Add words. A final line is kept. A partial line replaces the previous guess.

        Hearing words while nobody is open starts a conversation, so the first
        sentence is not dropped when the double-tap comes a moment late.
        """
        text = (text or "").strip()
        if self.open is None:
            self.open = OpenConversation()
        if final:
            if text:
                self.open.lines.append(text)
                for moment in self.open.moments:
                    if moment.pending:
                        moment.text = (moment.text + " " + text).strip()
                        moment.pending = False
            self.open.partial = ""
        else:
            self.open.partial = text

    def mark(self) -> str:
        """Single tap: remember the words around this moment. Returns that snippet."""
        if self.open is None:
            self.open = OpenConversation()
        text = self.open.snippet()
        self.open.moments.append(Moment(text, _now(), pending=True))
        return text

    def double_tap(self):
        """Close the current person if they said anything, then open the next one.

        Returns the closed conversation, or None when there was nothing to save.
        """
        closed = None
        if self.open is not None and self.open.has_content():
            closed = self._close()
        if self.open is None:
            self.open = OpenConversation()
        return closed

    def end(self):
        """Close the current person and leave nobody open. None if it was empty."""
        if self.open is None or not self.open.has_content():
            self.open = None
            return None
        return self._close()

    def _close(self) -> ClosedConversation:
        current = self.open
        self.open = None
        moments = []
        for moment in current.moments:
            text = moment.text.strip() or current.snippet()
            if text and text not in moments:
                moments.append(text)
        return ClosedConversation(current.transcript(), moments, current.started_at, _now())
