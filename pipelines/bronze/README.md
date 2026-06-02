# pipelines/bronze/

**Bronze** DLT tables — raw ingestion, append-only, schema-on-read.

Two sources land here: the Kafka/MSK transaction stream (Spark Structured Streaming)
and S3 batch files (Auto Loader). No business logic — capture faithfully, defer
cleaning to silver. Tables: `bronze.<name>`.
