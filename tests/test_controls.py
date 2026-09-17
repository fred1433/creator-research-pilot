"""The three controls a reader is entitled to before believing any of this.

They run on the same input, through the same production path, to the same review sheet and
the same draft. Nothing here calls a private helper that the real run does not use.

  1. A positive control. A correctly sourced finding proposed from outside the workflow is
     accepted, reaches the sheet and, once a human approves it, produces a draft. A guard
     that refuses everything is not a guard, it is a broken pipeline, and it would pass a
     test suite that only ever checks that bad things are rejected.

  2. An injected case. A finding proposed from outside, citing a real page and a sentence
     that is not on it, is rejected. It reaches neither the sheet nor a draft, and the row's
     status is unchanged by its existence.

  3. A causal mutation. The same input again, with the guard neutralised and nothing else
     changed. The business assertion of case 2 is then false: the invented address appears in
     the sheet and a draft renders with it. The guard is therefore the cause of the outcome,
     not a step that happens to sit next to it. This is not an exit code check: the assertion
     that fails is the one about the workflow's output.
"""

from __future__ import annotations

import json

import pytest
from conftest import case

from pilot import run as R
from pilot import verify
from pilot.drafts import render as render_draft
from pilot.model import DEC_APPROVED
from pilot.store import contact_key

POSITIVE = "hello@bramblewoodbakes.test"
INJECTED = "press@fernhollowforge.test"


def sheet_text(store) -> str:
    from pilot.sheet import to_string
    return to_string(R.build_rows(store))


def test_positive_control_a_correctly_sourced_proposal_is_accepted(cases, fetcher, checker, store):
    entry = case(cases, "bramblewoodbakes")
    assert entry["proposed"][0]["value"] == POSITIVE

    rec = R.process(entry, fetcher, checker, "controlled-key-not-a-secret")

    proposed = [f for f in rec.findings if f.origin == "proposed"]
    assert proposed, "the proposal did not survive the guard: the guard rejects everything"
    assert rec.contact_value == POSITIVE
    assert rec.summary() == "verified"

    store.upsert_channel(rec)
    assert POSITIVE.split("@")[1] in sheet_text(store)

    payload = json.loads(store.get_channel(rec.channel_key)["payload"])
    ck = contact_key(rec.contact_value, "email")
    store.set_decision(rec.channel_key, ck, DEC_APPROVED, "checked the source page",
                       "reviewer", rec.evidence_fingerprint())
    draft = render_draft(payload, {"decision": DEC_APPROVED})
    assert draft["ok"], draft.get("reason")
    assert draft["sent"] is False
    assert draft["to"] == POSITIVE


def test_injected_finding_never_reaches_the_sheet_or_a_draft(cases, fetcher, checker, store):
    entry = case(cases, "fernhollowforge")
    assert entry["proposed"][0]["value"] == INJECTED

    rec = R.process(entry, fetcher, checker, "controlled-key-not-a-secret")
    store.upsert_channel(rec)

    verdicts = [r.get("verdict") for r in rec.refused if r.get("finding", {}).get("value") == INJECTED]
    assert verdicts == [verify.REJECTED_QUOTE_ABSENT]

    assert rec.contact_value == ""
    assert rec.summary() == "not found"
    assert INJECTED not in sheet_text(store)

    payload = json.loads(store.get_channel(rec.channel_key)["payload"])
    draft = render_draft(payload, {"decision": DEC_APPROVED})
    assert draft["ok"] is False
    assert draft["reason"].startswith("blocked")


def test_causal_mutation_without_the_guard_the_invented_address_is_published(
        cases, fetcher, checker, store, monkeypatch):
    """Same input, same path, guard neutralised. The assertion above must now fail."""
    entry = case(cases, "fernhollowforge")

    monkeypatch.setattr(
        verify, "verify_finding",
        lambda finding, fetcher: {"verdict": "accepted", "ok": True, "content_sha256_now": ""})

    rec = R.process(entry, fetcher, checker, "controlled-key-not-a-secret")
    store.upsert_channel(rec)
    text = sheet_text(store)

    # The business assertion of the previous test, restated here, and it is now false.
    assert rec.contact_value == INJECTED
    # The sheet masks the mailbox, so the invented address appears as p***@fernhollowforge.test.
    # Masked or not, it is in the review surface and it is what a draft would be sent to.
    assert "p***@fernhollowforge.test" in text
    assert rec.summary() != "not found"

    payload = json.loads(store.get_channel(rec.channel_key)["payload"])
    draft = render_draft(payload, {"decision": DEC_APPROVED})
    assert draft["ok"] is True, "with the guard removed, the draft is produced from an invention"
    assert draft["to"] == INJECTED
