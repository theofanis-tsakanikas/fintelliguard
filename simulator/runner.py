"""Drives the generator into a sink at the configured rate, with optional bounds."""

from __future__ import annotations

import time
from collections.abc import Callable

from .config import SimulatorConfig
from .generator import TransactionGenerator
from .sinks import Sink, build_sink


class SimulatorRunner:
    """Pull from a `TransactionGenerator`, write to a `Sink`, pace at `rate_per_sec`.

    `sleep` and `time_source` are injectable so tests run instantly and deterministically.
    """

    def __init__(
        self,
        config: SimulatorConfig,
        sink: Sink | None = None,
        generator: TransactionGenerator | None = None,
        sleep: Callable[[float], None] = time.sleep,
        time_source: Callable[[], float] = time.monotonic,
    ) -> None:
        self.config = config
        self.generator = generator or TransactionGenerator(config)
        self.sink = sink or build_sink(config)
        self._sleep = sleep
        self._time = time_source

    def run(self) -> int:
        """Emit transactions until a bound is hit; return the number emitted."""
        interval = 1.0 / self.config.rate_per_sec
        emit_labels = self.config.emit_ground_truth
        started = self._time()
        count = 0

        for txn in self.generator.stream():
            record = txn.to_eval_dict() if emit_labels else txn.to_contract_dict()
            self.sink.emit(record)
            count += 1

            if self.config.max_records is not None and count >= self.config.max_records:
                break
            if (
                self.config.duration_seconds is not None
                and (self._time() - started) >= self.config.duration_seconds
            ):
                break
            self._sleep(interval)

        return count
