from agents.strategist.prompts import STRATEGIST_INSTRUCTION


def test_prompt_contains_portfolio():
    assert "{positions}" in STRATEGIST_INSTRUCTION


def test_prompt_contains_all_signal_types():
    for signal_type in ["technical_signals", "fundamental_signals",
                        "sentiment_signals", "smart_money_signals"]:
        assert f"{{{signal_type}}}" in STRATEGIST_INSTRUCTION


def test_prompt_contains_memory_fields():
    assert "{memory_buffer}" in STRATEGIST_INSTRUCTION
    assert "{day_digest}" in STRATEGIST_INSTRUCTION
    assert "{thesis}" in STRATEGIST_INSTRUCTION


def test_prompt_contains_smart_money_bias_instruction():
    assert "smart money" in STRATEGIST_INSTRUCTION.lower() or "smart_money" in STRATEGIST_INSTRUCTION.lower()
    assert "bias" in STRATEGIST_INSTRUCTION.lower() or "2-3x" in STRATEGIST_INSTRUCTION or "dominate" in STRATEGIST_INSTRUCTION.lower()
