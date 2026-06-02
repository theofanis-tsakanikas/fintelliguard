"""Command-line entry point: build a config from flags and run the simulator.

Example:
    python -m simulator --max-records 20 --seed 7
    python -m simulator --rate 500 --duration 5 --jsonl out.jsonl
"""

from __future__ import annotations

import argparse

from .config import SimulatorConfig, SinkType
from .runner import SimulatorRunner


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="simulator", description="FintelliGuard transaction simulator"
    )
    parser.add_argument("--rate", type=float, default=500.0, help="transactions per second")
    parser.add_argument("--duration", type=float, default=None, help="run length in seconds")
    parser.add_argument("--max-records", type=int, default=None, help="stop after N records")
    parser.add_argument("--fraud-rate", type=float, default=0.01, help="fraud injection rate [0,1]")
    parser.add_argument("--seed", type=int, default=42, help="RNG seed for reproducible runs")
    parser.add_argument("--sink", choices=[s.value for s in SinkType], default=SinkType.LOCAL.value)
    parser.add_argument(
        "--jsonl", default=None, help="JSONL output path (local sink; default stdout)"
    )
    parser.add_argument(
        "--no-ground-truth",
        action="store_true",
        help="omit eval labels (use for the production Kafka path)",
    )
    return parser.parse_args(argv)


def build_config(argv: list[str] | None = None) -> SimulatorConfig:
    """Translate CLI args into a `SimulatorConfig`."""
    args = _parse_args(argv)
    return SimulatorConfig(
        rate_per_sec=args.rate,
        duration_seconds=args.duration,
        max_records=args.max_records,
        fraud_injection_rate=args.fraud_rate,
        seed=args.seed,
        emit_ground_truth=not args.no_ground_truth,
        sink=SinkType(args.sink),
        jsonl_path=args.jsonl,
    )


def main(argv: list[str] | None = None) -> int:
    """Run the simulator; return the number of records emitted."""
    config = build_config(argv)
    runner = SimulatorRunner(config)
    with runner.sink:
        return runner.run()


if __name__ == "__main__":
    main()
