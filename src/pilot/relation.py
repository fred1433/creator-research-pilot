"""What relates a channel to the person or company a message would go to.

A reciprocal link is a good indication, not a general proof, so this module does not answer
"is this site theirs, yes or no". It answers the question a reviewer actually has:

    what evidence ties this channel to this recipient, for a professional approach of this
    kind?

and it answers it by naming the situation. The situations, and what each one is worth:

  * the channel publishes a professional address directly. The address is published by that
    channel. Whether the mailbox works is a separate question, answered elsewhere.
  * the channel points at a site, and that site points back at the exact channel identifier
    or handle. The association is supported on both sides. It remains to be judged whether
    the address on it is the right professional contact.
  * the channel points at an agency, and the agency's own roster names the channel or the
    creator. That is `representative_confirmed`: a mandate to be approached through a
    representative, which is not the same as the creator's own address, and the brief allows
    it as long as it says which it is.
  * a fan site, or a page that merely embeds a video. Presence, not ownership.
  * a shop, a sponsor, an affiliate link. A commercial relationship, no mandate.
  * links that are old, redirected or contradict each other. Review, with the contradicting
    elements and their dates side by side.

Every reason this module emits is concrete, because "unconfirmed" tells a reviewer nothing
about what to do next. "agency mentioned only by its own site" tells them exactly.
"""

from __future__ import annotations

import re
from typing import Any

from .model import REL_CONFLICTING, REL_CREATOR, REL_REPRESENTATIVE, REL_UNCONFIRMED

# Reasons, written for the person who has to act on them.
R_CHANNEL_PUBLISHED = "the channel publishes this address itself"
R_RECIPROCAL = "the channel links to this site and the site links back to this channel"
R_AGENCY_CONFIRMED = "the agency roster names this channel"
R_AGENCY_SELF_ONLY = "agency mentioned only by its own site: no roster entry naming this channel"
R_NAME_ONLY = "same name, channel not confirmed: the site names the creator but links to no channel"
R_FAN_PAGE = "third party page about the creator, not a property of theirs"
R_COMMERCIAL = "commercial link without a mandate: shop, sponsor or affiliate"
R_CONFLICT = "sources contradict each other about who to contact"
R_NO_SITE = "the channel publishes no site the workflow could read"

FAN_MARKERS = ("fan site", "fansite", "fan page", "unofficial", "tribute", "wiki")
COMMERCIAL_MARKERS = ("affiliate", "use my code", "sponsored by", "discount code",
                      "shop now", "buy now", "add to cart", "store.")
AGENCY_MARKERS = ("management", "talent", "agency", "agents", "booking", "representation",
                  "roster", "artists")
STALE_MARKERS = ("no longer represent", "we no longer work with", "former client",
                 "is no longer managed", "has left")


def _low(*parts: str) -> str:
    return " ".join(p or "" for p in parts).lower()


def links_back(site_body: str, site_text: str, channel_id: str, handle: str) -> str:
    """The fragment of the site that links back to the channel, or an empty string.

    Returned as text rather than as a boolean so it can be quoted, re-downloaded and shown.
    """
    body = (site_body or "")
    low = body.lower()
    needles = []
    if channel_id:
        needles.append(channel_id.lower())
    h = (handle or "").lstrip("@").lower()
    if h:
        needles += [f"youtube.com/@{h}", f"youtube.com/c/{h}", f"youtube.com/user/{h}"]
    for n in needles:
        i = low.find(n)
        if i >= 0:
            return body[max(0, i - 60): i + len(n) + 60].strip()
    return ""


def classify(site_body: str, site_text: str, channel_id: str, handle: str,
             title: str, site_host: str, contact_domain: str = "") -> dict[str, Any]:
    """Name the situation, and say what is missing for it to be stronger."""
    back = links_back(site_body, site_text, channel_id, handle)
    text_low = _low(site_text)

    conflict = next((m for m in STALE_MARKERS if m in text_low), "")
    if conflict:
        return {"relation": REL_CONFLICTING, "reason": R_CONFLICT,
                "detail": f"the page says {conflict!r} near the contact it publishes",
                "backlink_quote": back}

    if any(m in text_low for m in FAN_MARKERS) and not back:
        return {"relation": REL_UNCONFIRMED, "reason": R_FAN_PAGE, "detail": "", "backlink_quote": ""}

    looks_agency = any(m in _low(site_host, site_text[:4000]) for m in AGENCY_MARKERS)

    if back:
        if looks_agency:
            names_channel = bool(title) and title.lower() in text_low
            if names_channel:
                return {"relation": REL_REPRESENTATIVE, "reason": R_AGENCY_CONFIRMED,
                        "detail": "write to a representative, not to the creator directly",
                        "backlink_quote": back}
            return {"relation": REL_UNCONFIRMED, "reason": R_AGENCY_SELF_ONLY,
                    "detail": "", "backlink_quote": back}
        return {"relation": REL_CREATOR, "reason": R_RECIPROCAL,
                "detail": "", "backlink_quote": back}

    if any(m in text_low for m in COMMERCIAL_MARKERS):
        return {"relation": REL_UNCONFIRMED, "reason": R_COMMERCIAL, "detail": "", "backlink_quote": ""}

    if title and title.lower() in text_low:
        return {"relation": REL_UNCONFIRMED, "reason": R_NAME_ONLY, "detail": "", "backlink_quote": ""}

    return {"relation": REL_UNCONFIRMED, "reason": R_NAME_ONLY, "detail": "", "backlink_quote": ""}


def channel_published(address: str) -> dict[str, Any]:
    """The channel typed the address itself. The strongest situation the API can show."""
    return {"relation": REL_CREATOR, "reason": R_CHANNEL_PUBLISHED, "detail": "",
            "backlink_quote": ""}


def no_site() -> dict[str, Any]:
    return {"relation": REL_UNCONFIRMED, "reason": R_NO_SITE, "detail": "", "backlink_quote": ""}
