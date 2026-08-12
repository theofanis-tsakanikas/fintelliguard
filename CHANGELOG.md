# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **`SECURITY.md`** — scope, reporting, the hardened controls, and eight known limitations each
  paired with the control a real deployment would use instead. The first is the one that matters and
  was already stated in the README: the guardrail red-team score is a **regression score against an
  offline signature stand-in, not a measured safety property** of the deployed classifier.
- **`docs/adr/` — seven decision records**, extracted from reasoning that already existed in the
  code, the Terraform comments, `CLAUDE.md` and `docs/NARRATIVE.md`: the three-tier funnel, two
  clouds joined by a contract, the interpretable 14-feature set, the promotion-gate thresholds, the
  foundation-model choice, the deterministic verdict gate, and streaming as a run-then-destroy stage.
  Each records what was **rejected** and why.
- **`.github/dependabot.yml`** — seven ecosystems declared (pip, GitHub Actions, Docker and the four
  Terraform layers), all at `open-pull-requests-limit: 0`. Dependabot **security** updates stay
  enabled; routine version updates are off, because several pins here must move together or not at
  all and CI alone cannot prove a model-dependency bump.
- **This changelog.**

### Changed

- **README rewritten to the portfolio README standard.** No screenshot was removed — all 22 images
  are still referenced and all still resolve. Eight two-column pairs (from three) and a visible
  caption on every screenshot (from none), so each image now says what to look at rather than
  relying on alt text nobody sees.
- **`Quickstart` is its own section.** The commands were buried at line 499 inside "Testing
  philosophy"; they are now findable, and the testing section keeps the philosophy.
- **The limits are consolidated into `What this does not do`.** They were accurate but scattered
  across "Beyond the demo", "Testing philosophy (honest)" and inline notes. Same content, one place —
  including the LangGraph self-healing layer never having run live, drift being a library rather than
  a monitor, and `secret_recovery_window_days = 0` being right in dev and wrong in general.
- **Added `Cost`, `Decisions`, `Security` and a real `License` section**, per the shared standard.
  `Cost` separates what bills continuously (MSK, OpenSearch Serverless, Vector Search) from what
  scales to zero (both Mosaic serving endpoints), and names the two controls that keep it honest —
  `layers: core` and `streaming` being deliberately excluded from `stage: all`.

### Note

No project code, Terraform, bundle or pipeline was modified. This release is documentation and
repository configuration only.
