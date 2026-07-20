#!/usr/bin/env bash
#
# Empty every S3 bucket a Terraform layer owns, so `terraform destroy` can delete them.
#
# S3 refuses to delete a bucket that still holds objects, and for a VERSIONED bucket "empty"
# means every noncurrent version and delete marker too:
#
#     BucketNotEmpty: The bucket you tried to delete is not empty. You must delete all
#     versions in the bucket.
#
# `force_destroy = true` does not rescue this on a teardown. `terraform destroy` plans from
# PRIOR STATE and never applies attribute changes, so the provider reads the flag recorded in
# state rather than the one in config — an estate applied before the flag was added stays
# undeletable no matter what the config says. That cost three failed teardowns on the
# databricks layer, and the same trap was waiting on `infra/aws` (651 MB of IEEE-CIS data) and
# on the bedrock layer (the regulatory corpus).
#
# So the teardown does not depend on the flag at all. This runs per layer, before that layer
# is destroyed.
#
# Usage: empty_layer_buckets.sh <terraform-layer-dir>
set -euo pipefail

layer="${1:?usage: empty_layer_buckets.sh <terraform-layer-dir>}"

# `init` FIRST and unguarded: without a backend `state list` fails, and a failed probe must
# never be mistaken for "this layer owns no buckets" — that would skip the emptying and let
# the destroy fail on a bucket nobody looked at.
terraform -chdir="${layer}" init -input=false >/dev/null

for address in $(terraform -chdir="${layer}" state list 2>/dev/null | grep -E '^aws_s3_bucket\.' || true); do
  name="$(terraform -chdir="${layer}" state show -no-color "${address}" \
    | awk -F'"' '/^[[:space:]]+bucket[[:space:]]+=/ {print $2; exit}')"
  [ -z "${name}" ] && continue
  echo "emptying ${layer}:${address} -> ${name}"

  while :; do
    # Paginated: list-object-versions caps at 1000 keys, so a single pass silently leaves the
    # rest behind on any bucket that outgrew that — and the delete then fails for a reason the
    # log would attribute to permissions.
    raw="$(aws s3api list-object-versions --bucket "${name}" --max-keys 1000 \
      --output json 2>/dev/null || echo '{}')"
    # BOTH lists matter. `Versions` alone leaves the DELETE MARKERS behind, and a bucket
    # holding only markers is still not empty as far as S3 is concerned — while `aws s3 ls`
    # shows nothing, so it looks like the emptying worked.
    # Built through STDIN and straight to a file. The first version passed the object list
    # to jq as an argument (`--argjson`), which dies once a page is large enough:
    #
    #     /usr/bin/jq: Argument list too long
    #
    # A thousand keys is past ARG_MAX. It survived the raw bucket, which held one object, and
    # failed on the DBFS root, which holds many — the batch size, not the pagination, and the
    # test asserting pagination existed could not see it.
    printf '%s' "${raw}" \
      | jq '{Objects: [(.Versions // [])[], (.DeleteMarkers // [])[] | {Key, VersionId}],
             Quiet: true}' > /tmp/delete-batch.json
    count="$(jq '.Objects | length' /tmp/delete-batch.json)"
    [ "${count}" = "0" ] && break
    aws s3api delete-objects --bucket "${name}" --delete file:///tmp/delete-batch.json >/dev/null
    echo "  deleted ${count} version(s)/marker(s)"
  done
done

echo "${layer}: buckets emptied."
