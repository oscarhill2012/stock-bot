"""``apply_slack`` — single shared implementation of the slack-headroom calc."""
import pytest

from config._slack import apply_slack


def test_apply_slack_uses_integer_math_to_dodge_fp_inconsistency():
    """200 * 1.10 in FP would be 220.00000000000003; integer math must give 220 exactly."""
    assert apply_slack(200, 10) == 220
    assert apply_slack(600, 10) == 660
    assert apply_slack(160, 10) == 176


def test_apply_slack_with_zero_headroom_is_identity():
    assert apply_slack(200, 0) == 200
    assert apply_slack(1, 0) == 1


def test_apply_slack_rounds_up_on_remainder():
    """1 * 1.10 = 1.10 → ceil is 2. The ``+99`` term forces ceiling division."""
    assert apply_slack(1, 10) == 2


def test_apply_slack_rejects_negative_slack():
    """Negative headroom would shrink the cap and silently truncate output."""
    with pytest.raises(ValueError):
        apply_slack(100, -1)
