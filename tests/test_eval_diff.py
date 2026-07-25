"""Tests for the eval baseline/diff loop in scripts/eval_chips.py.

The diff is the collaboration surface for prompt work, so its accounting
has to be trustworthy: a structural change (a category appearing or
vanishing) must not be reported as a reword, and an unchanged item must
report as unchanged.
"""

import importlib.util
import os

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "eval_chips",
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "scripts", "eval_chips.py",
    ),
)
eval_chips = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(eval_chips)


def entry(section: str, title: str, chips: list[tuple[str, str]]) -> dict:
    return {
        "section_number": section,
        "title": title,
        "chips": [{"category": c, "text": t} for c, t in chips],
    }


class TestImportIsSideEffectFree:
    def test_importing_does_not_load_the_dotenv_key(self):
        """Importing this module must not put a live API key in os.environ.

        It used to call load_dotenv() at module scope, which meant any test
        that imported it silently gave every other test a real
        GEMINI_API_KEY — and those tests started calling Gemini for real.
        """
        assert not hasattr(eval_chips, "load_dotenv")


class TestRenderDiff:
    def test_identical_runs_report_no_change(self):
        results = {"m/1": entry("5.1", "A report", [("Outcome", "Approved")])}
        out = eval_chips.render_diff(results, results)
        assert "**0 items changed, 1 unchanged**" in out

    def test_a_gained_category_is_structural(self):
        before = {"m/1": entry("5.1", "A report", [("Outcome", "Approved")])}
        after = {"m/1": entry("5.1", "A report", [
            ("Outcome", "Approved"), ("Vote Breakdown", "5 for, 0 against"),
        ])}
        out = eval_chips.render_diff(before, after)
        assert "categories gained: Vote Breakdown ×1" in out
        assert "categories lost" not in out
        assert "reworded" not in out

    def test_a_lost_category_is_structural(self):
        before = {"m/1": entry("5.1", "A", [
            ("Outcome", "Approved"), ("Equity Impact", "Helps residents"),
        ])}
        after = {"m/1": entry("5.1", "A", [("Outcome", "Approved")])}
        out = eval_chips.render_diff(before, after)
        assert "categories lost: Equity Impact ×1" in out

    def test_same_category_different_text_is_a_reword(self):
        before = {"m/1": entry("5.1", "A", [("Outcome", "Approved")])}
        after = {"m/1": entry("5.1", "A", [("Outcome", "Adopted")])}
        out = eval_chips.render_diff(before, after)
        assert "1 chips reworded" in out
        assert "categories gained" not in out
        assert "categories lost" not in out

    def test_reword_shows_both_sides(self):
        before = {"m/1": entry("5.1", "A", [("Outcome", "Approved")])}
        after = {"m/1": entry("5.1", "A", [("Outcome", "Adopted")])}
        out = eval_chips.render_diff(before, after)
        assert "- **Outcome** — Approved" in out
        assert "+ **Outcome** — Adopted" in out

    def test_a_new_item_is_reported_as_new(self):
        after = {"m/2": entry("5.2", "Fresh fixture", [("Outcome", "Approved")])}
        out = eval_chips.render_diff({}, after)
        assert "+ NEW 5.2 Fresh fixture" in out
        assert "**1 items changed, 0 unchanged**" in out

    def test_a_removed_item_is_reported_as_gone(self):
        before = {"m/1": entry("5.1", "Dropped fixture", [("Outcome", "Approved")])}
        out = eval_chips.render_diff(before, {})
        assert "GONE 5.1 Dropped fixture" in out

    def test_unchanged_items_are_not_printed(self):
        before = {
            "m/1": entry("5.1", "Quiet item", [("Outcome", "Approved")]),
            "m/2": entry("5.2", "Noisy item", [("Outcome", "Approved")]),
        }
        after = {
            "m/1": entry("5.1", "Quiet item", [("Outcome", "Approved")]),
            "m/2": entry("5.2", "Noisy item", [("Outcome", "Defeated")]),
        }
        out = eval_chips.render_diff(before, after)
        assert "Quiet item" not in out
        assert "Noisy item" in out
        assert "**1 items changed, 1 unchanged**" in out

    def test_categories_are_ordered_canonically(self):
        """Chips read in the same order as the canonical category list."""
        before = {"m/1": entry("5.1", "A", [])}
        after = {"m/1": entry("5.1", "A", [
            ("Equity Impact", "e"), ("Outcome", "o"), ("Vote Breakdown", "v"),
        ])}
        out = eval_chips.render_diff(before, after)
        order = [
            out.index("**Outcome**"),
            out.index("**Vote Breakdown**"),
            out.index("**Equity Impact**"),
        ]
        assert order == sorted(order)


class TestBaselineRoundTrip:
    def test_save_then_load_returns_the_same_results(self, tmp_path, monkeypatch):
        path = tmp_path / "baseline.json"
        monkeypatch.setattr(eval_chips, "BASELINE_PATH", str(path))
        results = {"m/1": entry("5.1", "A report", [("Outcome", "Approved")])}
        eval_chips.save_baseline(results)
        assert eval_chips.load_baseline() == results

    def test_missing_baseline_loads_as_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            eval_chips, "BASELINE_PATH", str(tmp_path / "absent.json")
        )
        assert eval_chips.load_baseline() is None


class TestTitleEcho:
    """The echo heuristic is what --check gates on, so it needs to hold."""

    @pytest.mark.parametrize("chip", [
        "Saskatoon Homelessness Action Plan",
        "Approved: Saskatoon Homelessness Action Plan",
    ])
    def test_restating_the_title_is_an_echo(self, chip):
        assert eval_chips.is_title_echo(chip, "Saskatoon Homelessness Action Plan 2026")

    def test_a_real_description_is_not_an_echo(self):
        assert not eval_chips.is_title_echo(
            "Council extended the emergency shelter at 210 Pacific Ave until May 2027",
            "210 Pacific Avenue – Extension of Timeframe for Emergency Residential Shelter",
        )
