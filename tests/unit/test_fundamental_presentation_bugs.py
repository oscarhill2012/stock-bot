"""Tests for data-presentation bug fixes in the Fundamental analyst.

Covers three verified misrepresentation bugs where correct underlying data
was being presented incorrectly to the LLM, causing wrong signal reads:

  Bug 1 — net Form-4 dollars rendered unsigned, direction unlabelled.
  Bug 2 — cluster flags blind to dominant single buyer (conviction signal).
  Bug 3 — distorted trailing P/E with no guard / forward P/E surfacing.

These tests are written to FAIL first (TDD). The fixes in fetch.py,
extractors/fundamental.py, prompts.py, and config/analysts.json make them pass.
"""
from __future__ import annotations

from datetime import UTC, datetime

from agents.analysts.fundamental.fetch import _build_ticker_context
from contract.extractors.fundamental import _KEYS, extract_fundamental_features

# ---------------------------------------------------------------------------
# Shared fixture helpers
# ---------------------------------------------------------------------------

def _make_trade(
    *,
    side: str,
    insider_name: str,
    insider_title: str = "Director",
    shares: float,
    price_per_share: float,
    is_10b5_1: bool = False,
    is_officer: bool = False,
    transaction_code: str = "P",
    filed_at: str | None = None,
    ticker: str = "TSLA",
) -> dict:
    """Return an ``InsiderTrade.model_dump()``-compatible dict.

    Parameters
    ----------
    side:           "buy" or "sell"
    insider_name:   Filer's name (distinct names drive cluster_buy/sell flags).
    insider_title:  SEC title string for the filer.
    shares:         Number of shares transacted.
    price_per_share:
                    Price per share in USD.
    is_10b5_1:      Whether this is a pre-scheduled 10b5-1 transaction.
    is_officer:     Whether the filer holds an officer designation.
    transaction_code:
                    Form 4 Table I code (P=open-market buy, S=open-market sell).
    filed_at:       ISO 8601 string; must be within the 30-day window used
                    by the extractor relative to the test's as_of date.
                    Defaults to today's date so window filtering always passes.
    ticker:         Stock ticker symbol for the trade.
    """
    # Default filed_at to today so window filtering always keeps the trade in range.
    effective_filed_at = filed_at or datetime.now(UTC).isoformat()

    return {
        "ticker":              ticker,
        "insider_name":        insider_name,
        "insider_title":       insider_title,
        "side":                side,
        "shares":              shares,
        "price_per_share":     price_per_share,
        "is_10b5_1":           is_10b5_1,
        "is_officer":          is_officer,
        "transaction_code":    transaction_code,
        "filed_at":            effective_filed_at,
        "transaction_date":    datetime.now(UTC).date().isoformat(),
        "form_type":           "4",
        "footnote":            None,
        "is_director":         False,
        "is_ten_percent_owner": False,
    }


def _tsla_trades() -> list[dict]:
    """Return the TSLA scenario: 25 buys (one filer, CEO, ~$1 B) + 3 small sells (3 filers).

    This set should produce:
      - net_dollars ≈ +949_731_486 (net buying)
      - cluster_buying = False  (only one distinct buyer name)
      - cluster_selling = True  (three distinct seller names)
      - conviction_buy = True   (a single open-market buy above the threshold)
    """
    ceo_shares_per_trade = 160_000          # 25 trades × 160 000 shares
    ceo_price            = 237.43           # approx. price at the time

    buys = [
        _make_trade(
            side            = "buy",
            insider_name    = "Elon Musk",
            insider_title   = "Chief Executive Officer",
            shares          = ceo_shares_per_trade,
            price_per_share = ceo_price,
            is_officer      = True,
            transaction_code = "P",
        )
        for _ in range(25)
    ]

    # Three small sells by three distinct filers (each ~$16.7 M → total ~$50 M).
    sell_names  = ["Alice Smith", "Bob Jones", "Carol White"]
    sells = [
        _make_trade(
            side            = "sell",
            insider_name    = name,
            insider_title   = "SVP",
            shares          = 50_000,
            price_per_share = 237.43 * 1.4,   # higher price → slightly different amount
            is_officer      = True,
            transaction_code = "S",
        )
        for name in sell_names
    ]

    return buys + sells


# ---------------------------------------------------------------------------
# Bug 1 — signed net dollars render
# ---------------------------------------------------------------------------

class TestSignedNetDollarsRender:
    """The insider activity block must render net dollars with an explicit sign
    and a direction label so the LLM cannot misread a positive (net-buying)
    value as selling.
    """

    def _render_insider_block(self, trades: list[dict]) -> str:
        """Call ``_build_ticker_context`` with minimal data and return the block."""
        from data.models import Form4Bundle
        from data.models.trades import InsiderTrade

        # Construct an InsiderBundle-compatible object the way fetch.py uses it.
        # _build_ticker_context accepts insider_bundle: Form4Bundle | None.
        trade_models = [InsiderTrade(**t) for t in trades]
        bundle = Form4Bundle(trades=trade_models, derivatives=[])

        context = _build_ticker_context(
            ticker                    = "TSLA",
            ratios                    = None,
            filings_payload           = [],
            baseline_filings_payload  = [],
            insider_bundle            = bundle,
            insider_lookback_days     = 30,
        )
        return context

    def test_net_dollars_positive_for_net_buying(self) -> None:
        """A set of 25 large buys + 3 small sells must yield a positive net_dollars value."""
        trades = _tsla_trades()
        buys  = [t for t in trades if t["side"] == "buy"]
        sells = [t for t in trades if t["side"] == "sell"]

        buy_val  = sum(t["shares"] * t["price_per_share"] for t in buys)
        sell_val = sum(t["shares"] * t["price_per_share"] for t in sells)
        net = buy_val - sell_val

        assert net > 0, f"Expected positive net_dollars; got {net}"

    def test_net_dollars_rendered_with_explicit_sign(self) -> None:
        """Positive net buying MUST render as a string starting with '+'.

        Before the fix, ``{net_dollars:,.0f}`` emitted no sign on positive
        values, letting the LLM read +$949.7 M of buying as selling.
        """
        block = self._render_insider_block(_tsla_trades())

        # The rendered line must contain a '+' sign for the positive net value.
        assert "+" in block, (
            "Positive net Form-4 dollars must be rendered with a leading '+' sign; "
            f"got:\n{block}"
        )

    def test_net_dollars_rendered_with_direction_label(self) -> None:
        """The insider block must state the sign convention so the LLM cannot guess.

        The line must include either '+ = net buy' or equivalent direction text
        so the meaning of the sign is unambiguous in context.
        """
        block = self._render_insider_block(_tsla_trades())

        # Accept either "(+ = net buy" or "net buy" appearing in the relevant line.
        has_convention = (
            "+ = net buy" in block
            or "net buy" in block.lower()
        )
        assert has_convention, (
            "Insider block must include a sign-convention label ('+ = net buy / − = net sell' "
            f"or similar);\ngot:\n{block}"
        )

    def test_net_dollars_negative_rendered_with_minus(self) -> None:
        """A net-selling scenario must render with a '-' sign."""
        # One large sell, no buys — net should be negative.
        trades = [
            _make_trade(
                side             = "sell",
                insider_name     = "Elon Musk",
                insider_title    = "CEO",
                shares           = 1_000_000,
                price_per_share  = 250.0,
                transaction_code = "S",
            )
        ]
        block = self._render_insider_block(trades)

        # The number should appear negative (starts with '-').
        import re
        # Look for a negative formatted dollar figure on the net line.
        assert re.search(r"-[\d,]+", block), (
            "Net-selling scenario must render net dollars with a '-' sign; "
            f"got:\n{block}"
        )

    def test_prompt_contains_sign_convention_description(self) -> None:
        """The prompt template must describe the sign convention for the insider block.

        Without this, the LLM might ignore the sign even if it appears in the data.
        """
        from agents.analysts.fundamental.prompts import build_fundamental_instruction
        from agents.analysts.heuristics import FundamentalVocabulary

        vocab = FundamentalVocabulary(
            guidance         = ["raised"],
            tone             = ["confident"],
            risks            = ["regulatory"],
            insider_signals  = ["cluster_buying"],
        )
        rendered = build_fundamental_instruction(vocab)

        # The prompt must say something about the sign meaning net buy vs net sell.
        has_sign_desc = (
            "net buy" in rendered.lower()
            and "net sell" in rendered.lower()
        )
        assert has_sign_desc, (
            "Prompt must describe the sign convention for net Form-4 dollars "
            "('+' = net buy, '-' = net sell) in the INSIDER ACTIVITY data-block description"
        )


# ---------------------------------------------------------------------------
# Bug 2 — conviction signal: dominant single buyer
# ---------------------------------------------------------------------------

class TestConvictionBuySignal:
    """A single senior buyer above the conviction threshold must set conviction_buy=True,
    even when cluster_buying is False (one distinct name, not three).
    """

    def _extract(self, trades: list[dict]) -> dict:
        """Run the feature extractor with a minimal payload and the TSLA trade set."""
        raw = {
            "ratios":                    None,
            "filings":                   [],
            "insider_trades":            trades,
            "insider_derivative_trades": [],
        }
        return extract_fundamental_features(raw, ticker="TSLA")

    def test_cluster_buying_false_for_single_buyer(self) -> None:
        """25 buys by ONE distinct filer must NOT trigger the cluster_buying flag.

        This proves the additive nature of the conviction signal — it fills
        a gap left by the cluster flag, not a replacement for it.
        """
        features = self._extract(_tsla_trades())
        assert features["insider_cluster_buy_flag"] == 0.0, (
            "cluster_buy_flag should be 0.0 for a single-filer scenario; "
            f"got {features['insider_cluster_buy_flag']}"
        )

    def test_cluster_selling_true_for_three_sellers(self) -> None:
        """3 distinct sellers must still set cluster_sell=True (unchanged behaviour)."""
        features = self._extract(_tsla_trades())
        assert features["insider_cluster_sell_flag"] == 1.0, (
            "cluster_sell_flag should be 1.0 for three distinct sellers; "
            f"got {features['insider_cluster_sell_flag']}"
        )

    def test_conviction_buy_true_for_large_single_buy(self) -> None:
        """A CEO buying ~$950 M (well above any sane threshold) must set conviction_buy=1.0."""
        features = self._extract(_tsla_trades())
        assert "insider_conviction_buy_flag" in features, (
            "Feature 'insider_conviction_buy_flag' is missing from the extractor output; "
            "it must be added as a new feature column"
        )
        assert features["insider_conviction_buy_flag"] == 1.0, (
            "conviction_buy_flag must be 1.0 for a ~$950 M single-filer CEO buy; "
            f"got {features['insider_conviction_buy_flag']}"
        )

    def test_conviction_buy_false_below_threshold(self) -> None:
        """A small purchase below the conviction threshold must NOT set conviction_buy."""
        # Use a tiny purchase: 10 shares × $100 = $1 000 — well below any threshold.
        trades = [
            _make_trade(
                side             = "buy",
                insider_name     = "Minor Director",
                insider_title    = "Director",
                shares           = 10,
                price_per_share  = 100.0,
                is_officer       = False,
                transaction_code = "P",
            )
        ]
        features = self._extract(trades)
        assert features["insider_conviction_buy_flag"] == 0.0, (
            "conviction_buy_flag must be 0.0 for a tiny $1 000 purchase; "
            f"got {features['insider_conviction_buy_flag']}"
        )

    def test_conviction_sell_true_for_large_single_sell(self) -> None:
        """A large discretionary sell must set conviction_sell=1.0 (symmetric signal)."""
        trades = [
            _make_trade(
                side             = "sell",
                insider_name     = "Rich CFO",
                insider_title    = "CFO",
                shares           = 2_000_000,
                price_per_share  = 300.0,   # $600 M — above threshold
                is_officer       = True,
                transaction_code = "S",
            )
        ]
        features = self._extract(trades)
        assert "insider_conviction_sell_flag" in features, (
            "Feature 'insider_conviction_sell_flag' is missing"
        )
        assert features["insider_conviction_sell_flag"] == 1.0, (
            "conviction_sell_flag must be 1.0 for a $600 M single-filer sell; "
            f"got {features['insider_conviction_sell_flag']}"
        )

    def test_conviction_sell_false_below_threshold(self) -> None:
        """A tiny sell must NOT set conviction_sell."""
        trades = [
            _make_trade(
                side             = "sell",
                insider_name     = "Minor Seller",
                insider_title    = "Director",
                shares           = 5,
                price_per_share  = 50.0,
                is_officer       = False,
                transaction_code = "S",
            )
        ]
        features = self._extract(trades)
        assert features["insider_conviction_sell_flag"] == 0.0, (
            "conviction_sell_flag must be 0.0 for a tiny $250 sell"
        )

    def test_conviction_buy_flag_in_keys(self) -> None:
        """'insider_conviction_buy_flag' must appear in the locked _KEYS catalogue."""
        assert "insider_conviction_buy_flag" in _KEYS, (
            "'insider_conviction_buy_flag' missing from _KEYS — "
            "it must be added to the locked feature catalogue"
        )

    def test_conviction_sell_flag_in_keys(self) -> None:
        """'insider_conviction_sell_flag' must appear in the locked _KEYS catalogue."""
        assert "insider_conviction_sell_flag" in _KEYS, (
            "'insider_conviction_sell_flag' missing from _KEYS"
        )

    def test_conviction_buy_rendered_in_insider_block(self) -> None:
        """The conviction_buy flag must appear in the rendered insider activity block."""
        from data.models import Form4Bundle
        from data.models.trades import InsiderTrade

        trade_models = [InsiderTrade(**t) for t in _tsla_trades()]
        bundle = Form4Bundle(trades=trade_models, derivatives=[])

        block = _build_ticker_context(
            ticker                   = "TSLA",
            ratios                   = None,
            filings_payload          = [],
            baseline_filings_payload = [],
            insider_bundle           = bundle,
            insider_lookback_days    = 30,
        )

        assert "conviction_buy" in block.lower(), (
            "Rendered insider block must include the conviction_buy flag; "
            f"got:\n{block}"
        )

    def test_config_key_read_from_analysts_json(self) -> None:
        """The conviction threshold must be read from config/analysts.json, not hardcoded."""
        from config.analysts import get_analysts_config
        cfg = get_analysts_config()

        # The FundamentalCaps object must expose the conviction threshold setting.
        assert hasattr(cfg.fundamental, "insider_conviction_threshold_dollars"), (
            "FundamentalCaps must have 'insider_conviction_threshold_dollars' field; "
            "threshold must come from config/analysts.json, not a hardcoded constant"
        )
        # The threshold must be a positive number.
        assert cfg.fundamental.insider_conviction_threshold_dollars > 0


# ---------------------------------------------------------------------------
# Bug 3 — distorted trailing P/E guard
# ---------------------------------------------------------------------------

class TestPeDistortionGuard:
    """When trailing P/E exceeds the implausibility threshold, the render must
    surface forward P/E (if available) and flag the trailing as suspect.
    """

    def _render_ratios(self, ratios: dict) -> str:
        """Return the rendered context block with only the ratios section populated."""
        context = _build_ticker_context(
            ticker                   = "AMD",
            ratios                   = ratios,
            filings_payload          = [],
            baseline_filings_payload = [],
            insider_bundle           = None,
            insider_lookback_days    = 30,
        )
        return context

    def test_high_trailing_pe_with_forward_pe_surfaces_forward(self) -> None:
        """When trailing P/E is implausibly high and forward P/E is available,
        the ratios block must prominently surface forward P/E.
        """
        ratios = {
            "trailing_pe": 676.0,   # AMD export-control distortion
            "forward_pe":  28.5,    # reasonable forward estimate
        }
        block = self._render_ratios(ratios)

        # Forward P/E must appear in the rendered output.
        assert "28.5" in block or "Forward P/E" in block, (
            "When trailing P/E is distorted, forward P/E must be surfaced; "
            f"got:\n{block}"
        )

    def test_high_trailing_pe_with_forward_pe_flagged_as_distorted(self) -> None:
        """When trailing P/E is implausibly high, the block must flag it as
        possibly distorted by a one-time EPS item.
        """
        ratios = {
            "trailing_pe": 676.0,
            "forward_pe":  28.5,
        }
        block = self._render_ratios(ratios)

        distortion_signal = (
            "distort" in block.lower()
            or "one-time" in block.lower()
            or "suspect" in block.lower()
        )
        assert distortion_signal, (
            "When trailing P/E exceeds the implausibility threshold and forward P/E "
            "is present, the render must flag the trailing P/E as possibly distorted; "
            f"got:\n{block}"
        )

    def test_high_trailing_pe_without_forward_pe_flagged_suspect(self) -> None:
        """When trailing P/E is implausibly high and forward P/E is absent,
        the trailing must still be flagged as suspect — no crash.
        """
        ratios = {
            "trailing_pe": 676.0,
            # No forward_pe key at all.
        }
        block = self._render_ratios(ratios)

        suspect_signal = (
            "suspect" in block.lower()
            or "distort" in block.lower()
            or "one-time" in block.lower()
        )
        assert suspect_signal, (
            "When trailing P/E is implausibly high and forward P/E is absent, "
            "the trailing must be flagged as suspect; "
            f"got:\n{block}"
        )

    def test_normal_trailing_pe_not_flagged(self) -> None:
        """A normal (below-threshold) trailing P/E must NOT trigger the distortion flag."""
        ratios = {
            "trailing_pe": 25.0,
            "forward_pe":  20.0,
        }
        block = self._render_ratios(ratios)

        # A normal P/E must not contain alarmist distortion language.
        assert "distort" not in block.lower(), (
            "Normal trailing P/E must not be flagged as distorted; "
            f"got:\n{block}"
        )

    def test_pe_threshold_read_from_config(self) -> None:
        """The implausibility threshold must come from config/analysts.json."""
        from config.analysts import get_analysts_config
        cfg = get_analysts_config()

        assert hasattr(cfg.fundamental, "trailing_pe_implausibility_threshold"), (
            "FundamentalCaps must have 'trailing_pe_implausibility_threshold'; "
            "it must come from config/analysts.json, not a hardcoded constant"
        )
        assert cfg.fundamental.trailing_pe_implausibility_threshold > 0

    def test_prompt_contains_pe_distortion_cue(self) -> None:
        """The prompt must mention that a very high trailing P/E may be distorted."""
        from agents.analysts.fundamental.prompts import build_fundamental_instruction
        from agents.analysts.heuristics import FundamentalVocabulary

        vocab = FundamentalVocabulary(
            guidance        = ["raised"],
            tone            = ["confident"],
            risks           = ["regulatory"],
            insider_signals = ["cluster_buying"],
        )
        rendered = build_fundamental_instruction(vocab)

        # The prompt must cue the model to discount a suspicious trailing P/E.
        has_cue = (
            "distort" in rendered.lower()
            or "one-time" in rendered.lower()
            or "implaus" in rendered.lower()
        )
        assert has_cue, (
            "Prompt must cue the LLM that a very high trailing P/E may be "
            "distorted by a one-time EPS item; "
            "expected 'distort', 'one-time', or 'implaus' in rendered prompt"
        )
