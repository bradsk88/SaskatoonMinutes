"""Tests for app.clean_transcript_cache — fingerprint-gated CleanTranscripts."""

import os

from app.cache import InMemoryCache, LocalDirCache
from app.clean_transcript_cache import CleanTranscriptCache


def cache(fingerprint: str, inner=None) -> CleanTranscriptCache:
    return CleanTranscriptCache(fingerprint, inner=inner or InMemoryCache())


class TestRoundTrip:
    def test_save_and_load(self):
        c = cache("fp1")
        c.save("m1", {"7": "cleaned text"})
        assert c.load("m1") == {"7": "cleaned text"}

    def test_load_missing_returns_none(self):
        assert cache("fp1").load("nope") is None

    def test_item_ids_are_stringified(self):
        c = cache("fp1")
        c.save("m1", {7: "cleaned"})
        assert c.load("m1") == {"7": "cleaned"}


class TestFingerprintGating:
    """A changed cleanup prompt must not read through to stale text."""

    def test_different_fingerprint_is_a_miss(self):
        inner = InMemoryCache()
        cache("old-prompt", inner).save("m1", {"7": "cleaned by old prompt"})
        assert cache("new-prompt", inner).load("m1") is None

    def test_same_fingerprint_is_a_hit(self):
        inner = InMemoryCache()
        cache("fp1", inner).save("m1", {"7": "cleaned"})
        assert cache("fp1", inner).load("m1") == {"7": "cleaned"}

    def test_rewriting_under_new_fingerprint_replaces_the_value(self):
        inner = InMemoryCache()
        cache("old", inner).save("m1", {"7": "old text"})
        cache("new", inner).save("m1", {"7": "new text"})
        assert cache("new", inner).load("m1") == {"7": "new text"}
        assert cache("old", inner).load("m1") is None

    def test_payload_without_a_fingerprint_is_a_miss(self):
        """Values written before fingerprinting existed are not trusted."""
        inner = InMemoryCache()
        inner.save("m1", {"7": "legacy bare mapping"})
        assert cache("fp1", inner).load("m1") is None

    def test_malformed_payload_is_a_miss(self):
        inner = InMemoryCache()
        inner.save("m1", "not a dict")
        assert cache("fp1", inner).load("m1") is None

    def test_missing_items_key_is_a_miss(self):
        inner = InMemoryCache()
        inner.save("m1", {"cleanup_fingerprint": "fp1"})
        assert cache("fp1", inner).load("m1") is None


class TestLocalDirBacking:
    def test_round_trips_through_a_directory(self, tmp_path):
        inner = LocalDirCache(str(tmp_path), suffix=".clean.json")
        cache("fp1", inner).save("m1", {"7": "cleaned"})
        assert os.path.exists(tmp_path / "m1.clean.json")
        assert cache("fp1", inner).load("m1") == {"7": "cleaned"}

    def test_fingerprint_gating_survives_a_reload_from_disk(self, tmp_path):
        inner = LocalDirCache(str(tmp_path), suffix=".clean.json")
        cache("old", inner).save("m1", {"7": "stale"})
        assert cache("new", LocalDirCache(str(tmp_path), ".clean.json")).load("m1") is None
