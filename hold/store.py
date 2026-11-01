"""Magasin durable embarqué (SQLite) : aucun serveur à installer.

- `effects.effect_key` UNIQUE : un effet équivalent ne peut pas être réservé deux fois,
  même sous concurrence (dernière ligne de défense de l'invariant I1).
- `events` : journal chaîné, écrit dans la même transaction que la projection d'état.
- `relay_messages.wa_message_id` UNIQUE : un webhook rejoué est sans effet.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from .journal import GENESIS, Event, make_event

SCHEMA = """
CREATE TABLE IF NOT EXISTS missions(
  id TEXT PRIMARY KEY, title TEXT NOT NULL, status TEXT NOT NULL,
  profile_fp TEXT NOT NULL, profile_rev INTEGER NOT NULL, created_at TEXT NOT NULL,
  revoked INTEGER NOT NULL DEFAULT 0, head_seq INTEGER NOT NULL DEFAULT 0, head_hash TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS profiles(fingerprint TEXT PRIMARY KEY, json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events(
  mission TEXT NOT NULL, seq INTEGER NOT NULL, type TEXT NOT NULL, json TEXT NOT NULL, hash TEXT NOT NULL,
  PRIMARY KEY(mission, seq));
CREATE TABLE IF NOT EXISTS effects(
  effect_key TEXT PRIMARY KEY, mission TEXT NOT NULL, action TEXT NOT NULL, operation_id TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL, attributes TEXT NOT NULL, receipt TEXT, attempts INTEGER NOT NULL DEFAULT 1,
  not_found INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS usage(mission TEXT NOT NULL, counter TEXT NOT NULL, value INTEGER NOT NULL, PRIMARY KEY(mission, counter));
CREATE TABLE IF NOT EXISTS requests(
  question_id TEXT PRIMARY KEY, mission TEXT NOT NULL, kind TEXT NOT NULL, status TEXT NOT NULL,
  payload TEXT NOT NULL, answer TEXT, created_at TEXT NOT NULL, answered_at TEXT);
CREATE TABLE IF NOT EXISTS grants(
  id TEXT PRIMARY KEY, mission TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL,
  consumed INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS relay_messages(
  id INTEGER PRIMARY KEY AUTOINCREMENT, mission TEXT NOT NULL, direction TEXT NOT NULL, kind TEXT NOT NULL,
  mode TEXT NOT NULL, status TEXT NOT NULL, question_id TEXT, wa_message_id TEXT UNIQUE, peer TEXT NOT NULL,
  detail TEXT, error TEXT, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS effects_mission ON effects(mission);
CREATE INDEX IF NOT EXISTS relay_mission ON relay_messages(mission);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class Store:
    def __init__(self, path: str = "hold.db"):
        self.path = path
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None, timeout=15.0)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        if path != ":memory:":
            self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(SCHEMA)

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        """Transaction exclusive en écriture ; les appels imbriqués réutilisent la transaction."""
        with self._lock:
            if self._conn.in_transaction:
                yield self._conn
                return
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise

    def read(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._conn.execute(sql, args))

    # -- missions et profils ------------------------------------------------------
    def create_mission(self, c: sqlite3.Connection, mission_id: str, title: str, profile_fp: str, profile_rev: int, profile_json: str) -> None:
        c.execute("INSERT OR IGNORE INTO profiles(fingerprint, json) VALUES(?, ?)", (profile_fp, profile_json))
        c.execute(
            "INSERT INTO missions(id, title, status, profile_fp, profile_rev, created_at, head_hash) VALUES(?,?,?,?,?,?,?)",
            (mission_id, title, "OPEN", profile_fp, profile_rev, now_iso(), GENESIS),
        )

    def attach_profile(self, c: sqlite3.Connection, mission_id: str, profile_fp: str, profile_rev: int, profile_json: str) -> None:
        c.execute("INSERT OR IGNORE INTO profiles(fingerprint, json) VALUES(?, ?)", (profile_fp, profile_json))
        c.execute("UPDATE missions SET profile_fp=?, profile_rev=? WHERE id=?", (profile_fp, profile_rev, mission_id))

    def mission(self, mission_id: str) -> sqlite3.Row | None:
        rows = self.read("SELECT * FROM missions WHERE id=?", (mission_id,))
        return rows[0] if rows else None

    def missions(self) -> list[sqlite3.Row]:
        return self.read("SELECT * FROM missions ORDER BY created_at DESC")

    def profile_json(self, fingerprint: str) -> str | None:
        rows = self.read("SELECT json FROM profiles WHERE fingerprint=?", (fingerprint,))
        return rows[0]["json"] if rows else None

    def set_status(self, c: sqlite3.Connection, mission_id: str, status: str) -> None:
        c.execute("UPDATE missions SET status=? WHERE id=?", (status, mission_id))

    # -- journal ---------------------------------------------------------------------
    def append(self, c: sqlite3.Connection, mission_id: str, type_: str, actor: str, data: dict[str, Any], cause: int | None = None) -> Event:
        m = c.execute("SELECT head_seq, head_hash, profile_fp, profile_rev FROM missions WHERE id=?", (mission_id,)).fetchone()
        if not m:
            raise KeyError(f"Mission inconnue : {mission_id}")
        e = make_event(
            mission=mission_id, seq=m["head_seq"] + 1, at=now_iso(), type_=type_, actor=actor, cause=cause,
            profile=m["profile_fp"], revision=m["profile_rev"], data=data, prev=m["head_hash"],
        )
        c.execute("INSERT INTO events(mission, seq, type, json, hash) VALUES(?,?,?,?,?)", (mission_id, e.seq, e.type, json.dumps(e.to_dict(), ensure_ascii=False), e.hash))
        c.execute("UPDATE missions SET head_seq=?, head_hash=? WHERE id=?", (e.seq, e.hash, mission_id))
        return e

    def events(self, mission_id: str) -> list[Event]:
        return [Event(**json.loads(r["json"])) for r in self.read("SELECT json FROM events WHERE mission=? ORDER BY seq", (mission_id,))]

    # -- compteurs ---------------------------------------------------------------------
    def usage(self, mission_id: str) -> dict[str, int]:
        return {r["counter"]: r["value"] for r in self.read("SELECT counter, value FROM usage WHERE mission=?", (mission_id,))}

    def bump(self, c: sqlite3.Connection, mission_id: str, counter: str, delta: int = 1) -> int:
        c.execute(
            "INSERT INTO usage(mission, counter, value) VALUES(?,?,?) ON CONFLICT(mission, counter) DO UPDATE SET value=value+excluded.value",
            (mission_id, counter, delta),
        )
        return c.execute("SELECT value FROM usage WHERE mission=? AND counter=?", (mission_id, counter)).fetchone()["value"]

    # -- effets ---------------------------------------------------------------------------
    def effect(self, effect_key: str) -> sqlite3.Row | None:
        rows = self.read("SELECT * FROM effects WHERE effect_key=?", (effect_key,))
        return rows[0] if rows else None

    def effects(self, mission_id: str) -> list[sqlite3.Row]:
        return self.read("SELECT * FROM effects WHERE mission=? ORDER BY created_at", (mission_id,))
