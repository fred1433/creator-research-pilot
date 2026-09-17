# creator research pilot

A first runnable version of a creator research workflow: from a list of YouTube channel URLs
to a review sheet where every line carries the page it came from, the words it was read in
and the date it was read, and where "not found" is a result with the same standing as any
other.

It is a workflow, not a contact list and not a research service. Nobody is contacted by it.
The sample it ships with is a set of controlled test cases, executed by the workflow itself,
on domains reserved for documentation that resolve for nobody.

The one idea it is built around: **the model proposes and the code falsifies.** Anything
proposed, whether collected by this workflow, typed by a person or produced by an assistant,
carries a source URL and a literal quote. Before it can reach the sheet, the source is
downloaded again and the quote has to still be there. That single check is the product, and
`docs/control-report.md` shows it working, failing to work when it is removed, and accepting
a correctly sourced finding rather than refusing everything.

Page: <https://raffall-pilot.theaipipe.com>

---

## Install from a clean machine

Python 3.11 or newer, nothing else. No account, no service, no key needed for the replay
mode.

```bash
git clone https://github.com/fred1433/creator-research-pilot.git
cd creator-research-pilot
python3 -m venv .venv && . .venv/bin/activate
pip install -e . && pip install pytest
```

## Two modes, one code path

**Replay.** Runs the whole workflow against controlled sources on disk. No network, no quota,
no credit, no creator reached. This is what the tests and continuous integration use.

```bash
python -m pilot.run replay --input fixtures/controlled_cases.json --out examples/replay-output
```

**Real execution.** Same collection, same guard, same relation policy, same sheet, same
drafts, reading the public web.

```bash
export GOOGLE_API_KEY=...            # a key with access to the YouTube Data API
export MILLIONVERIFIER_API_KEY=...   # only needed with --live-check
python -m pilot.run run \
  --input my_channels.json \
  --out out/ \
  --private-out ../evidence/ \
  --live-check --delay 2.5
```

The input is a JSON list. `url` is the only required field:

```json
[{"url": "https://www.youtube.com/@achannel",
  "site": "https://asite.example",
  "note": "site supplied by hand, counted and labelled",
  "proposed": [{"value": "hello@asite.example",
                "source_url": "https://asite.example/contact",
                "quote": "For enquiries, write to hello@asite.example"}]}]
```

`site` and `proposed` are optional. Anything a human supplies is stored with
`origin: manual_input` or `origin: proposed` and counted in the run summary, so a hand-fed URL
can never be read as a collected one, and a proposal from an assistant goes through exactly
the same guard as everything else.

Outputs, in `--out`:

| file | what it is |
|---|---|
| `review.csv` | the review sheet, calculated columns and decision columns kept apart |
| `results.json` | the full record of every row, with its findings, its refusals and their reasons |
| `pilot.sqlite3` | channels, contacts, jobs and decisions |

With `--private-out`, the unmasked copy is written there and only there.

## The review loop

The sheet is a round trip. Export it, let a reviewer fill in the `d_` columns, read it back:

```bash
python -m pilot.run export           --db out/pilot.sqlite3 --out out/
python -m pilot.run import-decisions --db out/pilot.sqlite3 --csv out/review.csv
python -m pilot.run decide --db out/pilot.sqlite3 --channel UC... \
       --decision approved --reason "opened the source page" --by "name"
```

Then run the same list again. Every `c_` column is recomputed. A decision is kept when the
evidence behind it is unchanged, and returns to the queue, with the previous answer still
visible, when it is not. That is enforced by the schema rather than by care: the collection
path has no write access to the decisions table.

A Google Sheets adapter would write these same columns into a spreadsheet, the calculated
block protected and the decision block open. It is declared **not connected** here, because
connecting it means creating credentials in an account, and the account belongs to the owner
of the data. The CSV round trip carries the same semantics and is what the tests exercise.

## What a row says, in four separate answers

One word cannot carry four questions, so a row does not have one status. It has four, and
the three word summary at the front is a view over them, never a stored fact.

| field | values | question |
|---|---|---|
| `publication` | `found`, `not_found`, `access_blocked` | was a professional contact published, within the perimeter actually consulted |
| `relation` | `creator_confirmed`, `representative_confirmed`, `unconfirmed`, `conflicting` | what ties this channel to that recipient |
| `check_result` | the verification vendor's own word, or `not_checked` | what the mailbox check returned |
| `d_decision` | `pending`, `approved`, `rejected` | what a person decided |

`not_found` means nothing was found in the perimeter that was actually consulted. It never
means the creator has no address. `access_blocked` is a third answer on purpose, because a
step that could not run and a search that finished empty are not the same thing, and merging
them is how a workflow reports a limit as a fact about a person.

The relation is named by situation rather than scored, because "unconfirmed" tells a
reviewer nothing they can act on. A site that links back to the exact channel, an agency
whose roster names the channel, a fan page, a shop, a page that contradicts itself: each one
gets its own sentence in the sheet. A representative is a legitimate result and is labelled
as one, since a person writes to an agent differently than to a creator.

## What it refuses to do

* **It does not read YouTube's own pages.** The documented API is the only route. Without a
  key, or with a key that has no access to it, the row says so in the API's own words and
  carries `access_blocked`. The addresses behind YouTube's own access mechanism are excluded
  from automated collection. No captcha is bypassed. The limits encountered stay visible in
  the review.
* **It does not take an address from a page that has nothing to do with the channel.** A fan
  site, a press write-up, a link aggregator: each refusal is recorded by name, with its
  reason, so the reviewer can overturn one if they know better.
* **It does not call `catch_all` valid.** A catch-all domain accepts every address and
  decides later, so the check concluded nothing, and the sheet says exactly that.
* **It does not replace a published address with a suggested correction.** The suggestion is
  carried beside it, for a person.
* **It does not present an answer that cost nothing as a check.** `c_check_livemode` says
  whether a credit was really spent. An exhausted quota gives `not_checked`.
* **It does not obey a page it collected.** Collected pages are untrusted input: they are
  matched against patterns, never followed as instructions, and a page in the fixtures tries.
* **It does not follow a link into a private network.** Every URL is resolved and checked
  before the request leaves.
* **It does not send anything.** Drafts are produced, blocked when a fact has no accepted
  evidence behind it or when no human has approved the row, and never sent.

## The controls

```bash
python -m pytest tests -q
python scripts/control_report.py     # rewrites docs/control-report.md
```

`docs/control-report.md` carries the commit, the command, the test identifier, what was
expected and what was observed, for three things:

1. a **positive control**: a correctly sourced finding proposed from outside is accepted,
   reaches the sheet and produces a draft once approved. A guard that refuses everything is
   not a guard.
2. an **injected case**, through the same production path: a finding citing a real page and a
   sentence that is not on it is rejected, reaches neither the sheet nor a draft, and leaves
   the row's status unchanged. Deliberately injected; the rejection is executed by the
   workflow.
3. a **causal mutation**: the same input with the guard neutralised and nothing else changed.
   The assertion of control 2 then fails, which is what makes the guard the cause of the
   outcome rather than a step sitting next to it.

The suite also covers the human loop end to end: a decision survives a re-run, changed
evidence sends it back to the queue, an interrupted run resumes without duplicating a row or
losing a decision, one agency representing two channels stays one contact, and the same
channel written three ways stays one row.

## Nothing about a real person is in this repository

A real run writes its full output to `--private-out`, outside this tree. What is committed
here is fictional: the `.test` domains in `fixtures/` are reserved by RFC 2606 and resolve
for nobody. Masking happens where the shareable file is produced, not in a display component,
so a file cannot be shared before something had a chance to hide it.

`tests/test_no_real_people.py` walks every tracked file **and the whole git history** and
fails on any address that is neither masked nor on a reserved domain. Continuous integration
checks out the full history for exactly that reason: an address removed by a later commit is
still published by a public repository.

## What a run consumes

Measured on the controlled sample and on one real run of six channels, on 17 September 2026,
one request at a time with a pause of 2.5 seconds: two verification credits spent, one row
`verified`, five to review, of which four where the channel step could not run at all because
the key available to that run had no access to the YouTube Data API. No page of YouTube was
read instead, and the six site URLs were supplied by hand and counted as `manual_input`. That
run stays in a private evidence folder: see the last section but one.

| | |
|---|---|
| Documented API | one unit per channel, `channels.list`, once per run |
| Pages read | the site, then at most two pages a human would click to find a contact. No crawling |
| Rate | one request at a time, a pause between them, `robots.txt` read once per host and obeyed |
| Verification | one credit per new address, at most, and never the same mailbox twice in a run |
| Hosting | none. The workflow runs on one machine and writes files |
| Manual steps | counted in `manual_inputs` in the run report, and labelled row by row |

## Handover

* **The store.** SQLite so that one person on one machine can run this with nothing
  installed. Postgres takes over by replacing `src/pilot/store.py`: the schema is four tables
  and the rest of the code speaks to them through that one module.
* **The templates.** `src/pilot/drafts.py` ships an example template, labelled as an example.
  Replace it with yours. Every placeholder must resolve to a fact backed by an accepted
  finding, or the draft is blocked rather than filled with something plausible.
* **The targeting criteria.** They are not in here, and that is deliberate: which creators are
  worth approaching is the owner's judgement, and this workflow is the part that establishes
  whether a contact is real and whose it is.
* **Companies rather than creators.** The channel step is the only part that is specific to
  YouTube. The rest, establishing that a site belongs to whoever you think it belongs to,
  reading a published contact, proving it with a quote and routing what is doubtful to a
  person, applies unchanged to a company domain, with a company register standing where the
  channel identity stands today.
* **Nothing here depends on the author.** No private service, no personal account, no hosted
  component. Two environment variables, both the owner's.
