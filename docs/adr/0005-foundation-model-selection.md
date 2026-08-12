# ADR-0005: Amazon Nova Lite as the wired default, Claude Haiku as the switch

- **Status:** Accepted
- **Date:** 2026-06 (recorded 2026-08)

## Context

The Tier-2 agent needs a foundation model that can follow a structured verdict schema, ground its
reasoning in retrieved regulation, and stay cheap enough to run on every flagged transaction.

Claude Haiku 4.5 is the better fit on quality. It is also **account-gated**: Bedrock requires a
one-time Anthropic use-case approval, and until it is granted, streaming Anthropic models fail
outright. A default that fails on a fresh account is a default that makes the whole deploy look
broken to anyone reproducing it.

There is a second trap: the bare model id `anthropic.claude-haiku-4-5` does **not** resolve. Bedrock
serves Haiku 4.5 only under its dated id, `eu.anthropic.claude-haiku-4-5-20251001-v1:0`.

## Decision

Wire **Amazon Nova Lite** (`eu.amazon.nova-lite-v1:0`) as the default, and make the model a variable
so switching to `eu.anthropic.claude-haiku-4-5-20251001-v1:0` is a one-line change once the account
approval exists. **Sonnet is design-spec only** — named in the architecture as the model a production
deployment would evaluate, never wired, because its cost profile does not match a demonstrator.

Any change to the model id must be verified with `aws bedrock get-foundation-model` before it is
committed.

## Alternatives rejected

- **Claude Haiku 4.5 as the default.** The right model, the wrong default: a fresh account cannot
  run it, and the failure surfaces as a streaming error deep in an agent trace rather than as
  "request access first".
- **Sonnet as the default.** Better verdicts, at a cost per flagged transaction that undermines the
  funnel's entire economic argument ([ADR-0001](0001-three-tier-decisioning-funnel.md)).
- **Hardcoding the model id.** Would make the switch a code change rather than a variable, and would
  invite the bare-id mistake above.

## Consequences

- The repository deploys and produces verdicts on any account with Bedrock enabled, with no approval
  workflow in the way.
- Verdict quality is therefore the *floor* of what the architecture supports, not its ceiling — worth
  stating whenever the Tier-2 output is judged.
- The dated-id trap is recorded in `CLAUDE.md` as well as here, because it is the kind of detail that
  costs an hour when it recurs.
