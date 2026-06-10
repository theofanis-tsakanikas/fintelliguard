"""Deterministic synthetic transaction generator with realistic fraud injection.

All randomness flows from a single seeded `random.Random`, and timestamps come from a
fixed virtual start time — so a given `seed` reproduces the exact same stream (no wall
clock involved). Real-time pacing is the runner's job, not the generator's.

Fraud patterns are expressed purely in the RAW contract fields so they survive into the
15 downstream features:

  * velocity_spike   - many txns for one card, seconds apart (-> txn_velocity_1h/24h)
  * country_mismatch - ip_country != the card's home country (-> country_mismatch)
  * amount_outlier   - amount >> the card's typical amount    (-> amount_zscore)
  * unusual_hour     - timestamp hour in the dead of night     (-> is_unusual_hour)
  * new_device       - device_id never seen for the card       (-> device_seen_before)
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from random import Random

from .config import SimulatorConfig
from .schema import Transaction

# Fixed virtual clock origin — keeps runs reproducible by seed alone.
DEFAULT_START = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)

# Geography pool (ISO 3166-1 alpha-2). Skewed toward a few "home" markets.
COUNTRIES: tuple[str, ...] = ("DE", "US", "GB", "FR", "ES", "IT", "NL", "BR", "IN", "NG")

# Merchant category codes, including higher-risk categories (7995 gambling, 6011 ATM).
MCC_CODES: tuple[str, ...] = (
    "5411",  # grocery
    "5812",  # restaurants
    "5912",  # pharmacies
    "5944",  # jewelry
    "4111",  # transit
    "5732",  # electronics
    "7995",  # gambling
    "6011",  # ATM / cash
    "4814",  # telecom
    "5999",  # misc retail
)

UNUSUAL_HOURS: tuple[int, ...] = (0, 1, 2, 3, 4, 5)


class FraudPattern(StrEnum):
    """The injected fraud archetypes."""

    VELOCITY_SPIKE = "velocity_spike"
    COUNTRY_MISMATCH = "country_mismatch"
    AMOUNT_OUTLIER = "amount_outlier"
    UNUSUAL_HOUR = "unusual_hour"
    NEW_DEVICE = "new_device"


@dataclass(frozen=True)
class _Card:
    """Internal per-card profile that anchors the realistic + fraud signals."""

    card_hash: str
    home_country: str
    base_amount: float
    active_hours: tuple[int, ...]
    devices: tuple[str, ...]


def _hash_card(index: int, salt: str) -> str:
    """Stable pseudo card-hash, distinct between normal and compromised pools."""
    return hashlib.sha256(f"{salt}-{index}".encode()).hexdigest()[:32]


class TransactionGenerator:
    """Produces `Transaction` records, legitimate and fraudulent.

    Use `generate(n)` for a bounded sequence or `stream()` for an unbounded one. State
    (velocity clusters, new-device counter) is mutated as records are produced, so create
    a fresh generator to reproduce a sequence from the seed.
    """

    def __init__(self, config: SimulatorConfig, start_time: datetime | None = None) -> None:
        self.config = config
        self.start_time = start_time or DEFAULT_START
        self._rng = Random(config.seed)
        self._new_device_counter = 0
        # Per-compromised-card running clock, advanced by seconds to form tight clusters.
        self._velocity_cursor: dict[str, datetime] = {}

        self._merchants: list[tuple[str, str]] = self._build_merchants()
        self._cards: list[_Card] = self._build_cards(config.n_cards, salt="card")
        # Small, dedicated pool so velocity fraud concentrates on few cards (a cluster).
        n_compromised = max(5, config.n_cards // 100)
        self._compromised: list[_Card] = self._build_cards(n_compromised, salt="compromised")

        self._card_by_hash: dict[str, _Card] = {
            c.card_hash: c for c in (*self._cards, *self._compromised)
        }

    # ----- public introspection (used by tests / eval, not by the model) -----
    def home_country(self, card_hash: str) -> str:
        """Home country of a card — for verifying the country_mismatch signature."""
        return self._card_by_hash[card_hash].home_country

    def known_devices(self, card_hash: str) -> frozenset[str]:
        """Devices a card legitimately uses — for verifying the new_device signature."""
        return frozenset(self._card_by_hash[card_hash].devices)

    # ----- generation -----
    def generate(self, n: int) -> list[Transaction]:
        """Return exactly `n` transactions."""
        return [self._next() for _ in range(n)]

    def stream(self) -> Iterator[Transaction]:
        """Yield transactions forever."""
        while True:
            yield self._next()

    def _next(self) -> Transaction:
        if self._rng.random() < self.config.fraud_injection_rate:
            return self._make_fraud()
        return self._make_legit()

    # ----- builders -----
    def _build_merchants(self) -> list[tuple[str, str]]:
        merchants = []
        for i in range(self.config.n_merchants):
            merchants.append((f"M{i:05d}", self._rng.choice(MCC_CODES)))
        return merchants

    def _build_cards(self, count: int, salt: str) -> list[_Card]:
        device_pool = [f"D{i:05d}" for i in range(self.config.n_devices)]
        cards = []
        for i in range(count):
            start_hour = self._rng.randint(6, 10)
            active = tuple(range(start_hour, min(start_hour + self._rng.randint(10, 14), 23)))
            n_dev = self._rng.randint(1, 3)
            devices = tuple(self._rng.choice(device_pool) for _ in range(n_dev))
            cards.append(
                _Card(
                    card_hash=_hash_card(i, salt),
                    home_country=self._rng.choice(COUNTRIES[:5]),
                    base_amount=round(2.71828 ** self._rng.uniform(2.5, 5.0), 2),
                    active_hours=active,
                    devices=devices,
                )
            )
        return cards

    def _sample_time(self, hours: tuple[int, ...]) -> datetime:
        """A timestamp on the start date, with the hour drawn from `hours`."""
        return self.start_time.replace(
            hour=self._rng.choice(hours),
            minute=self._rng.randint(0, 59),
            second=self._rng.randint(0, 59),
        )

    def _legit_amount(self, card: _Card) -> float:
        return round(card.base_amount * (2.71828 ** self._rng.gauss(0.0, 0.4)), 2)

    def _new_transaction(
        self,
        *,
        card: _Card,
        device_id: str,
        ip_country: str,
        amount: float,
        when: datetime,
        is_fraud: bool,
        pattern: FraudPattern | None,
    ) -> Transaction:
        merchant_id, mcc_code = self._rng.choice(self._merchants)
        txn_id = str(uuid.UUID(int=self._rng.getrandbits(128)))
        return Transaction(
            transaction_id=txn_id,
            timestamp=when.isoformat(),
            amount=amount,
            merchant_id=merchant_id,
            card_hash=card.card_hash,
            device_id=device_id,
            ip_country=ip_country,
            mcc_code=mcc_code,
            is_fraud_truth=is_fraud,
            fraud_pattern=pattern.value if pattern else None,
        )

    def _make_legit(self) -> Transaction:
        card = self._rng.choice(self._cards)
        return self._new_transaction(
            card=card,
            device_id=self._rng.choice(card.devices),
            ip_country=card.home_country,
            amount=self._legit_amount(card),
            when=self._sample_time(card.active_hours),
            is_fraud=False,
            pattern=None,
        )

    def _make_fraud(self) -> Transaction:
        pattern = self._rng.choice(list(FraudPattern))
        if pattern is FraudPattern.VELOCITY_SPIKE:
            return self._fraud_velocity_spike()
        if pattern is FraudPattern.COUNTRY_MISMATCH:
            return self._fraud_country_mismatch()
        if pattern is FraudPattern.AMOUNT_OUTLIER:
            return self._fraud_amount_outlier()
        if pattern is FraudPattern.UNUSUAL_HOUR:
            return self._fraud_unusual_hour()
        return self._fraud_new_device()

    def _fraud_velocity_spike(self) -> Transaction:
        card = self._rng.choice(self._compromised)
        # Advance this card's private clock by only seconds → a tight per-card cluster.
        cursor = self._velocity_cursor.get(card.card_hash)
        when = cursor or self._sample_time(card.active_hours)
        self._velocity_cursor[card.card_hash] = when + timedelta(seconds=self._rng.randint(1, 15))
        return self._new_transaction(
            card=card,
            device_id=self._rng.choice(card.devices),
            ip_country=card.home_country,
            amount=self._legit_amount(card),
            when=when,
            is_fraud=True,
            pattern=FraudPattern.VELOCITY_SPIKE,
        )

    def _fraud_country_mismatch(self) -> Transaction:
        card = self._rng.choice(self._cards)
        foreign = self._rng.choice([c for c in COUNTRIES if c != card.home_country])
        return self._new_transaction(
            card=card,
            device_id=self._rng.choice(card.devices),
            ip_country=foreign,
            amount=self._legit_amount(card),
            when=self._sample_time(card.active_hours),
            is_fraud=True,
            pattern=FraudPattern.COUNTRY_MISMATCH,
        )

    def _fraud_amount_outlier(self) -> Transaction:
        card = self._rng.choice(self._cards)
        amount = round(card.base_amount * self._rng.uniform(8.0, 20.0), 2)
        return self._new_transaction(
            card=card,
            device_id=self._rng.choice(card.devices),
            ip_country=card.home_country,
            amount=amount,
            when=self._sample_time(card.active_hours),
            is_fraud=True,
            pattern=FraudPattern.AMOUNT_OUTLIER,
        )

    def _fraud_unusual_hour(self) -> Transaction:
        card = self._rng.choice(self._cards)
        return self._new_transaction(
            card=card,
            device_id=self._rng.choice(card.devices),
            ip_country=card.home_country,
            amount=self._legit_amount(card),
            when=self._sample_time(UNUSUAL_HOURS),
            is_fraud=True,
            pattern=FraudPattern.UNUSUAL_HOUR,
        )

    def _fraud_new_device(self) -> Transaction:
        card = self._rng.choice(self._cards)
        self._new_device_counter += 1
        fresh_device = f"D-new-{self._new_device_counter:06d}"
        return self._new_transaction(
            card=card,
            device_id=fresh_device,
            ip_country=card.home_country,
            amount=self._legit_amount(card),
            when=self._sample_time(card.active_hours),
            is_fraud=True,
            pattern=FraudPattern.NEW_DEVICE,
        )
