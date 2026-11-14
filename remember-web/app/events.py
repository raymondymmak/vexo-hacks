"""Tiny in-process pub/sub that fans events out to every SSE client."""
import asyncio
import json
import time
from typing import Any, AsyncIterator, Dict, Set


_CLOSE = {"event": "__close__"}


class Broker:
    def __init__(self) -> None:
        self._subs: Set[asyncio.Queue] = set()
        self._closing = False

    def close_all(self) -> None:
        """End every open stream so the server can shut down or reload without hanging."""
        self._closing = True
        for q in list(self._subs):
            try:
                q.put_nowait(_CLOSE)
            except asyncio.QueueFull:
                pass

    def publish(self, event: str, data: Dict[str, Any]) -> None:
        msg = {"event": event, "data": {**data, "ts": time.time()}}
        for q in list(self._subs):
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                pass  # slow client; it'll resync from /api/state on reconnect

    async def stream(self, disconnected) -> AsyncIterator[str]:
        q: asyncio.Queue = asyncio.Queue(maxsize=256)
        self._subs.add(q)
        try:
            yield "retry: 2000\n\n"
            yield _fmt("hello", {"clients": len(self._subs)})
            while not self._closing:
                if await disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=15)
                    if msg is _CLOSE:
                        break
                    yield _fmt(msg["event"], msg["data"])
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            self._subs.discard(q)


def _fmt(event: str, data: Dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


broker = Broker()
