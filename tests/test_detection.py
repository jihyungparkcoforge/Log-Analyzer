"""Synthetic experiments: uv run --with pytest pytest tests/test_detection.py -v."""

from datetime import UTC, datetime, timedelta
from math import comb

import pytest

from log_analyzer.detection import (
    BaseDetector,
    DetectionInput,
    InsufficientHistoryError,
    RepeatedMessageDetector,
    SpikeDetector,
    TrafficWindow,
)
from log_analyzer.detection.spike import _binomial_tail
from log_analyzer.models import LogEntry
from log_analyzer.parsing import LogParser

START = datetime(2026, 10, 19, 23, 0)


def entries(count, start=START, service="payment", message="timeout", level="ERROR"):
    return tuple(
        LogEntry(start + timedelta(seconds=i % 60), level, service, message, i + 1)
        for i in range(count)
    )


def history(start=START, requests=1000, failures=20, days=7):
    return tuple(
        TrafficWindow(start - timedelta(days=i), requests, failures)
        for i in range(1, days + 1)
    )


def traffic(count, requests=1000, start=START):
    return DetectionInput(entries(count, start), {start: requests}, history(start))


@pytest.mark.parametrize("detector", [SpikeDetector(), RepeatedMessageDetector()])
def test_quiet_parsed_log_and_empty_input(detector):
    parsed = LogParser().parse_lines(
        [
            "2026-10-19 23:00:00,INFO,payment,completed",
            "2026-10-19 23:01:00,WARNING,payment,slow",
            "malformed line",
        ]
    )
    assert parsed.malformed_count == 1
    assert detector.detect(DetectionInput(parsed.entries, {START: 0})) == ()
    assert detector.detect(DetectionInput(())) == ()


def test_inheritance_allows_common_calling_interface():
    with pytest.raises(TypeError):
        BaseDetector()
    detectors = [SpikeDetector(), RepeatedMessageDetector()]
    assert all(isinstance(d, BaseDetector) for d in detectors)
    assert all(d.detect(DetectionInput(())) == () for d in detectors)


@pytest.mark.parametrize("requests, failures", [(1000, 20), (5000, 100)])
def test_more_nightly_usage_at_same_failure_rate_is_not_a_spike(requests, failures):
    assert SpikeDetector().detect(traffic(failures, requests)) == ()


def test_nightly_failure_rate_increase_is_critical():
    (finding,) = SpikeDetector().detect(traffic(300, 5000))
    assert finding.detector == "spike"
    assert finding.started_at == START
    assert finding.count == 300
    assert finding.request_count == 5000
    assert finding.expected_errors == pytest.approx(5000 * 141 / 7002)
    assert finding.severity == "critical"
    assert finding.tail_probability < SpikeDetector().critical_cutoff
    assert "300 failures / 5000 requests" in finding.description


def test_same_error_count_has_different_meaning_at_different_usage():
    assert SpikeDetector().detect(traffic(100, 1000))
    assert SpikeDetector().detect(traffic(100, 5000)) == ()


def test_seasonal_baseline_uses_matching_slot_not_daytime():
    daytime = START.replace(hour=12)
    data = DetectionInput(
        entries(100), {START: 5000}, history() + history(daytime, failures=1)
    )
    assert SpikeDetector().detect(data) == ()


def test_future_history_is_not_used_and_calls_are_independent():
    detector = SpikeDetector()
    initial = traffic(100)
    future = TrafficWindow(START + timedelta(days=1), 1000, 900)
    assert detector.detect(initial) == detector.detect(
        DetectionInput(
            initial.entries, initial.request_counts, initial.history + (future,)
        )
    )
    assert detector.detect(traffic(20)) == ()
    assert detector.detect(initial) == detector.detect(initial)


def test_missing_traffic_and_history_are_explicit_errors():
    with pytest.raises(ValueError, match="missing request count"):
        SpikeDetector().detect(DetectionInput(entries(1)))
    with pytest.raises(InsufficientHistoryError, match="found 6"):
        SpikeDetector().detect(
            DetectionInput(entries(1), {START: 100}, history(days=6))
        )
    with pytest.raises(ValueError, match="exceed"):
        SpikeDetector().detect(traffic(2, requests=1))
    with pytest.raises(ValueError, match="duplicate"):
        SpikeDetector().detect(DetectionInput(entries(1), {START: 100}, history() * 2))


@pytest.mark.parametrize(
    "n,p,e",
    [
        (10, 0.2, 0),
        (10, 0.2, 2),
        (10, 0.2, 5),
        (10, 0.9, 8),
        (10, 0.9, 10),
        (100, 0.02, 12),
    ],
)
def test_binomial_tail_against_direct_probability_sum(n, p, e):
    expected = sum(comb(n, k) * p**k * (1 - p) ** (n - k) for k in range(e, n + 1))
    assert _binomial_tail(n, p, e) == pytest.approx(expected, rel=1e-11)


def test_spike_warning_and_critical_decision_levels():
    detector = SpikeDetector()
    p = 141 / 7002

    # Independent exact PMF sums establish the count where each decision changes.
    def tail(e):
        return sum(comb(100, k) * p**k * (1 - p) ** (100 - k) for k in range(e, 101))

    warning = next(e for e in range(101) if tail(e) <= detector.warning_cutoff)
    critical = next(e for e in range(101) if tail(e) <= detector.critical_cutoff)
    assert warning < critical
    assert detector.detect(traffic(warning - 1, 100)) == ()
    assert detector.detect(traffic(warning, 100))[0].severity == "warning"
    assert detector.detect(traffic(critical, 100))[0].severity == "critical"


@pytest.mark.parametrize(
    "count,severity",
    [(4, None), (5, "warning"), (9, "warning"), (10, "critical"), (11, "critical")],
)
def test_repeated_thresholds(count, severity):
    result = RepeatedMessageDetector().detect(DetectionInput(entries(count)))
    if severity is None:
        assert result == ()
    else:
        assert result[0].severity == severity
        assert result[0].count == count
        assert result[0].started_at == START


def test_repeated_messages_are_separated_by_service_text_and_level():
    data = DetectionInput(
        entries(4)
        + entries(4, service="auth")
        + entries(4, message="Timeout")
        + entries(20, level="INFO")
    )
    assert RepeatedMessageDetector().detect(data) == ()


def test_boundary_unsorted_input_and_deterministic_output():
    later = START + timedelta(minutes=5)
    data = DetectionInput(
        tuple(reversed(entries(10) + entries(10, later))),
        {START: 10, later: 10},
        history() + history(later),
    )
    for detector in (SpikeDetector(), RepeatedMessageDetector()):
        result = detector.detect(data)
        assert [f.started_at for f in result] == [START, later]
        assert [f.count for f in result] == [10, 10]
        assert detector.detect(data) == result


def test_parser_to_detectors_with_synthetic_traffic():
    lines = [
        f"2026-10-19 23:00:{i:02},ERROR,payment,Connection timeout to db-primary"
        for i in range(50)
    ] + ["invalid log"]
    parsed = LogParser().parse_lines(lines)
    data = DetectionInput(parsed.entries, {START: 1000}, history())
    assert parsed.malformed_count == 1
    for detector in (SpikeDetector(), RepeatedMessageDetector()):
        (finding,) = detector.detect(data)
        assert finding.count == 50
        assert finding.severity == "critical"


@pytest.mark.parametrize("minutes", [0, -1, 7, 1.5, True])
def test_invalid_window_sizes(minutes):
    for cls in (SpikeDetector, RepeatedMessageDetector):
        with pytest.raises(ValueError):
            cls(window_minutes=minutes)


def test_other_invalid_configuration_and_input():
    with pytest.raises(ValueError):
        SpikeDetector(history_days=0)
    with pytest.raises(ValueError):
        SpikeDetector(warning_daily_budget=float("nan"))
    with pytest.raises(ValueError):
        RepeatedMessageDetector(warning_threshold=10, critical_threshold=5)
    with pytest.raises(ValueError):
        TrafficWindow(START, 10, 11)
    with pytest.raises(ValueError):
        SpikeDetector().detect(DetectionInput((), {START: -1}))
    with pytest.raises(ValueError, match="aligned"):
        SpikeDetector().detect(DetectionInput((), {START + timedelta(seconds=1): 1}))
    with pytest.raises(ValueError, match="naive"):
        RepeatedMessageDetector().detect(
            DetectionInput(entries(1, START.replace(tzinfo=UTC)))
        )


def test_window_size_adjusts_daily_decision_levels():
    assert SpikeDetector(window_minutes=10).warning_cutoff == pytest.approx(0.05 / 144)
