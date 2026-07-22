"""The copilot's import graph must not require pyspark.

The served copilot artifact and its registration import `agents.databricks.copilot_pyfunc`,
which pulls in the tools package. Neither the model-serving container nor the deploy runner
ships pyspark, so a top-level pyspark import ANYWHERE in that graph fails the whole thing with
ModuleNotFoundError — which is exactly what killed copilot registration on deploy run
29892959804 (query_lakehouse imported pyspark at module load). pyspark belongs to
LakehouseTools alone, and only when a method actually runs on a Spark cluster.

This imports the graph in a fresh interpreter with pyspark BLOCKED, so the guard is real
rather than masked by pyspark happening to be installed in the test venv.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]

_PROBE = """
import sys

class _BlockPyspark:
    def find_spec(self, name, path=None, target=None):
        if name == "pyspark" or name.startswith("pyspark."):
            raise ImportError("pyspark is blocked to prove the copilot does not need it")
        return None

sys.meta_path.insert(0, _BlockPyspark())

import agents.databricks.copilot_pyfunc  # noqa: F401
import agents.databricks.tools  # noqa: F401
from agents.databricks.tools.get_fraud_score import FraudScoreTool  # noqa: F401
from agents.databricks.tools.search_similar_cases import SimilarCaseSearch  # noqa: F401
print("copilot import graph is pyspark-free")
"""


def test_copilot_import_graph_does_not_require_pyspark():
    result = subprocess.run(
        [sys.executable, "-c", _PROBE],
        capture_output=True,
        text=True,
        cwd=str(_ROOT),
    )
    assert result.returncode == 0, (
        "importing the copilot graph pulled in pyspark (or otherwise failed) — the serving "
        f"container and deploy runner have no pyspark:\n{result.stderr}"
    )
    assert "pyspark-free" in result.stdout
