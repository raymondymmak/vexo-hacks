"""SQLite store for people, the conversations you had with them, and promises.

Encounters stay distinct unless the wearer explicitly selects an existing person.
"""

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from difflib import SequenceMatcher


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _name_key(name: str) -> str:
    return " ".join(name.casefold().split())


def _loads(text, fallback):
    try:
        value = json.loads(text or "")
    except (ValueError, TypeError):
        return fallback
    return value if isinstance(value, type(fallback)) else fallback


class Store:
    def __init__(self, path) -> None:
        self.path = str(path)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._init()

    def _init(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS people (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                name_key TEXT NOT NULL UNIQUE,
                working_on TEXT NOT NULL DEFAULT '',
                cares_about TEXT NOT NULL DEFAULT '[]',
                deadlines TEXT NOT NULL DEFAULT '[]',
                summary TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY,
                person_id INTEGER NOT NULL REFERENCES people(id),
                transcript TEXT NOT NULL,
                summary TEXT NOT NULL,
                key_moments TEXT NOT NULL,
                started_at TEXT NOT NULL,
                ended_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS promises (
                id INTEGER PRIMARY KEY,
                person_id INTEGER NOT NULL REFERENCES people(id),
                conversation_id INTEGER REFERENCES conversations(id),
                what TEXT NOT NULL,
                by_when TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );
            """
        )
        columns = {row["name"] for row in self._conn.execute("PRAGMA table_info(people)")}
        for field in ("company", "role", "email", "phone", "contact_uid", "social_url", "delivery"):
            if field not in columns:
                self._conn.execute(f"ALTER TABLE people ADD COLUMN {field} TEXT NOT NULL DEFAULT ''")
        if "reviewed" not in columns:
            self._conn.execute("ALTER TABLE people ADD COLUMN reviewed INTEGER NOT NULL DEFAULT 0")
        for row in self._conn.execute("SELECT id FROM people WHERE contact_uid = ''").fetchall():
            self._conn.execute("UPDATE people SET contact_uid = ? WHERE id = ?", (str(uuid.uuid4()), row["id"]))
        conversation_columns = {row["name"] for row in self._conn.execute("PRAGMA table_info(conversations)")}
        if "utterances" not in conversation_columns:
            self._conn.execute("ALTER TABLE conversations ADD COLUMN utterances TEXT NOT NULL DEFAULT '[]'")
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def reset(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM promises")
            self._conn.execute("DELETE FROM conversations")
            self._conn.execute("DELETE FROM people")
            self._conn.commit()

    def merge(self, card: dict, transcript: str, moments: list, started_at: str, ended_at: str, person_id=None, utterances=None) -> dict:
        """Save a distinct encounter, or append to an explicitly confirmed person ID."""
        name = " ".join(str(card.get("name") or "").split())
        named = bool(name)
        key = "encounter-" + uuid.uuid4().hex
        display = name if named else "Someone you met"
        working_on = str(card.get("working_on") or "")
        summary = str(card.get("summary") or "")
        cares = list(card.get("cares_about") or [])
        deadlines = list(card.get("deadlines") or [])
        moment_list = list(moments or [])
        for extra in card.get("key_moments") or []:
            if extra not in moment_list:
                moment_list.append(extra)
        promises = list(card.get("promises") or [])
        now = _now()
        with self._lock:
            row = None
            if person_id is not None:
                row = self._conn.execute("SELECT * FROM people WHERE id = ?", (person_id,)).fetchone()
                if row is None:
                    raise ValueError("The selected contact does not exist.")
            if row is None:
                cur = self._conn.execute(
                    """INSERT INTO people
                       (name, name_key, working_on, cares_about, deadlines, summary, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (display, key, working_on, json.dumps(cares), json.dumps(deadlines), summary, now, now),
                )
                person_id = cur.lastrowid
            else:
                person_id = row["id"]
                merged_cares = _union(_loads(row["cares_about"], []), cares)
                merged_deadlines = _union(_loads(row["deadlines"], []), deadlines)
                self._conn.execute(
                    """UPDATE people
                       SET working_on = ?, cares_about = ?, deadlines = ?, summary = ?, updated_at = ?
                       WHERE id = ?""",
                    (
                        working_on or row["working_on"],
                        json.dumps(merged_cares),
                        json.dumps(merged_deadlines),
                        summary or row["summary"],
                        now,
                        person_id,
                    ),
                )
            for field in ("company", "role", "email", "phone"):
                value = " ".join(str(card.get(field) or "").split())[:240]
                if value:
                    self._conn.execute(f"UPDATE people SET {field} = ? WHERE id = ?", (value, person_id))
            self._conn.execute("UPDATE people SET reviewed = 0, contact_uid = CASE WHEN contact_uid = '' THEN ? ELSE contact_uid END WHERE id = ?", (str(uuid.uuid4()), person_id))
            cur = self._conn.execute(
                """INSERT INTO conversations
                   (person_id, transcript, summary, key_moments, started_at, ended_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (person_id, transcript or "", summary, json.dumps(moment_list), started_at, ended_at),
            )
            conversation_id = cur.lastrowid
            self._conn.execute("UPDATE conversations SET utterances = ? WHERE id = ?", (json.dumps(utterances or []), conversation_id))
            existing = {
                _name_key(item["what"])
                for item in self._conn.execute(
                    "SELECT what FROM promises WHERE person_id = ?", (person_id,)
                ).fetchall()
            }
            for promise in promises:
                what = " ".join(str(promise.get("what") or "").split())
                if not what or _name_key(what) in existing:
                    continue
                existing.add(_name_key(what))
                self._conn.execute(
                    """INSERT INTO promises (person_id, conversation_id, what, by_when, created_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (person_id, conversation_id, what, str(promise.get("by_when") or ""), now),
                )
            self._conn.commit()
            return self._person(person_id)

    def update_person(self, person_id: int, updates: dict):
        from .extract import normalize_card
        allowed = {"name", "company", "role", "email", "phone", "summary", "working_on", "promises", "reviewed", "social_url"}
        if set(updates) - allowed:
            raise ValueError("Unsupported contact field.")
        if "reviewed" in updates and not isinstance(updates["reviewed"], bool):
            raise ValueError("reviewed must be true or false.")
        cleaned = normalize_card(updates)
        social_url = str(updates.get("social_url") or "").strip()
        if social_url:
            from .socials import _valid_profile
            if not _valid_profile(social_url):
                raise ValueError("Use a LinkedIn or X profile URL.")
        with self._lock:
            if self._person(person_id) is None:
                return None
            for field in allowed - {"promises", "reviewed"}:
                if field in updates:
                    value = cleaned[field] if field != "social_url" else social_url
                    if field == "name":
                        value = value or "Someone you met"
                    self._conn.execute(f"UPDATE people SET {field} = ? WHERE id = ?", (value, person_id))
            if "promises" in updates:
                self._conn.execute("DELETE FROM promises WHERE person_id = ?", (person_id,))
                for promise in cleaned["promises"]:
                    self._conn.execute("INSERT INTO promises (person_id, what, by_when, created_at) VALUES (?, ?, ?, ?)", (person_id, promise["what"], promise["by_when"], _now()))
            self._conn.execute("UPDATE people SET reviewed = ?, updated_at = ? WHERE id = ?", (bool(updates.get("reviewed", False)), _now(), person_id))
            self._conn.commit()
            return self._person(person_id)

    def set_delivery(self, person_id, delivery, social_url=None, *, contact_uid,
                     original_social_url=None):
        """Write background results only to the same contact, preserving user edits."""
        with self._lock:
            row = self._conn.execute(
                "SELECT contact_uid, social_url FROM people WHERE id = ?", (person_id,)
            ).fetchone()
            if row is None or row["contact_uid"] != contact_uid:
                return None
            self._conn.execute("UPDATE people SET delivery = ? WHERE id = ? AND contact_uid = ?",
                               (json.dumps(delivery), person_id, contact_uid))
            if social_url and original_social_url is not None and row["social_url"] == original_social_url:
                self._conn.execute("UPDATE people SET social_url = ? WHERE id = ? AND contact_uid = ?",
                                   (social_url, person_id, contact_uid))
            self._conn.commit()
            return self._person(person_id)

    def count(self) -> int:
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) AS n FROM people").fetchone()["n"]

    def get(self, person_id: int):
        with self._lock:
            return self._person(person_id)

    def list_people(self) -> list:
        with self._lock:
            rows = self._conn.execute("SELECT id FROM people ORDER BY updated_at DESC, id DESC").fetchall()
            return [self._person(row["id"]) for row in rows]

    def search(self, query: str) -> list:
        """Keyword match over the card, then a light fuzzy match on the name."""
        text = " ".join((query or "").split())
        people = self.list_people()
        if not text:
            return people
        terms = [term.casefold() for term in text.split() if term]
        scored = []
        for person in people:
            hay = _haystack(person)
            score = sum(1 for term in terms if term in hay)
            if score:
                scored.append((score, person))
        if scored:
            scored.sort(key=lambda item: (-item[0], item[1]["name"]))
            return [person for _score, person in scored]
        folded = text.casefold()
        close = [person for person in people if _fuzzy_name(folded, person["name"])]
        close.sort(key=lambda person: person["name"])
        return close

    def _person(self, person_id: int):
        row = self._conn.execute("SELECT * FROM people WHERE id = ?", (person_id,)).fetchone()
        if row is None:
            return None
        conversations = self._conn.execute(
            "SELECT * FROM conversations WHERE person_id = ? ORDER BY id DESC", (person_id,)
        ).fetchall()
        promises = self._conn.execute(
            "SELECT * FROM promises WHERE person_id = ? ORDER BY id", (person_id,)
        ).fetchall()
        moments = []
        for conversation in conversations:
            for moment in _loads(conversation["key_moments"], []):
                if moment not in moments:
                    moments.append(moment)
        return {
            "id": row["id"],
            "name": row["name"],
            **{field: row[field] for field in ("company", "role", "email", "phone", "contact_uid")},
            "reviewed": bool(row["reviewed"]),
            "social_url": row["social_url"],
            "delivery": _loads(row["delivery"], {}),
            "working_on": row["working_on"],
            "cares_about": _loads(row["cares_about"], []),
            "deadlines": _loads(row["deadlines"], []),
            "summary": row["summary"],
            "promises": [
                {"id": item["id"], "what": item["what"], "by_when": item["by_when"]}
                for item in promises
            ],
            "key_moments": moments[:8],
            "conversation_count": len(conversations),
            "conversations": [
                {
                    "id": item["id"],
                    "summary": item["summary"],
                    "started_at": item["started_at"],
                    "ended_at": item["ended_at"],
                    "transcript": item["transcript"],
                    "utterances": _loads(item["utterances"], []),
                }
                for item in conversations
            ],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }


def _union(old: list, new: list) -> list:
    found = list(old)
    seen = {_name_key(str(item)) for item in found if str(item).strip()}
    for item in new:
        text = " ".join(str(item).split())
        if not text or _name_key(text) in seen:
            continue
        seen.add(_name_key(text))
        found.append(text)
    return found


def _haystack(person: dict) -> str:
    parts = [
        person.get("name") or "",
        " ".join(person.get(field) or "" for field in ("company", "role", "email", "phone")),
        person.get("working_on") or "",
        person.get("summary") or "",
        " ".join(person.get("cares_about") or []),
        " ".join(person.get("deadlines") or []),
        " ".join(person.get("key_moments") or []),
        " ".join(item.get("what", "") + " " + item.get("by_when", "") for item in person.get("promises") or []),
        " ".join(item.get("transcript", "") for item in person.get("conversations") or []),
    ]
    return " ".join(parts).casefold()


def _fuzzy_name(query: str, name: str) -> bool:
    target = " ".join(name.casefold().split())
    if not query or not target:
        return False
    if query in target or target in query:
        return True
    if SequenceMatcher(None, query, target).ratio() >= 0.72:
        return True
    return any(SequenceMatcher(None, query, part).ratio() >= 0.8 for part in target.split())
