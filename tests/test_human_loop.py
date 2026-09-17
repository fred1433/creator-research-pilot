"""The human loop: a decision that survives, and a decision that is asked again.

A review surface that forgets what a person decided is not a review surface. A review
surface that keeps a decision after the evidence behind it has changed is worse: it carries
an approval that nobody would give twice.

These tests exercise both halves through the sheet the reviewer actually uses, including the
CSV round trip, and they check that a second run of the same list does not add a row, does
not lose a decision and does not overwrite one.
"""

from __future__ import annotations

import json

from conftest import case

from pilot import run as R
from pilot.model import DEC_APPROVED, DEC_PENDING
from pilot.sheet import read_decisions, write_csv
from pilot.store import contact_key


def process_all(cases, fetcher, checker, store, stop_after=0, reprocess=False):
    return R.execute(cases, store, fetcher, checker, "controlled-key-not-a-secret",
                     stop_after=stop_after, verbose=False, reprocess=reprocess)


def test_a_decision_survives_a_rerun_when_the_evidence_has_not_changed(
        cases, fetcher, checker, store):
    process_all(cases, fetcher, checker, store)
    ch = next(c for c in store.channels() if "Bramblewood" in c["title"])
    payload = json.loads(ch["payload"])
    ck = contact_key(payload["contact_value"], "email")
    store.set_decision(ch["channel_key"], ck, DEC_APPROVED, "source page checked by hand",
                       "reviewer", ch["fingerprint"])

    # A real second pass: every row is read again and every calculated column rewritten.
    again = process_all(cases, fetcher, checker, store, reprocess=True)
    assert again["processed"] == 6, "the refresh has to actually read the rows again"

    rows = R.build_rows(store)
    row = next(r for r in rows if r["c_channel_key"] == ch["channel_key"])
    assert row["d_decision"] == DEC_APPROVED
    assert row["d_reason"] == "source page checked by hand"
    assert row["d_decided_by"] == "reviewer"
    assert len(rows) == 6, "a second pass over the same list must not add a row"


def test_changed_evidence_sends_the_decision_back_to_the_queue(cases, fetcher, checker, store):
    process_all(cases, fetcher, checker, store)
    ch = next(c for c in store.channels() if "Bramblewood" in c["title"])
    payload = json.loads(ch["payload"])
    ck = contact_key(payload["contact_value"], "email")
    store.set_decision(ch["channel_key"], ck, DEC_APPROVED, "checked", "reviewer",
                       ch["fingerprint"])

    state = store.decision_state(ch["channel_key"], payload["contact_value"], "email",
                                 "a-different-fingerprint")
    assert state["decision"] == DEC_PENDING
    assert state["changed"] is True
    # The previous answer stays visible: a reviewer asked to look again is entitled to see it.
    assert "approved" in state["reason"]


def test_the_sheet_round_trip_carries_a_decision_back(cases, fetcher, checker, store, tmp_path):
    process_all(cases, fetcher, checker, store)
    rows = R.build_rows(store)
    ch = next(c for c in store.channels() if "Northwind" in c["title"])
    for r in rows:
        if r["c_channel_key"] == ch["channel_key"]:
            r["d_decision"] = "rejected"
            r["d_reason"] = "we do not write to agencies"
            r["d_decided_by"] = "reviewer"
    path = tmp_path / "review.csv"
    write_csv(rows, str(path))

    back = read_decisions(str(path))
    assert [b["decision"] for b in back] == ["rejected"]
    payload = json.loads(ch["payload"])
    ck = contact_key(payload["contact_value"], payload["contact_kind"])
    store.set_decision(ch["channel_key"], ck, back[0]["decision"], back[0]["reason"],
                       back[0]["decided_by"], ch["fingerprint"])

    process_all(cases, fetcher, checker, store, reprocess=True)
    again = next(r for r in R.build_rows(store) if r["c_channel_key"] == ch["channel_key"])
    assert again["d_decision"] == "rejected"
    assert again["d_reason"] == "we do not write to agencies"


def test_an_interrupted_run_resumes_without_duplicating_or_losing_anything(
        cases, fetcher, checker, store):
    first = process_all(cases, fetcher, checker, store, stop_after=2)
    assert first["processed"] == 2
    partial = store.channels()
    assert len(partial) == 2

    ch = partial[0]
    payload = json.loads(ch["payload"])
    ck = contact_key(payload.get("contact_value", ""), payload.get("contact_kind", "email"))
    store.set_decision(ch["channel_key"], ck, DEC_APPROVED, "decided before the interruption",
                       "reviewer", ch["fingerprint"])

    # The run died here. Resuming picks up what is left, not what is done.
    second = process_all(cases, fetcher, checker, store)
    assert second["processed"] == 4
    assert len(store.channels()) == 6

    row = next(r for r in R.build_rows(store) if r["c_channel_key"] == ch["channel_key"])
    assert row["d_decision"] == DEC_APPROVED
    assert row["d_reason"] == "decided before the interruption"


def test_one_agency_representing_two_channels_is_one_contact(cases, fetcher, checker, store):
    process_all(cases, fetcher, checker, store)
    second = dict(case(cases, "northwindclimbing"))
    second["url"] = "https://www.youtube.com/@northwindclimbing2"
    rec = R.process(case(cases, "northwindclimbing"), fetcher, checker,
                    "controlled-key-not-a-secret")
    rec.channel_key = "UCctrl0000000000000099"
    rec.channel_url = second["url"]
    store.upsert_channel(rec)

    agency = [c for c in store.contacts() if "apexartists" in c["value"]]
    assert len(agency) == 1, "one mailbox, two channels: counting it twice inflates every number"
    links = store.db.execute(
        "SELECT COUNT(*) c FROM channel_contacts WHERE contact_key=?",
        (agency[0]["contact_key"],)).fetchone()["c"]
    assert links == 2


def test_the_same_channel_written_three_ways_is_one_row(cases, fetcher, checker, store):
    process_all(cases, fetcher, checker, store)
    keys = [c["channel_key"] for c in store.channels()]
    assert len(keys) == len(set(keys)) == 6
    assert len(cases) == 7, "the input list holds the same channel twice on purpose"
