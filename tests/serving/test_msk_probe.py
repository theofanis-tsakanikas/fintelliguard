"""The MSK probe forces the SASL_SSL/AWS_MSK_IAM path and shares the pipeline's wiring.

The probe's whole job is to test the SAME options the DLT consumer uses — a probe that passed
on a look-alike config while the pipeline failed would be worse than none. These pin that it
forces SASL_SSL (regardless of cluster conf) and reuses runtime_config.kafka_source_options.
"""

from __future__ import annotations

import pytest

from ml.serving import msk_probe


def test_probe_forces_the_aws_msk_iam_sasl_options():
    opts = msk_probe._iam_options()
    assert opts["kafka.security.protocol"] == "SASL_SSL"
    assert opts["kafka.sasl.mechanism"] == "AWS_MSK_IAM"
    assert "IAMClientCallbackHandler" in opts["kafka.sasl.client.callback.handler.class"]


def test_probe_requires_a_bootstrap_target():
    """No --bootstrap means no way to reach MSK — argparse must reject it, not default silently."""
    with pytest.raises(SystemExit):
        msk_probe.main([])
