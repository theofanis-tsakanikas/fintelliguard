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

from ml.features.transforms import HOUR_TOLERANCE, circular_hour_distance

from .config import SimulatorConfig
from .schema import Transaction

# Fixed virtual clock origin — keeps runs reproducible by seed alone.
DEFAULT_START = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)

# Days of card history the simulated stream spans. Cards need an age and the 30-day amount
# window needs more than one day to be a window; a single-date stream gave neither.
HISTORY_DAYS = 45

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
        # Every card has a private clock that only moves forward. Transactions for a card
        # are therefore ordered and realistically spaced, which is what makes the 1h/24h
        # window features mean anything.
        self._card_clock: dict[str, datetime] = {}

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

    def _card_start(self, card: _Card) -> datetime:
        """When this card first appears. Cards enter the population at different times.

        This is what gives `card_age_days` a range. Deterministic per card (derived from
        the card hash) so a card's age does not depend on the order transactions happen to
        be drawn in.
        """
        offset = int(card.card_hash[:8], 16) % HISTORY_DAYS
        return self.start_time + timedelta(days=offset)

    def _sample_time(self, card: _Card, hours: tuple[int, ...] | None = None) -> datetime:
        """The card's NEXT transaction time, advancing its private clock forward.

        Was `self.start_time.replace(hour=...)`: every transaction the simulator ever
        produced landed on the single start date, drawn independently of every other. Three
        silent consequences, all of which shaped the model the local funnel serves:

        * `card_age_days` could never exceed 0 — a constant feature, everywhere.
        * a card's whole life fitted in 24h, so the 30-day amount window and the
          active-hour band never saw more than one day of behaviour.
        * emission order was uncorrelated with event time, so ~50% of events arrived
          out of order — invisible only because Gold recomputes the whole table.

        A per-card clock with realistic gaps fixes all three: transactions for a card march
        forward, some within the hour (velocity), some within the day, some days apart. The
        1h/24h windows are populated because a card's events are actually near each other.
        """
        active = hours or card.active_hours
        clock = self._card_clock.get(card.card_hash) or self._card_start(card)
        when = self._snap_to_active(clock + self._inter_arrival(), active)
        self._card_clock[card.card_hash] = when
        return when

    def _inter_arrival(self) -> timedelta:
        """A gap to the card's next transaction, mixing bursts, same-day and quiet spells."""
        bucket = self._rng.random()
        if bucket < 0.30:  # same 1h window — velocity has something to count
            return timedelta(minutes=self._rng.randint(2, 45))
        if bucket < 0.70:  # same 24h window
            return timedelta(hours=self._rng.randint(2, 10))
        return timedelta(days=self._rng.randint(1, 4), hours=self._rng.randint(0, 6))

    @staticmethod
    def _snap_to_active(when: datetime, active: tuple[int, ...]) -> datetime:
        """Move FORWARD to the card's next active hour. Never backwards — the clock is a clock."""
        for _ in range(24):
            if when.hour in active:
                return when
            when += timedelta(hours=1)
        return when

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
            when=self._sample_time(card),
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
        # A burst: advance the card's clock by SECONDS rather than the usual gap, so these
        # land inside one 1h window and txn_velocity_1h actually spikes.
        clock = self._card_clock.get(card.card_hash) or self._card_start(card)
        when = clock + timedelta(seconds=self._rng.randint(1, 15))
        self._card_clock[card.card_hash] = when
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
            when=self._sample_time(card),
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
            when=self._sample_time(card),
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
            when=self._sample_time(card, self._unusual_hours_for(card)),
            is_fraud=True,
            pattern=FraudPattern.UNUSUAL_HOUR,
        )

    @staticmethod
    def _unusual_hours_for(card: _Card) -> tuple[int, ...]:
        """Night hours that are genuinely FAR from this card's active band.

        `UNUSUAL_HOURS` is 00:00-05:00 for every card, but cards are active from 06:00-10:00
        onward — so 05:00 on a card that starts at 06:00 is an hour's difference, and
        labelling it fraud teaches the model that ordinary early activity is suspicious.
        The feature (correctly) does not flag it, which is how this surfaced.

        Injecting only hours the card's own pattern makes unusual keeps the archetype a
        real signal rather than a label the features cannot support.
        """
        far = tuple(
            hour
            for hour in UNUSUAL_HOURS
            if min(circular_hour_distance(hour, active) for active in card.active_hours)
            > HOUR_TOLERANCE
        )
        return far or UNUSUAL_HOURS

    def _fraud_new_device(self) -> Transaction:
        card = self._rng.choice(self._cards)
        self._new_device_counter += 1
        fresh_device = f"D-new-{self._new_device_counter:06d}"
        return self._new_transaction(
            card=card,
            device_id=fresh_device,
            ip_country=card.home_country,
            amount=self._legit_amount(card),
            when=self._sample_time(card),
            is_fraud=True,
            pattern=FraudPattern.NEW_DEVICE,
        )
