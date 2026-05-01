"""Tests for app.cache — Cache protocol and InMemoryCache adapter."""

from app.cache import Cache, InMemoryCache


class TestInMemoryCache:
    def test_save_and_load(self):
        with InMemoryCache[int]() as c:
            c.save("m1", 7)
            assert c.load("m1") == 7

    def test_load_missing_returns_none(self):
        with InMemoryCache[int]() as c:
            assert c.load("missing") is None

    def test_last_write_wins(self):
        with InMemoryCache[int]() as c:
            c.save("m1", 7)
            c.save("m1", 8)
            assert c.load("m1") == 8

    def test_per_instance_isolation(self):
        a = InMemoryCache[int]()
        b = InMemoryCache[int]()
        with a:
            a.save("m1", 1)
        with b:
            assert b.load("m1") is None

    def test_exit_runs_on_exception(self):
        c = InMemoryCache[int]()
        try:
            with c:
                c.save("m1", 1)
                raise RuntimeError("boom")
        except RuntimeError:
            pass
        # The in-memory adapter has no flush, but the saved value persists
        # (we only check that __exit__ didn't blow up).
        assert c.load("m1") == 1


class TestProtocolConformance:
    def test_in_memory_cache_is_a_cache(self):
        assert isinstance(InMemoryCache[int](), Cache)

    def test_minimal_class_satisfies_protocol(self):
        class Tiny:
            def load(self, meeting_id):
                return None

            def save(self, meeting_id, value):
                pass

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return None

        assert isinstance(Tiny(), Cache)
