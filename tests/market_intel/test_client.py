"""CoinalyzeClient: no real network anywhere - a fake transport plays
the role `urllib` normally would, exactly like `notify.telegram`'s
`send_fn` injection.
"""

from __future__ import annotations

import pytest

from tidemark.market_intel.client import CoinalyzeClient
from tidemark.market_intel.errors import (
    CoinalyzeConnectionError,
    CoinalyzeHttpError,
    MissingApiKeyError,
    RateLimitedError,
)

# -- missing key: clean, eager failure ---------------------------------------


def test_missing_api_key_raises_immediately() -> None:
    with pytest.raises(MissingApiKeyError):
        CoinalyzeClient(api_key=None)


def test_empty_string_api_key_raises_immediately() -> None:
    with pytest.raises(MissingApiKeyError):
        CoinalyzeClient(api_key="")


# -- happy path ---------------------------------------------------------------


def test_successful_call_returns_parsed_json() -> None:
    def fake_transport(url: str, api_key: str, timeout: float) -> str:
        assert api_key == "secret-key"
        assert "funding-rate" in url
        return '[{"symbol":"BTCUSDT_PERP.A","value":0.001,"update":1000}]'

    client = CoinalyzeClient(
        api_key="secret-key", transport=fake_transport, sleep_fn=lambda s: None
    )
    result = client.funding_rate(["BTCUSDT_PERP.A"])

    assert result == [{"symbol": "BTCUSDT_PERP.A", "value": 0.001, "update": 1000}]


# -- 429 handling: bounded backoff respecting Retry-After ---------------------


def test_429_retries_then_succeeds_respecting_retry_after() -> None:
    attempts = {"n": 0}
    slept: list[float] = []

    def fake_transport(url: str, api_key: str, timeout: float) -> str:
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise CoinalyzeHttpError(429, "Too Many Requests", retry_after=2.5)
        return "[]"

    client = CoinalyzeClient(
        api_key="k", max_retries=5, transport=fake_transport, sleep_fn=slept.append
    )
    result = client.open_interest(["BTCUSDT_PERP.A"])

    assert result == []
    assert attempts["n"] == 3
    assert slept == [2.5, 2.5]


def test_429_gives_up_after_max_retries_and_raises_rate_limited() -> None:
    def always_429(url: str, api_key: str, timeout: float) -> str:
        raise CoinalyzeHttpError(429, "Too Many Requests", retry_after=1.0)

    slept: list[float] = []
    client = CoinalyzeClient(
        api_key="k", max_retries=2, transport=always_429, sleep_fn=slept.append
    )

    with pytest.raises(RateLimitedError) as exc_info:
        client.open_interest(["BTCUSDT_PERP.A"])

    # Bounded: exactly max_retries sleeps, never an unbounded retry loop.
    assert len(slept) == 2
    assert exc_info.value.attempts == 2


def test_retry_after_is_capped_at_max_retry_after_seconds() -> None:
    def always_429(url: str, api_key: str, timeout: float) -> str:
        raise CoinalyzeHttpError(429, "Too Many Requests", retry_after=9999.0)

    slept: list[float] = []
    client = CoinalyzeClient(
        api_key="k",
        max_retries=1,
        max_retry_after_seconds=10.0,
        transport=always_429,
        sleep_fn=slept.append,
    )

    with pytest.raises(RateLimitedError):
        client.open_interest(["BTCUSDT_PERP.A"])

    assert slept == [10.0]


# -- non-429 errors propagate without retry -----------------------------------


def test_401_propagates_immediately_without_retry() -> None:
    calls = {"n": 0}

    def unauthorized(url: str, api_key: str, timeout: float) -> str:
        calls["n"] += 1
        raise CoinalyzeHttpError(401, "Invalid/Missing API key")

    client = CoinalyzeClient(api_key="k", transport=unauthorized, sleep_fn=lambda s: None)

    with pytest.raises(CoinalyzeHttpError) as exc_info:
        client.open_interest(["BTCUSDT_PERP.A"])

    assert exc_info.value.status_code == 401
    assert calls["n"] == 1


def test_connection_error_propagates() -> None:
    def times_out(url: str, api_key: str, timeout: float) -> str:
        raise CoinalyzeConnectionError("simulated timeout")

    client = CoinalyzeClient(api_key="k", transport=times_out)

    with pytest.raises(CoinalyzeConnectionError):
        client.future_markets()


# -- rate-limit usage tracking --------------------------------------------


def test_call_units_are_tracked_per_symbol_in_a_request() -> None:
    def fake_transport(url: str, api_key: str, timeout: float) -> str:
        return "[]"

    client = CoinalyzeClient(api_key="k", transport=fake_transport, clock=lambda: 1000.0)
    client.open_interest(["A", "B", "C"])

    assert client.calls_in_last_minute == 3


def test_future_markets_costs_exactly_one_call_unit() -> None:
    def fake_transport(url: str, api_key: str, timeout: float) -> str:
        return "[]"

    client = CoinalyzeClient(api_key="k", transport=fake_transport, clock=lambda: 1000.0)
    client.future_markets()

    assert client.calls_in_last_minute == 1


# -- secret hygiene: the key never leaks -------------------------------------


def test_repr_and_str_never_leak_the_key() -> None:
    client = CoinalyzeClient(api_key="super-secret-key")
    assert "super-secret-key" not in repr(client)
    assert "super-secret-key" not in str(client)


def test_key_never_appears_in_a_raised_error_message() -> None:
    def unauthorized(url: str, api_key: str, timeout: float) -> str:
        raise CoinalyzeHttpError(401, "Invalid/Missing API key")

    client = CoinalyzeClient(api_key="super-secret-key", transport=unauthorized)

    with pytest.raises(CoinalyzeHttpError) as exc_info:
        client.open_interest(["BTCUSDT_PERP.A"])

    assert "super-secret-key" not in str(exc_info.value)
