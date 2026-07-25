"""Tests for scripts/roster_attractors.py — the primary cleanup metric.

The metric decides whether the cleanup pass survives, so its own failure
modes matter: a name counted when a source did contain it, a name missed
because of an accent, or one substitution reported twice would each move
the count the decision rule reads.
"""

from scripts.roster_attractors import dedupe, mentions, roster_terms, scan


def _pair(key: str, summary_a: str, summary_b: str, **source) -> dict:
    return {
        "key": key,
        "section_number": "5.1",
        "title": "An item",
        "recommendation": source.get("recommendation", ""),
        "content": source.get("content", ""),
        "transcript": source.get("transcript", ""),
        "A": {"description": summary_a, "chips": []},
        "B": {"description": summary_b, "chips": []},
    }


KEY = {"m/1": {"A": "clean", "B": "raw"}}


class TestRosterTerms:
    def test_titles_are_stripped_so_a_bare_surname_matches(self):
        terms = roster_terms()
        assert "Cynthia Block" in terms
        assert not any(term.startswith("Councillor ") for term in terms)

    def test_surnames_that_are_ordinary_words_are_not_terms_alone(self):
        """"Ford" in a summary is not a claim about Councillor Scott Ford."""
        assert "Ford" not in roster_terms()
        assert "Scott Ford" in roster_terms()

    def test_local_vocabulary_survives(self):
        terms = roster_terms()
        assert "Meewasin Valley Authority" in terms
        assert "Remai Modern" in terms


class TestMentions:
    def test_accents_do_not_hide_a_name(self):
        assert mentions("Métis", "the Metis Nation spoke")

    def test_substrings_of_longer_words_do_not_count(self):
        assert not mentions("Cree", "the screen was replaced")


class TestScan:
    def test_a_name_absent_from_every_source_is_flagged(self):
        hits = scan([_pair("m/1", "Remai Modern wrote in.", "A neighbour wrote in.",
                           transcript="Remly wrote in.")], KEY)
        assert [(h["term"], h["arm"]) for h in hits] == [("Remai Modern", "clean")]

    def test_a_name_the_official_text_contains_is_not_flagged(self):
        hits = scan([_pair("m/1", "Councillor Dubois asked.", "",
                           content="Councillor Bev Dubois asked a question.",
                           transcript="")], KEY)
        assert hits == []

    def test_a_name_the_raw_transcript_contains_is_not_flagged(self):
        hits = scan([_pair("m/1", "Meewasin Valley Authority presented.", "",
                           transcript="the Meewasin Valley Authority presented")],
                    KEY)
        assert hits == []

    def test_the_raw_arm_is_reported_under_its_own_label(self):
        hits = scan([_pair("m/1", "", "Nutana residents objected.",
                           transcript="Nutanic residents objected.")], KEY)
        assert [h["arm"] for h in hits] == ["raw"]

    def test_the_nearest_raw_tokens_come_back_for_the_human_ruling(self):
        hits = scan([_pair("m/1", "Caswell Hill was named.", "",
                           transcript="the Casual Hill area was named")], KEY)
        assert "Casual Hill" in hits[0]["nearest_raw"]


class TestDedupe:
    def test_one_substitution_is_reported_once(self):
        """The roster yields both "Remai Modern" and "Modern"."""
        hits = dedupe([
            {"key": "m/1", "arm": "clean", "term": "Remai Modern"},
            {"key": "m/1", "arm": "clean", "term": "Modern"},
        ])
        assert [h["term"] for h in hits] == ["Remai Modern"]

    def test_the_same_name_in_both_arms_stays_two_flags(self):
        hits = dedupe([
            {"key": "m/1", "arm": "clean", "term": "Nutana"},
            {"key": "m/1", "arm": "raw", "term": "Nutana"},
        ])
        assert len(hits) == 2
