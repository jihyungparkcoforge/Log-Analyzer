import json
from datetime import datetime
from pathlib import Path

from log_analyzer.detection import (
    DetectionInput,
    RepeatedMessageDetector,
    SpikeDetector,
)
from log_analyzer.parsing import LogParser
from log_analyzer.reporting import format_report
from log_analyzer.reporting.cli import main

DATA = Path(__file__).parent.parent / "data"
SAMPLE = DATA / "sample.log"


def test_valid_log_prints_report_to_stdout(capsys):
    assert main([str(SAMPLE)]) == 0
    out = capsys.readouterr().out
    assert "LOG ANALYSIS REPORT" in out
    assert f"Log file: {SAMPLE}" in out
    assert "malformed lines skipped" in out


def test_missing_file_is_a_clear_error(capsys, tmp_path):
    missing = tmp_path / "missing.log"
    assert main([str(missing)]) != 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert f"log file not found: {missing}" in captured.err


def test_directory_is_rejected(capsys, tmp_path):
    assert main([str(tmp_path)]) != 0
    assert "log file not found" in capsys.readouterr().err


def test_no_findings_exits_zero(capsys, tmp_path):
    log = tmp_path / "quiet.log"
    log.write_text(
        "2026-10-19 14:00:00,INFO,payment-service,ok\n"
        "2026-10-19 14:00:01,ERROR,payment-service,single failure\n"
    )
    assert main([str(log)]) == 0
    out = capsys.readouterr().out
    assert "Findings: 0" in out
    assert "No findings detected." in out


def test_empty_file_exits_zero(capsys, tmp_path):
    log = tmp_path / "empty.log"
    log.write_text("")
    assert main([str(log)]) == 0
    assert "No findings detected." in capsys.readouterr().out


def test_planted_spike_appears_in_sample_report(capsys):
    """Real parser, detectors and reporter against the committed sample."""
    spike = json.loads((DATA / "sample.log.metadata.json").read_text())["spike"]
    spike_start = datetime.fromisoformat(spike["start"])
    spike_end = datetime.fromisoformat(spike["end"])

    entries = LogParser().parse_file(SAMPLE).entries
    data = DetectionInput(entries)
    findings = RepeatedMessageDetector().detect(data) + SpikeDetector().detect(data)
    spikes = [f for f in findings if f.detector == "spike"]
    assert [f.count for f in spikes] == spike["window_error_counts"]
    assert all(f.count >= 10 for f in spikes)
    assert all(spike_start <= f.started_at < spike_end for f in spikes)
    planted = [
        f
        for f in findings
        if spike_start <= f.started_at < spike_end
        and f.service == spike["dominant_service"]
        and f.message == spike["top_message_template"]
    ]
    assert planted
    assert all(f.severity == "critical" for f in planted)

    assert main([str(SAMPLE)]) == 0
    out = capsys.readouterr().out
    assert f"Findings: {len(findings)}" in out
    for finding in planted:
        assert f"Started: {finding.started_at:%Y-%m-%d %H:%M:%S}" in out
        assert f"Count:   {finding.count} occurrences" in out
    # The generated sample plants only critical alerts, so order is checked only if both appear.
    if "[WARNING]" in out:
        assert out.index("[CRITICAL]") < out.index("[WARNING]")
    assert "[CRITICAL] Error spike" in out
    assert "Spike detection skipped" not in out
    for finding in spikes:
        assert f"Count:   {finding.count} errors" in out


def test_real_spike_finding_renders_with_counts():
    entries = (
        LogParser()
        .parse_lines(
            f"2026-10-19 15:30:{i:02},ERROR,payment-service,Connection timeout"
            for i in range(50)
        )
        .entries
    )
    data = DetectionInput(entries)
    findings = SpikeDetector().detect(data) + RepeatedMessageDetector().detect(data)
    report = format_report(findings)
    assert "[CRITICAL] Error spike" in report
    assert "Started: 2026-10-19 15:30:00" in report
    assert "Count:   50 errors" in report
    assert "Count:   50 occurrences" in report
