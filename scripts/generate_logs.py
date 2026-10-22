"""Regenerate the committed sample logs, or write a throwaway log elsewhere.

Two modes:

    # Fixture mode: one seeded plan, written once per registered serializer to its
    # FIXTURES path, each with a sibling <path>.metadata.json. Same seed, same bytes.
    uv run python scripts/generate_logs.py --seed 42

    # Ad-hoc mode: one file at --out plus <out>.metadata.json; never touches data/.
    # --seed is optional (reproducible if given); --format defaults to comma.
    uv run python scripts/generate_logs.py --out /tmp/foo.log [--format comma|json] [--seed 7]

With --seed and --out together, ad-hoc wins: only --out is written. With neither,
the script exits 2 instead of overwriting the fixtures.

The planted incident is built by construction and always fires both detectors, and
metadata is computed from the lines actually written. Commit the fixtures together.
"""

import argparse
import json
import random
import string
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol

# Output locations, resolved from this file so the script works from any cwd.
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
# Fixture-mode path per format name. Fixture mode writes every key that is also
# in SERIALIZERS; unregistered names are skipped.
FIXTURES = {
    "comma": DATA_DIR / "sample.log",
    "json": DATA_DIR / "sample.jsonl",
}

# Log span is [START, END); the incident is [SPIKE_START, SPIKE_END), three 5-minute windows.
START = datetime(2026, 10, 19, 14, 0, 0)
END = datetime(2026, 10, 19, 17, 0, 0)
SPIKE_START = datetime(2026, 10, 19, 15, 30, 0)
SPIKE_END = datetime(2026, 10, 19, 15, 45, 0)
# Matches the detectors' default window; boundaries are midnight-aligned.
WINDOW = timedelta(minutes=5)

# Share of all lines that are deliberately broken (at least 20 are always made).
MALFORMED_RATE = 0.02
# Broken lines forced into each incident window, so the parser is tested under load.
MALFORMED_IN_SPIKE_PER_WINDOW = 2
# Generator labels for broken lines; these are not the parser's reason tags.
MALFORMED_KINDS = (
    "bad_timestamp",
    "garbage",
    "truncated",
    "missing_fields",
    "blank",
    "stack_trace",
    "unknown_level",
)

# Services that emit background INFO/WARNING traffic and background errors.
SERVICES = (
    "payment-service",
    "order-service",
    "auth-service",
    "inventory-service",
    "api-gateway",
)
# The exact service and message repeated during the incident (no ids, so it groups).
PLANTED_SERVICE = "payment-service"
PLANTED_MESSAGE = "Connection timeout to db-primary"

# Background ERRORs per quiet window stay in 0..4 so the spike baseline stays low.
MAX_BACKGROUND_ERRORS = 4
# (min, max) inclusive counts per incident window: planted repeats, and other errors.
# Together they stay well above the spike detector's threshold and the repeat critical level.
REPEATS_PER_SPIKE_WINDOW = (15, 30)
OTHER_ERRORS_PER_SPIKE_WINDOW = (12, 25)

# Message templates. Placeholders: {id} {n} {q} {ms} {amt} are random; {uid} is unique per run.
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
# Quiet-period errors: `{uid}` is unique within a run, so these never form repeat alerts.
BACKGROUND_ERRORS = {
    "payment-service": [
        "Card declined order_id={uid}",
        "Gateway returned 502 order_id={uid}",
    ],
    "order-service": ["Failed to persist order order_id={uid}"],
    "auth-service": ["Invalid credentials user_id={uid}"],
    "inventory-service": ["Stock mismatch sku=SKU-{uid}"],
    "api-gateway": ["Upstream request failed request_id={uid}"],
}
# Other errors during the incident; varied on purpose, only the planted one repeats.
INCIDENT_ERRORS = {
    "payment-service": ["Payment authorization failed order_id={uid}"],
    "order-service": ["Order submission failed order_id={uid}"],
    "api-gateway": [
        "Upstream request failed request_id={uid}",
        "POST /api/payments 504 request_id={uid}",
    ],
}
# Comma-free continuation lines, so each is rejected as a malformed line.
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
    """Turns planned items into lines of one output format.

    Implement this (and register it in SERIALIZERS) to support a new output format.

    Attributes:
        name: Format name used by `--format` and recorded in the metadata.
    """

    name: str

    def serialize(self, item: Item) -> str:
        """Return the line text (without newline) for an event or malformed spec.

        Args:
            item: A valid Event, or a Malformed spec to corrupt into a bad line.
        """
        ...


class CommaSerializer:
    """`YYYY-MM-DD HH:MM:SS,LEVEL,service,message`, the CommaSeparatedStrategy format."""

    name = "comma"

    def __init__(self, rng: random.Random) -> None:
        """
        Args:
            rng: The run's random source, used for corruptions that need randomness.
        """
        self._rng = rng

    def serialize(self, item: Item) -> str:
        """Format an event as a comma line, or corrupt a malformed spec.

        Args:
            item: A valid Event, or a Malformed spec.

        Returns:
            A comma-format line without its trailing newline.
        """
        if isinstance(item, Event):
            return self._line(item)
        return self._corrupt(item)

    @staticmethod
    def _stamp(moment: datetime) -> str:
        """Render a timestamp in the strategy's `%Y-%m-%d %H:%M:%S` format.

        Args:
            moment: A naive datetime.
        """
        return f"{moment:%Y-%m-%d %H:%M:%S}"

    def _line(self, event: Event) -> str:
        """Render a valid event.

        Args:
            event: The event to format as `timestamp,LEVEL,service,message`.
        """
        return f"{self._stamp(event.timestamp)},{event.level},{event.service},{event.message}"

    def _corrupt(self, spec: Malformed) -> str:
        """Build a line of the requested kind that the comma parser must reject.

        Args:
            spec: Which kind of breakage to apply, and the event to base it on.

        Returns:
            The broken line, which never parses as a valid entry.

        Raises:
            ValueError: If `spec.kind` is not in MALFORMED_KINDS.
        """
        rng, base = self._rng, spec.base
        stamp = self._stamp(base.timestamp)
        if spec.kind == "bad_timestamp":
            clock = stamp.split(" ")[1]
            bad = rng.choice(
                [
                    f"20XX-10-19 {clock}",
                    f"2026-13-45 {clock}",
                    f"2026-10-19T{clock}",
                    f"19/10/2026 {clock}",
                ]
            )
            return f"{bad},{base.level},{base.service},{base.message}"
        if spec.kind == "unknown_level":
            level = rng.choice(["FATAL", "FATAL", "CRITICAL", "DEBUG"])
            return f"{stamp},{level},{base.service},{base.message}"
        if spec.kind == "missing_fields":
            return rng.choice(
                [f"{stamp},{base.level}", f"{stamp},{base.level},{base.service}", stamp]
            )
        if spec.kind == "truncated":
            # Cut before the third comma so fewer than four fields remain.
            line = self._line(base)
            third_comma = line.index(",", line.index(",", line.index(",") + 1) + 1)
            return line[: rng.randint(1, third_comma)]
        if spec.kind == "garbage":
            alphabet = "".join(
                c
                for c in string.ascii_letters + string.digits + string.punctuation
                if c != ","
            )
            return "".join(rng.choice(alphabet) for _ in range(rng.randint(20, 60)))
        if spec.kind == "blank":
            return rng.choice(["", "", "   ", "\t"])
        if spec.kind == "stack_trace":
            return rng.choice(STACK_LINES)
        raise ValueError(f"unknown malformed kind {spec.kind!r}")


class JsonSerializer:
    """JSON Lines: one compact `{"ts","level","service","message"}` object per line.

    `json.dumps` escapes quotes, commas and newlines, so every event stays on one
    physical line. Malformed kinds are broken for JsonStrategy specifically.
    """

    name = "json"
    KEYS = ("ts", "level", "service", "message")

    def __init__(self, rng: random.Random) -> None:
        """
        Args:
            rng: The run's random source, used for corruptions that need randomness.
        """
        self._rng = rng

    def serialize(self, item: Item) -> str:
        """Format an event as a JSON line, or corrupt a malformed spec.

        Args:
            item: A valid Event, or a Malformed spec.

        Returns:
            A single-line string without its trailing newline.
        """
        if isinstance(item, Event):
            return self._dump(self._record(item))
        return self._corrupt(item)

    @staticmethod
    def _dump(record: dict) -> str:
        """Encode a record compactly on one line (no spaces, no raw newlines)."""
        return json.dumps(record, separators=(",", ":"))

    def _record(self, event: Event) -> dict:
        """Build the JSON object for a valid event, keys in a fixed order."""
        return dict(
            zip(
                self.KEYS,
                (
                    CommaSerializer._stamp(event.timestamp),
                    event.level,
                    event.service,
                    event.message,
                ),
            )
        )

    def _corrupt(self, spec: Malformed) -> str:
        """Build a line of the requested kind that JsonStrategy must reject.

        Args:
            spec: Which kind of breakage to apply, and the event to base it on.

        Returns:
            The broken line, which never parses as a valid entry.

        Raises:
            ValueError: If `spec.kind` is not in MALFORMED_KINDS.
        """
        rng = self._rng
        record = self._record(spec.base)
        if spec.kind == "bad_timestamp":
            clock = record["ts"].split(" ")[1]
            record["ts"] = rng.choice(
                [
                    f"20XX-10-19 {clock}",
                    f"2026-13-45 {clock}",
                    f"2026-10-19T{clock}",
                    f"19/10/2026 {clock}",
                ]
            )
            return self._dump(record)
        if spec.kind == "unknown_level":
            record["level"] = rng.choice(["FATAL", "FATAL", "CRITICAL", "DEBUG"])
            return self._dump(record)
        if spec.kind == "missing_fields":
            del record[rng.choice(self.KEYS)]
            return self._dump(record)
        if spec.kind == "truncated":
            # Drop at least the closing brace, so the object can never be complete.
            line = self._dump(record)
            return line[: rng.randint(1, len(line) - 1)]
        if spec.kind == "garbage":
            # No braces, so the text cannot be mistaken for a JSON object.
            alphabet = "".join(
                c
                for c in string.ascii_letters + string.digits + string.punctuation
                if c not in "{},"
            )
            return "".join(rng.choice(alphabet) for _ in range(rng.randint(20, 60)))
        if spec.kind == "blank":
            return rng.choice(["", "", "   ", "\t"])
        if spec.kind == "stack_trace":
            return rng.choice(STACK_LINES)
        raise ValueError(f"unknown malformed kind {spec.kind!r}")


SERIALIZERS = {"comma": CommaSerializer, "json": JsonSerializer}


class _Fill:
    """Fills message templates with random values and run-unique ids."""

    def __init__(self, rng: random.Random) -> None:
        """
        Args:
            rng: The run's random source; also used to pre-shuffle the unique-id pool.
        """
        self._rng = rng
        self._unique = iter(rng.sample(range(100_000, 1_000_000), 5_000))

    def __call__(self, template: str) -> str:
        """Return the template with every placeholder replaced.

        Args:
            template: A message template such as `Order shipped order_id={id}`.

        Raises:
            StopIteration: If more than 5,000 unique ids are requested in one run.
        """
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
    """Pick a whole-second timestamp in [start, end).

    Args:
        rng: Random source.
        start: Inclusive lower bound.
        end: Exclusive upper bound.
    """
    return start + timedelta(seconds=rng.randrange(int((end - start).total_seconds())))


def _background_event(rng: random.Random, fill: _Fill, when: datetime) -> Event:
    """Create an INFO (mostly) or WARNING event for a random service.

    Args:
        rng: Random source.
        fill: Template filler for the message.
        when: Timestamp of the event.
    """
    service = rng.choice(SERVICES)
    if rng.random() < 0.15:
        return Event(when, "WARNING", service, fill(rng.choice(WARNING_MESSAGES)))
    return Event(when, "INFO", service, fill(rng.choice(INFO_MESSAGES[service])))


def _error_event(rng: random.Random, fill: _Fill, when: datetime, pool: dict) -> Event:
    """Create an ERROR with a unique message drawn from `pool`.

    Args:
        rng: Random source.
        fill: Template filler (supplies the unique id).
        when: Timestamp of the event.
        pool: Mapping of service name to error message templates.
    """
    service = rng.choice(list(pool))
    return Event(when, "ERROR", service, fill(rng.choice(pool[service])))


def plan_items(rng: random.Random) -> list[Item]:
    """Plan the whole run in memory: events, planted incident and malformed lines.

    Quiet windows hold at most MAX_BACKGROUND_ERRORS errors, all with unique
    messages; each of the three incident windows gets 27+ errors, at least 15 of
    them the identical planted message, so both detectors fire by construction.

    Args:
        rng: Random source; unseeded in normal runs.

    Returns:
        Events and malformed specs sorted by timestamp (stable for ties).
    """
    fill = _Fill(rng)
    items: list[Item] = []
    window_start = START
    while window_start < END:
        window_end = window_start + WINDOW
        for _ in range(rng.randint(85, 115)):
            items.append(
                _background_event(
                    rng, fill, _random_time(rng, window_start, window_end)
                )
            )
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


def build_metadata(
    items: list[Item], line_count: int, fmt: str, seed: int | None = None
) -> dict:
    """Describe the file just written, using only its items and line count.

    Only well-formed ERROR events count toward `window_error_counts`.

    Args:
        items: Everything that was serialized, in file order.
        line_count: Number of lines actually written, blanks included.
        fmt: Name of the format the file was written in.
        seed: The seed the run used; the "seed" key is omitted when None.

    Returns:
        A JSON-serializable dict matching data/README.md's metadata keys.
    """
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
    metadata = {"format": fmt}
    if seed is not None:
        metadata["seed"] = seed
    return metadata | {
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


def write_log(
    path: Path, items: list[Item], serializer: Serializer, seed: int | None = None
) -> dict:
    """Serialize `items` to `path` and write `<path>.metadata.json` beside it.

    Metadata comes from this write: the real line count, the malformed items and
    the planted-window error counts.

    Args:
        path: Log file to write; missing parent directories are created.
        items: The planned events and malformed specs, in file order.
        serializer: Turns each item into one line of the output format.
        seed: Recorded as the metadata's "seed" when given.

    Returns:
        The metadata that was written.

    Raises:
        RuntimeError: If a serialized line contained a newline.
    """
    text = "".join(serializer.serialize(item) + "\n" for item in items)
    # Every line ends in exactly one newline, so this is the real written line count.
    line_count = text.count("\n")
    if line_count != len(items):
        raise RuntimeError("a serialized line contained a newline")
    metadata = build_metadata(items, line_count, serializer.name, seed)

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as log:
        log.write(text)
    metadata_path = Path(f"{path}.metadata.json")
    with open(metadata_path, "w", encoding="utf-8", newline="\n") as meta:
        meta.write(json.dumps(metadata, indent=2) + "\n")
    print(f"wrote {path} ({line_count} lines) and {metadata_path}")
    return metadata


def _serializer(name: str, seed: int | None, rng: random.Random) -> Serializer:
    """Build the named serializer with its own random source.

    With a seed each serializer gets an independent stream derived from the seed
    and its name, so one format's bytes never depend on which others are registered.

    Args:
        name: A key of SERIALIZERS.
        seed: The run's seed, or None for an unseeded run.
        rng: The run's main random source, used when unseeded.
    """
    own = rng if seed is None else random.Random(f"{seed}/{name}")
    return SERIALIZERS[name](own)


def main(
    argv: list[str] | None = None, fixtures: Mapping[str, Path] | None = None
) -> int:
    """Run the generator in fixture mode (--seed) or ad-hoc mode (--out).

    Args:
        argv: Command-line arguments; defaults to `sys.argv[1:]`.
        fixtures: Fixture path per format name; defaults to FIXTURES. Tests inject
            a temporary directory here so the repo's data/ is never touched.

    Returns:
        0 on success; 2 for an unsupported `--format`, or if neither `--seed` nor
        `--out` was given. Nothing is written in either error case.
    """
    fixtures = FIXTURES if fixtures is None else fixtures
    parser = argparse.ArgumentParser(
        description="Regenerate the sample fixtures (--seed) or write one log (--out)."
    )
    parser.add_argument(
        "--seed",
        type=int,
        help="seed for a reproducible run; alone, regenerates every fixture",
    )
    parser.add_argument(
        "--out",
        type=Path,
        help="write only this log and <out>.metadata.json; leaves data/ alone",
    )
    parser.add_argument(
        "--format",
        help=(
            f"output format ({', '.join(SERIALIZERS)}); default comma with --out; "
            "with --seed alone, limits the fixtures written to this format"
        ),
    )
    args = parser.parse_args(argv)

    if args.format is not None and args.format not in SERIALIZERS:
        supported = ", ".join(SERIALIZERS)
        print(
            f"unsupported format '{args.format}' (supported: {supported})",
            file=sys.stderr,
        )
        return 2
    if args.seed is None and args.out is None:
        print("error: need --seed (fixtures) or --out (ad-hoc file)", file=sys.stderr)
        return 2

    rng = random.Random() if args.seed is None else random.Random(args.seed)
    items = plan_items(rng)
    if args.out is not None:
        name = args.format or "comma"
        write_log(args.out, items, _serializer(name, args.seed, rng), args.seed)
        return 0
    for name in SERIALIZERS:
        if args.format is None or args.format == name:
            write_log(
                fixtures[name], items, _serializer(name, args.seed, rng), args.seed
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
