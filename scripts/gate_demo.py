#!/usr/bin/env python3
"""Watch a control say no.

Every project shows you its green badge. A green badge is also what a disconnected control
looks like, and a detuned one, and a tautology. This repo shipped all three at once — a
guardrail that was never attached to the agent, a parity test comparing dataclass field
types that cannot diverge, and DLT expectations evaluated on rows the pipeline had already
filtered — with 210 passing tests the whole time.

So this does not show you a passing build. It breaks something on purpose and shows you the
gate refusing it, one attack at a time, with the reason it matters.

    make gate-attack              # narrated, pauses between attacks
    make gate-attack ARGS=--fast  # no pauses, for recording
    make gate-proof               # the same attacks, as a CI gate, no narration
"""

from __future__ import annotations

import argparse
import sys
import time

from scripts.gate_proof import ATTACKS, _attack, _baseline

# Narration for the attacks worth walking a human through. The rest still run in
# `gate_proof`; these are the ones that tell the story.
FEATURED = (
    "guardrail-detached",
    "velocity-off-by-one-restored",
    "medic-promotes-staging-to-production",
    "decision-softening-allowed",
    "dq-expectations-on-prefiltered-rows",
    "deploy-applies-without-a-plan",
)

BOLD = "\033[1m"
DIM = "\033[2m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RESET = "\033[0m"


def _say(text: str = "", colour: str = "") -> None:
    print(f"{colour}{text}{RESET}" if colour else text)


def _pause(fast: bool, seconds: float = 1.2) -> None:
    if fast:
        time.sleep(0.15)
        return
    try:
        input(f"{DIM}    [enter]{RESET}")
    except EOFError:  # piped input — degrade to a timed pause
        time.sleep(seconds)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Narrated walkthrough of the gates refusing.")
    parser.add_argument("--fast", action="store_true", help="no pauses (for recording)")
    args = parser.parse_args(argv)

    featured = [a for a in ATTACKS if a.name in FEATURED]

    _say()
    _say("  FintelliGuard — attacking its own gates", BOLD)
    _say("  A gate nobody has attacked is a gate nobody has tested.", DIM)
    _say()
    _say(
        f"  {len(featured)} controls. For each: break it, run the REAL gate, watch it refuse.", DIM
    )
    _say()
    _pause(args.fast)

    # A gate that is already red would "block" everything and prove nothing.
    _say("  First — every gate must be GREEN before an attack means anything.", DIM)
    for gate in sorted({a.gate for a in featured}):
        green, _detail = _baseline(gate, verbose=False)
        mark = f"{GREEN}green{RESET}" if green else f"{RED}RED{RESET}"
        _say(f"    {mark}  {gate}")
        if not green:
            _say("\n  Refusing to score attacks against an already-red gate.", RED)
            return 1
    _say()
    _pause(args.fast)

    failures = []
    for index, attack in enumerate(featured, start=1):
        _say(f"  {index}/{len(featured)}  {BOLD}{attack.name}{RESET}")
        _say(f"      {attack.rationale}", DIM)
        _say()
        _say(f"      breaking:  {YELLOW}{attack.path}{RESET}")
        status, detail = _attack(attack, verbose=False)

        if status == "BLOCKED":
            _say(f"      gate says: {GREEN}NO{RESET}")
            _say(f"      {DIM}{detail}{RESET}")
        else:
            _say(f"      gate says: {RED}nothing — the violation went through ({status}){RESET}")
            _say(f"      {DIM}{detail}{RESET}")
            failures.append(attack)
        _say()
        _pause(args.fast)

    if failures:
        _say(f"  {len(failures)} control(s) did not refuse their violation.", RED)
        return 1

    _say(f"  {len(featured)}/{len(featured)} controls refused, each for the right reason.", GREEN)
    _say()
    _say("  Every one of these is a bug this repository actually shipped.", DIM)
    _say("  `make gate-proof` runs the full set in CI.", DIM)
    _say()
    return 0


if __name__ == "__main__":
    sys.exit(main())
