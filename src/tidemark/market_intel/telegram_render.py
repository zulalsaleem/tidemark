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
from tidemark.market_intel.market_context import (
    LONG_WATCH,
    UNAVAILABLE,
    AssetContext,
    DerivedResult,
    MarketContextBundle,
)
from tidemark.market_intel.models import MARKET_NOT_FOUND, OK, MarketIntelSnapshot
from tidemark.market_intel.position_flow_classifier import NO_MATCH, PositionFlowResult

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


def _fmt_position_flow_input_line(name: str, inp, reference: dt.datetime) -> str:
    """Price change / OI change for Layer 2 - an explicit `+`/`-` sign
    always shown (`+0.300%`, `-0.300%`), unlike `_fmt_pct`'s Layer 1
    lines - matching the rulebook's own worked examples exactly.
    """
    if inp.status != OK:
        return f"{name}: UNAVAILABLE ({inp.status}: {inp.reason})"
    return (
        f"{name}: {inp.change_pct:+.3f}% "
        f"(period {_fmt_range(inp.period_start, inp.period_close, reference)})"
    )


def _fmt_bias_line(
    name: str, current: float, threshold: float, labels: tuple[str, str, str]
) -> str:
    """`labels` is (above, below, equal) - used for both long/short ratio
    (vs. 1.0) and buy/sell or liquidation comparisons (vs. 0, i.e.
    `current` already the difference of the two raw values).
    """
    above, below, equal = labels
    if current > threshold:
        return f"{name}: {above}"
    if current < threshold:
        return f"{name}: {below}"
    return f"{name}: {equal}"


def _fmt_supporting_context(snapshot: MarketIntelSnapshot) -> list[str]:
    """Layer 3: funding, long/short ratio, buy/sell flow, and
    liquidations stated as directional facts only - a sign or a
    greater-than/less-than/equal comparison, never a label like
    BULLISH/BEARISH/CROWDED/STRONG/WEAK, which no rulebook here defines.
    """
    lines = []

    funding = snapshot.funding_rate
    if funding.status != OK:
        lines.append("Funding: UNAVAILABLE")
    else:
        lines.append(
            _fmt_bias_line("Funding", funding.value, 0.0, ("POSITIVE", "NEGATIVE", "ZERO"))
        )

    ls = snapshot.long_short_ratio
    if ls.status != OK:
        lines.append("Long/short: UNAVAILABLE")
    else:
        lines.append(
            _fmt_bias_line("Long/short", ls.ratio, 1.0, ("LONG-BIASED", "SHORT-BIASED", "EVEN"))
        )

    buy, sell = snapshot.buy_volume, snapshot.sell_volume
    if buy.status != OK or sell.status != OK:
        lines.append("Buy/sell flow: UNAVAILABLE")
    else:
        lines.append(
            _fmt_bias_line(
                "Buy/sell flow",
                buy.value - sell.value,
                0.0,
                ("BUYING > SELLING", "SELLING > BUYING", "BUYING = SELLING"),
            )
        )

    liq = snapshot.liquidations
    if liq.status != OK:
        lines.append("Liquidations: UNAVAILABLE")
    else:
        lines.append(
            _fmt_bias_line(
                "Liquidations",
                liq.short_usd - liq.long_usd,
                0.0,
                ("SHORT > LONG", "LONG > SHORT", "LONG = SHORT"),
            )
        )

    return lines


def render_snapshot(
    snapshot: MarketIntelSnapshot,
    universe_context: CoinUniverseContext | None = None,
    position_flow: PositionFlowResult | None = None,
    market_context: MarketContextBundle | None = None,
) -> str:
    """Everything `/coin` replies with for one symbol, in three layers.

    Layer 1 (FACTS): grouped OI, then funding, then positioning, then
    liquidations and volume, one blank line between groups - unchanged
    from Merge 1/Phase 2. `universe_context`, when given, adds a
    "Universe median"/"Universe p75"/"As of" block beneath each of the
    four metrics the cache covers - strictly numbers, no comparison word
    of any kind. `None` (the default) renders exactly as before Phase 2.

    Layer 2 (POSITION FLOW): `position_flow`'s nine-state result (or
    NO_MATCH) from docs/rulebook/position-flow-v0.1.md, and the price/OI
    inputs that produced it. `None` (the default) omits this section
    entirely - existing callers/tests render exactly as before.

    Layer 3 (SUPPORTING CONTEXT): funding/long-short/buy-sell/
    liquidations as plain directional facts (sign or greater-than/less-
    than/equal) - never a label like BULLISH/BEARISH/CROWDED. Shown
    only alongside Layer 2, since it exists to accompany the
    classification, not to duplicate Layer 1's own lines.

    MARKET CONTEXT (Phase A): BTC and ETH alongside the coin, relative
    strength, BTC alignment, liquidation imbalance, and WHAT TO WATCH,
    appended only when `market_context` is given. `None` (the default)
    renders exactly as before.
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

    groups = [oi_group, funding_group, positioning_group, liquidations_and_volume_group]

    if position_flow is not None:
        pf = position_flow
        flow_lines = ["POSITION FLOW", ""]
        if pf.result == "NO_MATCH":
            flow_lines.append(f"Position flow: NO_MATCH ({pf.reason})")
        else:
            flow_lines.append(f"Position flow: {pf.result}")
        flow_lines.append(_fmt_position_flow_input_line("Price change", pf.price, reference))
        flow_lines.append(_fmt_position_flow_input_line("OI change", pf.open_interest, reference))
        flow_lines.append(f"Rulebook: {pf.rulebook_version}")
        groups.append("\n".join(flow_lines))

        context_lines = ["SUPPORTING CONTEXT", ""] + _fmt_supporting_context(snapshot)
        groups.append("\n".join(context_lines))

    if market_context is not None:
        groups.append(render_market_context_block(market_context, reference))

    body = "\n\n".join(groups)
    return f"{snapshot.symbol} ({snapshot.coinalyze_symbol})\n\n{body}\n\n{FOOTER}"


# -- market context (Phase A) -------------------------------------------------
#
# Every value below is read off a `MarketContextBundle` that
# `market_context.py` already built. Nothing here compares, classifies, or
# picks a value - it only states what the bundle says, with each value's
# LIVE or CLOSED period tag.

# No 15M/5M mention here: those layers are deferred, and the brief bars
# naming them in any output.
MARKET_CONTEXT_FOOTER = "Market information only. Context only, not a trade signal."


def _closed_tag(metric, reference: dt.datetime) -> str:
    return f"CLOSED 1H {_fmt_range(metric.period_start, metric.period_close, reference)}"


def _live_tag(metric, reference: dt.datetime) -> str:
    return f"LIVE, updated {_fmt_instant(metric.updated_at, reference)}"


def _as_of_tag(metric, reference: dt.datetime) -> str:
    """A point-in-time reading from the reference cache: fetched earlier in
    the same closed period, so it is labelled "as of", never LIVE.
    """
    return f"as of {_fmt_instant(metric.updated_at, reference)}"


def _metric_text(name: str, metric, fmt: Callable[[float], str], tag) -> str:
    if metric is None:
        return f"{name}: UNAVAILABLE"
    if metric.status != OK:
        return f"{name}: UNAVAILABLE ({metric.status}: {metric.reason})"
    return f"{name}: {fmt(metric.value)} ({tag(metric)})"


def _signed_pct(value: float) -> str:
    return f"{value:+.3f}%"


def _fmt_section1_line(asset: AssetContext, reference: dt.datetime) -> str:
    s1 = asset.section1
    if not s1.available:
        return f"Section 1 (4H): UNAVAILABLE ({s1.reason})"
    grade = f" / Grade {s1.grade}" if s1.grade else ""
    window = _fmt_range(s1.window_start, s1.evaluated_at, reference)
    return (
        f"Section 1 (4H): {s1.state} / Watch: {s1.watch}{grade} "
        f"(CLOSED 4H {window}) · rule {s1.rule_version}"
    )


def _fmt_asset_block(
    label: str, asset: AssetContext, reference: dt.datetime, point_in_time_tag=_live_tag
) -> list[str]:
    lines = [label, f"  {_fmt_section1_line(asset, reference)}"]
    if asset.status == UNAVAILABLE:
        lines.append(f"  Coinalyze: UNAVAILABLE ({asset.reason})")
        return lines

    pf = asset.position_flow
    if pf.result == NO_MATCH:
        lines.append(f"  Position flow (1H): NO_MATCH ({pf.reason})")
    else:
        lines.append(f"  Position flow (1H): {pf.result}")

    closed = lambda m: _closed_tag(m, reference)  # noqa: E731 - one-line tag adapter
    lines.append("  " + _metric_text("Price change", asset.price_change, _signed_pct, closed))
    lines.append(
        "  " + _metric_text("OI change (%)", asset.open_interest_change_pct, _signed_pct, closed)
    )
    point_in_time = lambda m: point_in_time_tag(m, reference)  # noqa: E731 - tag adapter
    lines.append("  " + _metric_text("Funding rate", asset.funding_rate, _fmt_pct, point_in_time))

    ls = asset.long_short_ratio
    if ls.status != OK:
        lines.append(f"  Long/short ratio: UNAVAILABLE ({ls.status}: {ls.reason})")
    else:
        lines.append(f"  Long/short ratio: {_fmt_ratio(ls.ratio)} ({closed(ls)})")

    liq = asset.liquidations
    if liq.status != OK:
        lines.append(f"  Liquidations: UNAVAILABLE ({liq.status}: {liq.reason})")
    else:
        lines.append(
            f"  Liquidations: long={_fmt_usd(liq.long_usd)} / short={_fmt_usd(liq.short_usd)} "
            f"({closed(liq)})"
        )
    return lines


def _fmt_relative_strength(bundle: MarketContextBundle, reference: dt.datetime) -> str:
    rs = bundle.derived.relative_strength
    if rs.reason is not None:
        return f"Relative strength vs BTC: {rs.label} ({rs.reason})"
    coin_pc = bundle.coin.price_change
    btc_pc = bundle.market.btc.price_change
    return (
        f"Relative strength vs BTC: {rs.label} "
        f"(coin {_signed_pct(coin_pc.value)} vs BTC {_signed_pct(btc_pc.value)}, "
        f"{_closed_tag(coin_pc, reference)})"
    )


def _fmt_alignment_line(result: DerivedResult) -> str:
    if result.reason is None:
        return f"BTC alignment: {result.label}"
    return f"BTC alignment: {result.label} ({result.reason})"


def _fmt_liquidation_imbalance_line(bundle: MarketContextBundle, reference: dt.datetime) -> str:
    result = bundle.derived.liquidation_imbalance
    if result.label == UNAVAILABLE:
        return f"Liquidation imbalance: UNAVAILABLE ({result.reason})"
    return (
        f"Liquidation imbalance: {result.label} "
        f"({_closed_tag(bundle.coin.liquidations, reference)})"
    )


def _fmt_watch_lines(bundle: MarketContextBundle) -> list[str]:
    """Summarises the layers above. Names only what Section 1 stored and
    the alignment label, and says 1H behaviour at that level is what to
    observe. Introduces no rule: no sweeps, reclaims, 15M or 5M layers,
    no entries.
    """
    watch = bundle.watch
    s1 = bundle.coin.section1
    if not s1.available:
        return [f"Section 1 (4H) UNAVAILABLE ({s1.reason}). No 4H WATCH to summarise."]
    if not watch.active:
        return [
            f"No Section 1 WATCH is active for {bundle.coin.symbol} "
            f"(state {s1.state}, watch {s1.watch}). Nothing to summarise at 1H."
        ]
    if watch.level_price is not None:
        level = f"stored level {watch.level_price:,.2f} ({watch.level_touches} touches)"
    else:
        level = "no held level named in the stored record"
    grade = f" (grade {watch.grade})" if watch.grade else ""
    observe = (
        "Observe 1H behaviour at that level."
        if watch.level_price is not None
        else "Observe 1H behaviour under this WATCH."
    )
    return [
        f"Section 1 {s1.watch}{grade}, {level}.",
        f"BTC alignment: {watch.btc_alignment}.",
        observe,
    ]


def render_market_context_block(bundle: MarketContextBundle, reference: dt.datetime) -> str:
    """The MARKET CONTEXT section for `/coin` and `intel market`."""
    parts = [
        "MARKET CONTEXT",
        "\n".join(_fmt_asset_block(f"COIN ({bundle.coin.symbol})", bundle.coin, reference)),
        # BTC and ETH may come from the reference cache, fetched earlier in this
        # closed period, so their point-in-time values are "as of", not LIVE.
        "\n".join(
            _fmt_asset_block("BTC (primary reference)", bundle.market.btc, reference, _as_of_tag)
        ),
        "\n".join(
            _fmt_asset_block("ETH (secondary reference)", bundle.market.eth, reference, _as_of_tag)
        ),
        "\n".join(
            [
                _fmt_relative_strength(bundle, reference),
                _fmt_alignment_line(bundle.derived.btc_alignment),
                _fmt_liquidation_imbalance_line(bundle, reference),
            ]
        ),
        "WHAT TO WATCH\n" + "\n".join(_fmt_watch_lines(bundle)),
    ]
    return "\n\n".join(parts)


def render_watch_alert(bundle: MarketContextBundle, reference: dt.datetime) -> str | None:
    """The Section 1 WATCH alert in the Phase A format: 4H structure, market
    context, position flow, key derivatives, WHAT TO WATCH, rulebook
    versions, and the context-only disclaimer. `None` when no WATCH is
    active, since no alert exists to send. Concise by design: /coin carries
    the full detail.
    """
    if not bundle.watch.active:
        return None
    coin = bundle.coin
    s1 = coin.section1
    emoji = "\U0001f7e2" if s1.watch == LONG_WATCH else "\U0001f534"
    direction = "LONG WATCH" if s1.watch == LONG_WATCH else "SHORT WATCH"
    grade = f" (Grade {s1.grade})" if s1.grade else ""
    display = coin.symbol.split(":")[0]

    pf = coin.position_flow
    if pf.result == NO_MATCH:
        flow_line = f"NO_MATCH ({pf.reason})"
    else:
        flow_line = (
            f"{pf.result} · price {_signed_pct(pf.price.change_pct)} · "
            f"OI change {_signed_pct(pf.open_interest.change_pct)}"
        )

    def short_section1(asset: AssetContext) -> str:
        if not asset.section1.available:
            return f"UNAVAILABLE ({asset.section1.reason})"
        return f"{asset.section1.state} ({asset.section1.watch})"

    funding_line = _metric_text(
        "Funding", coin.funding_rate, _fmt_pct, lambda m: _live_tag(m, reference)
    )

    ls = coin.long_short_ratio
    if ls.status == OK:
        ls_line = f"Long/short ratio: {_fmt_ratio(ls.ratio)} ({_closed_tag(ls, reference)})"
    else:
        ls_line = f"Long/short ratio: UNAVAILABLE ({ls.reason})"

    lines = [
        f"{emoji} {direction} — {display}{grade}",
        "",
        f"4H structure: {s1.state} · CLOSED 4H "
        f"{_fmt_range(s1.window_start, s1.evaluated_at, reference)}",
        "",
        "MARKET CONTEXT",
        f"BTC 4H: {short_section1(bundle.market.btc)}",
        f"ETH 4H: {short_section1(bundle.market.eth)}",
        _fmt_relative_strength(bundle, reference),
        _fmt_alignment_line(bundle.derived.btc_alignment),
        _fmt_liquidation_imbalance_line(bundle, reference),
        "",
        f"POSITION FLOW ({_closed_tag(coin.price_change, reference)})",
        flow_line,
        "",
        "KEY DERIVATIVES",
        funding_line,
        ls_line,
        "",
        "WHAT TO WATCH",
        *_fmt_watch_lines(bundle),
        "",
        "Rulebooks: " + ", ".join(bundle.rulebook_versions),
        MARKET_CONTEXT_FOOTER,
    ]
    return "\n".join(lines)


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
