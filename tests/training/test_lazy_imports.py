"""The training package must not drag the ML stack in at import."""

from __future__ import annotations

import ml.training as pkg


def test_all_and_the_lazy_map_agree():
    """`__all__` is spelled out for ruff's benefit, so it can drift from `_LAZY`."""
    assert sorted(pkg.__all__) == sorted(pkg._LAZY), (
        "__all__ and the lazy map disagree — a name in one and not the other is either "
        "unreachable or unresolvable"
    )


def test_every_exported_name_resolves():
    for name in pkg.__all__:
        assert getattr(pkg, name) is not None


def test_an_unknown_name_raises_attribute_error():
    """A scan-based __getattr__ would import something unexpected on a typo."""
    try:
        pkg.train_modle  # noqa: B018
    except AttributeError:
        return
    raise AssertionError("a misspelled name did not raise AttributeError")
