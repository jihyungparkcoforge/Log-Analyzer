import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

from log_analyzer.detection import (
    DetectionInput,
    RepeatedMessageDetector,
    SpikeDetector,
)
from log_analyzer.detection.summary import LLMSummarizer
from log_analyzer.parsing import STRATEGIES, LogParser
from log_analyzer.reporting import format_report
from log_analyzer.reporting.cli import main, resolve_format

DATA = Path(__file__).parent.parent / "data"
SAMPLE = DATA / "sample.log"


@pytest.fixture(autouse=True)
def isolated_env_file(monkeypatch, tmp_path):
    """Never load the developer's real credentials during CLI tests."""
    monkeypatch.chdir(tmp_path)


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

    strategy = STRATEGIES[resolve_format(SAMPLE)]()
    entries = LogParser(strategy=strategy).parse_file(SAMPLE).entries
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


def write_json_log(path: Path) -> None:
    """Write a small JSON Lines log with a burst of identical errors."""
    lines = [
        json.dumps(
            {
                "ts": f"2026-10-19 15:30:{i:02}",
                "level": "ERROR",
                "service": "payment-service",
                "message": "Connection timeout",
            }
        )
        for i in range(50)
    ]
    path.write_text("\n".join(lines) + "\n")


def test_format_json_flag_parses_and_reports(capsys, tmp_path):
    """--format json reads a JSON Lines file and reports the findings."""
    log = tmp_path / "burst.jsonl"
    write_json_log(log)
    assert main(["--format", "json", str(log)]) == 0
    out = capsys.readouterr().out
    assert "[CRITICAL] Error spike" in out
    assert "Count:   50 errors" in out
    assert "50 entries parsed" in out
    assert "malformed" not in out


def test_json_file_without_flag_aborts_when_malformed_rate_exceeds_threshold(
    capsys, tmp_path
):
    """Wrong format: 100% malformed exceeds the default 50% threshold and aborts."""
    log = tmp_path / "burst.jsonl"
    write_json_log(log)
    assert main([str(log)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "100.0%" in captured.err
    assert "50.0%" in captured.err
    assert "MALFORMED_THRESHOLD_PERCENT" in captured.err


def test_sibling_metadata_format_selects_json(capsys, tmp_path):
    """Without --format, `<log>.metadata.json` with "format": "json" picks JSON."""
    log = tmp_path / "burst.log"
    write_json_log(log)
    (tmp_path / "burst.log.metadata.json").write_text(json.dumps({"format": "json"}))
    assert main([str(log)]) == 0
    out = capsys.readouterr().out
    assert "50 entries parsed" in out
    assert "[CRITICAL] Error spike" in out


def test_format_flag_overrides_metadata(capsys, tmp_path):
    """--format wins over the metadata's format; 100% skip aborts without a TTY."""
    log = tmp_path / "burst.log"
    write_json_log(log)
    (tmp_path / "burst.log.metadata.json").write_text(json.dumps({"format": "json"}))
    assert main(["--format", "comma", str(log)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "100.0%" in captured.err


def test_malformed_over_threshold_tty_yes_continues(capsys, tmp_path, monkeypatch):
    """A terminal user can confirm and still get the report."""
    log = tmp_path / "burst.jsonl"
    write_json_log(log)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: "y")
    assert main([str(log)]) == 0
    captured = capsys.readouterr()
    assert "LOG ANALYSIS REPORT" in captured.out
    assert "100.0%" in captured.err


def test_malformed_over_threshold_tty_no_aborts(capsys, tmp_path, monkeypatch):
    """Declining the prompt aborts without a report."""
    log = tmp_path / "burst.jsonl"
    write_json_log(log)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: "n")
    assert main([str(log)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "100.0%" in captured.err


def test_sample_log_stays_under_malformed_threshold(capsys):
    """The committed sample is ~2% malformed, below the default 50% gate."""
    assert main([str(SAMPLE)]) == 0
    captured = capsys.readouterr()
    assert "LOG ANALYSIS REPORT" in captured.out
    assert "MALFORMED_THRESHOLD_PERCENT" not in captured.err


def test_metadata_without_format_key_defaults_to_comma(capsys, tmp_path):
    """Metadata lacking "format" (or unreadable) falls back to comma."""
    log = tmp_path / "quiet.log"
    log.write_text("2026-10-19 14:00:00,INFO,payment-service,ok\n")
    meta = tmp_path / "quiet.log.metadata.json"
    for content in ('{"seed": 1}', "not json", "[]"):
        meta.write_text(content)
        assert main([str(log)]) == 0
        assert "1 entries parsed" in capsys.readouterr().out


def test_unknown_format_flag_exits_2_without_analyzing(capsys, tmp_path):
    """An unsupported --format is an error on stderr and prints no report."""
    log = tmp_path / "quiet.log"
    log.write_text("2026-10-19 14:00:00,INFO,payment-service,ok\n")
    assert main(["--format", "xml", str(log)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "unsupported format 'xml' (supported: comma, json)" in captured.err


def test_unknown_metadata_format_exits_2(capsys, tmp_path):
    """An unsupported format named by metadata is rejected the same way."""
    log = tmp_path / "quiet.log"
    log.write_text("2026-10-19 14:00:00,INFO,payment-service,ok\n")
    (tmp_path / "quiet.log.metadata.json").write_text(json.dumps({"format": "syslog"}))
    assert main([str(log)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "unsupported format 'syslog'" in captured.err


def test_resolve_format_precedence(tmp_path):
    """Explicit flag beats metadata, which beats the comma default."""
    log = tmp_path / "a.log"
    log.write_text("")
    assert resolve_format(log) == "comma"
    (tmp_path / "a.log.metadata.json").write_text(json.dumps({"format": "json"}))
    assert resolve_format(log) == "json"
    assert resolve_format(log, "comma") == "comma"


def test_committed_jsonl_sample_matches_comma_findings(capsys):
    """data/sample.jsonl is the same incident; sibling metadata selects JSON."""
    jsonl = DATA / "sample.jsonl"
    spike = json.loads((DATA / "sample.jsonl.metadata.json").read_text())["spike"]
    comma_spike = json.loads((DATA / "sample.log.metadata.json").read_text())["spike"]
    assert spike["window_error_counts"] == comma_spike["window_error_counts"]
    assert resolve_format(jsonl) == "json"
    assert main([str(jsonl)]) == 0
    out = capsys.readouterr().out
    assert "[CRITICAL] Error spike" in out
    for count in spike["window_error_counts"]:
        assert f"Count:   {count} errors" in out


@pytest.mark.parametrize("explicit_model", [None, "cli-deployment"])
def test_dotenv_overrides_stale_exports_but_not_model_flag(
    monkeypatch, tmp_path, capsys, explicit_model
):
    """Load local credentials automatically while keeping --model highest priority."""
    import os

    for name in ("OPENAI_API_KEY", "ENDPOINT", "OPENAI_MODEL"):
        monkeypatch.setenv(name, "stale-export")
    (tmp_path / ".env").write_text(
        'OPENAI_API_KEY="local-test-key"\n'
        "ENDPOINT=https://example.services.ai.azure.com/openai/v1\n"
        "OPENAI_MODEL=local-deployment\n"
        "UNRELATED_SETTING=should-not-load\n"
    )
    monkeypatch.delenv("UNRELATED_SETTING", raising=False)
    expected_model = explicit_model or "local-deployment"

    def fake_summary(self, context):
        assert os.environ["OPENAI_API_KEY"] == "local-test-key"
        assert (
            os.environ["ENDPOINT"] == "https://example.services.ai.azure.com/openai/v1"
        )
        assert "UNRELATED_SETTING" not in os.environ
        assert self.model == expected_model
        return "Summary from local settings."

    monkeypatch.setattr(LLMSummarizer, "summarize", fake_summary)
    args = [str(SAMPLE), "--summary"]
    if explicit_model:
        args += ["--model", explicit_model]
    assert main(args) == 0
    assert "Summary from local settings." in capsys.readouterr().out
