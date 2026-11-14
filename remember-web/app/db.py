"""SQLite persistence for Remember.

Plain sqlite3 is plenty for a single-room demo server. All access happens on the
event-loop thread, guarded by a lock so background tasks can't interleave writes.
"""
import json
import os
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

DB_PATH = Path(os.getenv("REMEMBER_DB", Path(__file__).resolve().parent.parent / "remember.db"))
DEFAULT_BANDS = ["A", "B", "C"]

_lock = threading.RLock()
_conn: Optional[sqlite3.Connection] = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS people (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    company TEXT DEFAULT '',
    role TEXT DEFAULT '',
    email TEXT DEFAULT '',
    phone TEXT DEFAULT '',
    socials TEXT DEFAULT '{}',
    needs TEXT DEFAULT '[]',
    offers TEXT DEFAULT '[]',
    photo BLOB,
    photo_type TEXT DEFAULT '',
    photo_ver REAL DEFAULT 0,
    photo_link TEXT DEFAULT '',
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS bands (
    id TEXT PRIMARY KEY,
    owner_person_id INTEGER REFERENCES people(id) ON DELETE SET NULL,
    led TEXT NOT NULL DEFAULT 'idle',
    updated_at REAL NOT NULL
);
-- One row per conversation recorded by a band: the chat-style history.
-- transcript = JSON list of {"who": "me"|"them", "speaker": str, "text": str}.
-- It belongs to the band owner only and can be deleted any time.
CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    band_id TEXT NOT NULL,
    counterpart_person_id INTEGER REFERENCES people(id) ON DELETE SET NULL,
    summary TEXT DEFAULT '',
    extraction TEXT DEFAULT '{}',
    engine TEXT DEFAULT '',
    transcript TEXT DEFAULT '[]',
    created_at REAL NOT NULL
);
-- A contact is a card that lives in ONE band owner's phone.
CREATE TABLE IF NOT EXISTS contacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_band_id TEXT NOT NULL,
    person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    source TEXT NOT NULL DEFAULT 'conversation',
    conversation_summary TEXT DEFAULT '',
    why_remember TEXT DEFAULT '',
    promises TEXT DEFAULT '[]',
    match_reason TEXT DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE(owner_band_id, person_id)
);
CREATE TABLE IF NOT EXISTS matches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    person_a_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    person_b_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    reason TEXT DEFAULT '',
    introducer_person_id INTEGER REFERENCES people(id) ON DELETE SET NULL,
    created_at REAL NOT NULL,
    UNIQUE(person_a_id, person_b_id, kind)
);
"""


def conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA foreign_keys = ON")
        _conn.executescript(SCHEMA)
        cols = {r["name"] for r in _conn.execute("PRAGMA table_info(conversations)")}
        if "transcript" not in cols:  # databases created before chat history existed
            _conn.execute("ALTER TABLE conversations ADD COLUMN transcript TEXT DEFAULT '[]'")
            _conn.commit()
        pcols = {r["name"] for r in _conn.execute("PRAGMA table_info(people)")}
        for col, ddl in (("photo", "BLOB"), ("photo_type", "TEXT DEFAULT ''"), ("photo_ver", "REAL DEFAULT 0"),
                         ("photo_link", "TEXT DEFAULT ''")):
            if col not in pcols:  # databases created before profile photos existed
                _conn.execute(f"ALTER TABLE people ADD COLUMN {col} {ddl}")
        _conn.commit()
        ensure_default_bands()
    return _conn


def ensure_default_bands() -> None:
    with _lock:
        for b in DEFAULT_BANDS:
            _conn.execute(
                "INSERT OR IGNORE INTO bands (id, led, updated_at) VALUES (?, 'idle', ?)", (b, time.time())
            )
        _conn.commit()


def reset() -> None:
    with _lock:
        c = conn()
        c.executescript(
            "DELETE FROM matches; DELETE FROM contacts; DELETE FROM conversations;"
            "UPDATE bands SET owner_person_id = NULL, led = 'idle'; DELETE FROM people;"
            "DELETE FROM settings; DELETE FROM sqlite_sequence;"
        )
        c.commit()


# ---------------------------------------------------------------- helpers
def _j(v: Any, default: Any) -> Any:
    if v is None or v == "":
        return default
    try:
        return json.loads(v)
    except (TypeError, ValueError):
        return default


def _photo_url(row: sqlite3.Row) -> str:
    """Uploaded/linked photo first, then the person's GitHub avatar; '' means use initials."""
    if row["photo_type"]:
        return f"/api/people/{row['id']}/photo?v={int(row['photo_ver'] or 0)}"
    if row["photo_link"]:
        return row["photo_link"]
    gh = (_j(row["socials"], {}) or {}).get("github", "") or ""
    m = re.search(r"github\.com/([A-Za-z0-9-]{1,39})", gh)
    return f"https://github.com/{m.group(1)}.png?size=240" if m else ""


def person_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "photo": _photo_url(row),
        "id": row["id"],
        "name": row["name"],
        "company": row["company"] or "",
        "role": row["role"] or "",
        "email": row["email"] or "",
        "phone": row["phone"] or "",
        "socials": _j(row["socials"], {}),
        "needs": _j(row["needs"], []),
        "offers": _j(row["offers"], []),
    }


# ---------------------------------------------------------------- people
def get_person(pid: Optional[int]) -> Optional[Dict[str, Any]]:
    if pid is None:
        return None
    row = conn().execute("SELECT * FROM people WHERE id = ?", (pid,)).fetchone()
    return person_dict(row) if row else None


def find_person_by_name(name: str) -> Optional[Dict[str, Any]]:
    """Exact (case-insensitive) match first, then first-name match against band owners."""
    if not name:
        return None
    n = name.strip().lower()
    row = conn().execute("SELECT * FROM people WHERE lower(name) = ?", (n,)).fetchone()
    if row:
        return person_dict(row)
    first = n.split()[0]
    rows = conn().execute("SELECT * FROM people").fetchall()
    for r in rows:
        if r["name"].strip().lower().split()[0] == first:
            return person_dict(r)
    return None


def create_person(data: Dict[str, Any]) -> int:
    with _lock:
        cur = conn().execute(
            "INSERT INTO people (name, company, role, email, phone, socials, needs, offers, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                (data.get("name") or "Unknown").strip(),
                data.get("company") or "",
                data.get("role") or "",
                data.get("email") or "",
                data.get("phone") or "",
                json.dumps(data.get("socials") or {}),
                json.dumps(data.get("needs") or []),
                json.dumps(data.get("offers") or []),
                time.time(),
            ),
        )
        conn().commit()
        return cur.lastrowid


def merge_person(pid: int, data: Dict[str, Any]) -> None:
    """Fill blanks and union list fields; never wipe what we already know."""
    cur = get_person(pid)
    if not cur:
        return
    for k in ("company", "role", "email", "phone"):
        if data.get(k) and not cur[k]:
            cur[k] = data[k]
    socials = dict(cur["socials"])
    for k, v in (data.get("socials") or {}).items():
        if v and not socials.get(k):
            socials[k] = v
    needs = _union(cur["needs"], data.get("needs") or [])
    offers = _union(cur["offers"], data.get("offers") or [])
    with _lock:
        conn().execute(
            "UPDATE people SET company=?, role=?, email=?, phone=?, socials=?, needs=?, offers=? WHERE id=?",
            (cur["company"], cur["role"], cur["email"], cur["phone"], json.dumps(socials),
             json.dumps(needs), json.dumps(offers), pid),
        )
        conn().commit()


def _union(a: List[str], b: List[str]) -> List[str]:
    out, seen = [], set()
    for x in list(a) + list(b):
        k = x.strip().lower()
        if x.strip() and k not in seen:
            seen.add(k)
            out.append(x.strip())
    return out[:6]


def set_photo_link(pid: int, url: str) -> None:
    with _lock:
        conn().execute("UPDATE people SET photo_link = ? WHERE id = ?", (url, pid))
        conn().commit()


def get_setting(key: str, default: Any = None) -> Any:
    r = conn().execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return _j(r["value"], default) if r else default


def set_setting(key: str, value: Any) -> None:
    with _lock:
        conn().execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, json.dumps(value)))
        conn().commit()


def set_photo(pid: int, data: bytes, content_type: str) -> None:
    with _lock:
        conn().execute(
            "UPDATE people SET photo = ?, photo_type = ?, photo_ver = ? WHERE id = ?",
            (sqlite3.Binary(data), content_type, time.time(), pid),
        )
        conn().commit()


def clear_photo(pid: int) -> None:
    with _lock:
        conn().execute("UPDATE people SET photo = NULL, photo_type = '', photo_link = '', photo_ver = ? WHERE id = ?",
                       (time.time(), pid))
        conn().commit()


def get_photo(pid: int) -> Optional[tuple]:
    r = conn().execute("SELECT photo, photo_type FROM people WHERE id = ?", (pid,)).fetchone()
    if not r or not r["photo_type"] or r["photo"] is None:
        return None
    return bytes(r["photo"]), r["photo_type"]


def delete_person_if_orphan(pid: int) -> None:
    c = conn()
    owned = c.execute("SELECT 1 FROM bands WHERE owner_person_id = ?", (pid,)).fetchone()
    contacts = c.execute("SELECT 1 FROM contacts WHERE person_id = ?", (pid,)).fetchone()
    if not owned and not contacts:
        with _lock:
            c.execute("UPDATE conversations SET counterpart_person_id = NULL WHERE counterpart_person_id = ?", (pid,))
            c.execute("DELETE FROM people WHERE id = ?", (pid,))
            c.commit()


def list_people() -> List[Dict[str, Any]]:
    rows = conn().execute("SELECT * FROM people ORDER BY id").fetchall()
    owners = {r["owner_person_id"]: r["id"] for r in conn().execute("SELECT * FROM bands").fetchall()}
    out = []
    for r in rows:
        p = person_dict(r)
        p["band_id"] = owners.get(p["id"])
        out.append(p)
    return out


# ---------------------------------------------------------------- bands
def list_bands() -> List[Dict[str, Any]]:
    rows = conn().execute("SELECT * FROM bands ORDER BY id").fetchall()
    return [
        {"id": r["id"], "led": r["led"], "owner": get_person(r["owner_person_id"])}
        for r in rows
    ]


def get_band(band_id: str) -> Optional[Dict[str, Any]]:
    r = conn().execute("SELECT * FROM bands WHERE id = ?", (band_id,)).fetchone()
    if not r:
        return None
    return {"id": r["id"], "led": r["led"], "owner": get_person(r["owner_person_id"])}


def ensure_band(band_id: str) -> Dict[str, Any]:
    with _lock:
        conn().execute(
            "INSERT OR IGNORE INTO bands (id, led, updated_at) VALUES (?, 'idle', ?)", (band_id, time.time())
        )
        conn().commit()
    return get_band(band_id)


def set_band_led(band_id: str, led: str) -> None:
    with _lock:
        conn().execute("UPDATE bands SET led = ?, updated_at = ? WHERE id = ?", (led, time.time(), band_id))
        conn().commit()


def set_band_owner(band_id: str, pid: Optional[int]) -> None:
    with _lock:
        conn().execute("UPDATE bands SET owner_person_id = ? WHERE id = ?", (pid, band_id))
        conn().commit()


def band_for_person(pid: int) -> Optional[str]:
    r = conn().execute("SELECT id FROM bands WHERE owner_person_id = ?", (pid,)).fetchone()
    return r["id"] if r else None


# ---------------------------------------------------------------- conversations
def add_conversation(band_id: str, person_id: Optional[int], summary: str, extraction: Dict, engine: str,
                     transcript: Optional[List[Dict[str, str]]] = None, created_at: Optional[float] = None) -> int:
    with _lock:
        cur = conn().execute(
            "INSERT INTO conversations (band_id, counterpart_person_id, summary, extraction, engine, transcript, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (band_id, person_id, summary, json.dumps(extraction), engine, json.dumps(transcript or []),
             created_at or time.time()),
        )
        conn().commit()
        return cur.lastrowid


def latest_conversation(band_id: str, within_s: float = 3600) -> Optional[Dict[str, Any]]:
    r = conn().execute(
        "SELECT * FROM conversations WHERE band_id = ? AND created_at > ? ORDER BY id DESC LIMIT 1",
        (band_id, time.time() - within_s),
    ).fetchone()
    if not r:
        return None
    return {
        "id": r["id"],
        "band_id": r["band_id"],
        "counterpart_person_id": r["counterpart_person_id"],
        "summary": r["summary"],
        "extraction": _j(r["extraction"], {}),
    }


def conversations_with(band_id: str, person_id: int) -> List[Dict[str, Any]]:
    rows = conn().execute(
        "SELECT * FROM conversations WHERE band_id = ? AND counterpart_person_id = ? ORDER BY created_at",
        (band_id, person_id),
    ).fetchall()
    return [
        {
            "id": r["id"],
            "summary": r["summary"],
            "promises_made": _j(r["extraction"], {}).get("promises_made", []),
            "utterances": _j(r["transcript"], []),
            "created_at": r["created_at"],
        }
        for r in rows
    ]


def delete_conversation(conv_id: int) -> bool:
    with _lock:
        cur = conn().execute("DELETE FROM conversations WHERE id = ?", (conv_id,))
        conn().commit()
        return cur.rowcount > 0


def count_conversations() -> int:
    return conn().execute("SELECT COUNT(*) FROM conversations").fetchone()[0]


def relink_conversation(conv_id: int, pid: int) -> None:
    with _lock:
        conn().execute("UPDATE conversations SET counterpart_person_id = ? WHERE id = ?", (pid, conv_id))
        conn().commit()


# ---------------------------------------------------------------- contacts
def upsert_contact(owner_band_id: str, person_id: int, **fields: Any) -> int:
    now = time.time()
    c = conn()
    existing = c.execute(
        "SELECT * FROM contacts WHERE owner_band_id = ? AND person_id = ?", (owner_band_id, person_id)
    ).fetchone()
    with _lock:
        if existing:
            promises = _j(existing["promises"], [])
            new_promises = fields.get("promises") or []
            seen = {(p.get("text") or "").lower() for p in promises}
            promises += [p for p in new_promises if (p.get("text") or "").lower() not in seen]
            # Source only "upgrades": conversation -> intro/match -> handshake (verified card).
            rank = {"conversation": 0, "match": 1, "intro": 2, "handshake": 3}
            source = existing["source"]
            if fields.get("source") and rank.get(fields["source"], 0) >= rank.get(source, 0):
                source = fields["source"]
            c.execute(
                "UPDATE contacts SET source=?, conversation_summary=?, why_remember=?, promises=?,"
                " match_reason=?, updated_at=? WHERE id=?",
                (
                    source,
                    fields.get("conversation_summary") or existing["conversation_summary"],
                    fields.get("why_remember") or existing["why_remember"],
                    json.dumps(promises),
                    fields.get("match_reason") or existing["match_reason"],
                    now,
                    existing["id"],
                ),
            )
            c.commit()
            return existing["id"]
        cur = c.execute(
            "INSERT INTO contacts (owner_band_id, person_id, source, conversation_summary, why_remember,"
            " promises, match_reason, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                owner_band_id,
                person_id,
                fields.get("source") or "conversation",
                fields.get("conversation_summary") or "",
                fields.get("why_remember") or "",
                json.dumps(fields.get("promises") or []),
                fields.get("match_reason") or "",
                now,
                now,
            ),
        )
        c.commit()
        return cur.lastrowid


def get_contact_row(owner_band_id: str, person_id: int) -> Optional[sqlite3.Row]:
    return conn().execute(
        "SELECT * FROM contacts WHERE owner_band_id = ? AND person_id = ?", (owner_band_id, person_id)
    ).fetchone()


def delete_contact(cid: int) -> None:
    with _lock:
        conn().execute("DELETE FROM contacts WHERE id = ?", (cid,))
        conn().commit()


def _contact_dict(r: sqlite3.Row) -> Dict[str, Any]:
    p = get_person(r["person_id"]) or {}
    owner_band = get_band(r["owner_band_id"]) or {}
    return {
        "id": r["id"],
        "owner_band_id": r["owner_band_id"],
        "owner_name": (owner_band.get("owner") or {}).get("name") or f"Band {r['owner_band_id']} wearer",
        "person_id": r["person_id"],
        "person_band_id": band_for_person(r["person_id"]),
        "name": p.get("name", ""),
        "company": p.get("company", ""),
        "role": p.get("role", ""),
        "email": p.get("email", ""),
        "phone": p.get("phone", ""),
        "socials": p.get("socials", {}),
        "photo": p.get("photo", ""),
        "needs": p.get("needs", []),
        "offers": p.get("offers", []),
        "source": r["source"],
        "conversation_summary": r["conversation_summary"] or "",
        "why_remember": r["why_remember"] or "",
        "promises_made": _j(r["promises"], []),
        "match_reason": r["match_reason"] or "",
        "created_at": r["created_at"],
        "updated_at": r["updated_at"],
    }


def list_contacts(band_id: Optional[str] = None) -> List[Dict[str, Any]]:
    if band_id:
        rows = conn().execute(
            "SELECT * FROM contacts WHERE owner_band_id = ? ORDER BY updated_at DESC", (band_id,)
        ).fetchall()
    else:
        rows = conn().execute("SELECT * FROM contacts ORDER BY updated_at DESC").fetchall()
    return [_contact_dict(r) for r in rows]


def get_contact(cid: int) -> Optional[Dict[str, Any]]:
    r = conn().execute("SELECT * FROM contacts WHERE id = ?", (cid,)).fetchone()
    return _contact_dict(r) if r else None


def move_contacts(from_pid: int, to_pid: int) -> None:
    """Re-point contacts/matches from a provisional person to a verified one."""
    c = conn()
    rows = c.execute("SELECT * FROM contacts WHERE person_id = ?", (from_pid,)).fetchall()
    for r in rows:
        upsert_contact(
            r["owner_band_id"], to_pid,
            source=r["source"], conversation_summary=r["conversation_summary"],
            why_remember=r["why_remember"], promises=_j(r["promises"], []), match_reason=r["match_reason"],
        )
        delete_contact(r["id"])


# ---------------------------------------------------------------- matches
def add_match(a: int, b: int, kind: str, reason: str, introducer: Optional[int] = None) -> Optional[int]:
    if a == b:
        return None
    a, b = min(a, b), max(a, b)
    c = conn()
    with _lock:
        existing = c.execute(
            "SELECT id FROM matches WHERE person_a_id=? AND person_b_id=? AND kind=?", (a, b, kind)
        ).fetchone()
        if existing:
            c.execute("UPDATE matches SET reason=?, introducer_person_id=COALESCE(?, introducer_person_id),"
                      " created_at=? WHERE id=?", (reason, introducer, time.time(), existing["id"]))
            c.commit()
            return existing["id"]
        cur = c.execute(
            "INSERT INTO matches (person_a_id, person_b_id, kind, reason, introducer_person_id, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (a, b, kind, reason, introducer, time.time()),
        )
        c.commit()
        return cur.lastrowid


def list_matches() -> List[Dict[str, Any]]:
    rows = conn().execute("SELECT * FROM matches ORDER BY id").fetchall()
    return [
        {
            "id": r["id"],
            "a": r["person_a_id"],
            "b": r["person_b_id"],
            "kind": r["kind"],
            "reason": r["reason"],
            "introducer_id": r["introducer_person_id"],
            "created_at": r["created_at"],
        }
        for r in rows
    ]


def get_match(mid: int) -> Optional[Dict[str, Any]]:
    for m in list_matches():
        if m["id"] == mid:
            return m
    return None


def move_matches(from_pid: int, to_pid: int) -> None:
    for m in list_matches():
        if from_pid in (m["a"], m["b"]):
            other = m["b"] if m["a"] == from_pid else m["a"]
            add_match(to_pid, other, m["kind"], m["reason"], m["introducer_id"])
            with _lock:
                conn().execute("DELETE FROM matches WHERE id = ?", (m["id"],))
                conn().commit()
