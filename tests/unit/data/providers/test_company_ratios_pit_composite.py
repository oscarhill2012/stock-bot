"""PIT-composite ratios provider — XBRL fundamentals + sliced OHLCV technicals."""
from __future__ import annotations

from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest

from data.models import CompanyRatios, OHLCBar, PriceHistory


def _make_bars(n: int, last_close: float = 175.0) -> list[OHLCBar]:
    """Create ``n`` daily bars ending at 2023-03-14 with ``last_close``."""
    bars: list[OHLCBar] = []
    for i in range(n):
        ts    = datetime(2023, 1, 1, tzinfo=UTC).replace(day=min(i + 1, 28))
        close = last_close - (n - 1 - i) * 0.5
        bars.append(OHLCBar(
            timestamp=ts,
            open=close - 0.5,
            high=close + 1.0,
            low=close - 1.0,
            close=close,
            volume=1_000_000.0,
        ))
    return bars


@pytest.mark.asyncio
async def test_pit_composite_returns_filled_ratios(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provider composes XBRL fundamentals + price-derived technicals."""
    import data.providers.company_ratios.pit_composite as mod

    fake_facts = SimpleNamespace(
        long_name      = "Apple Inc.",
        sector         = "Technology",
        shares_out     = 15_700_000_000.0,
        eps_ttm        = 6.0,
        dps_ttm        = 0.92,
    )
    monkeypatch.setattr(mod, "_fetch_xbrl_facts", lambda symbol, as_of_date: fake_facts)
    monkeypatch.setattr(
        mod, "_fetch_price_series",
        lambda symbol, as_of: PriceHistory(ticker=symbol, bars=_make_bars(220, last_close=175.0)),
    )
    # Stub the XBRL summary so no EDGAR network call or env-var lookup is made.
    monkeypatch.setattr(mod, "_load_xbrl_summary", lambda *a, **k: {
        "profit_margin": None, "debt_to_equity": None, "roe": None,
        "revenue_growth_yoy": None, "free_cash_flow": None,
        "peg": None,
    })

    out = await mod.fetch("AAPL", as_of=datetime(2023, 3, 14, tzinfo=UTC))

    assert isinstance(out, CompanyRatios)
    assert out.long_name      == "Apple Inc."
    assert out.sector         == "Technology"
    assert out.last_price     == pytest.approx(175.0)
    assert out.market_cap     == pytest.approx(15_700_000_000.0 * 175.0)
    assert out.trailing_pe    == pytest.approx(175.0 / 6.0)
    assert out.dividend_yield == pytest.approx(0.92 / 175.0)
    assert out.fifty_day_average is not None
    assert out.two_hundred_day_average is not None


@pytest.mark.asyncio
async def test_pit_composite_handles_missing_facts(monkeypatch: pytest.MonkeyPatch) -> None:
    """Empty XBRL must yield a model with ``None`` fundamentals, not raise."""
    import data.providers.company_ratios.pit_composite as mod

    monkeypatch.setattr(mod, "_fetch_xbrl_facts", lambda s, d: SimpleNamespace(
        long_name=None, sector=None, shares_out=None, eps_ttm=None, dps_ttm=None,
    ))
    monkeypatch.setattr(
        mod, "_fetch_price_series",
        lambda s, a: PriceHistory(ticker=s, bars=_make_bars(5, last_close=100.0)),
    )
    monkeypatch.setattr(mod, "_load_xbrl_summary", lambda *a, **k: {
        "profit_margin": None, "debt_to_equity": None, "roe": None,
        "revenue_growth_yoy": None, "free_cash_flow": None,
        "peg": None,
    })

    out = await mod.fetch("XYZ", as_of=datetime(2023, 3, 14, tzinfo=UTC))

    assert isinstance(out, CompanyRatios)
    assert out.last_price  == pytest.approx(100.0)
    assert out.market_cap  is None
    assert out.trailing_pe is None


@pytest.mark.asyncio
async def test_pit_composite_handles_empty_prices(monkeypatch: pytest.MonkeyPatch) -> None:
    """Empty OHLCV must yield ``None`` price-derived fields, not raise."""
    import data.providers.company_ratios.pit_composite as mod

    monkeypatch.setattr(mod, "_fetch_xbrl_facts", lambda s, d: SimpleNamespace(
        long_name="X Co", sector="X", shares_out=1.0, eps_ttm=1.0, dps_ttm=None,
    ))
    monkeypatch.setattr(
        mod, "_fetch_price_series",
        lambda s, a: PriceHistory(ticker=s, bars=[]),
    )
    monkeypatch.setattr(mod, "_load_xbrl_summary", lambda *a, **k: {
        "profit_margin": None, "debt_to_equity": None, "roe": None,
        "revenue_growth_yoy": None, "free_cash_flow": None,
        "peg": None,
    })

    out = await mod.fetch("XYZ", as_of=datetime(2023, 3, 14, tzinfo=UTC))

    assert out.long_name  == "X Co"
    assert out.last_price is None
    assert out.market_cap is None


def test_pit_composite_registers_on_import() -> None:
    import data.providers.company_ratios.pit_composite  # noqa: F401
    from data.registry import _REGISTRY

    entry = _REGISTRY[("company_ratios", "pit_composite")]
    assert entry.upstream == "yfinance"   # shares yfinance limiter for price data


# ---------------------------------------------------------------------------
# Task 4.4 — six XBRL-derivable ratios + as_of population
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pit_composite_populates_new_ratios(monkeypatch: pytest.MonkeyPatch) -> None:
    """All six XBRL-derived ratio fields are populated when the summary returns full data."""
    import data.providers.company_ratios.pit_composite as mod

    # Stub out the XBRL facts (identity / price primitives) so the test is
    # isolated from any EDGAR network call.
    monkeypatch.setattr(mod, "_fetch_xbrl_facts", lambda symbol, as_of_date: SimpleNamespace(
        long_name="Apple Inc.", sector="Technology",
        shares_out=15_700_000_000.0, eps_ttm=6.0, dps_ttm=0.92,
    ))
    monkeypatch.setattr(
        mod, "_fetch_price_series",
        lambda symbol, as_of: PriceHistory(ticker=symbol, bars=_make_bars(220, last_close=175.0)),
    )

    # Full XBRL summary — all five ratio fields present.  ``peg`` is always
    # surfaced as ``None`` by ``_load_xbrl_summary`` (there is no PIT-correct
    # source for the forward-growth term — see the provider docstring).
    fake_xbrl: dict = {
        "profit_margin":      0.25,
        "debt_to_equity":     1.5,
        "roe":                0.15,
        "revenue_growth_yoy": 0.07,
        "free_cash_flow":     9.0e10,
        "peg":                None,
    }
    monkeypatch.setattr(mod, "_load_xbrl_summary", lambda *a, **k: fake_xbrl)

    out = await mod.fetch("AAPL", as_of=datetime(2023, 3, 10, tzinfo=UTC))

    assert isinstance(out, CompanyRatios)

    # Core XBRL-derived ratios.
    assert out.profit_margin      == pytest.approx(0.25)
    assert out.debt_to_equity     == pytest.approx(1.5)
    assert out.roe                == pytest.approx(0.15)
    assert out.revenue_growth_yoy == pytest.approx(0.07)
    assert out.free_cash_flow     == pytest.approx(9.0e10)
    # PEG is intentionally always None — no PIT-correct source available.
    assert out.peg                is None

    # as_of must be populated.
    from datetime import date
    assert out.as_of == date(2023, 3, 10)


@pytest.mark.asyncio
async def test_pit_composite_all_xbrl_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    """When XBRL summary returns all None, every new ratio field is None — no exception raised."""
    import data.providers.company_ratios.pit_composite as mod

    monkeypatch.setattr(mod, "_fetch_xbrl_facts", lambda s, d: SimpleNamespace(
        long_name="Stub Co", sector="Industrials",
        shares_out=1_000_000.0, eps_ttm=2.0, dps_ttm=None,
    ))
    monkeypatch.setattr(
        mod, "_fetch_price_series",
        lambda s, a: PriceHistory(ticker=s, bars=_make_bars(10, last_close=50.0)),
    )

    # Empty XBRL summary — all ratios absent (e.g. ADR with no EDGAR data).
    empty_xbrl: dict = {
        "profit_margin":      None,
        "debt_to_equity":     None,
        "roe":                None,
        "revenue_growth_yoy": None,
        "free_cash_flow":     None,
        "peg":                None,
        "_peg_source":        None,
    }
    monkeypatch.setattr(mod, "_load_xbrl_summary", lambda *a, **k: empty_xbrl)

    out = await mod.fetch("XYZ", as_of=datetime(2023, 3, 10, tzinfo=UTC))

    assert isinstance(out, CompanyRatios)
    assert out.profit_margin      is None
    assert out.debt_to_equity     is None
    assert out.roe                is None
    assert out.revenue_growth_yoy is None
    assert out.free_cash_flow     is None
    assert out.peg                is None

    # Price-derived fields must still be present (from yfinance branch).
    assert out.last_price == pytest.approx(50.0)


@pytest.mark.asyncio
async def test_pit_composite_partial_xbrl(monkeypatch: pytest.MonkeyPatch) -> None:
    """Partial XBRL data — some fields populated, others None — is handled gracefully."""
    import data.providers.company_ratios.pit_composite as mod

    monkeypatch.setattr(mod, "_fetch_xbrl_facts", lambda s, d: SimpleNamespace(
        long_name="Partial Co", sector="Healthcare",
        shares_out=500_000_000.0, eps_ttm=3.5, dps_ttm=0.5,
    ))
    monkeypatch.setattr(
        mod, "_fetch_price_series",
        lambda s, a: PriceHistory(ticker=s, bars=_make_bars(60, last_close=120.0)),
    )

    # Only profit_margin and roe available; the rest are missing concepts.
    partial_xbrl: dict = {
        "profit_margin":      0.18,
        "debt_to_equity":     None,   # StockholdersEquity concept missing
        "roe":                0.12,
        "revenue_growth_yoy": None,   # Prior-year Revenues missing
        "free_cash_flow":     None,   # CapEx concept missing
        "peg":                None,
        "_peg_source":        None,
    }
    monkeypatch.setattr(mod, "_load_xbrl_summary", lambda *a, **k: partial_xbrl)

    out = await mod.fetch("HLTH", as_of=datetime(2023, 6, 15, tzinfo=UTC))

    assert isinstance(out, CompanyRatios)

    # Populated fields must be correct.
    assert out.profit_margin == pytest.approx(0.18)
    assert out.roe           == pytest.approx(0.12)

    # Missing fields must be None, not zero or a default.
    assert out.debt_to_equity     is None
    assert out.revenue_growth_yoy is None
    assert out.free_cash_flow     is None
    assert out.peg                is None

    # as_of must be set.
    from datetime import date
    assert out.as_of == date(2023, 6, 15)


# ---------------------------------------------------------------------------
# Revenue-concept selection — pick the genuinely-filed XBRL revenue concept.
#
# No single us-gaap revenue concept is correct across all filers.  ASC-606
# filers (most tech) report their top line under
# ``RevenueFromContractWithCustomerExcludingAssessedTax`` and leave a stale
# single fragment under ``Revenues``; many energy/industrial filers are the
# reverse.  ``_select_revenue_series`` discriminates by period-distinctness:
# the genuinely-filed concept yields *different* TTM values a year apart,
# whereas a stale fragment returns the identical value for any ``as_of``.
# ---------------------------------------------------------------------------

# Probe dates mirroring the live EDGAR investigation that motivated the fix.
_AS_OF  = date(2025, 9, 2)
_PRIOR  = date(2024, 9, 2)

_REV_ASC606 = "RevenueFromContractWithCustomerExcludingAssessedTax"
_REV_LEGACY = "Revenues"
_REV_NET    = "SalesRevenueNet"


def _ttm_lookup(table: dict[tuple[str, date], float | None]):
    """Build a ``(concept, at) -> float | None`` callable backed by ``table``.

    Any ``(concept, date)`` pair absent from ``table`` resolves to ``None`` —
    modelling an XBRL concept the filer never reported.
    """
    def _lookup(concept: str, at: date) -> float | None:
        return table.get((concept, at))

    return _lookup


def test_select_revenue_prefers_period_distinct_asc606_filer() -> None:
    """ASC-606 filer (AAPL-shape): the ASC-606 concept moves year-on-year and wins.

    ``Revenues`` carries a stale fragment (identical at both dates) and must be
    rejected even though it is present.
    """
    from data.providers.company_ratios.pit_composite import _select_revenue_series

    table = {
        # Stale fragment — identical value at both dates → rejected.
        (_REV_LEGACY, _AS_OF): 215.6e9,
        (_REV_LEGACY, _PRIOR): 215.6e9,
        # Genuinely-filed top line — distinct across the year → selected.
        (_REV_ASC606, _AS_OF): 296.1e9,
        (_REV_ASC606, _PRIOR): 293.8e9,
    }
    now, prior, concept = _select_revenue_series(_ttm_lookup(table), _AS_OF, _PRIOR)

    assert concept == _REV_ASC606
    assert now   == pytest.approx(296.1e9)
    assert prior == pytest.approx(293.8e9)


def test_select_revenue_skips_stale_asc606_for_energy_filer() -> None:
    """Energy filer (XOM-shape): ASC-606 is the stale fragment; ``Revenues`` wins.

    The ASC-606 concept is first in priority order but is stale (equal values),
    so period-distinctness must fall through to the live ``Revenues`` series.
    """
    from data.providers.company_ratios.pit_composite import _select_revenue_series

    table = {
        # ASC-606 stale for this filer — identical → rejected despite priority.
        (_REV_ASC606, _AS_OF): 199.0e9,
        (_REV_ASC606, _PRIOR): 199.0e9,
        # Live top line under the legacy concept.
        (_REV_LEGACY, _AS_OF): 176.1e9,
        (_REV_LEGACY, _PRIOR): 169.5e9,
    }
    now, prior, concept = _select_revenue_series(_ttm_lookup(table), _AS_OF, _PRIOR)

    assert concept == _REV_LEGACY
    assert now   == pytest.approx(176.1e9)
    assert prior == pytest.approx(169.5e9)


def test_select_revenue_fallback_to_current_only_when_no_prior() -> None:
    """Recent IPO: a current value exists but no prior year — return (now, None).

    revenue_growth_yoy is uncomputable, but the margin denominator still needs
    a current revenue, so the first concept with a present current value wins.
    """
    from data.providers.company_ratios.pit_composite import _select_revenue_series

    table = {
        (_REV_ASC606, _AS_OF): 12.0e9,
        # No (_REV_ASC606, _PRIOR) entry — company did not exist a year ago.
    }
    now, prior, concept = _select_revenue_series(_ttm_lookup(table), _AS_OF, _PRIOR)

    assert concept == _REV_ASC606
    assert now   == pytest.approx(12.0e9)
    assert prior is None


def test_select_revenue_returns_none_when_no_concept_has_data() -> None:
    """Foreign filer / ADR with no us-gaap revenue at all → (None, None, None)."""
    from data.providers.company_ratios.pit_composite import _select_revenue_series

    now, prior, concept = _select_revenue_series(_ttm_lookup({}), _AS_OF, _PRIOR)

    assert (now, prior, concept) == (None, None, None)


def test_select_revenue_all_stale_falls_back_to_current() -> None:
    """All concepts stale (current == prior): yoy is uncomputable but margin works.

    The period-distinct pass finds nothing; the fallback pass returns the first
    concept with a present current value and a ``None`` prior so the caller
    leaves revenue_growth_yoy unset rather than computing a false 0.
    """
    from data.providers.company_ratios.pit_composite import _select_revenue_series

    table = {
        (_REV_LEGACY, _AS_OF): 50.0e9,
        (_REV_LEGACY, _PRIOR): 50.0e9,
    }
    now, prior, concept = _select_revenue_series(_ttm_lookup(table), _AS_OF, _PRIOR)

    assert concept == _REV_LEGACY
    assert now   == pytest.approx(50.0e9)
    assert prior is None


# ---------------------------------------------------------------------------
# Regression test: _load_xbrl_summary must yield non-zero revenue_growth_yoy
# for ASC-606 filers even when ``Revenues`` carries a stale fragment.
#
# This is the end-to-end path that was broken before the revenue-concept
# selector was introduced.  The OLD code read revenue only from ``Revenues``;
# for ASC-606 filers (AAPL, MSFT, META …) that concept carries a stale single
# fragment returning the identical value at every date, collapsing
# ``revenue_growth_yoy`` to a false 0.0 and inflating ``profit_margin``
# because the denominator (the stale fragment) was smaller than the real
# filed revenue.
#
# This test constructs a fake edgartools ``facts`` object whose query chain
# mimics the ASC-606 scenario — ``Revenues`` stale, ``RevenueFromContract…``
# genuinely distinct — then calls ``_load_xbrl_summary`` directly and asserts
# the POSITIVE signal: non-zero YoY growth and a plausible margin (< 100%).
# ---------------------------------------------------------------------------

def _make_fake_facts(
    concept_table: dict[tuple[str, date], float | None],
    other_concepts: dict[str, float | None] | None = None,
) -> object:
    """Build a fake edgartools ``EntityFacts``-shaped object.

    The returned object's ``query().by_concept(concept).as_of(at)`` chain
    returns a fake row whose ``.value`` is looked up from ``concept_table``
    keyed by ``(concept, date)``.  Concepts not in the table resolve to an
    empty list from ``.latest()``.

    ``other_concepts`` is an optional flat dict of ``{concept: value}`` for
    concepts that are always queried at ``as_of_date`` (not varying by date).
    These are looked up only by concept name — the date component is ignored.

    Parameters
    ----------
    concept_table:
        Mapping of ``(concept_name, date)`` → float value (or ``None``) for
        revenue-selection concepts that must vary by date.
    other_concepts:
        Flat mapping of concept → value for balance-sheet / income-statement
        concepts that are always pinned to the ``as_of_date``.

    Returns
    -------
    object
        A minimal fake that satisfies the ``facts.query().by_concept().as_of()``
        call chain used inside ``_load_xbrl_summary._ttm_at``.
    """
    other_concepts = other_concepts or {}

    def _make_query(concept: str):
        """Return a chainable query stub for the given ``concept``."""

        class _AsOf:
            def __init__(self, _concept: str, _at):
                self._concept = _concept
                self._at      = _at

            def latest(self):
                """Return a list with one fake row, or an empty list."""
                # First try the date-specific table (used for revenue concepts).
                if isinstance(self._at, date):
                    val = concept_table.get((self._concept, self._at))
                else:
                    val = None

                # Fall back to the date-agnostic table (balance-sheet / P&L).
                if val is None:
                    val = other_concepts.get(self._concept)

                if val is None:
                    return []

                return [SimpleNamespace(value=val)]

        class _ByConcept:
            def __init__(self, _concept: str):
                self._concept = _concept

            def as_of(self, at):
                return _AsOf(self._concept, at)

        class _Query:
            def by_concept(self, concept: str):
                return _ByConcept(concept)

        return _Query()

    class _FakeFacts:
        def query(self):
            return _make_query(concept)  # type: ignore[name-defined]

    # Rebuild so each ``query()`` call gets a fresh query object keyed to the
    # *actual* concept requested.  We need to close over the outer ``facts``
    # object itself, so we use __getattr__ indirection.
    class _FakeFacts2:
        def query(self):
            class _Q:
                def by_concept(self_, concept_name):
                    class _BC:
                        def as_of(self_, at):
                            val = concept_table.get((concept_name, at))
                            if val is None:
                                val = other_concepts.get(concept_name)
                            if val is None:
                                return SimpleNamespace(latest=lambda: [])
                            row = SimpleNamespace(value=val)
                            return SimpleNamespace(latest=lambda: [row])
                    return _BC()
            return _Q()

    return _FakeFacts2()


def test_load_xbrl_summary_asc606_filer_yields_nonzero_growth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ASC-606 filer scenario: ``Revenues`` stale → selector picks the live concept.

    Before ``_select_revenue_series`` was introduced, ``_load_xbrl_summary``
    read revenue only from ``Revenues``.  For ASC-606 filers that concept
    carries a stale single fragment — identical value at every date — so
    ``revenue_growth_yoy`` collapsed to ``(stale - stale) / stale = 0.0`` and
    ``profit_margin`` used the wrong (smaller) denominator.

    This test stubs the edgartools ``Company`` and ``EntityFacts`` objects so
    no network call is made, then asserts the POSITIVE signals:
    - ``revenue_growth_yoy`` is non-zero (regression: was 0.0 before the fix).
    - ``profit_margin`` is between 0 and 1 (regression: was > 1.0 before fix,
      because net income was divided by the stale fragment rather than real rev).
    """
    import data.providers.company_ratios.pit_composite as mod

    # --- AAPL-shaped XBRL scenario -------------------------------------------
    # ``Revenues`` carries a stale single fragment — same value at every date.
    # ``RevenueFromContractWithCustomerExcludingAssessedTax`` is the live top
    # line with distinct values year-on-year.  All other concepts are present
    # but constant (they don't affect the revenue-selector logic).

    _STALE_REV    = 215_638_000_000.0   # same at both dates → rejected
    _REV_NOW      = 296_105_000_000.0   # genuinely-filed top line at as_of
    _REV_PRIOR    = 293_787_000_000.0   # genuinely-filed top line a year ago
    _NET_INCOME   =  79_000_000_000.0
    _EQUITY       = 364_980_000_000.0

    # Date pair matching the existing test suite's probe dates.
    as_of = _AS_OF   # 2025-09-02
    prior = _PRIOR   # 2024-09-02

    concept_table: dict[tuple[str, date], float | None] = {
        # Stale legacy concept — equal at both dates → must be rejected.
        (_REV_LEGACY, as_of): _STALE_REV,
        (_REV_LEGACY, prior): _STALE_REV,
        # Live ASC-606 concept — distinct values → must be selected.
        (_REV_ASC606, as_of): _REV_NOW,
        (_REV_ASC606, prior): _REV_PRIOR,
    }

    other_concepts: dict[str, float | None] = {
        "NetIncomeLoss":                                       _NET_INCOME,
        "StockholdersEquity":                                  _EQUITY,
        "LongTermDebtNoncurrent":                              85_750_000_000.0,
        "LongTermDebtCurrent":                                 10_912_000_000.0,
        "ShortTermBorrowings":                                  5_980_000_000.0,
        "NetCashProvidedByUsedInOperatingActivities":          91_443_000_000.0,
        "PaymentsToAcquirePropertyPlantAndEquipment":           6_539_000_000.0,
    }

    fake_facts = _make_fake_facts(concept_table, other_concepts)

    # Patch ``Company`` so ``_load_xbrl_summary`` never hits the network.
    class _FakeCompany:
        def get_facts(self):
            return fake_facts

    monkeypatch.setattr(mod, "_ensure_identity", lambda: None)
    # Patch ``Company`` in the module's own namespace — ``pit_composite.py``
    # imports it as ``from edgar import Company``, so we must patch the name
    # where it is actually looked up, not on the ``edgar`` package itself.
    monkeypatch.setattr(mod, "Company", lambda symbol: _FakeCompany())

    result = mod._load_xbrl_summary("AAPL", as_of)

    expected_growth = (_REV_NOW - _REV_PRIOR) / _REV_PRIOR
    expected_margin = _NET_INCOME / _REV_NOW

    # --- Positive-signal assertions (the point of the regression test) -------

    # Before the fix: revenue_growth_yoy was (stale - stale) / stale = 0.0.
    # After the fix: it must be non-zero and match the live concept's growth.
    assert result["revenue_growth_yoy"] is not None, (
        "revenue_growth_yoy must not be None for an ASC-606 filer with EDGAR data"
    )
    assert result["revenue_growth_yoy"] != 0.0, (
        "revenue_growth_yoy collapsed to 0.0 — selector is using the stale "
        "Revenues fragment instead of RevenueFromContractWithCustomer…"
    )
    assert result["revenue_growth_yoy"] == pytest.approx(expected_growth, rel=1e-6), (
        f"Expected growth ≈ {expected_growth:.4%}, got {result['revenue_growth_yoy']:.4%}"
    )

    # Before the fix: profit_margin was net_income / stale_rev — overstated.
    # After the fix: it must use the real filed revenue as denominator.
    assert result["profit_margin"] is not None, (
        "profit_margin must not be None when NetIncomeLoss and revenue are present"
    )
    assert 0.0 < result["profit_margin"] < 1.0, (
        f"profit_margin = {result['profit_margin']:.4%} is implausible "
        f"(was > 1.0 before fix due to wrong revenue denominator)"
    )
    assert result["profit_margin"] == pytest.approx(expected_margin, rel=1e-6)
