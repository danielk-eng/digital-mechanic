"""Local SQLite storage: users and consents, diagnostics, tickets, repair sessions, chat, audit.

All personal data stays on the server (152-FZ: local storage, retention, deletion on request).
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS users(
  id TEXT PRIMARY KEY, name TEXT, role TEXT, lang TEXT DEFAULT 'ru', experience TEXT DEFAULT 'experienced',
  big_font INTEGER DEFAULT 0, voice_out INTEGER DEFAULT 0, consent_at REAL, created_at REAL, detail TEXT DEFAULT 'brief');
CREATE TABLE IF NOT EXISTS diagnoses(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, user_id TEXT, vehicle_id TEXT, session_id TEXT, status TEXT,
  anomaly_type TEXT, anomaly_score REAL, confidence REAL, signal_quality TEXT, decision TEXT, urgency TEXT, payload TEXT);
CREATE TABLE IF NOT EXISTS tickets(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, user_id TEXT, vehicle_id TEXT, session_id TEXT, ticket_id TEXT,
  urgency TEXT, anomaly_type TEXT, payload TEXT, status TEXT DEFAULT 'created');
CREATE TABLE IF NOT EXISTS repairs(
  id INTEGER PRIMARY KEY AUTOINCREMENT, started REAL, updated REAL, finished REAL, user_id TEXT, vehicle_id TEXT,
  session_id TEXT, ticket_id TEXT, title TEXT, steps TEXT, current INTEGER DEFAULT 0, status TEXT DEFAULT 'open');
CREATE TABLE IF NOT EXISTS messages(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, conv TEXT, user_id TEXT, vehicle_id TEXT, role TEXT, content TEXT, meta TEXT);
CREATE TABLE IF NOT EXISTS audit(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, user_id TEXT, role TEXT, vehicle_id TEXT, action TEXT,
  input TEXT, output TEXT, tools TEXT, sources TEXT, latency_ms INTEGER, tokens INTEGER, ok INTEGER);
CREATE INDEX IF NOT EXISTS diag_v ON diagnoses(vehicle_id, ts);
CREATE INDEX IF NOT EXISTS tick_v ON tickets(vehicle_id, ts);
CREATE INDEX IF NOT EXISTS rep_v ON repairs(vehicle_id, status);
CREATE INDEX IF NOT EXISTS msg_c ON messages(conv, id);
CREATE INDEX IF NOT EXISTS aud_ts ON audit(ts);
"""


class Store:
    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._con = sqlite3.connect(self.path, check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        self._con.executescript(SCHEMA)
        cols = {r[1] for r in self._con.execute("PRAGMA table_info(users)")}
        if "detail" not in cols:  # migrate DBs created before brief/detailed answers
            self._con.execute("ALTER TABLE users ADD COLUMN detail TEXT DEFAULT 'brief'")
            self._con.commit()

    def x(self, sql: str, args: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._con.execute(sql, args)
            self._con.commit()
            return cur

    def q(self, sql: str, args: tuple = ()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._con.execute(sql, args).fetchall()]

    def one(self, sql: str, args: tuple = ()) -> dict | None:
        r = self.q(sql, args)
        return r[0] if r else None

    # users
    def upsert_user(self, uid: str, **kw: Any) -> dict:
        u = self.one("SELECT * FROM users WHERE id=?", (uid,))
        if not u:
            self.x("INSERT INTO users(id, created_at) VALUES (?,?)", (uid, time.time()))
        for k, v in kw.items():
            if k in {"name", "role", "lang", "experience", "detail", "big_font", "voice_out", "consent_at"}:
                self.x(f"UPDATE users SET {k}=? WHERE id=?", (v, uid))
        return self.one("SELECT * FROM users WHERE id=?", (uid,))

    def user(self, uid: str) -> dict | None:
        return self.one("SELECT * FROM users WHERE id=?", (uid,))

    def delete_user_data(self, uid: str) -> None:
        for t in ("messages", "audit"):
            self.x(f"DELETE FROM {t} WHERE user_id=?", (uid,))
        for t in ("diagnoses", "tickets", "repairs"):
            self.x(f"UPDATE {t} SET user_id=NULL WHERE user_id=?", (uid,))  # keep fleet records, unlink person
        self.x("DELETE FROM users WHERE id=?", (uid,))

    # domain
    def add_diagnosis(self, uid: str, check: dict, decision: str, urgency: str | None) -> None:
        self.x("INSERT INTO diagnoses(ts,user_id,vehicle_id,session_id,status,anomaly_type,anomaly_score,confidence,signal_quality,decision,urgency,payload) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
               (time.time(), uid, check["vehicle_id"], check["session_id"], check["status"], check.get("anomaly_type"), check.get("anomaly_score"),
                check.get("confidence"), check.get("signal_quality"), decision, urgency, json.dumps(check, ensure_ascii=False)))

    def add_ticket(self, uid: str, payload: dict, ticket_id: str) -> None:
        self.x("INSERT INTO tickets(ts,user_id,vehicle_id,session_id,ticket_id,urgency,anomaly_type,payload) VALUES (?,?,?,?,?,?,?,?)",
               (time.time(), uid, payload["vehicle_id"], payload["session_id"], ticket_id, payload["urgency"], payload["anomaly_type"], json.dumps(payload, ensure_ascii=False)))

    def tickets(self, vehicle_id: str | None = None) -> list[dict]:
        if vehicle_id:
            rows = self.q("SELECT * FROM tickets WHERE vehicle_id=? ORDER BY ts DESC", (vehicle_id,))
        else:
            rows = self.q("SELECT * FROM tickets ORDER BY ts DESC")
        for r in rows:
            r["payload"] = json.loads(r["payload"])
        return rows

    def repair_open(self, vehicle_id: str) -> dict | None:
        r = self.one("SELECT * FROM repairs WHERE vehicle_id=? AND status='open' ORDER BY id DESC LIMIT 1", (vehicle_id,))
        if r:
            r["steps"] = json.loads(r["steps"])
        return r

    def repairs(self, status: str | None = None) -> list[dict]:
        rows = self.q("SELECT * FROM repairs" + (" WHERE status=?" if status else "") + " ORDER BY id DESC", (status,) if status else ())
        for r in rows:
            r["steps"] = json.loads(r["steps"])
        return rows

    def start_repair(self, uid: str, vehicle_id: str, session_id: str | None, ticket_id: str | None, title: str, steps: list[dict]) -> dict:
        self.x("UPDATE repairs SET status='replaced', updated=? WHERE vehicle_id=? AND status='open'", (time.time(), vehicle_id))
        now = time.time()
        self.x("INSERT INTO repairs(started,updated,user_id,vehicle_id,session_id,ticket_id,title,steps) VALUES (?,?,?,?,?,?,?,?)",
               (now, now, uid, vehicle_id, session_id, ticket_id, title, json.dumps(steps, ensure_ascii=False)))
        return self.repair_open(vehicle_id)

    def save_repair(self, r: dict) -> None:
        self.x("UPDATE repairs SET steps=?, current=?, status=?, updated=?, finished=? WHERE id=?",
               (json.dumps(r["steps"], ensure_ascii=False), r["current"], r["status"], time.time(), r.get("finished"), r["id"]))

    def add_message(self, conv: str, uid: str, vehicle_id: str | None, role: str, content: str, meta: dict | None = None) -> None:
        self.x("INSERT INTO messages(ts,conv,user_id,vehicle_id,role,content,meta) VALUES (?,?,?,?,?,?,?)",
               (time.time(), conv, uid, vehicle_id, role, content, json.dumps(meta or {}, ensure_ascii=False)))

    def messages(self, conv: str, limit: int = 30) -> list[dict]:
        return list(reversed(self.q("SELECT * FROM messages WHERE conv=? ORDER BY id DESC LIMIT ?", (conv, limit))))

    def audit(self, **kw: Any) -> None:
        cols = ["user_id", "role", "vehicle_id", "action", "input", "output", "tools", "sources", "latency_ms", "tokens", "ok"]
        vals = [kw.get(c) if not isinstance(kw.get(c), (list, dict)) else json.dumps(kw.get(c), ensure_ascii=False) for c in cols]
        self.x(f"INSERT INTO audit(ts,{','.join(cols)}) VALUES (?{',?' * len(cols)})", (time.time(), *vals))

    def purge(self, retention_days: int) -> int:
        cutoff = time.time() - retention_days * 86400
        n = self.x("DELETE FROM audit WHERE ts<?", (cutoff,)).rowcount
        n += self.x("DELETE FROM messages WHERE ts<?", (cutoff,)).rowcount
        return n
