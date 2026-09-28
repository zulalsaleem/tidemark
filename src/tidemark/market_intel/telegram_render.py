"""Telegram-formatted rendering of a `MarketIntelSnapshot` for /coin.

Deliberately its own renderer, not shared with `cli.py`'s
`_render_market_intel_snapshot` - `cli.py` is the outer wiring layer
that depends on `market_intel`, never the reverse, so importing from it
here would be backwards. This mirrors ADR 0010's own precedent
(`evidence.py` keeping its own copy of session-grouping logic rather
than importing `replay/report.py`'s): the two renderers serve different
audiences (a terminal vs. a chat message with its own footer/disclaimer)
on different cadences, and coupling them would let a change made for one
reason silently change what the other renders.

Presentation only: every number below is formatted (abbreviated,
rounded, or given a compact timestamp) for chat readability, but no
value, period, or label is computed differently than Merge 1 defines it
- this module reads `MarketIntelSnapshot` and never touches the data
layer. Raw data only - no interpretation, no bias, no trading
recommendation. `docs/rulebook/derivatives-context-v0.1.md` is not read
here.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable

from tidemark.market_intel.coin_universe_context import CoinUniverseContext, MetricContext
from tidemark.market_intel.models import MARKET_NOT_FOUND, OK, MarketIntelSnapshot

FOOTER = "Data: Coinalyze (Binance USDT-M perpetuals). Market info only — not a trade signal."

_VOLUME_UNIT_ASSET_NAMES = {"base asset units", "quote asset units"}


def _fmt_usd(value: float) -> str:
    """`$1.04B`, `-$34.6M`, `$177.8K`, `$523.40` - one decimal once a
    suffix's scaled value reaches double digits, two below that, so a
    figure never shows fewer than roughly three significant digits.
    """
    sign = "-" if value < 0 else ""
    magnitude = abs(value)
    if magnitude >= 1_000_000_000:
        scaled, suffix = magnitude / 1_000_000_000, "B"
    elif magnitude >= 1_000_000:
        scaled, suffix = magnitude / 1_000_000, "M"
    elif magnitude >= 1_000:
        scaled, suffix = magnitude / 1_000, "K"
    else:
        return f"{sign}${magnitude:,.2f}"
    decimals = 1 if scaled >= 10 else 2
    return f"{sign}${scaled:,.{decimals}f}{suffix}"


def _fmt_volume(value: float) -> str:
    """Whole-number, comma-grouped for anything sizeable (`597,404`);
    more decimals for a quantity under 1 unit, so a sub-1 BTC/ETH
    reading isn't rounded away to `0`.
    """
    sign = "-" if value < 0 else ""
    magnitude = abs(value)
    if magnitude >= 1000:
        return f"{sign}{magnitude:,.0f}"
    if magnitude >= 1:
        return f"{sign}{magnitude:,.2f}"
    return f"{sign}{magnitude:.4f}"


def _fmt_pct(value: float) -> str:
    return f"{value:.3f}%"


def _fmt_ratio(value: float) -> str:
    return f"{value:.3f}"


def _fmt_instant(instant: dt.datetime, reference: dt.datetime) -> str:
    """`19:56 UTC`, or `2026-09-26 19:56 UTC` if `instant` isn't the
    same UTC day as `reference` (the snapshot's own `generated_at`).
    """
    time_part = instant.strftime("%H:%M")
    if instant.date() != reference.date():
        return f"{instant.strftime('%Y-%m-%d')} {time_part} UTC"
    return f"{time_part} UTC"


def _fmt_range(start: dt.datetime, close: dt.datetime, reference: dt.datetime) -> str:
    """`18:00–19:00 UTC`, or date-prefixed the same way `_fmt_instant` is,
    keyed off the period's close (the more recent boundary).
    """
    span = f"{start.strftime('%H:%M')}–{close.strftime('%H:%M')}"
    if close.date() != reference.date():
        return f"{close.strftime('%Y-%m-%d')} {span} UTC"
    return f"{span} UTC"


def _fmt_metric_line(name: str, metric, reference: dt.datetime) -> str:
    """Open interest / OI change / funding / predicted funding - the
    four metrics whose unit is always exactly "USD" or "%" (see
    service.py: OI-related metrics always pass `convert_to_usd=true`).
    """
    if metric.status != OK:
        return f"{name}: UNAVAILABLE ({metric.status}: {metric.reason})"

    value_str = _fmt_usd(metric.value) if metric.unit == "USD" else _fmt_pct(metric.value)
    if getattr(metric, "is_point_in_time", False):
        return f"{name}: {value_str} (live, updated {_fmt_instant(metric.updated_at, reference)})"
    return (
        f"{name}: {value_str} "
        f"(period {_fmt_range(metric.period_start, metric.period_close, reference)})"
    )


def _asset_name_for_unit(unit: str, symbol: str) -> str:
    """Names the actual asset instead of the generic "base/quote asset
    units" label - "SOL", not "base asset units". `symbol` is the ccxt
    unified form already on the snapshot (e.g. "SOL/USDT:USDT"); the
    denomination itself (which asset it's measured in) is unchanged data
    from Merge 1, only how it's named here changes.
    """
    if unit not in _VOLUME_UNIT_ASSET_NAMES:
        return unit  # "contracts", or an unrecognized unit - shown as-is
    base, _, rest = symbol.partition("/")
    quote = rest.split(":")[0]
    return base if unit == "base asset units" else quote


def _fmt_volume_line(name: str, metric, symbol: str, reference: dt.datetime) -> str:
    if metric.status != OK:
        return f"{name}: UNAVAILABLE ({metric.status}: {metric.reason})"
    asset = _asset_name_for_unit(metric.unit, symbol)
    return (
        f"{name}: {_fmt_volume(metric.value)} {asset} "
        f"(period {_fmt_range(metric.period_start, metric.period_close, reference)})"
    )


def _fmt_long_short_line(metric, reference: dt.datetime) -> str:
    if metric.status != OK:
        return f"Long/short ratio: UNAVAILABLE ({metric.status}: {metric.reason})"
    return (
        f"Long/short ratio: {_fmt_ratio(metric.ratio)} "
        f"(long {_fmt_pct(metric.long_pct)} / short {_fmt_pct(metric.short_pct)}) "
        f"(period {_fmt_range(metric.period_start, metric.period_close, reference)})"
    )


def _fmt_liquidations_line(metric, reference: dt.datetime) -> str:
    if metric.status != OK:
        return f"Liquidations: UNAVAILABLE ({metric.status}: {metric.reason})"
    return (
        f"Liquidations: long={_fmt_usd(metric.long_usd)} / short={_fmt_usd(metric.short_usd)} "
        f"(period {_fmt_range(metric.period_start, metric.period_close, reference)})"
    )


def _fmt_universe_context_lines(
    ctx: MetricContext | None, value_fmt: Callable[[float], str], reference: dt.datetime
) -> str:
    """Three lines beneath a metric's own current-value line - median,
    p75, and when the cache was computed - or "" to omit entirely when
    `ctx` is None (no cache row at all, or this symbol has no current
    value for this particular metric). Age is stated plainly, in whole
    hours, never a judgment word.
    """
    if ctx is None:
        return ""
    as_of = _fmt_instant(ctx.computed_at, reference)
    age_suffix = ""
    if ctx.is_stale:
        age_suffix = f" ({int(ctx.age.total_seconds() // 3600)}h old)"
    return (
        f"\n  Universe median: {value_fmt(ctx.median)}"
        f"\n  Universe p75: {value_fmt(ctx.p75)}"
        f"\n  As of: {as_of}{age_suffix}"
    )


def render_snapshot(
    snapshot: MarketIntelSnapshot,
    universe_context: CoinUniverseContext | None = None,
) -> str:
    """Everything `/coin` replies with for one symbol.

    Grouped OI, then funding, then positioning, then liquidations and
    volume, one blank line between groups - otherwise every label and
    the live-vs-closed-period distinction are exactly Merge 1's.

    `universe_context`, when given, adds a "Universe median"/"Universe
    p75"/"As of" block beneath each of the four metrics the cache
    covers - strictly numbers, no comparison word of any kind. `None`
    (the default) renders exactly as before Phase 2, with no universe
    lines at all - a missing/never-refreshed cache must never change
    what /coin's existing output looks like.
    """
    if snapshot.market_status == MARKET_NOT_FOUND:
        return (
            f"{snapshot.symbol}: not a Binance USDT-M perpetual market "
            f"(MARKET_NOT_FOUND).\n\n{FOOTER}"
        )

    reference = snapshot.generated_at
    uc = universe_context

    oi_pct_line = ""
    if uc is not None and uc.oi_change_pct is not None:
        oi_period = _fmt_range(
            snapshot.open_interest_change.period_start,
            snapshot.open_interest_change.period_close,
            reference,
        )
        oi_pct_line = (
            f"\nOpen interest change (%): {_fmt_pct(uc.oi_change_pct.current)} (period {oi_period})"
            f"{_fmt_universe_context_lines(uc.oi_change_pct, _fmt_pct, reference)}"
        )
    oi_group = (
        "\n".join(
            [
                _fmt_metric_line("Open interest", snapshot.open_interest, reference),
                _fmt_metric_line("Open interest change", snapshot.open_interest_change, reference),
            ]
        )
        + oi_pct_line
    )

    funding_line = _fmt_metric_line("Funding rate", snapshot.funding_rate, reference)
    if uc is not None:
        funding_line += _fmt_universe_context_lines(uc.funding_rate, _fmt_pct, reference)
    funding_group = "\n".join(
        [
            funding_line,
            _fmt_metric_line("Predicted funding rate", snapshot.predicted_funding_rate, reference),
        ]
    )

    positioning_group = _fmt_long_short_line(snapshot.long_short_ratio, reference)
    if uc is not None:
        positioning_group += _fmt_universe_context_lines(uc.long_short_ratio, _fmt_ratio, reference)

    bs_line = ""
    if uc is not None and uc.buy_sell_ratio is not None:
        bs_line = (
            f"\nBuy/sell ratio: {_fmt_ratio(uc.buy_sell_ratio.current)}"
            f"{_fmt_universe_context_lines(uc.buy_sell_ratio, _fmt_ratio, reference)}"
        )
    liquidations_and_volume_group = (
        "\n".join(
            [
                _fmt_liquidations_line(snapshot.liquidations, reference),
                _fmt_volume_line(
                    "Futures volume", snapshot.futures_volume, snapshot.symbol, reference
                ),
                _fmt_volume_line("Buy volume", snapshot.buy_volume, snapshot.symbol, reference),
                _fmt_volume_line("Sell volume", snapshot.sell_volume, snapshot.symbol, reference),
            ]
        )
        + bs_line
    )

    body = "\n\n".join([oi_group, funding_group, positioning_group, liquidations_and_volume_group])
    return f"{snapshot.symbol} ({snapshot.coinalyze_symbol})\n\n{body}\n\n{FOOTER}"


# -- hourly BTC briefing (Merge 3) -------------------------------------------
#
# `structure`/`classification` are duck-typed here deliberately (see
# `briefing.StructureSnapshot`/`derivatives_classifier.ClassificationResult`)
# rather than imported for a type hint - `briefing.py` imports
# `render_briefing` from this module, so importing its dataclass back
# would be circular. Same "reads objects in, plain text out" contract as
# the rest of this file.

BRIEFING_FOOTER = (
    "Sources: Tidemark Section 1 (BTC structure) and Coinalyze (Binance "
    "USDT-M perpetuals, derivatives context). Market info only — not "
    "a trade signal."
)


def _fmt_structure_section(structure, reference: dt.datetime) -> str:
    if not structure.available:
        reason = (
            "stored Section 1 record is stale"
            if structure.stale
            else "no stored Section 1 record for BTC"
        )
        return f"UNAVAILABLE ({reason})"

    grade_part = f", grade {structure.grade}" if structure.grade else ""
    return (
        f"State: {structure.state} / Watch: {structure.watch}{grade_part}\n"
        f"Rule version: {structure.rule_version}\n"
        f"4H candle: {_fmt_instant(structure.evaluated_at, reference)}"
    )


def _fmt_classifier_input_line(name: str, inp, reference: dt.datetime) -> str:
    if inp.status != OK:
        return f"{name}: UNAVAILABLE ({inp.status}: {inp.reason})"
    value = inp.change_pct if hasattr(inp, "change_pct") else inp.value
    return (
        f"{name}: {_fmt_pct(value)} "
        f"(period {_fmt_range(inp.period_start, inp.period_close, reference)})"
    )


def render_briefing(structure, classification, generated_at: dt.datetime) -> str:
    """Everything the hourly BTC briefing sends: two independent
    sections - stored Section 1 structure, then the D1-D6/NO_MATCH
    derivatives classification and the inputs that produced it. No
    trade direction, no bias line - `classification.interpretation` is
    the rulebook's own verbatim text (or nothing at all for NO_MATCH,
    which states only its reason).
    """
    lines = ["BTC STRUCTURE", "", _fmt_structure_section(structure, generated_at)]

    lines.append("")
    lines.append("DERIVATIVES CONTEXT")
    lines.append("")
    if classification.result == "NO_MATCH":
        lines.append(f"NO_MATCH ({classification.reason})")
    else:
        lines.append(f"{classification.result}: {classification.interpretation}")

    lines.append("")
    lines.append(_fmt_classifier_input_line("Price", classification.price, generated_at))
    lines.append(
        _fmt_classifier_input_line("Open interest", classification.open_interest, generated_at)
    )
    lines.append(_fmt_classifier_input_line("Funding", classification.funding, generated_at))

    lines.append("")
    lines.append(f"rulebook: {classification.rulebook_version}")

    lines.append("")
    lines.append(BRIEFING_FOOTER)
    return "\n".join(lines)
