"""Drafts: a template the owner supplies, filled only with facts that survived the guard.

Nothing here sends anything, and nothing here writes a sentence of its own. The template is
an example, labelled as one, and the owner replaces it with theirs. Every placeholder is
resolved from an accepted finding, and each resolved value carries the source it came from,
so a draft can be read next to the evidence that produced it.

Two refusals, and they are the point of the module:

  * a placeholder with no accepted finding behind it blocks the draft. No default, no
    paraphrase, no "I loved your recent video". A missing fact is a blocked draft and a line
    in the review queue, because the alternative is a plausible sentence about a video nobody
    watched.
  * a contact that a human has not approved blocks the draft as well. The decision column is
    a gate, not a comment.
"""

from __future__ import annotations

import re
from typing import Any

from .model import DEC_APPROVED

PLACEHOLDER = re.compile(r"\{\{([a-z_]+)\}\}")

EXAMPLE_TEMPLATE = """Subject: {{title}} and a question

Hello,

I am writing about {{title}} on YouTube. I found this address on {{contact_source_host}},
which is the professional contact published for the channel.

[The rest of this message is the owner's template. This one is an example shipped with the
workflow so the mechanism can be read and tested.]
"""

BLOCKED_MISSING = "blocked: no accepted evidence for {field}"
BLOCKED_NOT_APPROVED = "blocked: the contact has not been approved by a reviewer"
BLOCKED_NO_CONTACT = "blocked: there is no contact to write to"


def facts_from(record: dict[str, Any]) -> dict[str, dict[str, str]]:
    """The facts a draft may use, each tied to the finding that proved it.

    The dictionary is built from accepted findings only. A field absent from it cannot be
    filled, which is what makes the refusal below mechanical rather than a matter of care.
    """
    facts: dict[str, dict[str, str]] = {}
    for f in record.get("findings", []):
        if f.get("kind") == "channel_identity" and record.get("title"):
            facts["title"] = {"value": record["title"], "source": f.get("final_url", "")}
        if f.get("kind") == "contact_email":
            host = re.sub(r"^https?://(www\.)?([^/]+).*$", r"\2", f.get("final_url", ""))
            facts["contact_source_host"] = {"value": host, "source": f.get("final_url", "")}
    if record.get("title") and "title" not in facts:
        # The title came from somewhere unverified. It is not a usable fact.
        pass
    return facts


def render(record: dict[str, Any], decision: dict[str, Any],
           template: str = EXAMPLE_TEMPLATE) -> dict[str, Any]:
    """Return a draft, or a refusal saying exactly what is missing. Never sends."""
    if not record.get("contact_value"):
        return {"ok": False, "reason": BLOCKED_NO_CONTACT, "template_label": "example template"}
    if (decision or {}).get("decision") != DEC_APPROVED:
        return {"ok": False, "reason": BLOCKED_NOT_APPROVED, "template_label": "example template"}

    facts = facts_from(record)
    needed = sorted(set(PLACEHOLDER.findall(template)))
    missing = [n for n in needed if n not in facts]
    if missing:
        return {"ok": False, "reason": BLOCKED_MISSING.format(field=missing[0]),
                "missing": missing, "template_label": "example template"}

    body = PLACEHOLDER.sub(lambda m: facts[m.group(1)]["value"], template)
    return {
        "ok": True,
        "template_label": "example template",
        "body": body,
        "facts_used": {k: facts[k] for k in needed},
        "to": record["contact_value"],
        "sent": False,
    }
