"""The content fingerprint that decides train-vs-reuse.

The two properties that matter: it is STABLE for unchanged source (or a deploy would retrain
every time and the whole mechanism saves nothing), and it CHANGES when any covered source byte
changes (or a stale model — new features, old weights — would silently keep serving).
"""

from __future__ import annotations

from pathlib import Path

from ml.training import fingerprint as fp


def test_the_covered_sources_all_exist():
    """A fingerprint over a path that does not exist would silently cover nothing."""
    for source in fp._SOURCES:
        assert source.exists(), f"fingerprint source {source} does not exist"


def test_compute_fingerprint_is_a_sha256_hex_digest():
    value = fp.compute_fingerprint()
    assert len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def test_compute_fingerprint_is_deterministic():
    assert fp.compute_fingerprint() == fp.compute_fingerprint()


def _write(base: Path, rel: str, content: bytes) -> Path:
    path = base / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def test_fingerprint_changes_when_a_covered_file_changes(tmp_path):
    a = _write(tmp_path, "pkg/one.py", b"x = 1\n")
    b = _write(tmp_path, "pkg/two.py", b"y = 2\n")
    before = fp._fingerprint([a, b], tmp_path)

    a.write_bytes(b"x = 999\n")
    after = fp._fingerprint([a, b], tmp_path)
    assert before != after, "a changed source byte did not change the fingerprint"


def test_fingerprint_is_independent_of_iteration_order(tmp_path):
    a = _write(tmp_path, "pkg/one.py", b"x = 1\n")
    b = _write(tmp_path, "pkg/two.py", b"y = 2\n")
    assert fp._fingerprint([a, b], tmp_path) == fp._fingerprint([b, a], tmp_path)


def test_fingerprint_is_independent_of_checkout_location(tmp_path):
    """Path is hashed RELATIVE to the base, so the same content under a different root matches.

    This is what lets the CI runner and the training cluster agree on the fingerprint for one
    commit despite hashing the tree at different absolute paths.
    """
    root1 = tmp_path / "checkout-a"
    root2 = tmp_path / "somewhere-else"
    a1 = _write(root1, "pkg/one.py", b"x = 1\n")
    a2 = _write(root2, "pkg/one.py", b"x = 1\n")
    assert fp._fingerprint([a1], root1) == fp._fingerprint([a2], root2)


def test_fingerprint_reflects_the_relative_path_not_only_content(tmp_path):
    """Two files with identical bytes at different paths must not collide to the same hash."""
    a = _write(tmp_path, "pkg/one.py", b"same\n")
    b = _write(tmp_path, "pkg/two.py", b"same\n")
    assert fp._fingerprint([a], tmp_path) != fp._fingerprint([b], tmp_path)


def test_collect_expands_directories_and_skips_pycache(tmp_path):
    _write(tmp_path, "pkg/real.py", b"a\n")
    _write(tmp_path, "pkg/__pycache__/real.cpython-311.py", b"compiled\n")
    collected = fp._collect([tmp_path / "pkg"])
    names = {p.name for p in collected}
    assert "real.py" in names
    assert not any("__pycache__" in p.parts for p in collected), "compiled cache leaked in"
