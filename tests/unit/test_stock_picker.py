import json
from pathlib import Path

from orchestrator.stock_picker import get_watchlist, normalise_to_symbols

# Resolve the real config the picker reads, so the parity test below stays
# correct through any watchlist edit rather than hard-pinning ticker symbols.
_WATCHLIST_PATH = Path(__file__).resolve().parents[2] / "config" / "watchlist.json"


def test_normalise_to_symbols_passes_through_legacy_strings():
    """Legacy flat-string entries are returned unchanged, in order."""
    raw = ["AAPL", "MSFT"]
    assert normalise_to_symbols(raw) == ["AAPL", "MSFT"]


def test_normalise_to_symbols_extracts_symbol_from_extended_objects():
    """Extended ``{"symbol", "name"}`` entries are reduced to their symbols."""
    raw = [
        {"symbol": "AAPL", "name": "Apple"},
        {"symbol": "MSFT", "name": "Microsoft"},
    ]
    assert normalise_to_symbols(raw) == ["AAPL", "MSFT"]


def test_normalise_to_symbols_handles_mixed_formats():
    """A mix of legacy strings and extended objects normalises cleanly."""
    raw = ["AAPL", {"symbol": "MSFT", "name": "Microsoft"}]
    assert normalise_to_symbols(raw) == ["AAPL", "MSFT"]


def test_get_watchlist_returns_list():
    tickers = get_watchlist()
    assert isinstance(tickers, list)
    assert len(tickers) > 0


def test_get_watchlist_matches_config():
    """The returned symbols are exactly those configured in watchlist.json.

    Asserting parity with the config file (rather than pinning specific
    tickers) keeps this test correct across watchlist edits while still
    proving the config-to-symbols wiring end to end.
    """
    with _WATCHLIST_PATH.open() as f:
        raw = json.load(f)["tickers"]

    # Each config entry is either a bare symbol string or a {"symbol": ...} dict.
    expected = [item if isinstance(item, str) else item["symbol"] for item in raw]

    assert get_watchlist() == expected
