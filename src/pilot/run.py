"""The workflow, end to end, and the command that runs it.

The order the decisions are made in is the part that matters:

    collect  ->  propose  ->  GUARD  ->  name the relation  ->  check the mailbox
             ->  four statuses  ->  store  ->  review sheet  ->  draft

Nothing is believed before the guard, and the guard is the only place a proposal becomes a
fact. That single choke point is what a test can neutralise to show it carries weight.

Two modes, one production path:

    python -m pilot.run run --input my_channels.json --out out/ --live-check
    python -m pilot.run replay --input fixtures/controlled_cases.json --out data/

`run` reads the public web and, with `--live-check`, spends one verification credit per new
address. `replay` reads controlled sources from `fixtures/sources/`, touches no network,
spends nothing and reaches no creator. Same collection, same guard, same relation policy,
same sheet, same drafts.

Everything a human supplied by hand is labelled `manual_input` and counted in the summary,
so a hand-fed URL can never be read as a collected one.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from typing import Any

from . import contacts as K
from . import relation as R
from . import youtube as YT
from .drafts import render as render_draft
from .emailcheck import ControlledChecker, MailboxChecker, reading
from .fetcher import LiveFetcher, ReplayFetcher
from .mask import mask_text
from .model import (CHK_NOT_CHECKED, DEC_PENDING, PUB_ACCESS_BLOCKED, PUB_FOUND, PUB_NOT_FOUND,
                    REL_CREATOR, REL_REPRESENTATIVE, REL_UNCONFIRMED, ChannelRecord, Finding,
                    utcstamp)
from .sheet import COLUMNS, read_decisions, row_for, sheets_adapter, write_csv
from .store import Store, contact_key, job_key
from .verify import verify_all


def git_commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True,
                              text=True, timeout=10).stdout.strip() or "unknown"
    except Exception:
        return "unknown"


# ------------------------------------------------------------------ one channel

def process(entry: dict[str, Any], fetcher, checker,
            api_key: str = "", access_note: str = "") -> ChannelRecord:
    """One channel, from an input URL to four statuses. Every branch ends in a reason."""
    url = entry["url"]
    rec = ChannelRecord(channel_key=job_key(url), channel_url=url,
                        handle=YT.handle_of(url), sample_note=entry.get("note", ""))

    # -- the channel, through the documented API and through nothing else -------
    api = YT.channels_list(url, api_key, fetcher)
    sites: list[str] = []
    if api.get("ok"):
        rec.channel_key = api["channel_id"] or rec.channel_key
        rec.key_kind = "channel_id"
        rec.title = api.get("title", "")
        rec.handle = (api.get("custom_url") or rec.handle).lstrip("@")
        rec.access = f"youtube data api channels.list: {api.get('http_status', 'ok')}"
        rec.add(YT.identity_finding(api))
        sites = YT.sites_in_description(api.get("description", ""))
    else:
        reason = access_note or api.get("reason")
        rec.access = f"youtube data api channels.list unavailable: {reason}"
        if not access_note and api.get("detail"):
            rec.access += f" ({api['detail']})"
        rec.publication = PUB_ACCESS_BLOCKED
        rec.title = entry.get("title", "") or rec.handle

    # Proposals from outside the workflow: what an assistant, a list or a colleague believes.
    # They enter here, unbelieved, and go through the same guard as everything collected. This
    # is the path that answers "how do you stop an AI-invented finding": nothing is taken on
    # the word of whoever proposed it, including us.
    outside: list[Finding] = []
    for p in entry.get("proposed", []):
        try:
            outside.append(Finding(
                kind="contact_email", value=p["value"], requested_url=p["source_url"],
                quote=p["quote"], origin="proposed",
                note=p.get("note", "proposed from outside the workflow, not yet checked")))
        except Exception as ex:
            rec.refused.append({"value": p.get("value", ""), "source_url": p.get("source_url", ""),
                                "reason": f"refused before any check: {ex}"})

    # -- the site: collected from the channel, or supplied by a human ----------
    manual = (entry.get("site") or "").strip()
    candidates = K.candidate_sites(sites)
    if candidates:
        rec.site_url, rec.site_origin = candidates[0], "collected"
    elif manual:
        rec.site_url, rec.site_origin = manual, "manual_input"

    proposals: list[Finding] = []
    rel = R.no_site()

    if rec.site_url:
        home = fetcher.get(rec.site_url)
        if not home.get("text"):
            rec.refused.append({"value": rec.site_url, "source_url": rec.site_url,
                                "reason": f"the site could not be read: {home.get('status')}"})
        else:
            rec.site_url = home.get("final_url") or rec.site_url
            rel = R.classify(home.get("body", ""), home.get("text", ""),
                             rec.channel_key if rec.key_kind == "channel_id" else "",
                             rec.handle, rec.title, K.host_of(rec.site_url))
            if rel.get("backlink_quote"):
                try:
                    proposals.append(Finding(
                        kind="relation", value=rec.site_url, requested_url=rec.site_url,
                        final_url=rec.site_url, quote=rel["backlink_quote"][:300],
                        context=rel.get("reason", ""), origin=rec.site_origin,
                        note=rel.get("reason", "")))
                except Exception:
                    pass

            pages = [home]
            for cp in K.contact_pages(home):
                p = fetcher.get(cp)
                if p.get("text"):
                    pages.append(p)

            established = rel["relation"] in (REL_CREATOR, REL_REPRESENTATIVE)
            for p in pages:
                kept, refused = K.addresses_on_page(p, established)
                proposals += kept
                rec.refused += refused
                form = K.contact_form_on_page(p)
                if form is not None and established:
                    proposals.append(form)

    # -- THE GUARD. Nothing above this line has been believed yet. -------------
    accepted, refused = verify_all(proposals + outside, fetcher)
    rec.refused += refused

    # -- pick one contact and check the mailbox --------------------------------
    emails = [f for f in accepted if f.kind == "contact_email"]
    forms = [f for f in accepted if f.kind == "contact_form"]
    if emails:
        best = _prefer(emails, rec.site_url)
        rec.contact_value, rec.contact_kind = best.value, "email"
        result = checker.check(best.value)
        rec.check_result = result.get("result", CHK_NOT_CHECKED)
        rec.check_livemode = bool(result.get("livemode"))
        rec.check_reason = result.get("reason", "")
        if result.get("suggestion"):
            rec.refused.append({
                "value": result["suggestion"], "source_url": "",
                "reason": "not used: a suggested correction never replaces a published address"})
    elif forms:
        rec.contact_value, rec.contact_kind = forms[0].value, "form"
        rec.check_result = CHK_NOT_CHECKED
        rec.check_reason = "a contact form has no mailbox to check"

    for f in accepted:
        rec.add(f)

    # -- the four statuses -----------------------------------------------------
    if rec.contact_value:
        rec.publication = PUB_FOUND
    elif rec.publication != PUB_ACCESS_BLOCKED:
        rec.publication = PUB_NOT_FOUND
    rec.relation = rel["relation"]
    rec.relation_reason = rel["reason"] + (f": {rel['detail']}" if rel.get("detail") else "")
    rec.review_reason, rec.needs_human = _row_reason(rec, rel, bool(rec.site_url))
    rec.last_seen = utcstamp()
    return rec


def _prefer(found: list[Finding], site_url: str) -> Finding:
    """An address on the site's own domain beats one on anybody else's."""
    host = K.host_of(site_url)

    def score(f: Finding) -> tuple[int, int]:
        dom = f.value.lower().partition("@")[2]
        own = 1 if host and (dom == host or dom.endswith("." + host)) else 0
        generic = 1 if f.value.lower().split("@")[0] in ("info", "hello", "contact", "enquiries") else 0
        return (own, -generic)

    return sorted(found, key=score, reverse=True)[0]


def _row_reason(rec: ChannelRecord, rel: dict[str, Any], had_site: bool) -> tuple[str, bool]:
    """What this row says, and whether a human has anything to weigh.

    The second half matters as much as the first. A row with nothing published and nothing a
    person could overturn is a finished answer, not a queue item, and padding the queue with
    finished answers is how a review surface stops being read.
    """
    refused_by_relation = any(
        r.get("reason", "").startswith("refused: the address sits on") for r in rec.refused)

    if rec.publication == PUB_ACCESS_BLOCKED and not rec.contact_value:
        return (f"the channel step could not run: {rec.access}. The perimeter actually "
                f"consulted is therefore incomplete, and this is not a statement that this "
                f"channel publishes nothing"), True
    if rec.contact_value and rec.contact_kind == "form":
        return "no address published, and a professional contact form is open to a human", True
    if rec.contact_value:
        if rec.relation == REL_REPRESENTATIVE:
            return "write to a representative, not to the creator directly", True
        if rec.check_result == "ok":
            return "", False
        if rec.check_result == "catch_all":
            return ("the check cannot conclude on this mailbox: a second, independent "
                    "confirmation of the person and the address is needed before writing"), True
        if rec.check_result == CHK_NOT_CHECKED:
            return f"the mailbox was not checked on this run: {rec.check_reason}", True
        return (f"the mailbox check returned {rec.check_result}: "
                f"{reading(rec.check_result)}"), True
    if had_site and rec.relation in (REL_CREATOR, REL_REPRESENTATIVE):
        return ("the site is established as a property of this channel and publishes no "
                "professional contact"), False
    if had_site:
        return rel["reason"], refused_by_relation
    if rec.publication == PUB_ACCESS_BLOCKED:
        return f"the channel step could not run: {rec.access}", True
    return "the channel publishes no site the workflow could read", False


# ------------------------------------------------------------------ a whole list

def execute(entries: list[dict[str, str]], store: Store, fetcher, checker,
            api_key: str = "", stop_after: int = 0, verbose: bool = True,
            access_note: str = "", reprocess: bool = False) -> dict[str, Any]:
    """Run the queue. A job is marked done only once its record is written.

    `stop_after` exists for the interruption test: it stops the run between two jobs, the
    way a lost connection would, so a resumed run can be shown to pick up what is left
    without duplicating what is done and without touching a decision.
    """
    store.queue([e["url"] for e in entries])
    if reprocess:
        store.requeue([e["url"] for e in entries])
    by_key = {job_key(e["url"]): e for e in entries}
    done = 0
    for job in store.pending_jobs():
        entry = by_key.get(job["job_key"])
        if entry is None:
            continue
        if stop_after and done >= stop_after:
            break
        try:
            rec = process(entry, fetcher, checker, api_key, access_note)
        except Exception as ex:
            store.job_failed(entry["url"], f"{type(ex).__name__}: {ex}")
            if verbose:
                print(f"    failed, stays in the queue: {ex}", flush=True)
            continue
        store.upsert_channel(rec)
        store.job_done(entry["url"])
        done += 1
        if verbose:
            print(f"[{done}] {rec.title or rec.channel_url}: {rec.summary()} "
                  f"({rec.publication} / {rec.relation})", flush=True)
    return {"processed": done, "failed": store.failed_jobs()}


def build_rows(store: Store) -> list[dict[str, str]]:
    """The sheet, with each row's decision resolved against the evidence behind it."""
    rows = []
    for ch in store.channels():
        payload = json.loads(ch["payload"])
        decision = store.decision_state(ch["channel_key"], payload.get("contact_value", ""),
                                        payload.get("contact_kind", ""), ch["fingerprint"])
        rows.append(row_for(payload, decision))
    return rows


def report(store: Store, fetcher, checker, mode: str,
           api_note: str) -> dict[str, Any]:
    """Everything a reader needs to judge the run, including what it could not do."""
    rows = build_rows(store)
    channels = [json.loads(c["payload"]) for c in store.channels()]
    manual = sum(1 for c in channels if c.get("site_origin") == "manual_input")
    drafts = []
    for c in channels:
        d = store.decision_state(c["channel_key"], c.get("contact_value", ""),
                                 c.get("contact_kind", ""), c.get("evidence_fingerprint", ""))
        out = render_draft(c, d)
        drafts.append({"channel_key": c["channel_key"], "ok": out["ok"],
                       "reason": out.get("reason", ""), "sent": False})
    return {
        "generated_at": utcstamp(),
        "mode": mode,
        "commit": git_commit(),
        "youtube_access": api_note,
        "sheets_adapter": sheets_adapter(),
        "counts": store.counts(),
        "manual_inputs": manual,
        "verification_credits_spent": checker.spent,
        "channels": channels,
        "rows": rows,
        "drafts": drafts,
        "contacts_deduplicated": len(store.contacts()),
        "failed_jobs": store.failed_jobs(),
        "decisions": store.decisions(),
        "fetch_log": getattr(fetcher, "log", []),
    }


def write_outputs(payload: dict[str, Any], out_dir: str, private_dir: str = "") -> None:
    """The shareable copy is masked as it is written. The full copy never lands in the repo."""
    os.makedirs(out_dir, exist_ok=True)
    if private_dir:
        os.makedirs(private_dir, exist_ok=True)
        with open(os.path.join(private_dir, "results.full.json"), "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1)

    masked = json.loads(mask_text(json.dumps(payload, ensure_ascii=False)))
    with open(os.path.join(out_dir, "results.json"), "w", encoding="utf-8") as fh:
        json.dump(masked, fh, ensure_ascii=False, indent=1)
    write_csv(masked["rows"], os.path.join(out_dir, "review.csv"))


# ------------------------------------------------------------------ CLI

def probe_api(api_key: str, fetcher) -> tuple[str, str]:
    """Ask the documented API once whether this key may use it.

    Once rather than per channel: if the key has no access, the answer will not change on the
    twelfth call, and a run should not spend twelve requests learning it again. The outcome
    is recorded on every record either way.
    """
    if not api_key:
        return "", YT.NO_KEY
    p = YT.channels_list("https://www.youtube.com/@youtube", api_key, fetcher)
    if p.get("ok"):
        return api_key, "available"
    return "", f"{p.get('reason')} ({p.get('detail', '')})".strip()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="pilot", description="Creator research pilot")
    sub = ap.add_subparsers(dest="cmd", required=True)

    for name, help_text in (("run", "execute on a real list, reading the public web"),
                            ("replay", "execute on controlled sources, no network, no spend")):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--input", required=True, help="JSON list of {url, site?, note?}")
        p.add_argument("--out", default="data", help="shareable output, addresses masked")
        p.add_argument("--private-out", default="", help="full output, outside this repository")
        p.add_argument("--db", default="", help="sqlite file, default <out>/pilot.sqlite3")
        p.add_argument("--delay", type=float, default=2.0)
        p.add_argument("--stop-after", type=int, default=0)
        p.add_argument("--reprocess", action="store_true",
                       help="read the list again and rewrite every calculated column")
        if name == "run":
            p.add_argument("--live-check", action="store_true",
                           help="spend one verification credit per new address")

    d = sub.add_parser("decide", help="record a human decision on one row")
    d.add_argument("--db", required=True)
    d.add_argument("--channel", required=True)
    d.add_argument("--decision", required=True, choices=["approved", "rejected", "pending"])
    d.add_argument("--reason", default="")
    d.add_argument("--by", default="reviewer")

    i = sub.add_parser("import-decisions", help="read the decision columns back from a sheet")
    i.add_argument("--db", required=True)
    i.add_argument("--csv", required=True)

    e = sub.add_parser("export", help="rewrite the sheet and the report from the store")
    e.add_argument("--db", required=True)
    e.add_argument("--out", default="data")

    a = ap.parse_args(argv)

    if a.cmd in ("run", "replay"):
        out_dir = a.out
        db_path = a.db or os.path.join(out_dir, "pilot.sqlite3")
        os.makedirs(out_dir, exist_ok=True)
        store = Store(db_path)

        if a.cmd == "replay":
            base = os.path.dirname(a.input) or "."
            fetcher = ReplayFetcher(os.path.join(base, "sources"))
            responses_path = os.path.join(base, "mailbox_responses.json")
            responses = json.load(open(responses_path, encoding="utf-8")) \
                if os.path.exists(responses_path) else {}
            checker = ControlledChecker(responses)
            api_key = "controlled-key-not-a-secret"
            api_note = "answered from controlled sources: no network, no quota, no credit"
        else:
            fetcher = LiveFetcher(delay=a.delay)
            api_key, api_note = probe_api(os.environ.get("GOOGLE_API_KEY", ""), fetcher)
            checker = MailboxChecker(live=getattr(a, "live_check", False))
        print(f"youtube data api: {api_note}", flush=True)

        entries = json.load(open(a.input, encoding="utf-8"))
        execute(entries, store, fetcher, checker, api_key, stop_after=a.stop_after,
                access_note='' if api_key else api_note, reprocess=a.reprocess)
        payload = report(store, fetcher, checker, a.cmd, api_note)
        write_outputs(payload, out_dir, a.private_out)
        print(json.dumps(payload["counts"], indent=1))
        print(f"manual inputs: {payload['manual_inputs']}, "
              f"verification credits spent: {payload['verification_credits_spent']}, "
              f"jobs still queued: {len(store.failed_jobs())}")
        return 0

    if a.cmd == "decide":
        store = Store(a.db)
        ch = store.get_channel(a.channel)
        if not ch:
            print(f"no channel with key {a.channel}")
            return 1
        payload = json.loads(ch["payload"])
        ck = contact_key(payload.get("contact_value", ""), payload.get("contact_kind", "email"))
        store.set_decision(a.channel, ck, a.decision, a.reason, a.by, ch["fingerprint"])
        print(f"{a.channel}: {a.decision} by {a.by}")
        return 0

    if a.cmd == "import-decisions":
        store = Store(a.db)
        n = 0
        for row in read_decisions(a.csv):
            ch = store.get_channel(row["channel_key"])
            if not ch:
                continue
            payload = json.loads(ch["payload"])
            ck = contact_key(payload.get("contact_value", ""), payload.get("contact_kind", "email"))
            store.set_decision(row["channel_key"], ck, row["decision"], row["reason"],
                               row["decided_by"] or "sheet", ch["fingerprint"])
            n += 1
        print(f"{n} decision(s) read back from the sheet")
        return 0

    if a.cmd == "export":
        store = Store(a.db)
        rows = build_rows(store)
        os.makedirs(a.out, exist_ok=True)
        write_csv(rows, os.path.join(a.out, "review.csv"))
        print(f"{len(rows)} row(s), columns: {', '.join(COLUMNS[:4])} ...")
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
