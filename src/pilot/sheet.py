"""The review sheet: what a human sees, and the only columns a human may write in.

The sheet is a CSV, and the CSV is a round trip. It is exported, a reviewer fills in the
decision columns, it is imported back, and the decisions are stored in a table the
collection path never writes to. A later run recomputes every calculated column and leaves
the decisions alone unless the evidence they were taken against has changed.

Columns come in two blocks and the blocks are never mixed:

  `c_` columns are calculated. A run owns them and overwrites them freely.
  `d_` columns are decisions. A person owns them. No code path in the collection pipeline
  writes to them, which is what makes "the upsert never overwrites human work" a property of
  the schema rather than a promise.

A spreadsheet is what the buyer named as an example of a review surface, so the columns are
the ones a reviewer actually uses: what was found, on which page, in which words, what the
mailbox check said, and what is missing for the row to be decidable. The evidence columns
carry a clickable source per row, so no decision has to be taken on a summary.
"""

from __future__ import annotations

import csv
import io
from typing import Any

from .emailcheck import reading
from .mask import mask_email

CALCULATED = [
    "c_channel_key", "c_title", "c_channel_url", "c_summary", "c_publication",
    "c_relation", "c_relation_reason", "c_contact", "c_contact_kind", "c_check_result",
    "c_check_means", "c_site_url", "c_site_origin", "c_review_reason",
    "c_evidence_url", "c_evidence_quote", "c_observed_at", "c_fingerprint",
]
DECISION = ["d_decision", "d_reason", "d_decided_by", "d_decided_at"]
COLUMNS = CALCULATED + DECISION


def _first_evidence(payload: dict[str, Any], kinds: tuple[str, ...]) -> dict[str, Any]:
    for f in payload.get("findings", []):
        if f.get("kind") in kinds:
            return f
    return {}


def row_for(record: dict[str, Any], decision: dict[str, Any]) -> dict[str, str]:
    """One sheet row. Addresses are masked here because this file gets shared."""
    ev = _first_evidence(record, ("contact_email", "contact_form", "relation",
                                 "channel_identity", "access_limit"))
    return {
        "c_channel_key": record.get("channel_key", ""),
        "c_title": record.get("title", ""),
        "c_channel_url": record.get("channel_url", ""),
        "c_summary": record.get("summary", ""),
        "c_publication": record.get("publication", ""),
        "c_relation": record.get("relation", ""),
        "c_relation_reason": record.get("relation_reason", ""),
        "c_contact": mask_email(record.get("contact_value", "")),
        "c_contact_kind": record.get("contact_kind", ""),
        "c_check_result": record.get("check_result", ""),
        "c_check_means": reading(record.get("check_result", "")),
        "c_check_livemode": "yes" if record.get("check_livemode") else "no",
        "c_site_url": record.get("site_url", ""),
        "c_site_origin": record.get("site_origin", ""),
        "c_review_reason": record.get("review_reason", ""),
        "c_evidence_url": ev.get("final_url", ""),
        "c_evidence_quote": ev.get("quote", ""),
        "c_observed_at": ev.get("observed_at", ""),
        "c_fingerprint": record.get("evidence_fingerprint", "")[:16],
        "d_decision": decision.get("decision", "pending"),
        "d_reason": decision.get("reason", ""),
        "d_decided_by": decision.get("decided_by", ""),
        "d_decided_at": decision.get("decided_at", ""),
    }


def write_csv(rows: list[dict[str, str]], path: str) -> None:
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in COLUMNS})


def to_string(rows: list[dict[str, str]]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=COLUMNS)
    w.writeheader()
    for r in rows:
        w.writerow({k: r.get(k, "") for k in COLUMNS})
    return buf.getvalue()


def read_decisions(path: str) -> list[dict[str, str]]:
    """Read back only the decision columns. Anything a reviewer typed elsewhere is ignored."""
    out = []
    with open(path, encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            decision = (r.get("d_decision") or "").strip().lower()
            if decision in ("", "pending"):
                continue
            out.append({
                "channel_key": (r.get("c_channel_key") or "").strip(),
                "decision": decision,
                "reason": (r.get("d_reason") or "").strip(),
                "decided_by": (r.get("d_decided_by") or "").strip(),
            })
    return out


SHEETS_ADAPTER_STATUS = "not connected"
SHEETS_ADAPTER_NOTE = (
    "A Google Sheets adapter would write these same columns to a spreadsheet in the owner's "
    "account, with the calculated block protected and the decision block open. It is not "
    "connected here: connecting it means creating credentials in an account, which belongs to "
    "the owner of the data and not to a demonstration. The CSV round trip carries the same "
    "semantics and is what the tests exercise."
)


def sheets_adapter() -> dict[str, str]:
    return {"status": SHEETS_ADAPTER_STATUS, "note": SHEETS_ADAPTER_NOTE}
