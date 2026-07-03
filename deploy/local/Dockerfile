# Image for the local end-to-end funnel: the simulator (producer) and the streaming scorer
# share it. Installs the self-sufficient `.[local]` runtime (Kafka client + Prometheus +
# XGBoost/pandas) — no cloud SDKs, no Spark.
FROM python:3.11-slim

WORKDIR /app

# confluent-kafka ships manylinux/aarch64 wheels (librdkafka bundled); gcc is a fallback
# for any sdist path. libgomp1 is XGBoost's OpenMP runtime.
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Copy the package sources the funnel imports, then install the local runtime extra.
COPY pyproject.toml README.md ./
COPY simulator ./simulator
COPY ml ./ml
COPY agents ./agents
COPY pipelines ./pipelines

RUN pip install --no-cache-dir ".[local]"

# Overridden per-service by docker-compose (simulator vs scorer).
CMD ["python", "-m", "ml.serving.stream_service"]
