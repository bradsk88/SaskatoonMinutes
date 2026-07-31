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


class TestDescriptionBullets:
    """The Description is bullets, so the eval measures bullets."""

    def _report(self, description):
        report = eval_chips.Report()
        report.add_item(
            "m1", {"item_id": "1", "title": "Proposed Rezoning", "section_number": "8.1"},
            [], description=description,
        )
        return report

    def test_a_stored_paragraph_counts_as_one_bullet(self):
        report = self._report("Rezones 902-938 3rd Avenue North to residential.")
        assert report.descriptions == 1
        assert report.bullets == 1

    def test_the_overrun_budget_is_the_whole_description(self):
        """The reader's cost is the block, not the line: four bullets of
        60 characters overruns even though no single bullet does."""
        long_bullets = ["x" * 60] * 4
        assert sum(len(b) for b in long_bullets) > eval_chips.MAX_DESCRIPTION_CHARS
        assert self._report(long_bullets).description_overruns == 1

    def test_bullets_inside_the_budget_do_not_overrun(self):
        assert self._report(["Rezones 3rd Avenue North", "Allows 83 units"]).description_overruns == 0

    def test_a_bullet_that_continues_the_one_above_is_counted(self):
        """The sentence-chopping failure: one fact wearing four hats."""
        report = self._report([
            "City rezones 3rd Avenue North properties",
            "It shifts from industrial to residential use",
            "This allows for higher-density housing",
        ])
        assert report.description_continuations == 1

    def test_distinct_bullets_are_not_flagged(self):
        report = self._report([
            "New 83-unit apartment building gets five-year tax breaks",
            "Former transit building becomes theatre, studios and retail",
        ])
        assert report.description_continuations == 0

    def test_a_first_bullet_may_open_with_this(self):
        """Only a bullet that needs the one above it is a continuation."""
        assert self._report(["This year's budget rises 4.2%"]).description_continuations == 0


class TestDescriptionDiff:
    def test_a_paragraph_and_its_one_bullet_form_are_unchanged(self):
        """The shape changed on disk before the words did; a diff that
        reports every item as changed tells the reader nothing."""
        base = {"a": {**entry("8.1", "Rezoning", []), "description": "Rezones the lots."}}
        curr = {"a": {**entry("8.1", "Rezoning", []), "description": ["Rezones the lots."]}}
        out = eval_chips.render_diff(base, curr)
        assert "0 items changed, 1 unchanged" in out

    def test_changed_bullets_are_shown_on_one_line_each_side(self):
        base = {"a": {**entry("8.1", "Rezoning", []), "description": ["Old fact"]}}
        curr = {"a": {**entry("8.1", "Rezoning", []),
                      "description": ["New fact", "Second fact"]}}
        out = eval_chips.render_diff(base, curr)
        assert "- _description_ — Old fact" in out
        assert "+ _description_ — New fact · Second fact" in out


class TestContinuationGate:
    """The gate is a share, so one unlucky sample does not turn CI red."""

    def _report(self, n_items: int, n_continuing: int):
        report = eval_chips.Report()
        for i in range(n_items):
            description = ["City rezones 3rd Avenue North properties"]
            if i < n_continuing:
                description.append("It shifts to residential use")
            report.add_item(
                "m1",
                {"item_id": str(i), "title": "Rezoning", "section_number": "8.1"},
                [], description=description,
            )
        return report

    def test_one_slip_in_twenty_four_passes(self):
        report = self._report(24, 1)
        assert report.continuation_share <= eval_chips.MAX_DESCRIPTION_CONTINUATION

    def test_a_prompt_that_chops_sentences_fails(self):
        report = self._report(24, 4)
        assert report.continuation_share > eval_chips.MAX_DESCRIPTION_CONTINUATION
