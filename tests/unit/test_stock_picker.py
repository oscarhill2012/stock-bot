from orchestrator.stock_picker import get_watchlist, normalise_to_symbols


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


def test_get_watchlist_contains_expected_tickers():
    tickers = get_watchlist()
    assert "AAPL" in tickers
    assert "MSFT" in tickers
