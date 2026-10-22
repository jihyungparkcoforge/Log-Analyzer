"""Regenerate data/sample.log and data/sample.log.metadata.json.

Every run is unseeded, so background traffic differs between runs; the planted
incident is built by construction and always fires both detectors. Metadata is
computed from the lines actually written. Humans regenerate, then commit both files.

    uv run python scripts/generate_logs.py [--format comma]
"""

import argparse
import json
import random
import string
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
LOG_PATH = DATA_DIR / "sample.log"
METADATA_PATH = DATA_DIR / "sample.log.metadata.json"

START = datetime(2026, 10, 19, 14, 0, 0)
END = datetime(2026, 10, 19, 17, 0, 0)
SPIKE_START = datetime(2026, 10, 19, 15, 30, 0)
SPIKE_END = datetime(2026, 10, 19, 15, 45, 0)
WINDOW = timedelta(minutes=5)

MALFORMED_RATE = 0.02
MALFORMED_IN_SPIKE_PER_WINDOW = 2
MALFORMED_KINDS = (
    "bad_timestamp",
    "garbage",
    "truncated",
    "missing_fields",
    "blank",
    "stack_trace",
    "unknown_level",
)

SERVICES = (
    "payment-service",
    "order-service",
    "auth-service",
    "inventory-service",
    "api-gateway",
)
PLANTED_SERVICE = "payment-service"
PLANTED_MESSAGE = "Connection timeout to db-primary"

# Background ERRORs per quiet window stay in 0..4 so the spike baseline stays low.
MAX_BACKGROUND_ERRORS = 4
REPEATS_PER_SPIKE_WINDOW = (15, 30)
OTHER_ERRORS_PER_SPIKE_WINDOW = (12, 25)

INFO_MESSAGES = {
    "payment-service": [
        "Payment authorized order_id={id}",
        "Refund processed order_id={id}",
        "Payment captured amount={amt} currency=USD",
    ],
    "order-service": [
        "Order created order_id={id}",
        "Order shipped order_id={id}",
        "Order cancelled order_id={id}, reason=customer_request",
    ],
    "auth-service": [
        "User login successful user_id={id}",
        "Session expired user_id={id}",
        "Token refreshed user_id={id}",
    ],
    "inventory-service": [
        "Stock updated sku=SKU-{n} qty={q}",
        "Reservation released sku=SKU-{n}",
    ],
    "api-gateway": [
        "GET /api/orders 200 latency_ms={ms}",
        "POST /api/payments 201 latency_ms={ms}",
    ],
}
WARNING_MESSAGES = [
    "Slow response latency_ms={ms}",
    "Retrying request attempt=2 order_id={id}",
    "Rate limit approaching client_id={id}",
    "Cache miss ratio high ratio=0.{q}",
]
# `{uid}` is unique within a run, so background errors never form repeat alerts.
BACKGROUND_ERRORS = {
    "payment-service": ["Card declined order_id={uid}", "Gateway returned 502 order_id={uid}"],
    "order-service": ["Failed to persist order order_id={uid}"],
    "auth-service": ["Invalid credentials user_id={uid}"],
    "inventory-service": ["Stock mismatch sku=SKU-{uid}"],
    "api-gateway": ["Upstream request failed request_id={uid}"],
}
INCIDENT_ERRORS = {
    "payment-service": ["Payment authorization failed order_id={uid}"],
    "order-service": ["Order submission failed order_id={uid}"],
    "api-gateway": [
        "Upstream request failed request_id={uid}",
        "POST /api/payments 504 request_id={uid}",
    ],
}
STACK_LINES = [
    "\tat com.example.payment.DbClient.connect(DbClient.java:142)",
    "\tat com.example.payment.PaymentHandler.charge(PaymentHandler.java:88)",
    "Caused by: java.net.SocketTimeoutException: connect timed out",
    "    at org.springframework.web.servlet.FrameworkServlet.service(FrameworkServlet.java:897)",
]


@dataclass(frozen=True)
class Event:
    """One well-formed log event, independent of any output format."""

    timestamp: datetime
    level: str
    service: str
    message: str


@dataclass(frozen=True)
class Malformed:
    """A deliberately broken line.

    Attributes:
        kind: Generator label (see MALFORMED_KINDS), not a parser reason tag.
        timestamp: Where the line sits in the file's time order.
        base: The event a serializer may corrupt; unused by kinds like `garbage`.
    """

    kind: str
    timestamp: datetime
    base: Event


Item = Event | Malformed


class Serializer(Protocol):
    """Turns planned items into lines of one output format."""

    name: str

    def serialize(self, item: Item) -> str:
        """Return the line text (without newline) for an event or malformed spec."""
        ...


class CommaSerializer:
    """`YYYY-MM-DD HH:MM:SS,LEVEL,service,message`, the CommaSeparatedStrategy format."""

    name = "comma"

    def __init__(self, rng: random.Random) -> None:
        """Keep the run's RNG for corruptions that need randomness."""
        self._rng = rng

    def serialize(self, item: Item) -> str:
        """Format an event as a comma line, or corrupt a malformed spec."""
        if isinstance(item, Event):
            return self._line(item)
        return self._corrupt(item)

    @staticmethod
    def _stamp(moment: datetime) -> str:
        """Render a timestamp in the strategy's `%Y-%m-%d %H:%M:%S` format."""
        return f"{moment:%Y-%m-%d %H:%M:%S}"

    def _line(self, event: Event) -> str:
        """Render a valid event."""
        return f"{self._stamp(event.timestamp)},{event.level},{event.service},{event.message}"

    def _corrupt(self, spec: Malformed) -> str:
        """Build a line of the requested kind that the comma parser must reject."""
        rng, base = self._rng, spec.base
        stamp = self._stamp(base.timestamp)
        if spec.kind == "bad_timestamp":
            clock = stamp.split(" ")[1]
            bad = rng.choice(
                [f"20XX-10-19 {clock}", f"2026-13-45 {clock}", f"2026-10-19T{clock}", f"19/10/2026 {clock}"]
            )
            return f"{bad},{base.level},{base.service},{base.message}"
        if spec.kind == "unknown_level":
            level = rng.choice(["FATAL", "FATAL", "CRITICAL", "DEBUG"])
            return f"{stamp},{level},{base.service},{base.message}"
        if spec.kind == "missing_fields":
            return rng.choice([f"{stamp},{base.level}", f"{stamp},{base.level},{base.service}", stamp])
        if spec.kind == "truncated":
            # Cut before the third comma so fewer than four fields remain.
            line = self._line(base)
            third_comma = line.index(",", line.index(",", line.index(",") + 1) + 1)
            return line[: rng.randint(1, third_comma)]
        if spec.kind == "garbage":
            alphabet = "".join(c for c in string.ascii_letters + string.digits + string.punctuation if c != ",")
            return "".join(rng.choice(alphabet) for _ in range(rng.randint(20, 60)))
        if spec.kind == "blank":
            return rng.choice(["", "", "   ", "\t"])
        if spec.kind == "stack_trace":
            return rng.choice(STACK_LINES)
        raise ValueError(f"unknown malformed kind {spec.kind!r}")


SERIALIZERS = {"comma": CommaSerializer}


class _Fill:
    """Fills message templates with random values and run-unique ids."""

    def __init__(self, rng: random.Random) -> None:
        """Pre-shuffle a pool of unique ids for the `{uid}` placeholder."""
        self._rng = rng
        self._unique = iter(rng.sample(range(100_000, 1_000_000), 5_000))

    def __call__(self, template: str) -> str:
        """Return the template with every placeholder replaced."""
        rng = self._rng
        return template.format(
            id=rng.randint(10_000, 99_999),
            uid=next(self._unique),
            n=rng.randint(100, 999),
            q=rng.randint(1, 99),
            ms=rng.randint(5, 900),
            amt=f"{rng.uniform(5, 500):.2f}",
        )


def _random_time(rng: random.Random, start: datetime, end: datetime) -> datetime:
    """Pick a whole-second timestamp in [start, end)."""
    return start + timedelta(seconds=rng.randrange(int((end - start).total_seconds())))


def _background_event(rng: random.Random, fill: _Fill, when: datetime) -> Event:
    """Create an INFO (mostly) or WARNING event for a random service."""
    service = rng.choice(SERVICES)
    if rng.random() < 0.15:
        return Event(when, "WARNING", service, fill(rng.choice(WARNING_MESSAGES)))
    return Event(when, "INFO", service, fill(rng.choice(INFO_MESSAGES[service])))


def _error_event(rng: random.Random, fill: _Fill, when: datetime, pool: dict) -> Event:
    """Create an ERROR with a unique message drawn from `pool`."""
    service = rng.choice(list(pool))
    return Event(when, "ERROR", service, fill(rng.choice(pool[service])))


def plan_items(rng: random.Random) -> list[Item]:
    """Plan the whole run in memory: events, planted incident and malformed lines.

    Quiet windows hold at most MAX_BACKGROUND_ERRORS errors, all with unique
    messages; each of the three incident windows gets 27+ errors, at least 15 of
    them the identical planted message, so both detectors fire by construction.
    """
    fill = _Fill(rng)
    items: list[Item] = []
    window_start = START
    while window_start < END:
        window_end = window_start + WINDOW
        for _ in range(rng.randint(85, 115)):
            items.append(_background_event(rng, fill, _random_time(rng, window_start, window_end)))
        if SPIKE_START <= window_start < SPIKE_END:
            repeats = rng.randint(*REPEATS_PER_SPIKE_WINDOW)
            for _ in range(repeats):
                when = _random_time(rng, window_start, window_end)
                items.append(Event(when, "ERROR", PLANTED_SERVICE, PLANTED_MESSAGE))
            for _ in range(rng.randint(*OTHER_ERRORS_PER_SPIKE_WINDOW)):
                when = _random_time(rng, window_start, window_end)
                items.append(_error_event(rng, fill, when, INCIDENT_ERRORS))
        else:
            for _ in range(rng.randint(0, MAX_BACKGROUND_ERRORS)):
                when = _random_time(rng, window_start, window_end)
                items.append(_error_event(rng, fill, when, BACKGROUND_ERRORS))
        window_start = window_end

    valid = len(items)
    in_spike = MALFORMED_IN_SPIKE_PER_WINDOW * int((SPIKE_END - SPIKE_START) / WINDOW)
    total_bad = max(round(valid * MALFORMED_RATE / (1 - MALFORMED_RATE)), in_spike, 20)
    kinds = [MALFORMED_KINDS[i % len(MALFORMED_KINDS)] for i in range(total_bad)]
    rng.shuffle(kinds)
    times = []
    for index in range(in_spike):
        slot = SPIKE_START + (index // MALFORMED_IN_SPIKE_PER_WINDOW) * WINDOW
        times.append(_random_time(rng, slot, slot + WINDOW))
    times += [_random_time(rng, START, END) for _ in range(total_bad - in_spike)]
    for kind, when in zip(kinds, times):
        if SPIKE_START <= when < SPIKE_END:
            base = Event(when, "ERROR", PLANTED_SERVICE, PLANTED_MESSAGE)
        else:
            base = _background_event(rng, fill, when)
        items.append(Malformed(kind, when, base))

    items.sort(key=lambda item: item.timestamp)
    return items


def build_metadata(items: list[Item], line_count: int, fmt: str) -> dict:
    """Describe the file just written, using only its items and line count."""
    malformed = [item for item in items if isinstance(item, Malformed)]
    by_kind = {kind: 0 for kind in MALFORMED_KINDS}
    for item in malformed:
        by_kind[item.kind] += 1
    window_counts = []
    window_start = SPIKE_START
    while window_start < SPIKE_END:
        window_end = window_start + WINDOW
        window_counts.append(
            sum(
                1
                for item in items
                if isinstance(item, Event)
                and item.level == "ERROR"
                and window_start <= item.timestamp < window_end
            )
        )
        window_start = window_end
    return {
        "format": fmt,
        "start": f"{START:%Y-%m-%d %H:%M:%S}",
        "end": f"{END:%Y-%m-%d %H:%M:%S}",
        "total_lines": line_count,
        "malformed_lines": len(malformed),
        "malformed_by_kind": by_kind,
        "spike": {
            "start": f"{SPIKE_START:%Y-%m-%d %H:%M:%S}",
            "end": f"{SPIKE_END:%Y-%m-%d %H:%M:%S}",
            "window_error_counts": window_counts,
            "approx_error_count": sum(window_counts),
            "dominant_service": PLANTED_SERVICE,
            "top_message_template": PLANTED_MESSAGE,
        },
    }


def main(argv: list[str] | None = None) -> int:
    """Generate the sample log and its metadata; return a process exit code."""
    parser = argparse.ArgumentParser(description="Regenerate data/sample.log and its metadata.")
    parser.add_argument("--format", default="comma", help="output format (supported: comma)")
    args = parser.parse_args(argv)

    if args.format not in SERIALIZERS:
        supported = ", ".join(SERIALIZERS)
        print(f"unsupported format '{args.format}' (supported: {supported})", file=sys.stderr)
        return 2

    rng = random.Random()
    serializer = SERIALIZERS[args.format](rng)
    items = plan_items(rng)
    text = "".join(serializer.serialize(item) + "\n" for item in items)
    # Every line ends in exactly one newline, so this is the real written line count.
    line_count = text.count("\n")
    if line_count != len(items):
        raise RuntimeError("a serialized line contained a newline")
    metadata = build_metadata(items, line_count, serializer.name)

    DATA_DIR.mkdir(exist_ok=True)
    LOG_PATH.write_text(text, encoding="utf-8")
    METADATA_PATH.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {LOG_PATH} ({line_count} lines) and {METADATA_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
