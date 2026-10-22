"""Tests for line parse strategies.

Every strategy must honour the LineParseStrategy contract. The contract tests
run against each entry in STRATEGY_CASES, so supporting a new log format means
adding one StrategyCase below (plus format-specific tests, if any) and nothing else.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

import pytest

from log_analyzer.models import LogEntry
from log_analyzer.parsing import (
    CommaSeparatedStrategy,
    JsonStrategy,
    LogParser,
    ParseError,
)
from log_analyzer.parsing.strategy import LineParseStrategy


@dataclass(frozen=True)
class StrategyCase:
    """Everything the contract tests need to know about one strategy/format.

    Attributes:
        name: Short id used in test names.
        factory: Builds a fresh strategy instance.
        good_line: A valid line for this format.
        expected: The entry `good_line` must produce when parsed as line 7.
        bad_lines: (line, expected reason) pairs, each of which must be rejected.
    """

    name: str
    factory: Callable[[], LineParseStrategy]
    good_line: str
    expected: LogEntry
    bad_lines: tuple[tuple[str, str], ...]


COMMA_CASE = StrategyCase(
    name="comma",
    factory=CommaSeparatedStrategy,
    good_line="2026-10-19 14:00:05,INFO,order-service,Order shipped order_id=89131",
    expected=LogEntry(
        timestamp=datetime(2026, 10, 19, 14, 0, 5),
        level="INFO",
        service="order-service",
        message="Order shipped order_id=89131",
        line_no=7,
    ),
    bad_lines=(
        ("", "blank_line"),
        ("2026-10-19 14:00:05,INFO,svc", "missing_fields"),
        ("2026-13-45 14:00:05,INFO,svc,msg", "bad_timestamp"),
        ("2026-10-19 14:00:05,DEBUG,svc,msg", "unknown_level"),
        ("2026-10-19 14:00:05,INFO,,msg", "empty_service"),
        ("2026-10-19 14:00:05,INFO,svc,", "empty_message"),
    ),
)

JSON_CASE = StrategyCase(
    name="json",
    factory=JsonStrategy,
    good_line=(
        '{"ts":"2026-10-19 14:00:05","level":"INFO","service":"order-service",'
        '"message":"Order shipped order_id=89131"}'
    ),
    expected=COMMA_CASE.expected,
    bad_lines=(
        ("", "blank_line"),
        ("not json at all", "invalid_json"),
        ('["ts","level","service","message"]', "invalid_json"),
        (
            '{"ts":"2026-10-19 14:00:05","level":"INFO","service":"svc"}',
            "missing_fields",
        ),
        (
            '{"ts":"2026-13-45 14:00:05","level":"INFO","service":"svc","message":"m"}',
            "bad_timestamp",
        ),
        (
            '{"ts":"2026-10-19 14:00:05","level":"FATAL","service":"svc","message":"m"}',
            "unknown_level",
        ),
        (
            '{"ts":"2026-10-19 14:00:05","level":"INFO","service":"","message":"m"}',
            "empty_service",
        ),
        (
            '{"ts":"2026-10-19 14:00:05","level":"INFO","service":"svc","message":""}',
            "empty_message",
        ),
    ),
)

# Register new strategies here; every contract test below picks them up.
STRATEGY_CASES = [COMMA_CASE, JSON_CASE]

# Inputs no strategy may crash on: they must parse or raise ParseError.
HOSTILE_LINES = [
    "",
    "\n",
    "\r\n",
    "   ",
    ",",
    ",,,,",
    "\x00",
    "�" * 5,
    "💥,💥,💥,💥",
    "x" * 100_000,
    "\tat com.example.Foo.bar(Foo.java:42)",
    "Traceback (most recent call last):",
    "{",
    "{}",
    "[" * 100_000,
    "null",
    "NaN",
    "1e999999",
    "9" * 10_000,
    '{"ts":null,"level":null,"service":null,"message":null}',
    '{"ts":1,"level":2,"service":3,"message":4}',
    '{"ts":"2026-10-19 14:00:05","level":"INFO","service":"s","message":{"a":[1]}}',
    '{"ts":"\u0000","level":"\ud800","service":"s","message":"m"}',
]

all_cases = pytest.mark.parametrize(
    "case", STRATEGY_CASES, ids=[case.name for case in STRATEGY_CASES]
)


# --- Contract: applies to every registered strategy -------------------------


@all_cases
def test_contract_good_line_parses_to_expected_entry(case):
    """A valid line yields the expected LogEntry, including the given line number."""
    assert case.factory().parse_line(case.good_line, 7) == case.expected


@all_cases
def test_contract_entry_has_the_documented_shape(case):
    """Entries carry a naive datetime, a valid level and non-empty text fields."""
    entry = case.factory().parse_line(case.good_line, 1)
    assert isinstance(entry, LogEntry)
    assert entry.timestamp.tzinfo is None
    assert entry.level in {"INFO", "WARNING", "ERROR"}
    assert entry.service
    assert entry.message


@all_cases
def test_contract_line_number_is_passed_through(case):
    """The line_no argument is stored on the entry unchanged."""
    entry = case.factory().parse_line(case.good_line, 12345)
    assert entry.line_no == 12345


@all_cases
def test_contract_line_ending_is_irrelevant(case):
    """A trailing LF or CRLF does not change the parsed entry."""
    strategy = case.factory()
    plain = strategy.parse_line(case.good_line, 1)
    assert strategy.parse_line(case.good_line + "\n", 1) == plain
    assert strategy.parse_line(case.good_line + "\r\n", 1) == plain


@all_cases
def test_contract_bad_lines_raise_parse_error_with_reason(case):
    """Each known-bad line raises ParseError carrying the expected reason tag."""
    strategy = case.factory()
    for line, reason in case.bad_lines:
        with pytest.raises(ParseError) as excinfo:
            strategy.parse_line(line, 1)
        assert excinfo.value.reason == reason, line


@all_cases
def test_contract_reasons_are_short_snake_case_tags(case):
    """Reason tags are non-empty snake_case identifiers, safe to use as report keys."""
    strategy = case.factory()
    for line, _ in case.bad_lines:
        with pytest.raises(ParseError) as excinfo:
            strategy.parse_line(line, 1)
        reason = excinfo.value.reason
        assert reason
        assert reason == reason.lower()
        assert reason.replace("_", "").isalnum()


@all_cases
@pytest.mark.parametrize("line", HOSTILE_LINES)
def test_contract_hostile_input_never_crashes(case, line):
    """Odd input either parses or raises ParseError, never any other exception."""
    try:
        entry = case.factory().parse_line(line, 1)
    except ParseError:
        return
    assert isinstance(entry, LogEntry)


@all_cases
def test_contract_strategy_is_stateless(case):
    """Parsing a bad line first does not affect a later good line, and repeats agree."""
    strategy = case.factory()
    bad_line, _ = case.bad_lines[0]
    with pytest.raises(ParseError):
        strategy.parse_line(bad_line, 1)
    first = strategy.parse_line(case.good_line, 7)
    second = strategy.parse_line(case.good_line, 7)
    assert first == second == case.expected


@all_cases
def test_contract_instances_can_be_created_independently(case):
    """Separate instances behave identically (no shared mutable class state)."""
    a, b = case.factory(), case.factory()
    assert a.parse_line(case.good_line, 7) == b.parse_line(case.good_line, 7)


@all_cases
def test_contract_works_end_to_end_through_log_parser(case):
    """LogParser accepts the strategy, keeps good lines and records every bad one."""
    bad_lines = [line for line, _ in case.bad_lines]
    lines = [case.good_line, *bad_lines, case.good_line]
    result = LogParser(strategy=case.factory()).parse_lines(lines)

    assert [e.line_no for e in result.entries] == [1, len(lines)]
    assert result.malformed_count == len(bad_lines)
    assert dict(result.malformed_reasons) == {
        reason: sum(1 for _, r in case.bad_lines if r == reason)
        for _, reason in case.bad_lines
    }
    assert [s.line_no for s in result.malformed_samples] == list(range(2, len(lines)))


# --- ParseError -------------------------------------------------------------


def test_parse_error_is_a_value_error_carrying_reason():
    """ParseError subclasses ValueError and exposes its reason tag."""
    error = ParseError("bad_timestamp")
    assert isinstance(error, ValueError)
    assert error.reason == "bad_timestamp"
    assert str(error) == "bad_timestamp"


# --- Format-specific: CommaSeparatedStrategy --------------------------------
# Add a similar section for each new strategy's quirks (the contract above
# already covers what every strategy shares).


@pytest.mark.parametrize("level", ["INFO", "WARNING", "ERROR"])
def test_comma_accepts_all_valid_levels(level):
    """INFO, WARNING and ERROR are all accepted levels."""
    line = f"2026-10-19 14:00:05,{level},svc,msg"
    assert CommaSeparatedStrategy().parse_line(line, 1).level == level


def test_comma_message_keeps_extra_commas():
    """Everything after the third comma belongs to the message."""
    line = "2026-10-19 14:00:05,ERROR,svc,failed: a, b, c"
    assert CommaSeparatedStrategy().parse_line(line, 1).message == "failed: a, b, c"


def test_comma_strips_whitespace_around_fields():
    """Padding around each field is removed."""
    line = " 2026-10-19 14:00:05 , ERROR , svc , some message "
    entry = CommaSeparatedStrategy().parse_line(line, 1)
    assert (entry.level, entry.service, entry.message) == (
        "ERROR",
        "svc",
        "some message",
    )
    assert entry.timestamp == datetime(2026, 10, 19, 14, 0, 5)


@pytest.mark.parametrize(
    ("line", "reason"),
    [
        ("   \t", "blank_line"),
        ("\n", "blank_line"),
        ("just some garbage", "missing_fields"),
        ("\tat com.example.Foo.bar(Foo.java:42)", "missing_fields"),
        ("2026-10-19T14:00:05,INFO,svc,msg", "bad_timestamp"),
        ("2026-10-19 14:00:05.123,INFO,svc,msg", "bad_timestamp"),
        ("not-a-date,INFO,svc,msg", "bad_timestamp"),
        ("2026-10-19 14:00:05,info,svc,msg", "unknown_level"),
        ("2026-10-19 14:00:05,INFO,   ,msg", "empty_service"),
        ("2026-10-19 14:00:05,INFO,svc,   ", "empty_message"),
    ],
)
def test_comma_rejects_with_reason(line, reason):
    """Each malformed comma-format variant is tagged with the expected reason."""
    with pytest.raises(ParseError) as excinfo:
        CommaSeparatedStrategy().parse_line(line, 1)
    assert excinfo.value.reason == reason


def test_comma_checks_fields_in_documented_order():
    """A bad timestamp is reported before an unknown level on the same line."""
    with pytest.raises(ParseError) as excinfo:
        CommaSeparatedStrategy().parse_line("nope,DEBUG,,", 1)
    assert excinfo.value.reason == "bad_timestamp"


# --- Format-specific: JsonStrategy ------------------------------------------


def json_line(**overrides) -> str:
    """Build a JSON log line from a valid record with some fields overridden."""
    record = {
        "ts": "2026-10-19 14:00:05",
        "level": "ERROR",
        "service": "svc",
        "message": "msg",
        **overrides,
    }
    return json.dumps(record)


def test_json_message_may_contain_quotes_commas_and_newlines():
    """json.dumps escaping keeps awkward messages on one line and round-trips them."""
    message = 'said "hi", then {left}, \\ and a\nnewline'
    line = json_line(message=message)
    assert "\n" not in line
    assert JsonStrategy().parse_line(line, 1).message == message


def test_json_extra_keys_are_ignored():
    """Unknown keys do not affect parsing."""
    entry = JsonStrategy().parse_line(json_line(trace_id="abc", extra={"a": 1}), 1)
    assert (entry.level, entry.service, entry.message) == ("ERROR", "svc", "msg")


def test_json_strips_whitespace_around_string_values_and_the_line():
    """Padding inside values and around the whole line is removed."""
    line = (
        "  "
        + json_line(ts=" 2026-10-19 14:00:05 ", service=" svc ", message=" m ")
        + "  "
    )
    entry = JsonStrategy().parse_line(line, 1)
    assert (entry.service, entry.message) == ("svc", "m")
    assert entry.timestamp == datetime(2026, 10, 19, 14, 0, 5)


def test_json_key_order_and_spacing_do_not_matter():
    """A pretty-printed object with shuffled keys parses the same as the compact form."""
    line = (
        '{ "message": "msg", "service": "svc", "level": "ERROR", '
        '"ts": "2026-10-19 14:00:05" }'
    )
    assert JsonStrategy().parse_line(line, 1) == JsonStrategy().parse_line(
        json_line(), 1
    )


@pytest.mark.parametrize(
    ("line", "reason"),
    [
        ("   \t", "blank_line"),
        ("{", "invalid_json"),
        ("{}", "missing_fields"),
        ("null", "invalid_json"),
        ('"just a string"', "invalid_json"),
        ("42", "invalid_json"),
        ("[" * 100_000, "invalid_json"),
        (json.dumps({"ts": "2026-10-19 14:00:05"}), "missing_fields"),
        (json_line(ts="2026-10-19T14:00:05"), "bad_timestamp"),
        (json_line(ts=20261019), "bad_timestamp"),
        (json_line(ts=None), "bad_timestamp"),
        (json_line(level="info"), "unknown_level"),
        (json_line(level=5), "unknown_level"),
        (json_line(service="   "), "empty_service"),
        (json_line(service=None), "empty_service"),
        (json_line(message="   "), "empty_message"),
        (json_line(message={"nested": 1}), "empty_message"),
    ],
)
def test_json_rejects_with_reason(line, reason):
    """Each malformed JSON variant is tagged with the expected reason."""
    with pytest.raises(ParseError) as excinfo:
        JsonStrategy().parse_line(line, 1)
    assert excinfo.value.reason == reason


def test_json_checks_in_documented_order():
    """Missing keys are reported before value problems, and ts before level."""
    with pytest.raises(ParseError) as excinfo:
        JsonStrategy().parse_line('{"ts":"nope","level":"DEBUG"}', 1)
    assert excinfo.value.reason == "missing_fields"
    with pytest.raises(ParseError) as excinfo:
        JsonStrategy().parse_line(json_line(ts="nope", level="DEBUG"), 1)
    assert excinfo.value.reason == "bad_timestamp"


def test_json_and_comma_agree_on_the_same_event():
    """Both formats produce an identical LogEntry for the same event."""
    comma = CommaSeparatedStrategy().parse_line(COMMA_CASE.good_line, 7)
    assert comma == JsonStrategy().parse_line(JSON_CASE.good_line, 7)


def test_json_default_parser_format_is_still_comma():
    """LogParser keeps comma as its default; JSON is opt-in via the strategy."""
    result = LogParser().parse_lines([JSON_CASE.good_line])
    assert result.entries == ()
    assert result.malformed_count == 1
