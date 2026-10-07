# 12. Alert enricher: separating the research run from Telegram

Date: 2026-10-07

## Status

Accepted

## Context

`tidemark run` evaluated Section 1, journaled the result, ran the change
detector, and sent the Telegram alert itself, all in one process. That
was the simplest arrangement while the alert was just the Section 1
facts. ADR 0011's Phase A added market context (BTC and ETH reference
points, relative strength, BTC alignment, position flow, derivatives), and
that context comes from Coinalyze, a network service that can be down.

Two things pull against each other here:

- A WATCH alert is worth more with context, so the alert needs data from
  `market_intel`.
- The research engine must stay untouched by market data, and the
  import-boundary test enforces that. `market_intel` must not import the
  research engine either. If `tidemark run` called `market_intel` to
  enrich its alert, the research run would take a Coinalyze dependency and
  a derivatives outage could delay or suppress a structural evaluation.

## Decision

A third component, the **alert enricher** (`tidemark.enrich`), sits
between the journal and Telegram:

```
Research Engine  ->  journal_entries  ->  Alert Enricher  ->  Telegram
   (run)                (rows)             (alert enrich)
```

Timed in practice as `:02 ingest -> :06 evaluate -> :08 enrich and send`
(see the systemd example in the README). The three steps are separate
processes on separate timers, so none can block another.

**`tidemark run` no longer sends anything.** It evaluates, journals, and
records the change detector's decision on the journal row
(`alert_reason`). It constructs no notifier. A test makes the notifier factory
raise and runs the command, so any send path fails the test.

**The three-way boundary.**
- The research engine (`data/exchange.py`, `context/`, `journal/`,
  `replay/`) never imports `market_intel` or `enrich`. It imports
  nothing from `notify` either.
- `market_intel` never imports the research engine, `enrich`, or
  `notify`. Its one allowed read of research data is still
  `context_read.py`, which reads `journal_entries` only.
- The enricher is the only component that reads both sides. It may import
  `tidemark.data.models` (journal rows), `tidemark.notify.telegram`'s
  Section 1 text helper, and `market_intel`. Nothing imports it except the
  CLI. The import-boundary suite checks all four directions.

**What the enricher decides, and what it does not.** Whether an entry is
alert-worthy is the change detector's decision, read from `alert_reason`.
The enricher never recomputes it. The Section 1 facts in every alert come
from the journal row itself, rendered by the same `notify` helper that
built the alert before this change. The context comes from
`market_intel`'s existing renderer. No second renderer exists.

**The durable cursor.** Two tables in market_intel's own declarative base
(`alert_enrich_checkpoint`, `alert_enrich_sent`), never a research table:

- The checkpoint is the id of the last journal entry the enricher has
  finished with. A run processes entries with a larger id, in id order.
- **The checkpoint advances only as part of a successful outcome.** A quiet
  entry (no alert reason) advances it past itself. A sent alert advances
  it past itself in the same transaction that records the send. A failed
  send never advances it, and the run stops there.
- **Each sent alert is recorded against its journal entry id, unique.**
  A re-run, a restart, or a second invocation finds the record and does
  not send again, even if the checkpoint were somehow behind.
- **First run.** With no checkpoint, the enricher starts at the latest
  journal entry, records it, and sends nothing. The journal is never
  replayed. The output says so.
- **Dry run** (`--dry-run`, also the default) composes what would be sent
  and writes nothing. It doesn't even create the cursor tables, so a dry
  run leaves the database byte-for-byte as it was.

**Why alerts survive a derivatives outage.** The Section 1 facts come from
the journal entry, which is already in the database. They don't depend on
Coinalyze. When Coinalyze is unreachable, rate-limited, unconfigured, or
returns an error, the enricher renders the affected context as
`UNAVAILABLE` with a fixed reason and still sends the alert. The reason is
never the raw exception text. Reference alignment still uses the stored
Section 1 states of BTC and ETH, since those come from the database rather
than Coinalyze. Nothing is ever shown as zero or inferred. A structural
alert is more important than its derivatives context, so the derivatives
side can fail without silencing the structure.

## Consequences

- `tidemark run`'s Telegram behaviour is gone. A structural alert now
  arrives only when the enricher runs. A missed enricher run delays
  alerts, but the cursor means nothing is lost: the next run picks up
  every entry in order.
- **`journal alerts` lists the enricher's sent alerts.** It reads
  `alert_enrich_sent`, joined back to the journal row each alert describes,
  and shows the send time. The join lives in `tidemark.enrich`, not in the
  research store, so the research engine still never imports market_intel.
  Alerts sent by `tidemark run` before this change are not in the sent
  table, so they no longer appear in the listing. Their journal rows still
  carry `alert_sent=True`, and the research store's own `journal_alerts`
  still returns them.
- **Coinalyze cost.** One enrich run shares a single `ReferenceSnapshotCache`
  across its alerts, so BTC and ETH are fetched once per closed period.
  A catch-up of N alerts in one period costs 7 (coin) x N plus 7 (BTC) plus
  7 (ETH), which is 14 + 7N call-units, not 21N. Alerts in different closed
  periods each fetch the references again. An alert is rare, so the cost is
  paid rarely, not on every 4H evaluation.
- Section 1 and Section 2 rules, parameters, and thresholds are unchanged.
  The change detector's logic is unchanged; the enricher sends what it
  already decided.

## Accepted limitation: at-least-once delivery in one narrow window

If Telegram accepts a message and the local write that records it then
fails, the next run sends that alert again. The window is the gap between a
successful `send_text` returning and the transaction that writes the sent
record and advances the checkpoint.

**Why this is accepted rather than closed:**

- Telegram's `sendMessage` has no idempotency key, so the enricher cannot
  ask Telegram whether a message it sent earlier arrived. A two-phase
  outbox (write "sending", send, then mark "sent") would still leave the
  same window open, because the mark-sent write can fail after the send
  succeeds. The only real fix would be a message-side identifier that the
  recipient could dedupe on. That is a larger design, and it is not needed
  to meet the goal here.
- The two failure modes are not equally bad. Losing an alert is worse for a
  human making decisions than seeing one twice. The window is the few
  milliseconds between an HTTP 200 and a local commit, and it needs a
  database write to fail at exactly that moment. A duplicate is visible to
  the reader, while a lost alert is silent.
- If the local database stays unwritable, every later run also fails to
  read or record, so the enricher stops rather than sending repeatedly. The
  repeat risk exists only for the single failed write, not for a persistent
  outage.

Mitigation available if it is ever needed: put the journal entry id in the
message text, so a reader can see a duplicate is the same entry. It is not
added now, because it would change every alert's text for a rare failure.

**Single writer assumption.** Two `alert enrich --send` processes running at
once could each send an entry before either records it. The systemd timer
in the README is a oneshot, which systemd never overlaps with itself. Anyone
running it some other way must keep it to one process.

## Open decisions

1. Whether to put the journal entry id in the message text, so a duplicate
   from the window above can be recognised by a reader (see the accepted
   limitation).
