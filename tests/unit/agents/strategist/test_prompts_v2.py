"""Strategist v2 prompt tests — Tier 1, no LLM.

Covers:
- Slot presence (all ADK runtime placeholders reachable).
- Three-verb contract (buy / sell / update).
- Selective-output rule (first-tick mandate + silence = hold).
- Forbidden-field guidance present.
- Build-time cap substitution (MAX_BUY_DELTA, MAX_BUY_DELTA_PCT).
- Legacy vocabulary absent.
- Full render smoke test.
"""
from __future__ import annotations

from agents.strategist.prompts import STRATEGIST_INSTRUCTION


# ---------------------------------------------------------------------------
# Slot presence — runtime placeholders the context_shim / ADK must populate.
# ---------------------------------------------------------------------------

def test_template_has_held_positions_slot():
    """A2.6: prompt template uses temp:-prefixed placeholder."""
    assert "{temp:held_positions_view}" in STRATEGIST_INSTRUCTION


def test_template_has_ticker_evidence_slot():
    """A2.6: prompt template uses temp:-prefixed placeholder."""
    assert "{temp:ticker_evidence}" in STRATEGIST_INSTRUCTION


def test_template_has_first_tick_flag_slot():
    """Task 8: FIRST_TICK_FLAG placeholder present for Task 9 wiring."""
    assert "{temp:first_tick_flag}" in STRATEGIST_INSTRUCTION


def test_template_has_state_slots():
    """Every state slot the context_shim callback must populate is present.

    Note: ``{tickers}`` now appears once in the template (in the "Your Job"
    section).  The previous trailing ``Watchlist: {tickers}`` line was a
    duplicate carried over from the v1 template.  Substring checks alone
    cannot verify a slot is wired correctly; the runtime guard is the
    ``.format(...)`` call in ``test_template_renders_with_all_required_slots``
    which raises ``KeyError`` if any slot is missing.
    """
    assert "{portfolio}" in STRATEGIST_INSTRUCTION
    assert "{memory_buffer}" in STRATEGIST_INSTRUCTION
    assert "{day_digest}" in STRATEGIST_INSTRUCTION
    assert "{thesis}" in STRATEGIST_INSTRUCTION
    assert "{tickers}" in STRATEGIST_INSTRUCTION


def test_template_no_longer_has_legacy_signal_slots():
    """Legacy four-list dump replaced by single ticker_evidence block."""
    assert "{technical_signals}" not in STRATEGIST_INSTRUCTION
    assert "{fundamental_signals}" not in STRATEGIST_INSTRUCTION
    assert "{sentiment_signals}" not in STRATEGIST_INSTRUCTION
    assert "{smart_money_signals}" not in STRATEGIST_INSTRUCTION


def test_template_no_longer_has_active_positions_dump():
    """Old v1 flat-list positions slot is gone."""
    assert "Active Positions: {positions}" not in STRATEGIST_INSTRUCTION


# ---------------------------------------------------------------------------
# Three-verb contract — buy / sell / update.
# ---------------------------------------------------------------------------

def test_template_has_three_verb_table():
    """The OUTPUT CONTRACT table must name all three verbs."""
    text = STRATEGIST_INSTRUCTION

    assert "| buy" in text,    "buy verb row missing from contract table"
    assert "| sell" in text,   "sell verb row missing from contract table"
    assert "| update" in text, "update verb row missing from contract table"


def test_template_documents_buy_fields():
    """buy verb requires weight + rationale; catalyst is optional."""
    text = STRATEGIST_INSTRUCTION
    assert "rationale" in text
    assert "weight" in text
    assert "catalyst" in text


def test_template_documents_sell_fields():
    """sell verb requires reason; weight is optional (omit for full close)."""
    text = STRATEGIST_INSTRUCTION
    assert "reason" in text


def test_template_documents_update_fields():
    """update verb requires reason; no weight/rationale/catalyst allowed."""
    text = STRATEGIST_INSTRUCTION
    # Forbidden-field guidance must be explicit so the model knows not to emit
    # those fields on an update stance.
    assert "update" in text
    assert "no ``weight``" in text


def test_template_mentions_forbidden_fields_by_verb():
    """The prompt explicitly calls out which fields are forbidden per verb."""
    text = STRATEGIST_INSTRUCTION
    # buy must not include reason
    assert "no ``reason``" in text
    # sell must not include rationale
    assert "no ``rationale``" in text


def test_template_drops_old_structural_fields_as_requirements():
    """target_price, stop_price, horizon must NOT appear as required schema fields.

    The three-verb schema rewrite removed these fields from the schema.
    They may appear in "forbidden" guidance (telling the model NOT to emit
    them), but must not appear in the JSON example, in the contract table as
    required/optional fields, or in field-constraint bullets that imply they
    are valid outputs.

    The "ALL verbs: no target_price, stop_price, horizon" line in the
    forbidden-fields section is intentional — it explicitly tells the model
    these fields are gone, which is the correct way to prevent the model from
    emitting them.
    """
    text = STRATEGIST_INSTRUCTION

    # The JSON example must not demonstrate these fields (that would imply
    # they are valid outputs and teach the model to emit them).
    assert '"target_price"' not in text, \
        '"target_price" must not appear as a JSON key in the example'
    assert '"stop_price"' not in text, \
        '"stop_price" must not appear as a JSON key in the example'
    assert '"horizon"' not in text, \
        '"horizon" must not appear as a JSON key in the example'

    # The field-constraints section must not list them as schema-enforced
    # fields (e.g. "- horizon: one of ...").
    assert "- horizon:" not in text, \
        "horizon must not appear as a field-constraint bullet"
    assert "- target_price" not in text, \
        "target_price must not appear as a field-constraint bullet"
    assert "- stop_price" not in text, \
        "stop_price must not appear as a field-constraint bullet"


def test_template_drops_old_six_verb_vocabulary():
    """Legacy verbs open / add / trim / close / hold must not appear in the
    OUTPUT CONTRACT table.

    These were the six verbs of the previous schema.  The new schema uses
    buy / sell / update.  Old verb names in the contract table would
    re-introduce the dual-vocabulary ambiguity.
    """
    text = STRATEGIST_INSTRUCTION
    # Check they are absent from the intent column of the contract table.
    assert "| open" not in text,  "legacy verb 'open' still in table"
    assert "| add" not in text,   "legacy verb 'add' still in table"
    assert "| trim" not in text,  "legacy verb 'trim' still in table"
    assert "| close" not in text, "legacy verb 'close' still in table"
    assert "| hold" not in text,  "legacy verb 'hold' still in table"


# ---------------------------------------------------------------------------
# Selective-output rule — first-tick mandate + silence = hold.
# ---------------------------------------------------------------------------

def test_template_has_selective_output_rule():
    """The prompt must communicate that silence means 'no change' after tick 1."""
    text = STRATEGIST_INSTRUCTION
    assert "Silence is a valid response" in text


def test_template_has_first_tick_mandate():
    """First tick of a window requires a stance for every watchlist ticker."""
    text = STRATEGIST_INSTRUCTION
    # The phrase "First tick of a window" introduces the mandate.
    assert "First tick of a window" in text
    # Baseline establishment is the stated purpose.
    assert "baseline view" in text


# ---------------------------------------------------------------------------
# Build-time cap substitution.
# ---------------------------------------------------------------------------

def test_max_buy_delta_substituted():
    """{{MAX_BUY_DELTA}} must resolve to the float string '0.05'."""
    # No raw marker should survive build-time substitution.
    assert "{{MAX_BUY_DELTA}}" not in STRATEGIST_INSTRUCTION
    # The resolved value must appear somewhere in the rendered template.
    assert "0.05" in STRATEGIST_INSTRUCTION


def test_max_buy_delta_pct_substituted():
    """{{MAX_BUY_DELTA_PCT}} must resolve to '5'."""
    assert "{{MAX_BUY_DELTA_PCT}}" not in STRATEGIST_INSTRUCTION
    assert "5" in STRATEGIST_INSTRUCTION


def test_no_unreplaced_cap_markers():
    """All {{NAME}} build-time markers must have been substituted.

    The raw template uses ``{{NAME}}`` for build-time substitution (e.g.
    ``{{MAX_BUY_DELTA_PCT}}``) and also uses ``{{`` / ``}}`` for literal
    braces in the JSON example (so ADK's runtime ``.format()`` pass sees
    ``{`` / ``}`` rather than format errors).

    This test therefore checks for the specific unreplaced-marker pattern:
    ``{{`` followed by an uppercase identifier.  Bare ``{{`` without an
    identifier suffix are intentional escaped braces in the JSON block and
    must be left alone.
    """
    import re
    # Pattern: {{ followed by one or more uppercase letters/underscores/digits,
    # indicating a cap marker that was not substituted.
    unreplaced = re.findall(r"\{\{[A-Z][A-Z0-9_]+\}\}", STRATEGIST_INSTRUCTION)
    assert unreplaced == [], (
        f"Unreplaced build-time marker(s) found: {unreplaced} — "
        "add the missing .replace() call in the substitution block."
    )


# ---------------------------------------------------------------------------
# Legacy vocabulary absent.
# ---------------------------------------------------------------------------

def test_no_preferred_weight_field():
    """Legacy 'preferred_weight' field must not appear."""
    assert "preferred_weight" not in STRATEGIST_INSTRUCTION


def test_no_conviction_json_key():
    """Legacy 'conviction' JSON key must not appear.

    The word 'conviction' may appear in English prose (e.g. 'when conviction
    supports it'), so we check for the JSON key form with quotes.
    """
    assert '"conviction"' not in STRATEGIST_INSTRUCTION


# ---------------------------------------------------------------------------
# Full render smoke test — all runtime slots filled.
# ---------------------------------------------------------------------------

def test_template_renders_with_all_required_slots():
    """Smoke test — the template must fill cleanly with all required slot values.

    Python's ``str.format`` / ``str.format_map`` both interpret the colon in
    ``temp:key`` as the field/format-spec separator, so neither can fill
    ``temp:``-prefixed keys directly.

    Workaround: use ``str.replace`` to substitute the ``temp:``-prefixed
    placeholders first, then call ``.format()`` in the normal way.  Any
    *missing* slot still raises ``KeyError`` before the assertions execute.
    """
    # Pre-substitute all temp:-prefixed slots so .format() can handle them.
    template = (
        STRATEGIST_INSTRUCTION
        .replace("{temp:strategist_mode}",     "Cold start — your portfolio is empty.")
        .replace("{temp:held_positions_view}", "(No held positions — portfolio is flat.)")
        .replace("{temp:ticker_evidence}",     "AAPL\n  Aggregate: bullish (magnitude 0.42)")
        .replace("{temp:recent_trades_view}",  "(No closed positions yet this run.)")
        .replace("{temp:_last_schema_error}",  "")
        # Task 8 — FIRST_TICK_FLAG slot; Task 9 will wire this from context_shim.
        .replace("{temp:first_tick_flag}",     "True")
    )
    rendered = template.format(
        portfolio="cash=100, positions={}",
        memory_buffer="[]",
        day_digest="(empty)",
        thesis="(empty)",
        tickers="['AAPL','MSFT']",
    )

    # Basic sanity checks on the rendered output.
    assert "No held positions" in rendered
    assert "AAPL" in rendered
    # The first-tick flag value must appear after substitution.
    assert "True" in rendered
    # Three-verb contract must survive rendering intact.
    assert "buy" in rendered
    assert "sell" in rendered
    assert "update" in rendered
