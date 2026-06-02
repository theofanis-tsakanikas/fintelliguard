"""DLT medallion pipelines (bronze -> silver -> gold).

Each layer separates pure, locally-testable Spark transforms (`*_transforms.py`) from a
thin DLT framework layer (`*_pipeline.py`) that only runs on Databricks.
"""
