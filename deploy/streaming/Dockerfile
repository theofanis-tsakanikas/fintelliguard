# Image for the in-VPC Fargate generator: the simulator producing to MSK with IAM auth.
# Same base as the local funnel image, plus the `.[msk]` extra (the AWS MSK IAM token signer)
# so the producer can authenticate to the brokers with no stored secret. The ECS task
# definition overrides the command; this CMD is the default (produce to Kafka at 30/s).
FROM python:3.11-slim

WORKDIR /app

# confluent-kafka ships manylinux/aarch64 wheels (librdkafka bundled); gcc is a fallback for
# any sdist path. The MSK signer is pure-Python (needs botocore, pulled transitively).
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY simulator ./simulator
COPY ml ./ml
COPY agents ./agents
COPY pipelines ./pipelines

# local = Kafka client + generator runtime; msk = the AWS MSK IAM SASL token signer.
RUN pip install --no-cache-dir ".[local,msk]"

CMD ["python", "-m", "simulator", "--sink", "kafka", "--no-ground-truth", "--rate", "30"]
