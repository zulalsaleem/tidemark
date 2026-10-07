"""Unified market context for `/coin`, `intel market`, and the WATCH alert:
BTC and ETH as market reference points, the requested symbol as the coin,
and the derived comparisons between them. See docs/adr/0011 (Phase A
addendum).

Every calculation lives here. `telegram_render.py` only formats what this
module returns - it never compares, classifies, or chooses a value.

Rules this module follows:
- Section 1 is READ, never recomputed. `context_read.read_section1` is the
  only source; a missing or stale record becomes UNAVAILABLE, never a
  fallback computation.
- Relative strength, alignment, and liquidation imbalance are direct
  comparisons of already-fetched values. No thresholds are invented here.
- Missing data yields NO_MATCH or UNAVAILABLE with a reason - never zero,
  never an inference from whatever fields remain.
- Only `context_read.py` touches the research database. This module
  imports nothing from `tidemark.data`.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from tidemark.market_intel.clamping import closed_period
from tidemark.market_intel.client import CoinalyzeClient
from tidemark.market_intel.context_read import Section1Read, read_section1
from tidemark.market_intel.errors import (
    CoinalyzeConnectionError,
    CoinalyzeHttpError,
    RateLimitedError,
)
from tidemark.market_intel.future_markets import FutureMarketsCache
from tidemark.market_intel.models import (
    OK,
    ClosedPeriodMetric,
    LiquidationsMetric,
    LongShortRatioMetric,
    MarketIntelSnapshot,
    PointInTimeMetric,
)
from tidemark.market_intel.position_flow import classify_snapshot
from tidemark.market_intel.position_flow_classifier import RULEBOOK_VERSION, PositionFlowResult
from tidemark.market_intel.service import DEFAULT_INTERVAL, fetch_market_intel

BTC_SYMBOL = "BTC/USDT:USDT"
ETH_SYMBOL = "ETH/USDT:USDT"
REFERENCE_SYMBOLS = (BTC_SYMBOL, ETH_SYMBOL)

# Section 1 state and watch names, exactly as section-01-htf-context-v1.1.md
# writes them. Literal strings rather than an import from `context.htf`,
# which the market_intel boundary forbids.
BULLISH = "BULLISH"
BEARISH = "BEARISH"
LONG_WATCH = "LONG_WATCH"
SHORT_WATCH = "SHORT_WATCH"

UP = "UP"
DOWN = "DOWN"
_DIRECTION_BY_STATE = {BULLISH: UP, BEARISH: DOWN}

# Asset-level status. OK / MARKET_NOT_FOUND come straight from the snapshot;
# UNAVAILABLE means the Coinalyze fetch for that asset did not complete.
UNAVAILABLE = "UNAVAILABLE"

# Derived labels, exactly the strings the brief specifies.
RELATIVE_STRONGER = "COIN STRONGER THAN BTC"
RELATIVE_WEAKER = "COIN WEAKER THAN BTC"
RELATIVE_IN_LINE = "IN LINE WITH BTC"
ALIGNED = "ALIGNED"
AGAINST_BTC = "AGAINST BTC"
NO_DIRECTIONAL_ALIGNMENT = "NO_DIRECTIONAL_ALIGNMENT"
LONG_LIQUIDATIONS_GREATER = "LONG LIQUIDATIONS > SHORT"
SHORT_LIQUIDATIONS_GREATER = "SHORT LIQUIDATIONS > LONG"
LIQUIDATIONS_BALANCED = "LIQUIDATIONS BALANCED"
NO_MATCH = "NO_MATCH"

_BTC_SELF_REASON = "the requested symbol is the BTC reference itself"


@dataclass(frozen=True)
class DerivedResult:
    """One derived comparison: its label, and the reason whenever the label
    is NO_MATCH, UNAVAILABLE, or NO_DIRECTIONAL_ALIGNMENT.
    """

    label: str
    reason: str | None = None


@dataclass(frozen=True)
class AssetContext:
    """One asset's fields - used for the requested coin (CoinContext) and
    for BTC/ETH (MarketContext). Every metric is the snapshot's own value
    with its own status, period, or live timestamp; nothing is re-derived
    here except `position_flow`, which is the unchanged position-flow-v0.1
    classification of that asset's own closed 1H reading.

    All metric fields are `None` only when `status` is UNAVAILABLE (the
    fetch did not complete at all). Otherwise each metric carries its own
    OK / NO_DATA / MARKET_NOT_FOUND status.
    """

    symbol: str
    status: str
    reason: str | None
    section1: Section1Read
    price_change: ClosedPeriodMetric | None
    open_interest_change_pct: ClosedPeriodMetric | None
    position_flow: PositionFlowResult | None
    funding_rate: PointInTimeMetric | None
    long_short_ratio: LongShortRatioMetric | None
    liquidations: LiquidationsMetric | None


@dataclass(frozen=True)
class MarketContext:
    """BTC (primary) and ETH (secondary) reference assets, same fields,
    same logic - no ETH-specific handling anywhere.
    """

    btc: AssetContext
    eth: AssetContext


@dataclass(frozen=True)
class Derived:
    relative_strength: DerivedResult
    btc_alignment: DerivedResult
    liquidation_imbalance: DerivedResult


@dataclass(frozen=True)
class WatchSummary:
    """What WHAT TO WATCH may say: a Section 1 WATCH's direction and grade,
    the stored level it names (only when one exists in the stored record),
    and BTC alignment. Summarises the layers above; introduces nothing new.
    """

    active: bool
    watch: str | None
    grade: str | None
    level_price: float | None
    level_touches: int | None
    btc_alignment: str


@dataclass(frozen=True)
class MarketContextBundle:
    coin: AssetContext
    market: MarketContext
    derived: Derived
    watch: WatchSummary

    @property
    def rulebook_versions(self) -> tuple[str, ...]:
        """Every rulebook version a rendered message used, in display order:
        each available Section 1 record's own version, then position-flow.
        """
        assets = (self.coin, self.market.btc, self.market.eth)
        versions: list[str] = []
        for asset in assets:
            version = asset.section1.rule_version
            if asset.section1.available and version not in versions:
                versions.append(version)
        if any(asset.position_flow is not None for asset in assets):
            versions.append(RULEBOOK_VERSION)
        return tuple(versions)


# -- Section 1 ---------------------------------------------------------------


def section1_direction(section1: Section1Read) -> str | None:
    """UP for a BULLISH stored state, DOWN for BEARISH, otherwise None.

    NEUTRAL, INSUFFICIENT_STRUCTURE, STRUCTURE_BROKEN_*, and an unavailable
    record all give None: none of those is a directional bull/bear state
    this merge can align against. Mapping STRUCTURE_BROKEN_* conservatively
    to None is a decision to confirm, not a rulebook reading.
    """
    if not section1.available:
        return None
    return _DIRECTION_BY_STATE.get(section1.state)


def _watched_level(section1: Section1Read) -> dict | None:
    """The stored level a WATCH names: for LONG_WATCH the held support with
    the most touches, for SHORT_WATCH the held resistance with the most.
    Selects among levels Section 1 already stored - it adds no level of its
    own. Mirrors the existing Section 1 alert's own selection rule
    (`notify/telegram.py` `_body_lines`).
    """
    role = "support" if section1.watch == LONG_WATCH else "resistance"
    held = [lvl for lvl in section1.active_levels if lvl.get("held") and lvl.get("role") == role]
    if not held:
        return None
    return max(held, key=lambda lvl: lvl["touches"])


# -- asset construction ------------------------------------------------------


def asset_context_from_snapshot(
    snapshot: MarketIntelSnapshot, section1: Section1Read
) -> AssetContext:
    reason = None if snapshot.market_status == OK else snapshot.price_change.reason
    return AssetContext(
        symbol=snapshot.symbol,
        status=snapshot.market_status,
        reason=reason,
        section1=section1,
        price_change=snapshot.price_change,
        open_interest_change_pct=snapshot.open_interest_change_pct,
        position_flow=classify_snapshot(snapshot),
        funding_rate=snapshot.funding_rate,
        long_short_ratio=snapshot.long_short_ratio,
        liquidations=snapshot.liquidations,
    )


def unavailable_asset_context(symbol: str, reason: str, section1: Section1Read) -> AssetContext:
    return AssetContext(
        symbol=symbol,
        status=UNAVAILABLE,
        reason=reason,
        section1=section1,
        price_change=None,
        open_interest_change_pct=None,
        position_flow=None,
        funding_rate=None,
        long_short_ratio=None,
        liquidations=None,
    )


# -- derived comparisons -----------------------------------------------------


def _usable_price_change(asset: AssetContext) -> tuple[ClosedPeriodMetric | None, str | None]:
    if asset.price_change is None:
        return None, f"{asset.symbol} context UNAVAILABLE ({asset.reason})"
    if asset.price_change.status != OK:
        return None, (
            f"{asset.symbol} price change {asset.price_change.status} ({asset.price_change.reason})"
        )
    return asset.price_change, None


def relative_strength(coin: AssetContext, btc: AssetContext) -> DerivedResult:
    """Direct comparison of the coin's and BTC's price change over the SAME
    closed 1H period. No threshold for "strong" or "weak" exists, so the
    only outcomes are greater, less, or equal.
    """
    if coin.symbol == BTC_SYMBOL:
        return DerivedResult(NO_MATCH, _BTC_SELF_REASON)

    coin_pc, reason = _usable_price_change(coin)
    if reason is not None:
        return DerivedResult(NO_MATCH, reason)
    btc_pc, reason = _usable_price_change(btc)
    if reason is not None:
        return DerivedResult(NO_MATCH, reason)

    if coin_pc.period_start != btc_pc.period_start or coin_pc.period_close != btc_pc.period_close:
        return DerivedResult(NO_MATCH, "price changes cover different closed periods")

    if coin_pc.value > btc_pc.value:
        return DerivedResult(RELATIVE_STRONGER)
    if coin_pc.value < btc_pc.value:
        return DerivedResult(RELATIVE_WEAKER)
    return DerivedResult(RELATIVE_IN_LINE)


def btc_alignment(coin: AssetContext, btc: AssetContext) -> DerivedResult:
    """Compares the two stored Section 1 states' directions. AGAINST BTC is
    a label, never a rejection - the human decides.
    """
    if coin.symbol == BTC_SYMBOL:
        return DerivedResult(NO_MATCH, _BTC_SELF_REASON)

    coin_dir = section1_direction(coin.section1)
    btc_dir = section1_direction(btc.section1)
    if coin_dir is None or btc_dir is None:
        missing = []
        if coin_dir is None:
            missing.append(_section1_summary(coin))
        if btc_dir is None:
            missing.append(_section1_summary(btc))
        return DerivedResult(NO_DIRECTIONAL_ALIGNMENT, "; ".join(missing))

    detail = f"{coin.symbol} {coin.section1.state}, BTC {btc.section1.state}"
    if coin_dir == btc_dir:
        return DerivedResult(ALIGNED, detail)
    return DerivedResult(AGAINST_BTC, detail)


def _section1_summary(asset: AssetContext) -> str:
    if not asset.section1.available:
        return f"{asset.symbol} Section 1 UNAVAILABLE"
    return f"{asset.symbol} Section 1 {asset.section1.state}, not directional"


def liquidation_imbalance(coin: AssetContext) -> DerivedResult:
    """Long versus short liquidation USD for the coin's own closed 1H
    period. A description of which side was liquidated more, nothing
    more - it is not a liquidity hunt and implies no intent.
    """
    liq = coin.liquidations
    if liq is None or liq.status != OK:
        reason = "no liquidation reading" if liq is None else f"{liq.status} ({liq.reason})"
        return DerivedResult(UNAVAILABLE, reason)
    if liq.long_usd > liq.short_usd:
        return DerivedResult(LONG_LIQUIDATIONS_GREATER)
    if liq.short_usd > liq.long_usd:
        return DerivedResult(SHORT_LIQUIDATIONS_GREATER)
    return DerivedResult(LIQUIDATIONS_BALANCED)


# -- assembly ----------------------------------------------------------------


def watch_summary(coin: AssetContext, alignment: DerivedResult) -> WatchSummary:
    section1 = coin.section1
    active = section1.available and section1.watch in (LONG_WATCH, SHORT_WATCH)
    level = _watched_level(section1) if active else None
    return WatchSummary(
        active=active,
        watch=section1.watch if active else None,
        grade=section1.grade if active else None,
        level_price=level["price"] if level else None,
        level_touches=level["touches"] if level else None,
        btc_alignment=alignment.label,
    )


def build_market_context(
    coin: AssetContext, btc: AssetContext, eth: AssetContext
) -> MarketContextBundle:
    alignment = btc_alignment(coin, btc)
    return MarketContextBundle(
        coin=coin,
        market=MarketContext(btc=btc, eth=eth),
        derived=Derived(
            relative_strength=relative_strength(coin, btc),
            btc_alignment=alignment,
            liquidation_imbalance=liquidation_imbalance(coin),
        ),
        watch=watch_summary(coin, alignment),
    )


# -- fetching ----------------------------------------------------------------


def reference_symbols_to_fetch(coin_symbol: str) -> tuple[str, ...]:
    """The BTC/ETH snapshots a lookup must fetch beyond the coin's own -
    zero for a reference symbol that is itself the coin. Each costs one
    `fetch_market_intel` (7 Coinalyze call-units).
    """
    return tuple(symbol for symbol in REFERENCE_SYMBOLS if symbol != coin_symbol)


def _unavailable_reason(exc: Exception) -> str:
    if isinstance(exc, RateLimitedError):
        return "Coinalyze rate limit reached this minute"
    if isinstance(exc, CoinalyzeHttpError):
        return f"Coinalyze returned HTTP {exc.status_code}"
    return "could not reach Coinalyze"


class ReferenceSnapshotCache:
    """In-memory BTC/ETH snapshots for ONE closed 1H period - the period the
    coin's own data describes. A lookup in the same period reuses them; a
    lookup in a new period finds nothing and refetches. Holding only the
    current period bounds memory and means BTC, ETH and the coin never
    describe different hours. Not persisted: it need not survive a restart.
    """

    def __init__(self) -> None:
        self._period_start: dt.datetime | None = None
        self._snapshots: dict[str, MarketIntelSnapshot] = {}

    def get(self, symbol: str, period_start: dt.datetime) -> MarketIntelSnapshot | None:
        if period_start != self._period_start:
            return None
        return self._snapshots.get(symbol)

    def put(self, symbol: str, period_start: dt.datetime, snapshot: MarketIntelSnapshot) -> None:
        if period_start != self._period_start:
            self._period_start = period_start
            self._snapshots = {}
        self._snapshots[symbol] = snapshot


def _period_start(now: dt.datetime) -> dt.datetime:
    return closed_period(now, DEFAULT_INTERVAL).start


def uncached_reference_symbols(
    coin_symbol: str,
    now: dt.datetime,
    reference_cache: ReferenceSnapshotCache | None,
) -> tuple[str, ...]:
    """The reference snapshots a lookup would still have to fetch right now -
    what the budget pre-check counts. With no cache, all of them.
    """
    if reference_cache is None:
        return reference_symbols_to_fetch(coin_symbol)
    period_start = _period_start(now)
    return tuple(
        symbol
        for symbol in reference_symbols_to_fetch(coin_symbol)
        if reference_cache.get(symbol, period_start) is None
    )


def _fetch_reference(
    client: CoinalyzeClient,
    cache: FutureMarketsCache,
    symbol: str,
    venue: str,
    now: dt.datetime,
    database_url: str,
    reference_cache: ReferenceSnapshotCache | None,
) -> AssetContext:
    section1 = read_section1(database_url, symbol, now)
    period_start = _period_start(now)
    snapshot = reference_cache.get(symbol, period_start) if reference_cache else None
    if snapshot is None:
        try:
            snapshot = fetch_market_intel(client, cache, symbol, venue, now)
        except (RateLimitedError, CoinalyzeHttpError, CoinalyzeConnectionError) as exc:
            # Never cached: the next lookup in this period should try again.
            return unavailable_asset_context(symbol, _unavailable_reason(exc), section1)
        if reference_cache is not None and snapshot.market_status == OK:
            reference_cache.put(symbol, period_start, snapshot)
    return asset_context_from_snapshot(snapshot, section1)


def fetch_market_context(
    client: CoinalyzeClient,
    cache: FutureMarketsCache,
    coin_snapshot: MarketIntelSnapshot,
    venue: str,
    now: dt.datetime,
    database_url: str,
    reference_cache: ReferenceSnapshotCache | None = None,
) -> MarketContextBundle:
    """Assembles the full bundle for one coin whose snapshot is already
    fetched. BTC/ETH come from `reference_cache` when it holds them for this
    closed period; otherwise they are fetched and cached. The coin's own
    snapshot is cached too when the coin is a reference symbol, so it
    isn't fetched again in the same period. A failed reference fetch
    becomes that asset's UNAVAILABLE context, never an error for the reply.
    """
    period_start = _period_start(now)
    coin = asset_context_from_snapshot(
        coin_snapshot, read_section1(database_url, coin_snapshot.symbol, now)
    )
    coin_is_reference = coin.symbol in REFERENCE_SYMBOLS
    if reference_cache is not None and coin_is_reference and coin_snapshot.market_status == OK:
        reference_cache.put(coin.symbol, period_start, coin_snapshot)
    btc = coin if coin.symbol == BTC_SYMBOL else None
    eth = coin if coin.symbol == ETH_SYMBOL else None
    if btc is None:
        btc = _fetch_reference(client, cache, BTC_SYMBOL, venue, now, database_url, reference_cache)
    if eth is None:
        eth = _fetch_reference(client, cache, ETH_SYMBOL, venue, now, database_url, reference_cache)
    return build_market_context(coin, btc, eth)
