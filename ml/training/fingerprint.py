"""A content fingerprint of everything that determines the trained model.

If the feature definitions or the training code change, the model a previous run produced is
STALE: serving would compute the new features and feed them to a model trained on the old
ones, which is exactly the parity break `CLAUDE.md` calls non-negotiable. If none of it
changed, re-running training rebuilds a byte-for-byte equivalent model at real cluster cost.

So the deploy's train-or-skip decision is not "does a model exist" but "does a model built
from THIS code already hold the production alias". Every registered version is tagged with
this fingerprint (see `ml.training.registry`), and `ml.training.reuse_decision` compares the
production version's tag against the fingerprint of the code being deployed.

The hash is content-addressed and location-independent: it is computed from each source file's
path RELATIVE to `ml/` plus its bytes, so it is identical on the CI runner (hashing the
checked-out repo) and on the training cluster (hashing the synced bundle code) for the same
commit.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from pathlib import Path

# `ml/` — the base every source path is made relative to, so the hash does not depend on where
# the repo is checked out.
_ML = Path(__file__).resolve().parents[1]

# The files whose content defines the model. Feature definitions AND the training code: a
# change to either produces a different model. Deliberately NOT the whole repo — serving code,
# docs and infra do not change what the model learns, and folding them in would force needless
# retrains on unrelated edits.
_SOURCES: tuple[Path, ...] = (
    _ML / "features",
    _ML / "training" / "train.py",
    _ML / "training" / "promote.py",
    _ML / "training" / "dataset.py",
)

# The Unity Catalog model-version tag key the fingerprint is stored under.
FINGERPRINT_TAG = "content_fingerprint"


def _collect(sources: Iterable[Path]) -> list[Path]:
    """Every `.py` file the sources cover, directories expanded, `__pycache__` excluded.

    `__pycache__` holds compiled `.pyc` (not matched by `*.py`) but a stray checkout can leave
    other artefacts there; excluding it keeps the hash a function of source, not of whether the
    tree was imported before it was hashed.
    """
    files: list[Path] = []
    for path in sources:
        if path.is_dir():
            files.extend(f for f in path.rglob("*.py") if "__pycache__" not in f.parts)
        elif path.is_file():
            files.append(path)
    return files


def _fingerprint(files: Iterable[Path], base: Path) -> str:
    digest = hashlib.sha256()
    # Sort by the RELATIVE path so ordering is stable regardless of filesystem iteration order.
    for file in sorted(files, key=lambda f: f.relative_to(base).as_posix()):
        rel = file.relative_to(base).as_posix()
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(file.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def compute_fingerprint() -> str:
    """The fingerprint of the currently-checked-out model-defining source."""
    return _fingerprint(_collect(_SOURCES), _ML)


if __name__ == "__main__":
    print(compute_fingerprint())
