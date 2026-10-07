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
- **The `journal alerts` command shows nothing for new alerts.** It filters
  on `journal_entries.alert_sent`, which the enricher cannot write (it may
  not write research tables), and `run` now always leaves `alert_sent`
  False. The sent history lives in `alert_enrich_sent` instead, and each
  send run reports every alert it delivers. `journal alerts` should be
  rebased onto that table in a follow-up, not patched here.
- Coinalyze cost. Each alerted entry costs one snapshot for the coin plus
  one for each of BTC and ETH, 21 call-units, because the enricher does
  not cache across alerts. The cost is paid only for alerts, which are
  rare, not for every 4H evaluation. Several alerts in one missed-run
  catch-up each cost 21, which the client's own rate-limit handling
  absorbs.
- **At-least-once in one narrow window.** If Telegram accepts a message
  and the local record write then fails, the next run will send it again.
  The window is the gap between a successful `send_text` and the
  transaction that records it. Closing it needs a two-phase outbox, which
  this merge doesn't build. The enricher is designed to run as a single
  scheduled process, so two invocations never overlap.
- **Single writer assumption.** Two `alert enrich --send` processes running
  at once could each send an entry before either records it. The systemd
  timer in the README is a oneshot, which systemd never overlaps with
  itself. Anyone running it some other way must keep it to one process.
- Section 1 and Section 2 rules, parameters, and thresholds are unchanged.
  The change detector's logic is unchanged; the enricher sends what it
  already decided.

## Open decisions

1. Rebase `journal alerts` onto `alert_enrich_sent` (see Consequences).
2. Whether the enricher should cache BTC and ETH context across the alerts
   of a single catch-up run, which would drop the 21-unit cost to 7 per
   alert after the first. ADR 0011's period cache is the obvious shape, but
   a catch-up run is rare, so it is not built here.
3. Whether a send-then-record outbox is worth its complexity, given the
   narrow window described above.
