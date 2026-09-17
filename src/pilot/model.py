"""The data model.

One idea holds the whole pipeline together: nothing is ever stored as a bare value. Every
fact the workflow believes is a `Finding`, and a Finding without a re-downloadable source
URL and a literal quote from that page cannot exist. The constructor refuses it.

That refusal is the point. A language model can be asked to "cite its sources" and will
happily produce a plausible URL and a plausible quote. What it cannot do is make the quote
appear on the page once the page is downloaded again. So the model proposes and the code
falsifies, and the falsification lives here, in the type itself, not in a prompt.

The second idea is that one word cannot carry four questions. A row is not simply
"verified": the contact was published or it was not, the relation to the channel is of one
kind or another, the mailbox check returned what it returned, and a human has or has not
decided. Those four are stored apart and only joined for display, because collapsing them
early is how a workflow ends up asserting something nobody checked.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any

# A quote shorter than this matches by accident. Longer than this rarely survives the
# whitespace and entity normalisation that any two renderings of a page disagree about.
QUOTE_MIN = 8
QUOTE_MAX = 300

# 1. Was a professional contact published, in the perimeter actually consulted?
PUB_FOUND = "found"
PUB_NOT_FOUND = "not_found"
PUB_ACCESS_BLOCKED = "access_blocked"

# 2. What relates this channel to that recipient?
REL_CREATOR = "creator_confirmed"
REL_REPRESENTATIVE = "representative_confirmed"
REL_UNCONFIRMED = "unconfirmed"
REL_CONFLICTING = "conflicting"

# 3. What did the mailbox check return? The vendor's own word, or this one.
CHK_NOT_CHECKED = "not_checked"

# 4. What did a human decide?
DEC_PENDING = "pending"
DEC_APPROVED = "approved"
DEC_REJECTED = "rejected"
DECISIONS = (DEC_PENDING, DEC_APPROVED, DEC_REJECTED)

# The three-word summary shown to a reader. It is a view over the four fields above, never
# a field of its own, and `not found` has exactly the same standing as the other two.
VERIFIED = "verified"
NEEDS_REVIEW = "needs review"
NOT_FOUND = "not found"


def utcstamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


class UnprovenFinding(ValueError):
    """Raised when something tries to build a Finding without a citable source."""


@dataclass
class Finding:
    """A single claim, with the evidence that makes it checkable.

    `kind`           what the claim is about: channel_identity, site_link, relation,
                     contact_email, access_limit
    `value`          the claim itself, as short as possible
    `requested_url`  the URL asked for
    `final_url`      the URL that answered, after redirects: a redirected source is a
                     different source and the reader is entitled to see both
    `quote`          text copied literally from that page, which must reappear on re-download
    `context`        the surrounding sentence. A quote proves a string is on a page; the
                     context is what lets a reader judge whether the page supports the
                     conclusion drawn from it. "We no longer represent Alice" followed by an
                     address contains a perfectly real quote and a false conclusion.
    `content_sha256` fingerprint of the normalised page text at observation time, so a later
                     re-download that still contains the quote but has otherwise changed is
                     visible rather than silent
    `origin`         `collected` when the workflow obtained it, `manual_input` when a human
                     supplied it. Counted and labelled, never blended into the collected set.
    """

    kind: str
    value: str
    requested_url: str
    quote: str
    final_url: str = ""
    context: str = ""
    observed_at: str = field(default_factory=utcstamp)
    content_sha256: str = ""
    origin: str = "collected"
    note: str = ""

    def __post_init__(self) -> None:
        if not self.requested_url or not re.match(r"^https?://", self.requested_url):
            raise UnprovenFinding(
                f"finding {self.kind!r} has no downloadable source URL: {self.requested_url!r}"
            )
        if not self.final_url:
            self.final_url = self.requested_url
        q = (self.quote or "").strip()
        if len(q) < QUOTE_MIN:
            raise UnprovenFinding(
                f"finding {self.kind!r} quote is too short to prove anything: {q!r}"
            )
        if len(q) > QUOTE_MAX:
            raise UnprovenFinding(f"finding {self.kind!r} quote is too long to survive re-rendering")

    @property
    def source_url(self) -> str:
        return self.final_url or self.requested_url

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ChannelRecord:
    """Everything the workflow knows about one channel, keyed by its channel key.

    The key is the YouTube channel id when the documented API could supply it, and the
    normalised handle when it could not. Handles get renamed and URLs get rewritten, so the
    id is the better key and the record says which one it is using. Every write upserts on
    it, which is what makes a second pass over an overlapping list an update rather than a
    pile of duplicates.
    """

    channel_key: str
    channel_url: str
    key_kind: str = "handle"          # channel_id | handle
    handle: str = ""
    title: str = ""
    sample_note: str = ""

    # What the collection step could and could not reach.
    access: str = ""                  # verbatim note about the documented API route
    site_url: str = ""
    site_origin: str = "collected"    # collected | manual_input

    # The four separate statuses.
    publication: str = PUB_NOT_FOUND
    relation: str = REL_UNCONFIRMED
    relation_reason: str = ""
    contact_value: str = ""
    contact_kind: str = ""            # email | form
    check_result: str = CHK_NOT_CHECKED
    check_livemode: bool = False
    check_reason: str = ""

    review_reason: str = ""
    needs_human: bool = False
    findings: list[Finding] = field(default_factory=list)
    refused: list[dict[str, Any]] = field(default_factory=list)
    first_seen: str = field(default_factory=utcstamp)
    last_seen: str = field(default_factory=utcstamp)

    def add(self, finding: Finding) -> None:
        self.findings.append(finding)

    # -- the summary, computed, never stored as truth --------------------------

    def summary(self) -> str:
        # `verified` is reserved for the complete chain: a contact published on a property
        # established as the creator's own, and a mailbox the check accepted. A contact that
        # reaches a representative is a legitimate result and still a human's call, so it goes
        # to review with the reason saying why.
        if self.publication == PUB_FOUND and self.relation == REL_CREATOR \
                and self.check_result == "ok":
            return VERIFIED
        if self.publication == PUB_FOUND or self.publication == PUB_ACCESS_BLOCKED:
            return NEEDS_REVIEW
        # Nothing was published within the perimeter actually consulted. That is a result, and
        # it only becomes a review item when there is something a human could act on: a
        # candidate this workflow refused for a reason a person might overturn. A claim the
        # guard rejected is not one of those: it was not true, and there is nothing to weigh.
        return NEEDS_REVIEW if self.needs_human else NOT_FOUND

    def evidence_fingerprint(self) -> str:
        """What a human's decision was taken against.

        A decision survives a re-run when this is unchanged and goes back to the queue when
        it is not. It covers the things a reviewer looked at: the contact, the relation, the
        check, and the identity of every accepted source. It deliberately excludes
        timestamps, which change on every run and would send every decision back to the
        queue for nothing.
        """
        parts = [
            self.contact_value.lower(), self.contact_kind, self.publication, self.relation,
            self.check_result, self.site_url.lower(),
        ]
        for f in sorted(self.findings, key=lambda f: (f.kind, f.value, f.final_url)):
            parts += [f.kind, f.value, f.final_url, f.quote]
        return sha256("|".join(parts))

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["findings"] = [f.to_dict() if isinstance(f, Finding) else f for f in self.findings]
        d["summary"] = self.summary()
        d["evidence_fingerprint"] = self.evidence_fingerprint()
        return d
