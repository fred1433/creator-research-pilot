"""Storage, deduplication and the job queue.

SQLite, because the pilot has to be re-runnable by one person on one machine with nothing
installed. The README says where Postgres takes over and why nothing above this layer
changes when it does.

Three things are deduplicated, and they are three different things:

  * a **channel**, keyed by its channel id when the documented API supplied one and by its
    normalised handle when it did not. A creator renames a handle and rewrites a URL, and the
    same channel then arrives twice in a list of two hundred.
  * a **contact**, keyed by the address itself. One agency that represents four channels is
    one contact with four links to channels, not four contacts. Counting it four times would
    inflate every number a reader might trust and would send the same mailbox four messages.
  * a **job**, keyed by the input URL. It holds attempts and the last error, and it is what
    makes a resumed run touch what is left rather than everything.

Decisions live in their own table and are never written by the collection path. That is the
rule behind "the upsert never overwrites human work": computed columns and decision columns
are not merely displayed apart, they are stored apart, so there is no code path in which a
re-run can silently replace a human's answer.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Iterable

from .model import ChannelRecord, DEC_PENDING, utcstamp

SCHEMA = """
CREATE TABLE IF NOT EXISTS channels (
  channel_key      TEXT PRIMARY KEY,
  key_kind         TEXT,
  channel_url      TEXT NOT NULL,
  handle           TEXT,
  title            TEXT,
  publication      TEXT,
  relation         TEXT,
  relation_reason  TEXT,
  check_result     TEXT,
  contact_value    TEXT,
  contact_kind     TEXT,
  site_url         TEXT,
  site_origin      TEXT,
  summary          TEXT,
  review_reason    TEXT,
  fingerprint      TEXT,
  payload          TEXT NOT NULL,
  first_seen       TEXT,
  last_seen        TEXT
);
CREATE TABLE IF NOT EXISTS contacts (
  contact_key TEXT PRIMARY KEY,
  value       TEXT NOT NULL,
  kind        TEXT,
  check_result TEXT,
  first_seen  TEXT,
  last_seen   TEXT
);
CREATE TABLE IF NOT EXISTS channel_contacts (
  channel_key TEXT NOT NULL,
  contact_key TEXT NOT NULL,
  relation    TEXT,
  PRIMARY KEY (channel_key, contact_key)
);
CREATE TABLE IF NOT EXISTS jobs (
  job_key    TEXT PRIMARY KEY,
  input_url  TEXT NOT NULL,
  state      TEXT NOT NULL,
  attempts   INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  last_try   TEXT
);
CREATE TABLE IF NOT EXISTS decisions (
  channel_key TEXT NOT NULL,
  contact_key TEXT NOT NULL,
  decision    TEXT NOT NULL,
  reason      TEXT,
  decided_by  TEXT,
  decided_at  TEXT,
  fingerprint TEXT,
  PRIMARY KEY (channel_key, contact_key)
);
"""


def job_key(url: str) -> str:
    """One channel written three ways in one list is one job.

    A list assembled by hand always contains the same channel as a handle, as a channel URL,
    with a trailing slash, with a tracking parameter and with a tab suffix.
    """
    k = (url or "").strip().rstrip("/").split("?")[0].split("#")[0].lower()
    k = k.replace("http://", "https://").replace("https://m.", "https://www.")
    k = k.replace("https://youtube.com", "https://www.youtube.com")
    for suffix in ("/about", "/videos", "/featured", "/streams", "/shorts", "/community"):
        k = k.removesuffix(suffix)
    return k


def contact_key(value: str, kind: str = "email") -> str:
    return f"{kind}:{(value or '').strip().lower()}"


class Store:
    def __init__(self, path: str = ":memory:") -> None:
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    # -- channels and contacts ------------------------------------------------

    def upsert_channel(self, rec: ChannelRecord) -> None:
        prior = self.get_channel(rec.channel_key)
        first_seen = prior["first_seen"] if prior else rec.first_seen
        self.db.execute(
            """INSERT INTO channels
               (channel_key, key_kind, channel_url, handle, title, publication, relation,
                relation_reason, check_result, contact_value, contact_kind, site_url,
                site_origin, summary, review_reason, fingerprint, payload, first_seen, last_seen)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(channel_key) DO UPDATE SET
                 key_kind=excluded.key_kind, channel_url=excluded.channel_url,
                 handle=excluded.handle, title=excluded.title,
                 publication=excluded.publication, relation=excluded.relation,
                 relation_reason=excluded.relation_reason, check_result=excluded.check_result,
                 contact_value=excluded.contact_value, contact_kind=excluded.contact_kind,
                 site_url=excluded.site_url, site_origin=excluded.site_origin,
                 summary=excluded.summary, review_reason=excluded.review_reason,
                 fingerprint=excluded.fingerprint, payload=excluded.payload,
                 last_seen=excluded.last_seen""",
            (rec.channel_key, rec.key_kind, rec.channel_url, rec.handle, rec.title,
             rec.publication, rec.relation, rec.relation_reason, rec.check_result,
             rec.contact_value, rec.contact_kind, rec.site_url, rec.site_origin,
             rec.summary(), rec.review_reason, rec.evidence_fingerprint(),
             json.dumps(rec.to_dict(), ensure_ascii=False), first_seen, utcstamp()),
        )
        if rec.contact_value:
            ck = contact_key(rec.contact_value, rec.contact_kind or "email")
            self.db.execute(
                """INSERT INTO contacts (contact_key, value, kind, check_result, first_seen, last_seen)
                   VALUES (?,?,?,?,?,?)
                   ON CONFLICT(contact_key) DO UPDATE SET
                     check_result=excluded.check_result, last_seen=excluded.last_seen""",
                (ck, rec.contact_value, rec.contact_kind or "email", rec.check_result,
                 utcstamp(), utcstamp()),
            )
            self.db.execute(
                """INSERT INTO channel_contacts (channel_key, contact_key, relation)
                   VALUES (?,?,?)
                   ON CONFLICT(channel_key, contact_key) DO UPDATE SET relation=excluded.relation""",
                (rec.channel_key, ck, rec.relation),
            )
        self.db.commit()

    def get_channel(self, channel_key: str) -> dict[str, Any] | None:
        r = self.db.execute("SELECT * FROM channels WHERE channel_key=?", (channel_key,)).fetchone()
        return dict(r) if r else None

    def channels(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.db.execute("SELECT * FROM channels ORDER BY title, channel_key")]

    def contacts(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.db.execute("SELECT * FROM contacts ORDER BY value")]

    def counts(self) -> dict[str, int]:
        rows = self.db.execute("SELECT summary, COUNT(*) c FROM channels GROUP BY summary")
        return {r["summary"]: r["c"] for r in rows}

    # -- jobs -----------------------------------------------------------------

    def queue(self, urls: Iterable[str]) -> int:
        """Put a list of inputs in the queue. Already-known inputs stay as they are."""
        added = 0
        for u in urls:
            k = job_key(u)
            cur = self.db.execute(
                "INSERT OR IGNORE INTO jobs (job_key, input_url, state, attempts) VALUES (?,?,?,0)",
                (k, u, "pending"))
            added += cur.rowcount if cur.rowcount > 0 else 0
        self.db.commit()
        return added

    def pending_jobs(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.db.execute(
            "SELECT * FROM jobs WHERE state IN ('pending','failed') ORDER BY attempts, job_key")]

    def failed_jobs(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.db.execute(
            "SELECT * FROM jobs WHERE state='failed' ORDER BY attempts DESC")]

    def job_done(self, url: str) -> None:
        self.db.execute(
            "UPDATE jobs SET state='done', last_error='', last_try=? WHERE job_key=?",
            (utcstamp(), job_key(url)))
        self.db.commit()

    def job_failed(self, url: str, error: str) -> None:
        self.db.execute(
            """UPDATE jobs SET state='failed', attempts=attempts+1, last_error=?, last_try=?
               WHERE job_key=?""",
            (error[:500], utcstamp(), job_key(url)))
        self.db.commit()

    # -- decisions ------------------------------------------------------------

    def set_decision(self, channel_key: str, ckey: str, decision: str, reason: str,
                     decided_by: str, fingerprint: str) -> None:
        self.db.execute(
            """INSERT INTO decisions
               (channel_key, contact_key, decision, reason, decided_by, decided_at, fingerprint)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(channel_key, contact_key) DO UPDATE SET
                 decision=excluded.decision, reason=excluded.reason,
                 decided_by=excluded.decided_by, decided_at=excluded.decided_at,
                 fingerprint=excluded.fingerprint""",
            (channel_key, ckey, decision, reason, decided_by, utcstamp(), fingerprint))
        self.db.commit()

    def get_decision(self, channel_key: str, ckey: str) -> dict[str, Any] | None:
        r = self.db.execute(
            "SELECT * FROM decisions WHERE channel_key=? AND contact_key=?",
            (channel_key, ckey)).fetchone()
        return dict(r) if r else None

    def decisions(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.db.execute("SELECT * FROM decisions ORDER BY channel_key")]

    def decision_state(self, channel_key: str, rec_contact: str, rec_kind: str,
                       fingerprint: str) -> dict[str, Any]:
        """What a reviewer's answer is worth after a re-run.

        Unchanged evidence keeps the decision. Changed evidence returns it to the queue with
        the reason saying so, and keeps the previous answer visible, because a reviewer who
        is asked to look again is entitled to see what they said the first time.
        """
        if not rec_contact:
            return {"decision": DEC_PENDING, "reason": "", "decided_at": "", "changed": False}
        ck = contact_key(rec_contact, rec_kind or "email")
        prior = self.get_decision(channel_key, ck)
        if not prior:
            return {"decision": DEC_PENDING, "reason": "", "decided_at": "", "changed": False}
        if prior["fingerprint"] == fingerprint:
            return {**prior, "changed": False}
        return {**prior, "decision": DEC_PENDING, "changed": True,
                "reason": f"evidence changed since this was decided ({prior['decision']}: "
                          f"{prior['reason']})"}
