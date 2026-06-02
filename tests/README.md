# tests/

Unit + integration tests, mirroring the layers (`pipelines/`, `ml/`, `agents/`,
`simulator/`). Run with `pytest` (configured in root `pyproject.toml`) or `make test`.

**Done = runs + tested:** a layer is complete only when it runs end-to-end **and** has a
passing test — never on generated-but-unrun code.
