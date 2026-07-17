"""Parse the Bedrock Terraform layer into a resource graph that can be interrogated.

This exists because of a real bug. `guardrail.tf` declared a complete guardrail — PII
anonymisation, a denied topic, a prompt-attack filter, contextual grounding — and
`agent.tf` never bound it. `terraform apply` produced an agent that reasoned about
regulated transactions with no guardrail in the request path, while the console showed a
healthy policy and CI stayed green. Four tests covered the guardrail; all four asserted
that a *string* appeared in a *file*:

    assert re.search(r'type\\s*=\\s*"PROMPT_ATTACK"', guardrail_tf_text)

That assertion cannot fail for any reason worth catching. It passes when the filter is
detuned to `input_strength = "NONE"`, when the grounding threshold is dropped to 0.01,
when the PII action becomes `"NONE"` — and it passed for the entire life of the
detachment bug, because a declaration is not an attachment.

So this module does not grep. It parses the HCL into `{(type, name): body}` and exposes
reference resolution, which lets a test ask the only question that matters: **does this
control actually point at something that exists, and is that thing actually turned on?**
That is referential integrity — a control that names a target is checked against the set
of targets that exist — and it is what `tests/agents/bedrock/guardrails/
test_guardrail_attachment.py` is built on.

The parse is offline and credential-free: no AWS calls, no state, no plan.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import hcl2

TERRAFORM_DIR = Path(__file__).resolve().parent / "terraform"

# A Terraform reference as it survives HCL parsing: "${aws_bedrock_guardrail.this.id}".
# Captures the addressable part (type.name); the attribute tail is irrelevant to whether
# the target exists.
_REF = re.compile(r"\$\{([a-z][a-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_-]*)(?:\.[^}]*)?\}")


@dataclass(frozen=True)
class Reference:
    """A resolved pointer from one resource to another."""

    type: str
    name: str

    @property
    def address(self) -> str:
        return f"{self.type}.{self.name}"


class TerraformModel:
    """The parsed resource graph of a Terraform directory."""

    def __init__(self, resources: dict[tuple[str, str], dict[str, Any]]):
        self._resources = resources

    # -- construction ------------------------------------------------------- #

    @classmethod
    def from_dir(cls, directory: Path) -> TerraformModel:
        resources: dict[tuple[str, str], dict[str, Any]] = {}
        tf_files = sorted(directory.glob("*.tf"))
        if not tf_files:
            raise FileNotFoundError(f"no .tf files in {directory}")
        for path in tf_files:
            with path.open(encoding="utf-8") as handle:
                parsed = hcl2.load(handle)
            for block in parsed.get("resource", []):
                for rtype, bodies in block.items():
                    for rname, body in bodies.items():
                        resources[(rtype, rname)] = body
        return cls(resources)

    # -- queries ------------------------------------------------------------ #

    def get(self, rtype: str, rname: str) -> dict[str, Any] | None:
        return self._resources.get((rtype, rname))

    def one(self, rtype: str) -> dict[str, Any]:
        """The single resource of `rtype`. Raises if absent or ambiguous."""
        matches = [(k, v) for k, v in self._resources.items() if k[0] == rtype]
        if not matches:
            raise AssertionError(f"no {rtype} resource is declared")
        if len(matches) > 1:
            raise AssertionError(f"{rtype} is declared {len(matches)}x; disambiguate the test")
        return matches[0][1]

    def exists(self, ref: Reference) -> bool:
        return (ref.type, ref.name) in self._resources

    @property
    def addresses(self) -> set[str]:
        return {f"{t}.{n}" for t, n in self._resources}

    # -- reference resolution ----------------------------------------------- #

    @staticmethod
    def reference_in(value: Any) -> Reference | None:
        """The resource reference inside an HCL expression, if it is one.

        Returns None for a literal (`"DRAFT"`, `0.75`). A caller asserting attachment must
        treat None as a failure: a hardcoded guardrail id is exactly as unattached as a
        missing one, and only reference resolution can tell them apart.
        """
        if not isinstance(value, str):
            return None
        match = _REF.search(value)
        if not match:
            return None
        return Reference(match.group(1), match.group(2))

    def resolve(self, value: Any) -> Reference:
        """The reference in `value`, proven to point at a resource that exists.

        This is the check the detachment bug needed: a dangling reference and an absent
        one fail here identically, and neither can hide behind a passing string match.
        """
        ref = self.reference_in(value)
        if ref is None:
            raise AssertionError(
                f"expected a resource reference, got the literal {value!r} — "
                "a literal cannot be checked against anything that exists"
            )
        if not self.exists(ref):
            raise AssertionError(
                f"dangling reference: {ref.address} is referenced but never declared "
                f"(declared: {sorted(self.addresses)})"
            )
        return ref


def json_body(value: str) -> Any:
    """The document inside a `jsonencode(...)` expression, as Python data.

    Policies live inside `jsonencode(...)`, so the parser hands them back as one opaque
    string. That is how `AllowFromPublic = true` sat on the regulatory corpus, and
    `Permission = ["aoss:*"]` sat two lines under a comment saying "no wildcards", with a
    green build: nothing could see inside them. Unwrapping the expression makes the policy
    a data structure a test can interrogate.

    Terraform interpolations (`${local.x}`) stay as literal text inside the JSON strings,
    which is fine — a test asserting on `AllowFromPublic` does not care what the collection
    is called.
    """
    text = value.strip()
    opener = "${jsonencode("
    if not text.startswith(opener) or not text.endswith(")}"):
        raise AssertionError(f"not a jsonencode expression: {value[:80]!r}")
    return json.loads(text[len(opener) : -len(")}")])


@lru_cache(maxsize=1)
def bedrock_model() -> TerraformModel:
    """The parsed `agents/bedrock/terraform` layer (cached — the files do not change)."""
    return TerraformModel.from_dir(TERRAFORM_DIR)
