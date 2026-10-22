import json
from datetime import datetime
from pathlib import Path

from log_analyzer.detection import (
    DetectionInput,
    RepeatedMessageDetector,
    SpikeDetector,
)
from log_analyzer.detection.summary import LLMSummarizer
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


def test_json_flag_saves_report_matching_text(capsys, tmp_path):
    out_file = tmp_path / "report.json"
    assert main([str(SAMPLE), "--json", str(out_file)]) == 0
    text = capsys.readouterr().out
    report = json.loads(out_file.read_text())
    assert f"Findings: {report['finding_count']}" in text
    assert report["finding_count"] == len(report["findings"])
    assert report["log_file"] == str(SAMPLE)
    assert report["summary"] is None
    for number, finding in enumerate(report["findings"], start=1):
        started = datetime.fromisoformat(finding["started_at"])
        assert f"{number}. [{finding['severity'].upper()}]" in text
        assert f"Started: {started:%Y-%m-%d %H:%M:%S}" in text


def test_json_unwritable_path_exits_nonzero(capsys, tmp_path):
    target = tmp_path / "missing-dir" / "report.json"
    assert main([str(SAMPLE), "--json", str(target)]) == 2
    assert "cannot write" in capsys.readouterr().err


def test_summary_flag_appends_llm_summary(capsys, tmp_path, monkeypatch):
    sent = []

    def fake_request(payload):
        sent.append(payload)
        return {
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "Fake summary."}],
                }
            ],
        }

    monkeypatch.setattr(LLMSummarizer, "_request", staticmethod(fake_request))
    out_file = tmp_path / "report.json"
    argv = [str(SAMPLE), "--summary", "--model", "test-model", "--json", str(out_file)]
    assert main(argv) == 0
    out = capsys.readouterr().out
    assert out.index("LOG ANALYSIS REPORT") < out.index(
        "LLM SUMMARY (model: test-model)"
    )
    assert "Fake summary." in out
    assert sent[0]["model"] == "test-model"
    assert json.loads(out_file.read_text())["summary"] == "Fake summary."


def test_summary_without_api_key_still_prints_report(capsys, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert main([str(SAMPLE), "--summary"]) == 1
    captured = capsys.readouterr()
    assert "LOG ANALYSIS REPORT" in captured.out
    assert "LLM SUMMARY" not in captured.out
    assert "summary failed: Set OPENAI_API_KEY" in captured.err
