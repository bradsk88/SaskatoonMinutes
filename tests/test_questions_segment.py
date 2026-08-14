"""The moment council starts talking.

After the last guest speaker, the chair turns the floor over: "Now we
move to questions for administration." ``mark_questions`` stamps that
moment on the item so the page can point the play button at what
council had to say instead of at the first speech.
"""

from app.speakers import mark_questions


def _seg(start_ms, text, end_ms=None):
    return {
        "start_ms": start_ms,
        "end_ms": end_ms if end_ms is not None else start_ms + 5000,
        "text": text,
    }


def _item(start_ms=0, end_ms=4 * 3600 * 1000, speakers=("A Speaker",)):
    return {
        "time_start_ms": start_ms,
        "time_end_ms": end_ms,
        "speakers": [{"name": n} for n in speakers],
    }


# Stamped from the real transcript of G&P 2026-08-12: the chair's turn
# came at 2:08:42, right after the last guest speaker (Andrew Keith)
# concluded. If detection logic changes, this moment must not move.
GPC_2026_08_12_QUESTIONS_MS = 7_722_919


def test_gpc_2026_08_12_questions_open_at_2_08_42():
    item = _item()
    mark_questions(item, [_seg(GPC_2026_08_12_QUESTIONS_MS,
                               "Now we move to questions for administration.")])
    assert item["questions_start_ms"] == GPC_2026_08_12_QUESTIONS_MS


def test_no_announcement_means_no_stamp():
    item = _item()
    mark_questions(item, [
        _seg(1000, "Questions for administration? Councillor Dubois."),
        _seg(2000, "Any other questions for the administration?"),
    ])
    assert "questions_start_ms" not in item


def test_announcement_outside_the_window_is_ignored():
    item = _item(start_ms=10_000, end_ms=20_000)
    mark_questions(item, [
        _seg(0, "We move to questions for administration."),
        _seg(30_000, "Moved to questions of administration."),
    ])
    assert "questions_start_ms" not in item


def test_variants_of_the_chairs_formula():
    for text in (
        "Now we move to questions for administration.",
        "We'll proceed to questions of administration.",
        "So we will turn to questions to administration now.",
    ):
        item = _item()
        mark_questions(item, [_seg(1234, text)])
        assert item["questions_start_ms"] == 1234, text


def test_an_item_with_no_speakers_keeps_no_stamp():
    # Their windows sprawl over another topic's Q&A (G&P 2026-08-12's
    # 6.3.1 is bookmarked 1:23:14-4:16:32; its reading is at 4:14) and
    # there are no speeches to skip past.
    item = _item(speakers=())
    mark_questions(item, [
        _seg(GPC_2026_08_12_QUESTIONS_MS,
             "Now we move to questions for administration."),
    ])
    assert "questions_start_ms" not in item


def test_window_override_is_respected():
    item = _item(start_ms=10_000, end_ms=20_000)
    mark_questions(
        item,
        [_seg(5_000, "Now we move to questions for administration.")],
        window=(0, 60_000),
    )
    assert item["questions_start_ms"] == 5_000
