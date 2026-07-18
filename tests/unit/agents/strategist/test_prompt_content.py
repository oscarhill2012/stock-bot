"""Content guards for the strategist instruction template."""
from agents.strategist.prompts import STRATEGIST_INSTRUCTION


def test_prompt_explains_the_three_technical_reads_and_horizons():
    """The strategist prompt teaches the three technical reads and horizon precursors."""
    instr = STRATEGIST_INSTRUCTION

    assert "Reading the technical reads and analyst horizons" in instr
    # The three reads named.
    assert "reversal" in instr.lower()
    assert "Volatility regime" in instr
    assert "200d MA" in instr
    # Horizons framed as information, not a hold instruction.
    assert "horizon" in instr.lower()


def test_prompt_has_no_duplicate_holding_discipline_header():
    """Prompt hygiene: the holding-discipline guidance appears exactly once."""
    assert STRATEGIST_INSTRUCTION.count("### Holding discipline") == 1
