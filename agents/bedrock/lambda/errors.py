"""Errors for the FraudScoring action-group Lambda.

Flat top-level module (the Lambda zip root; `lambda` is a Python keyword so this dir is
not a package). Stdlib-only.
"""

from __future__ import annotations


class ActionGroupError(Exception):
    """Malformed action-group event (e.g. missing required parameters)."""


class FeatureStoreError(Exception):
    """Online Feature Store lookup failed."""


class FraudScoreError(Exception):
    """Calling the Mosaic Model Serving endpoint failed."""
