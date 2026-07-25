"""Tests for scripts/ab_judge.py — arm aggregation for the cleanup A/B.

The bug these pin cost a reversed conclusion: one arm of one item failed
its judge call, the surviving arm was averaged in unpaired, and the
headline faithfulness delta changed sign on a 9-item sample.
"""

from scripts.ab_judge import pair_up


def _verdict(faithfulness: int) -> dict:
    return {
        "faithfulness": faithfulness,
        "specificity": 5,
        "non_redundancy": 5,
    }


class TestPairUp:
    def test_keeps_items_scored_under_both_arms(self):
        scored = {"m/1": {"clean": _verdict(5), "raw": _verdict(3)}}
        paired, dropped = pair_up(scored)
        assert set(paired) == {"m/1"}
        assert dropped == 0

    def test_drops_an_item_missing_one_arm(self):
        """An unpaired arm has nothing to compare against — it cannot vote."""
        scored = {
            "m/1": {"clean": _verdict(5), "raw": _verdict(3)},
            "m/2": {"raw": _verdict(5)},
        }
        paired, dropped = pair_up(scored)
        assert set(paired) == {"m/1"}
        assert dropped == 1

    def test_an_unpaired_arm_cannot_swing_the_mean(self):
        """The regression, stated as arithmetic.

        Without pairing, m/2's lone raw=5 lifts raw's mean above clean's
        and inverts the result.
        """
        scored = {
            "m/1": {"clean": _verdict(4), "raw": _verdict(2)},
            "m/2": {"raw": _verdict(5)},
        }
        paired, _ = pair_up(scored)
        clean = [v["clean"]["faithfulness"] for v in paired.values()]
        raw = [v["raw"]["faithfulness"] for v in paired.values()]
        assert sum(clean) / len(clean) > sum(raw) / len(raw)

    def test_no_items_is_not_a_crash(self):
        assert pair_up({}) == ({}, 0)
