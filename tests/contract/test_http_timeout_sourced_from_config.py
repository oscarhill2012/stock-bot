"""Contract test: the Quiver provider uses get_config().quiver_http_timeout_seconds.

Patches the data-config singleton with a sentinel timeout and asserts
quiver._fetch_trades calls requests.get with that exact timeout.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from data.config import DataConfig, FetchDefaults

SENTINEL_TIMEOUT = 42.0


def _sentinel_config() -> DataConfig:
    """Build a DataConfig whose quiver timeout is a unique sentinel.

    Returns
    -------
    DataConfig
        A fully-valid ``DataConfig`` instance where ``quiver_http_timeout_seconds``
        carries an obviously-invalid sentinel float.  The value 42.0 is chosen
        because it will never collide with a real production default.
    """
    return DataConfig(
        providers={
            "price_history":      "yfinance",
            "company_ratios":     "pit_composite",
            "news":               "alpha_vantage",
            "social_sentiment":   "finnhub",
            "insider_trades":     "edgar",
            "politician_trades":  "fmp",
            "notable_holders":    "edgar",
            "filings":            "edgar",
            "earnings":           "finnhub",
        },
        defaults                    = FetchDefaults(),
        quiver_http_timeout_seconds = SENTINEL_TIMEOUT,
    )


def test_quiver_uses_config_http_timeout(monkeypatch) -> None:
    """quiver._fetch_trades passes get_config().quiver_http_timeout_seconds to requests.get.

    Replaces the ``_cache`` singleton with a sentinel ``DataConfig``, then
    patches ``requests.get`` to capture the keyword arguments it receives.
    Asserts the ``timeout`` kwarg matches the sentinel exactly.

    Parameters
    ----------
    monkeypatch:
        pytest ``monkeypatch`` fixture.
    """
    from data import config as data_config_mod
    from data.providers.politician_trades import quiver

    monkeypatch.setattr(data_config_mod, "_cache", _sentinel_config())

    fake_response = MagicMock()
    fake_response.content = b"[]"
    fake_response.json.return_value = []

    with patch("data.providers.politician_trades.quiver.requests.get",
               return_value=fake_response) as fake_get:
        quiver._fetch_trades("AAPL", api_key="fake")

    fake_get.assert_called_once()
    _, kwargs = fake_get.call_args
    assert kwargs["timeout"] == SENTINEL_TIMEOUT
