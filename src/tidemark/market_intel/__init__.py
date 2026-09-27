"""Live market-intelligence layer: Coinalyze derivatives data.

Strictly isolated from the research engine - see
docs/adr/0011-market-intelligence-layer.md. This package:

- never imports `tidemark.data.exchange`, `tidemark.context`,
  `tidemark.journal`, or `tidemark.replay`;
- never writes to `observations`, `journal_entries`, `context_records`,
  `candles`, or any other research table;
- is never imported by the research engine.

It reads HTTP and returns plain objects. Nothing else. Raw data only -
no interpretation, no bias, no trading recommendation. See
`docs/rulebook/derivatives-context-v0.1.md` for a PROVISIONAL,
not-yet-wired interpretation document that this package does not read.
"""

from __future__ import annotations

from tidemark.market_intel.models import MarketIntelSnapshot
from tidemark.market_intel.service import fetch_market_intel

__all__ = ["MarketIntelSnapshot", "fetch_market_intel"]
