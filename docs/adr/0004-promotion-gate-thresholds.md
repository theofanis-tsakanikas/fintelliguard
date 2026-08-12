# ADR-0004: The promotion gate floors at AUC 0.83, not 0.92

- **Status:** Accepted
- **Date:** 2026-06 (recorded 2026-08)

## Context

A model must not reach production because someone ran a notebook and liked the result. It needs an
automated gate with a number.

The tempting number is the one in the literature. Public write-ups on IEEE-CIS quote AUC around
0.92–0.96 — but those are leaderboard results built on the full anonymised feature set, heavy feature
engineering and ensembles. This project deliberately uses 14 interpretable features
([ADR-0003](0003-interpretable-feature-contract.md)), which tops out around 0.85 on the same data.

Setting the gate at 0.92 would mean **no model this architecture can produce would ever pass**. The
gate would be theatre: either permanently red, or quietly lowered the first time it blocked a
release — which is worse, because then nobody trusts any of the gates.

## Decision

Promote Staging → Production only when, on a held-out test set:

```
AUC-ROC ≥ 0.83   AND   fraud-class precision ≥ 0.85
```

Enforced in `ml/training/promote.py`, **fail-closed** — a run that cannot compute both metrics does
not promote. Both thresholds are read from the code by the generated model card, so the documented
number and the enforced number cannot diverge.

Precision is gated alongside AUC deliberately: AUC alone can look healthy while the model floods
analysts with false positives, and the analyst-hours are the cost this system exists to reduce.

## Alternatives rejected

- **AUC ≥ 0.92.** Borrowed from a different feature set. An unreachable gate is not a gate.
- **AUC only.** Says nothing about the false-positive burden on the flagged 1%.
- **Recall as the second metric.** Attractive for fraud, but it trades directly against the analyst
  cost this design is built to control; precision was chosen with that trade-off stated rather than
  implied.
- **A human sign-off instead of a threshold.** Not reproducible, and not something CI can enforce.

## Consequences

- The live run cleared it at **AUC 0.8661** and **precision 0.8699** — comfortably above, but not by
  so much that the gate is decorative.
- The threshold is a property of the feature contract. Changing the contract means revisiting this
  number, not silently inheriting it.
- The gate is one of the controls `make gate-proof` attacks: restoring an off-by-one in the
  comparison must be refused by the named test, not merely by a non-zero exit code.
