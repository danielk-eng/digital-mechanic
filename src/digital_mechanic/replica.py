"""Shared state for serverless instances (Vercel): every instance has its own SQLite in /tmp, so the tables that must be the
same everywhere (companies, which truck belongs to whom, people, QR login links, tickets, handoffs, repairs) are mirrored to one private Vercel Blob.

Pull before each request (at most every PULL_TTL s; always before a write request, so a write starts from the latest state),
push after a write request when the mirrored tables changed. A push is a three-way merge, not "last writer wins": the instance
re-reads the shared copy, applies only its own changes since its last pull (inserts, updates, deletes by row identity), re-numbers
its new rows if another instance took the same autoincrement id, and uploads with the copy's ETag (x-if-match): if another
instance wrote in between, the upload is refused and the merge is redone (handoffs sent at the same moment from several
instances all survive).
Off unless BLOB_READ_WRITE_TOKEN is set (the deb stand and the tests keep plain SQLite).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import time

import httpx

log = logging.getLogger(__name__)
TABLES = ("companies", "company_vehicles", "users", "login_tokens", "tickets", "handoffs", "repairs")  # + the chief's inbox: a ticket or handoff made on one instance shows on the dashboard served by another
API = "https://vercel.com/api/blob"
PATH = os.environ.get("REPLICA_BLOB_PATH", "state/tenancy.json")
PULL_TTL = float(os.environ.get("REPLICA_PULL_TTL_S", "2"))
# row identity for merging: autoincrement ids collide across instances, so these tables are keyed by what made the row
IDENT = {"tickets": ("ts", "user_id", "vehicle_id"), "handoffs": ("ts", "user_id", "vehicle_id"), "repairs": ("started", "user_id", "vehicle_id"),
         "companies": ("id",), "company_vehicles": ("vehicle_id",), "users": ("id",), "login_tokens": ("hash",)}
AUTO_ID = ("tickets", "handoffs", "repairs")


def _key(t: str, row: dict) -> str:
    return json.dumps([row.get(c) for c in IDENT.get(t, ())] if t in IDENT else row, sort_keys=True, ensure_ascii=False, default=str)


def merge(remote: dict, local: dict, base: dict) -> dict:
    """Shared copy + this instance's changes since `base` (its last pulled/pushed state)."""
    out = {}
    for t in TABLES:
        R = {_key(t, r): r for r in (remote.get(t) or [])}
        L = {_key(t, r): r for r in (local.get(t) or [])}
        B = {_key(t, r): r for r in (base.get(t) or [])}
        M = dict(R)
        mine = set()
        for k, r in L.items():
            if B.get(k) != r:
                M[k] = r; mine.add(k)
        for k, r in B.items():
            if k not in L and R.get(k) == r:
                M.pop(k, None)  # deleted here, untouched elsewhere
        rows = list(M.values())
        if t in AUTO_ID:  # keep the shared rows' ids, move this instance's new rows past them
            used, fixed = set(), []
            for k, r in sorted(M.items(), key=lambda kv: kv[0] in mine):
                if r.get("id") in used or r.get("id") is None:
                    r = {**r, "id": max([x for x in used if isinstance(x, int)] + [0]) + 1}
                used.add(r.get("id")); fixed.append(r)
            rows = sorted(fixed, key=lambda r: r.get("id") or 0)
        out[t] = rows
    return out


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
        self.base: dict = {}  # the mirrored tables as last pulled/pushed (merge base)
        self.http = httpx.Client(timeout=4.0) if self.on else None
        self.stats = {"pulls": 0, "pushes": 0, "error": ""}
        self.cas = os.environ.get("REPLICA_CAS", "1") == "1"

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
            self.base = self.dump()
            self.digest = self.hash(self.base)
            return True
        except Exception as e:  # the stand keeps working on its local copy
            log.warning("replica pull failed: %s", type(e).__name__)
            self.stats["error"] = f"pull {type(e).__name__} {getattr(getattr(e, 'response', None), 'status_code', '')}"
            return False

    def _head_etag(self) -> str:
        """Authoritative ETag from the Blob API (the content GET answers with a weak, compressed-variant ETag that x-if-match
        never matches). Read BEFORE the content: the content is then at least that version, so a stale ETag only makes the CAS
        refuse and retry, never overwrite."""
        r = self.http.get(API + "/", params={"url": self.url()}, headers=self._hdr())
        if r.status_code == 404:
            return ""
        r.raise_for_status()
        return str(r.json().get("etag", ""))

    def _get(self) -> tuple[dict, str]:
        tag = self._head_etag() if self.cas else ""
        h = self._hdr()
        h.pop("x-api-version"); h.pop("x-vercel-blob-store-id")
        r = self.http.get(self.url(), params={"cache": "0", "t": str(time.time())}, headers=h)
        if r.status_code == 404:
            return {}, ""
        r.raise_for_status()
        self.stats["get_etag"] = tag[:60]
        self.stats["get_cache"] = (r.headers.get("x-vercel-cache", "") + " age=" + r.headers.get("age", ""))[:40]
        return r.json(), tag

    def push(self) -> bool:
        """Merge this instance's changes into the shared copy and upload it (if the mirrored tables changed).
        Compare-and-swap: the upload carries the ETag of the copy it was merged into (x-if-match); if another instance wrote
        in between, the Blob API refuses it and we re-read, re-merge and try again."""
        if not self.on:
            return False
        local = self.dump()
        if self.hash(local) == self.digest:
            return False
        try:
            prev, cas = None, self.cas
            for attempt in range(6):
                remote, etag = self._get()
                merged = merge(remote, local, self.base)
                # the read gave the same ETag that was just refused (stale read / format not accepted) or 4 refusals in a row:
                # plain merged upload (still keeps every instance's rows it can see)
                if cas and (etag == prev or attempt >= 4):
                    cas = False
                    self.stats["cas_fallback"] = self.stats.get("cas_fallback", 0) + 1
                extra = {"x-if-match": etag} if etag and cas else {}
                prev = etag
                r = self.http.put(API + "/", params={"pathname": PATH}, content=json.dumps(merged, ensure_ascii=False, default=str).encode(),
                                  headers=self._hdr(**{"x-vercel-blob-access": "private", "x-add-random-suffix": "0", "x-allow-overwrite": "1",
                                                        "x-content-type": "application/json", "x-cache-control-max-age": "60", **extra}))
                if extra and (r.status_code in (409, 412) or (r.status_code == 400 and "precondition" in r.text.lower())):
                    self.stats["conflicts"] = self.stats.get("conflicts", 0) + 1
                    self.stats["conflict_body"] = r.text[:160]
                    time.sleep(random.uniform(0.05, 0.25) * (attempt + 1))
                    continue
                r.raise_for_status()
                try:
                    self.stats["put_etag"] = str(r.json().get("etag", ""))[:60]
                except Exception:
                    pass
                break
            else:
                raise RuntimeError("blob kept changing")
            if self.hash(merged) != self.hash(local):
                self.load(merged)
            self.base = self.dump()
            self.digest = self.hash(self.base)
            self.stats["pushes"] += 1
            self.etag = ""
            self.last_pull = time.time()
            return True
        except Exception as e:
            log.warning("replica push failed: %s", type(e).__name__)
            self.stats["error"] = f"push {type(e).__name__} {getattr(getattr(e, 'response', None), 'status_code', '')}"
            return False
