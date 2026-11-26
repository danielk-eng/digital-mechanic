"""Shared state for serverless instances (Vercel): every instance has its own SQLite in /tmp, so the tables that must be the
same everywhere (companies, which truck belongs to whom, people, QR login links) are mirrored to one private Vercel Blob.

Pull before each request (at most every PULL_TTL s; always before a write request, so a write starts from the latest state),
push after a write request when the mirrored tables changed. Last writer wins: fine for a demo stand with a handful of admins.
Off unless BLOB_READ_WRITE_TOKEN is set (the deb stand and the tests keep plain SQLite).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time

import httpx

log = logging.getLogger(__name__)
TABLES = ("companies", "company_vehicles", "users", "login_tokens")
API = "https://vercel.com/api/blob"
PATH = os.environ.get("REPLICA_BLOB_PATH", "state/tenancy.json")
PULL_TTL = float(os.environ.get("REPLICA_PULL_TTL_S", "2"))


class Replica:
    def __init__(self, store, token: str | None = None):
        self.store = store
        self.token = token if token is not None else os.environ.get("BLOB_READ_WRITE_TOKEN", "")
        parts = self.token.split("_")
        self.store_id = parts[3] if len(parts) > 3 else ""
        self.on = bool(self.token and self.store_id)
        self.etag = ""
        self.last_pull = 0.0
        self.digest = ""  # hash of the mirrored tables as last pulled/pushed
        self.http = httpx.Client(timeout=4.0) if self.on else None
        self.stats = {"pulls": 0, "pushes": 0, "error": ""}

    # ------------------------------------------------------------------ local tables
    def dump(self) -> dict:
        return {t: self.store.q(f"SELECT * FROM {t}") for t in TABLES}

    @staticmethod
    def hash(d: dict) -> str:
        return hashlib.sha256(json.dumps(d, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()

    def load(self, d: dict) -> None:
        with self.store._lock:
            con = self.store._con
            for t in TABLES:
                rows = d.get(t)
                if rows is None:
                    continue
                cols = {r[1] for r in con.execute(f"PRAGMA table_info({t})")}
                con.execute(f"DELETE FROM {t}")
                for r in rows:
                    ks = [k for k in r if k in cols]
                    if ks:
                        con.execute(f"INSERT OR REPLACE INTO {t}({','.join(ks)}) VALUES({','.join('?' * len(ks))})", [r[k] for k in ks])
            con.commit()

    # ------------------------------------------------------------------ blob
    def _hdr(self, **kw) -> dict:
        return {"authorization": f"Bearer {self.token}", "x-api-version": "13", "x-vercel-blob-store-id": self.store_id, **kw}

    def url(self) -> str:
        return f"https://{self.store_id}.private.blob.vercel-storage.com/{PATH}"

    def pull(self, force: bool = False) -> bool:
        """Bring the mirrored tables up to the shared copy. True if something was loaded."""
        if not self.on or (not force and time.time() - self.last_pull < PULL_TTL):
            return False
        self.last_pull = time.time()
        try:
            h = self._hdr()
            h.pop("x-api-version"); h.pop("x-vercel-blob-store-id")
            if self.etag:
                h["if-none-match"] = self.etag
            r = self.http.get(self.url(), params={"cache": "0"}, headers=h)
            if r.status_code in (304, 404):
                if r.status_code == 404 and not self.digest:
                    self.digest = "-"  # nothing shared yet: the first write creates it
                return False
            r.raise_for_status()
            d = r.json()
            self.etag = r.headers.get("etag", "")
            new = self.hash(d)
            if new == self.digest:
                return False
            self.load(d)
            self.stats["pulls"] += 1
            self.digest = self.hash(self.dump())
            return True
        except Exception as e:  # the stand keeps working on its local copy
            log.warning("replica pull failed: %s", type(e).__name__)
            self.stats["error"] = f"pull {type(e).__name__} {getattr(getattr(e, 'response', None), 'status_code', '')}"
            return False

    def push(self) -> bool:
        """Upload the mirrored tables if they changed since the last pull/push."""
        if not self.on:
            return False
        d = self.dump()
        h = self.hash(d)
        if h == self.digest:
            return False
        try:
            r = self.http.put(API + "/", params={"pathname": PATH}, content=json.dumps(d, ensure_ascii=False, default=str).encode(),
                              headers=self._hdr(**{"x-vercel-blob-access": "private", "x-add-random-suffix": "0", "x-allow-overwrite": "1",
                                                    "x-content-type": "application/json", "x-cache-control-max-age": "60"}))
            r.raise_for_status()
            self.digest = h
            self.stats["pushes"] += 1
            self.etag = ""
            self.last_pull = time.time()
            return True
        except Exception as e:
            log.warning("replica push failed: %s", type(e).__name__)
            self.stats["error"] = f"push {type(e).__name__} {getattr(getattr(e, 'response', None), 'status_code', '')}"
            return False
