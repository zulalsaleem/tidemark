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

Raw data only - no interpretation, no bias, no trading recommendation.
`docs/rulebook/derivatives-context-v0.1.md` is not read here.
"""

from __future__ import annotations

from tidemark.market_intel.models import MARKET_NOT_FOUND, OK, MarketIntelSnapshot

FOOTER = "Data: Coinalyze (Binance USDT-M perpetuals). Market info only — not a trade signal."


def _fmt_metric_line(name: str, metric) -> str:
    if metric.status != OK:
        return f"{name}: UNAVAILABLE ({metric.status}: {metric.reason})"
    if getattr(metric, "is_point_in_time", False):
        return (
            f"{name}: {metric.value} {metric.unit} (live, updated {metric.updated_at.isoformat()})"
        )
    return (
        f"{name}: {metric.value} {metric.unit} "
        f"(period {metric.period_start.isoformat()} to {metric.period_close.isoformat()})"
    )


def render_snapshot(snapshot: MarketIntelSnapshot) -> str:
    """Everything `/coin` replies with for one symbol."""
    if snapshot.market_status == MARKET_NOT_FOUND:
        return (
            f"{snapshot.symbol}: not a Binance USDT-M perpetual market "
            f"(MARKET_NOT_FOUND).\n\n{FOOTER}"
        )

    lines = [f"{snapshot.symbol} ({snapshot.coinalyze_symbol})", ""]
    lines.append(_fmt_metric_line("Open interest", snapshot.open_interest))
    lines.append(_fmt_metric_line("Open interest change", snapshot.open_interest_change))
    lines.append(_fmt_metric_line("Funding rate", snapshot.funding_rate))
    lines.append(_fmt_metric_line("Predicted funding rate", snapshot.predicted_funding_rate))

    ls = snapshot.long_short_ratio
    if ls.status != OK:
        lines.append(f"Long/short ratio: UNAVAILABLE ({ls.status}: {ls.reason})")
    else:
        lines.append(
            f"Long/short ratio: {ls.ratio} "
            f"(long {ls.long_pct}{ls.percent_unit} / short {ls.short_pct}{ls.percent_unit}) "
            f"(period {ls.period_start.isoformat()} to {ls.period_close.isoformat()})"
        )

    liq = snapshot.liquidations
    if liq.status != OK:
        lines.append(f"Liquidations: UNAVAILABLE ({liq.status}: {liq.reason})")
    else:
        lines.append(
            f"Liquidations: long={liq.long_usd} {liq.unit} / short={liq.short_usd} {liq.unit} "
            f"(period {liq.period_start.isoformat()} to {liq.period_close.isoformat()})"
        )

    lines.append(_fmt_metric_line("Futures volume", snapshot.futures_volume))
    lines.append(_fmt_metric_line("Buy volume", snapshot.buy_volume))
    lines.append(_fmt_metric_line("Sell volume", snapshot.sell_volume))
    lines.append("")
    lines.append(FOOTER)
    return "\n".join(lines)
