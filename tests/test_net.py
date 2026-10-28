"""Tests for the one guarded HTTP door.

The point of ``open_url`` is that a non-web scheme is refused *before* urllib is
reached. The refusal is asserted with a real ``urlopen`` that must never run, so
a regression that removed the check would attempt a connection and fail here
rather than silently opening a ``file://`` path.
"""

from __future__ import annotations

import urllib.request

import pytest

from goldbot.net import ALLOWED_SCHEMES, UnsafeURL, open_url


@pytest.mark.parametrize("scheme", ["file", "ftp", "gopher", "data", "javascript"])
def test_a_non_web_scheme_is_refused_before_urllib(monkeypatch, scheme):
    def explode(*args, **kwargs):  # pragma: no cover - must never be reached
        raise AssertionError("urlopen was reached for a refused scheme")

    monkeypatch.setattr(urllib.request, "urlopen", explode)
    with pytest.raises(UnsafeURL):
        open_url(f"{scheme}://example.com/x", timeout=1)


def test_a_bare_string_with_no_scheme_is_refused(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: pytest.fail("urlopen reached"))
    with pytest.raises(UnsafeURL):
        open_url("example.com/x", timeout=1)


@pytest.mark.parametrize("scheme", sorted(ALLOWED_SCHEMES))
def test_web_schemes_reach_urlopen(monkeypatch, scheme):
    seen = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(target, timeout=None):
        seen["target"] = target
        seen["timeout"] = timeout
        return _Resp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    with open_url(f"{scheme}://example.com/x", timeout=5) as resp:
        assert resp is not None
    assert seen["timeout"] == 5


def test_a_prepared_request_is_accepted_and_its_scheme_read(monkeypatch):
    """Call sites pass a Request; the scheme is read from ``full_url``."""
    req = urllib.request.Request("https://api.example.com/v1")
    monkeypatch.setattr(urllib.request, "urlopen", lambda target, timeout=None: _Ctx())
    with open_url(req, timeout=3):
        pass


def test_a_prepared_request_with_a_bad_scheme_is_refused():
    req = urllib.request.Request("file:///etc/passwd")
    with pytest.raises(UnsafeURL):
        open_url(req, timeout=1)


class _Ctx:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False
