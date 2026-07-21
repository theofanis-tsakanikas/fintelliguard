"""A read_only UC external location over a bucket sub-prefix needs that prefix to pre-exist.

Unity Catalog validates an external location at creation by LISTING its URL. A writable
location passes by writing a probe object (which creates the prefix); a `read_only` one cannot,
so the prefix must already exist — and an S3 prefix does not exist until an object is written
under it. On a clean estate the raw landing zone (read_only) is created before any IEEE-CIS
data lands, so without a seed object it fails with a "No such file or directory" that UC
reports as a missing LIST permission (deploy run 29876467301). Earlier deploys only passed
because the bucket still held data from a prior iteration.

The gate is referential integrity, not a string grep: parse both layers and assert every
read_only external location whose URL points at a sub-prefix is backed by an S3 object seeding
that prefix. A grep for the marker's key would pass if someone pointed the location at a
different prefix; this fails unless the marker and the location actually agree.
"""

from __future__ import annotations

from pathlib import Path

import hcl2

_ROOT = Path(__file__).resolve().parents[2]


def _unwrap(value):
    # python-hcl2 returns scalar attributes directly on current versions but has wrapped them
    # in a one-element list on others; tolerate both so the gate does not depend on the parser.
    if isinstance(value, list) and len(value) == 1:
        return value[0]
    return value


def _resources(tf_path: Path) -> dict[tuple[str, str], dict]:
    with open(tf_path, encoding="utf-8") as handle:
        doc = hcl2.load(handle)
    out: dict[tuple[str, str], dict] = {}
    for block in doc.get("resource", []):
        for rtype, bodies in block.items():
            for name, body in bodies.items():
                out[(rtype, name)] = body
    return out


def _url_prefix(url: str) -> str:
    """`s3://${bucket}/raw` -> `raw`; the path under the bucket, no leading/trailing slash."""
    after_scheme = url.split("://", 1)[-1]
    parts = after_scheme.split("/", 1)
    return parts[1].strip("/") if len(parts) == 2 else ""


def test_readonly_external_location_prefixes_are_seeded_by_a_marker_object():
    aws_res = _resources(_ROOT / "infra" / "aws" / "s3.tf")
    db_res = _resources(_ROOT / "infra" / "databricks" / "uc_storage.tf")

    object_keys = [
        _unwrap(body.get("key", ""))
        for (rtype, _), body in aws_res.items()
        if rtype == "aws_s3_object"
    ]

    readonly = {
        name: body
        for (rtype, name), body in db_res.items()
        if rtype == "databricks_external_location" and _unwrap(body.get("read_only")) is True
    }
    assert readonly, "expected a read_only external location (the raw IEEE-CIS landing zone)"

    for name, body in readonly.items():
        prefix = _url_prefix(_unwrap(body["url"]))
        assert prefix, (
            f"read_only external location {name!r} points at a bucket root, not a sub-prefix — "
            "the seed-object reasoning does not apply; re-check this gate if that was intended"
        )
        assert any(str(key).startswith(prefix + "/") for key in object_keys), (
            f"read_only external location {name!r} validates s3://.../{prefix} at creation, but "
            f"infra/aws seeds no S3 object under {prefix}/ — on a clean estate the prefix does "
            "not exist and UC rejects the location. Seed it with an aws_s3_object marker."
        )


def test_the_raw_marker_is_kms_encrypted_so_uc_can_read_it_during_validation():
    """The marker is validated by the UC storage-credential role, which is granted decrypt on
    the estate CMK only. A marker written unencrypted (or under a different key) would be
    unreadable and validation would fail for a reason that looks like a permissions bug."""
    aws_res = _resources(_ROOT / "infra" / "aws" / "s3.tf")
    markers = [
        body
        for (rtype, _), body in aws_res.items()
        if rtype == "aws_s3_object" and str(_unwrap(body.get("key", ""))).startswith("raw/")
    ]
    assert markers, "no raw/ marker object found to check encryption on"
    for body in markers:
        assert _unwrap(body.get("server_side_encryption")) == "aws:kms", (
            "the raw prefix marker is not KMS-encrypted; UC cannot read it during validation"
        )
        assert "kms_key_id" in body, "the marker names no CMK, so it may use the wrong key"
