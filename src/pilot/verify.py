"""The guard. Everything upstream proposes; this is the only thing that decides.

It takes a Finding, downloads the source URL again, and looks for the quote in the page as a
reader would see it. If the quote is not there, the finding is rejected and whatever
depended on it degrades to review or to not found.

Why re-download rather than trust the first read: the failure this guards against is not a
network glitch, it is a confident invention. An assistant asked for "the creator's business
email with a source" returns a URL that exists and a quote that does not. Both halves look
right. Only a second request separates them.

Why a literal quote rather than a summary: a paraphrase always matches something. The quote
is the discriminating test, the same way a rejected neighbouring address is what proves a
mail server discriminates between mailboxes.

What this guard does NOT prove, and no quote check ever can: that the page is authoritative,
or that it supports the conclusion drawn from it. "We no longer represent Alice" sits on a
real page next to a real address, and a quote check passes on both. That is why the relation
question is decided separately, in `relation.py`, against what the page says about who is
who, and why every accepted finding keeps its surrounding context for the human who reads it.

The guard is deliberately one choke point: `verify_finding` is the single function that can
turn a proposal into an accepted fact. That is what makes it testable. Neutralise it and an
injected contact walks into the review sheet and into a draft, which is exactly what
`tests/test_causal_mutation.py` asserts, on the same input and the same production path.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from .model import Finding, sha256

ACCEPTED = "accepted"
ACCEPTED_SOURCE_CHANGED = "accepted, source changed since it was observed"
REJECTED_QUOTE_ABSENT = "rejected: quote not found on the page"
RECHECK_FAILED = "recheck failed: source could not be downloaded"
REJECTED_DISALLOWED = "rejected: source disallowed by robots.txt"
REJECTED_REDIRECTED = "rejected: the source redirected to a different site"


def _fold(s: str) -> str:
    """Compare the way a reader compares: case, accents and spacing carry no meaning here.

    Nothing more is folded than that. Punctuation is kept, because dropping it is how a quote
    starts matching text it never came from.
    """
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.replace("\u2019", "'").replace("\u2018", "'")
    s = s.replace("\u201c", '"').replace("\u201d", '"')
    s = s.replace("\u2013", "-").replace("\u2014", "-")
    return re.sub(r"\s+", " ", s).strip().lower()


def _host(url: str) -> str:
    m = re.match(r"https?://([^/]+)", url or "")
    return (m.group(1) if m else "").lower().removeprefix("www.")


def verify_finding(finding: Finding, fetcher: Any) -> dict[str, Any]:
    """Re-download the source and look for the quote. Returns a verdict as data.

    A verdict is data rather than an exception because a rejected finding is a normal
    outcome that has to be shown to a human, not an error that stops the run.

    The distinction between "the quote is not there" and "the page could not be read" is
    kept: the first is a claim that failed, the second is a check that did not happen. Both
    keep the value out of the sheet; only the first says anything about the claim.
    """
    page = fetcher.get(finding.source_url)
    status = page.get("status", "")
    observed = {
        "requested_url": finding.source_url,
        "final_url": page.get("final_url", ""),
        "source_status": status,
        "checked_at": None,
    }

    if str(status).startswith("disallowed"):
        return {"verdict": REJECTED_DISALLOWED, "ok": False, **observed}
    if not page.get("text"):
        return {"verdict": RECHECK_FAILED, "ok": False, **observed}

    final = page.get("final_url") or finding.source_url
    if _host(final) and _host(final) != _host(finding.source_url):
        return {"verdict": REJECTED_REDIRECTED, "ok": False, **observed}

    needle = _fold(finding.quote)
    matched_in = ""
    if needle in _fold(page["text"]):
        matched_in = "visible text"
    elif needle in _fold(page.get("body", "")):
        # A link is a fact about the markup: a browser renders `href="...youtube.com/@x"` as a
        # link to that channel although the address never appears in the visible text. The
        # distinction is kept rather than blurred, because a reviewer reading "found in the
        # markup" knows to open the page and click rather than to look for a sentence.
        matched_in = "markup"
    if not matched_in:
        return {"verdict": REJECTED_QUOTE_ABSENT, "ok": False, "quote": finding.quote, **observed}
    observed["matched_in"] = matched_in

    fresh = sha256(page["text"])
    if finding.content_sha256 and fresh != finding.content_sha256:
        return {"verdict": ACCEPTED_SOURCE_CHANGED, "ok": True,
                "content_sha256_then": finding.content_sha256,
                "content_sha256_now": fresh, **observed}
    return {"verdict": ACCEPTED, "ok": True, "content_sha256_now": fresh, **observed}


def verify_all(findings: list[Finding], fetcher: Any) -> tuple[list[Finding], list[dict[str, Any]]]:
    """Split proposals into the ones that survived a second look and the ones that did not."""
    kept: list[Finding] = []
    refused: list[dict[str, Any]] = []
    for f in findings:
        v = verify_finding(f, fetcher)
        if v["ok"]:
            f.content_sha256 = v.get("content_sha256_now", f.content_sha256)
            kept.append(f)
        else:
            refused.append({"finding": f.to_dict(), **v})
    return kept, refused
