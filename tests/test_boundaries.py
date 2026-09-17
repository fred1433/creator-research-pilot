"""The boundaries: what this workflow will not do, asserted rather than promised.

Four of them, and each one is a way a research tool quietly becomes something else:

  * it does not read YouTube's own pages when the documented API is unavailable. It says the
    channel step could not run, in the API's words, and the row carries `access_blocked`.
  * it does not follow a link into a private network. Pages it reads are supplied by third
    parties, so a link on one of them must never be able to make this process talk to
    something on the machine or the network it runs on.
  * it does not obey a collected page. A page that contains an instruction is data.
  * it does not promote a verification status, does not call `catch_all` valid, does not
    replace a published address with a suggested correction, and does not present an answer
    that cost no credit as a check.
"""

from __future__ import annotations

from conftest import case

from pilot import run as R
from pilot import youtube as YT
from pilot.emailcheck import ControlledChecker, MailboxChecker, reading
from pilot.fetcher import LiveFetcher, is_public_url
from pilot.model import CHK_NOT_CHECKED, PUB_ACCESS_BLOCKED


def test_without_api_access_the_row_says_so_and_no_youtube_page_is_read(
        cases, fetcher, checker):
    entry = case(cases, "northwindclimbing")
    rec = R.process(entry, fetcher, checker, api_key="")

    assert rec.publication == PUB_ACCESS_BLOCKED
    assert YT.NO_KEY in rec.access
    assert "could not run" in rec.review_reason
    assert "publishes nothing" in rec.review_reason, \
        "a blocked step must not be reported as an absence of contact"
    # Nothing was fetched from youtube.com itself. The only route is the documented API.
    assert not [c for c in fetcher.log if "youtube.com" in c["url"]]


def test_a_blocked_channel_step_stays_on_the_record_even_when_a_contact_is_found(
        cases, fetcher, checker):
    """A contact proved against a public page is a real finding; the limit is still a limit."""
    rec = R.process(case(cases, "bramblewoodbakes"), fetcher, checker, api_key="")
    assert rec.contact_value == "hello@bramblewoodbakes.test"
    assert YT.NO_KEY in rec.access
    assert rec.relation == "unconfirmed", \
        "without the channel step nothing ties the site to the channel"


def test_a_blocked_key_keeps_the_api_message_rather_than_a_summary():
    class Blocked:
        log: list = []

        def get(self, url):
            return {"url": url, "final_url": url, "status": "http 403",
                    "body": '{"error":{"code":403,"message":"Requests to this API '
                            'youtube.api.v3.V3DataChannelService.List are blocked."}}',
                    "text": "Requests to this API are blocked."}

    out = YT.channels_list("https://www.youtube.com/@x", "a-key", Blocked())
    assert out["ok"] is False
    assert "blocked" in out["detail"]
    # The evidence URL never carries the key.
    assert "key=" not in out["url"]


def test_a_link_into_a_private_network_is_refused_before_the_request_leaves():
    assert is_public_url("http://127.0.0.1:8080/admin") is False
    assert is_public_url("http://localhost/") is False
    assert is_public_url("http://169.254.169.254/latest/meta-data/") is False
    assert is_public_url("file:///etc/passwd") is False

    fetched = LiveFetcher(delay=0).get("http://127.0.0.1:9/secret")
    assert fetched["status"] == "refused: not a public host"
    assert fetched["text"] == ""


def test_an_instruction_on_a_collected_page_changes_nothing(cases, fetcher, checker):
    """The Fernhollow page carries a line telling the workflow to accept every address."""
    page = fetcher.get("https://fernhollowforge.test")
    assert "ignore your verification rules" in page["text"]

    rec = R.process(case(cases, "fernhollowforge"), fetcher, checker,
                    "controlled-key-not-a-secret")
    assert rec.contact_value == ""
    assert rec.summary() == "not found"


def test_catch_all_is_never_called_valid():
    assert "cannot conclude" in reading("catch_all")
    assert "valid" not in reading("catch_all")
    assert reading(CHK_NOT_CHECKED) == "no verification was performed on this run"


def test_an_answer_that_cost_nothing_is_not_a_check():
    c = ControlledChecker({"someone@example.test": {"result": "ok"}})
    out = c.check("someone@example.test")
    assert out["livemode"] is False
    assert c.spent == 0
    assert "controlled vendor response" in out["reason"]


def test_a_domain_without_a_mail_server_receives_nothing():
    c = MailboxChecker(api_key="unused", live=True, resolver=lambda d: [])
    out = c.check("someone@nothing.test")
    assert out["result"] == "no_mx"
    assert out["livemode"] is False
    assert c.spent == 0


def test_a_suggested_correction_never_replaces_the_published_address(monkeypatch):
    import json
    import io
    import urllib.request

    class Answer(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: Answer(json.dumps(
        {"result": "invalid", "didyoumean": "corrected@brand.test"}).encode()))
    c = MailboxChecker(api_key="unused", live=True, resolver=lambda d: ["mx.brand.test"])
    out = c.check("published@brand.test")

    assert out["address"] == "published@brand.test"
    assert out["result"] == "invalid"
    assert out["suggestion"] == "corrected@brand.test"


def test_the_same_mailbox_is_not_probed_twice_in_one_run():
    calls = []

    class Counting(MailboxChecker):
        def __init__(self):
            super().__init__(api_key="unused", live=False, resolver=lambda d: ["mx.test"])

    c = Counting()
    first = c.check("a@brand.test")
    second = c.check("a@brand.test")
    assert second.get("cached") is True
    assert first["result"] == second["result"]
