# Guardrail Coverage — Red-Team Report

> Generated from code by `python -m ml.governance.generate`. Do not edit by hand.

The input/output guardrail (`agents/bedrock/terraform/guardrail.tf`, modelled offline in `agents/bedrock/guardrails/policy.py`) is evaluated against a labelled red-team set.

- **Adversarial probes blocked:** 19/19 (100%)
- **Benign false positives:** 0/6 (0%)

| Threat category | Blocked | Correct |
| --- | --- | --- |
| benign | 0/6 | 6/6 |
| jailbreak | 4/4 | 4/4 |
| out_of_scope | 3/3 | 3/3 |
| pii_leak | 6/6 | 6/6 |
| prompt_injection | 6/6 | 6/6 |

Coverage is enforced in CI: the block rate must be 100% and the false-positive rate 0%. The coverage test also parses `guardrail.tf` so removing a policy class fails the build.

