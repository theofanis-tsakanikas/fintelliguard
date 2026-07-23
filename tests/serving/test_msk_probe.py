"""The MSK probe forces the SASL_SSL/AWS_MSK_IAM path and proves the SAME options the pipeline uses.

The probe's whole job is to test the SAME options the DLT consumer uses — a probe that passed
on a look-alike config while the pipeline failed would be worse than none. The probe INLINES
those options (it runs as a bare spark_python_task where `pipelines` is not importable), so the
parity is enforced here instead: these must equal runtime_config.kafka_source_options(SASL_SSL).
"""

from __future__ import annotations

import pytest

from ml.serving import msk_probe


def test_probe_forces_the_aws_msk_iam_sasl_options():
    opts = msk_probe._iam_options()
    assert opts["kafka.security.protocol"] == "SASL_SSL"
    assert opts["kafka.sasl.mechanism"] == "AWS_MSK_IAM"
    assert "IAMClientCallbackHandler" in opts["kafka.sasl.client.callback.handler.class"]


def test_probe_options_match_the_pipeline_consumer_exactly():
    """The inlined probe options must not drift from the DLT consumer's — parity at test time."""
    from pipelines.runtime_config import kafka_source_options

    class _Sasl:
        conf = type(
            "c", (), {"get": staticmethod(lambda k, d="": "SASL_SSL" if "protocol" in k else d)}
        )

    assert msk_probe._iam_options() == kafka_source_options(_Sasl())


def test_probe_requires_a_bootstrap_target():
    """No --bootstrap means no way to reach MSK — argparse must reject it, not default silently."""
    with pytest.raises(SystemExit):
        msk_probe.main([])
