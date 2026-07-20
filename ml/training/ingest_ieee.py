"""Put the IEEE-CIS training data where the DLT pipeline reads it.

Why this exists
---------------
`docs/DEPLOY.md` §6 carried this as a manual command:

    aws s3 cp ieee-cis/ s3://fintelliguard-raw/raw/ieee-cis/ --recursive

Nothing ran it, and the file it names is a 650 MB Kaggle download that was never in the
repository — so `bronze.ieee_cis_raw` had nothing to read, `gold.txn_features_training` was
never built, no model was ever trained, and the serving endpoints in the main bundle failed
with `Registered model 'fintelliguard.ml.fraud_scorer' does not exist`. The whole ML half of
the deploy was blocked on a step that lived only in prose. Same shape as the regulatory
corpus, one layer down.

What it fetches, and what it does not
-------------------------------------
Only `train_transaction.csv`. It carries `isFraud` — the label — plus `TransactionID`,
`card1` and `TransactionAmt`, which is exactly what `ml/features/adapter_ieee.py` maps into
the 14 canonical features. `train_identity.csv` joins extra device/browser columns that no
canonical feature reads, and it is a separate download; fetching it would cost bandwidth to
produce columns the pipeline drops.

Idempotence is not a nicety here. This runs on every deploy, and the download is 650 MB: an
unconditional fetch would add minutes and bandwidth to a re-deploy that needs nothing. The
object's presence in S3 is the check, so a re-deploy against a populated bucket costs one
HEAD request.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import tempfile
import zipfile
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

COMPETITION = "ieee-fraud-detection"
# Must match `IEEE_RAW_PATH` in pipelines/bronze/bronze_pipeline.py, which is
# f"s3://{RAW_BUCKET}/raw/ieee-cis/". Auto Loader reads every CSV under it.
PREFIX = "raw/ieee-cis/"
FILENAME = "train_transaction.csv"


def already_present(s3, bucket: str, key: str) -> bool:
    try:
        s3.head_object(Bucket=bucket, Key=key)
        return True
    except ClientError as error:
        if error.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
            return False
        raise


def download(destination: Path, competition: str = COMPETITION, filename: str = FILENAME) -> Path:
    """Fetch one competition file with the Kaggle CLI and return the extracted CSV.

    The CLI reads `KAGGLE_USERNAME` / `KAGGLE_KEY` from the environment. Two failures look
    alike and are not:

      * 403 with valid credentials means the competition RULES have not been accepted. That
        is per-competition and cannot be done from an API token — someone has to click it
        once on the competition page.
      * 401 means the credentials themselves are wrong or absent.

    Both are surfaced verbatim rather than summarised, because the remedies differ and the
    status code is the only thing that distinguishes them.
    """
    for variable in ("KAGGLE_USERNAME", "KAGGLE_KEY"):
        if not os.environ.get(variable):
            raise RuntimeError(
                f"{variable} is not set. The IEEE-CIS download needs Kaggle API credentials "
                "(kaggle.json -> repository secrets)."
            )

    destination.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            "kaggle",
            "competitions",
            "download",
            "-c",
            competition,
            "-f",
            filename,
            "-p",
            str(destination),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"kaggle download failed ({result.returncode}). If this is 403 with valid "
            f"credentials, the competition rules for '{competition}' have not been accepted "
            f"— that is a one-time click on the competition page.\n"
            f"stdout: {result.stdout.strip()}\nstderr: {result.stderr.strip()}"
        )

    # The CLI hands back either the file or a zip of it, depending on size and version.
    csv_path = destination / filename
    if not csv_path.exists():
        archives = list(destination.glob("*.zip"))
        if not archives:
            raise RuntimeError(
                f"kaggle reported success but produced neither {filename} nor a zip in "
                f"{destination} — contents: {sorted(p.name for p in destination.iterdir())}"
            )
        with zipfile.ZipFile(archives[0]) as archive:
            archive.extractall(destination)
    if not csv_path.exists():
        raise RuntimeError(f"{filename} is still missing after extracting {destination}")
    return csv_path


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bucket", required=True, help="raw data bucket (infra/aws output)")
    parser.add_argument("--region", default="eu-central-1")
    args = parser.parse_args(argv)

    key = f"{PREFIX}{FILENAME}"
    s3 = boto3.session.Session(region_name=args.region).client("s3")

    if already_present(s3, args.bucket, key):
        print(f"s3://{args.bucket}/{key} already present — skipping the 650 MB download")
        return

    with tempfile.TemporaryDirectory() as workdir:
        csv_path = download(Path(workdir))
        size_mb = csv_path.stat().st_size / 1_048_576
        print(f"downloaded {csv_path.name} ({size_mb:,.0f} MB) — uploading")
        s3.upload_file(str(csv_path), args.bucket, key)

    print(f"uploaded s3://{args.bucket}/{key}")


if __name__ == "__main__":
    main()
