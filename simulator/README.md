# simulator/

Python transaction generator (~**500 txns/sec**) that publishes synthetic payment
events to Kafka. Drives the streaming pipeline in dev and the demo. In dev it targets
**local Kafka (Docker)**; MSK is reserved for integration testing and the final demo.
