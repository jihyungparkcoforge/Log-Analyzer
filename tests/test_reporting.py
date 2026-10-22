import json
from datetime import datetime

from log_analyzer.detection import Finding
from log_analyzer.reporting import format_report, report_to_dict, sort_findings

T0 = datetime(2026, 10, 19, 15, 30, 0)
T1 = datetime(2026, 10, 19, 15, 35, 2)


def spike(severity="critical", started_at=T0, count=36):
    return Finding(
        detector="spike",
        description=f"{count} ERROR events",
        started_at=started_at,
        severity=severity,
        count=count,
    )


def repeated(severity="warning", started_at=T1, count=7, service="api-gateway"):
    return Finding(
        detector="repeated_message",
        description=f"{service}: 'Upstream returned 502' repeated {count} times",
        started_at=started_at,
        severity=severity,
        count=count,
        service=service,
        message="Upstream returned 502",
    )


def test_findings_sorted_by_severity():
    findings = [repeated("warning"), spike("critical")]
    assert [f.severity for f in sort_findings(findings)] == ["critical", "warning"]
    report = format_report(findings)
    assert report.index("[CRITICAL]") < report.index("[WARNING]")


def test_equal_severity_is_deterministic():
    a = repeated(started_at=T1, service="payment-service")
    b = repeated(started_at=T1, service="api-gateway")
    c = repeated(started_at=T0, service="zz-service")
    expected = [c, b, a]
    assert sort_findings([a, b, c]) == expected
    assert sort_findings([c, a, b]) == expected
    assert format_report([a, b, c]) == format_report([b, c, a])


def test_both_detector_types_with_details():
    report = format_report([spike(), repeated()], log_file="data/sample.log")
    assert "Log file: data/sample.log" in report
    assert "Findings: 2" in report
    assert "1. [CRITICAL] Error spike" in report
    assert "2. [WARNING] Repeated error message" in report
    assert "Started: 2026-10-19 15:30:00" in report
    assert "Started: 2026-10-19 15:35:02" in report
    assert "Count:   36 errors" in report
    assert "Count:   7 occurrences" in report
    assert "Service: api-gateway" in report
    assert "Details: 36 ERROR events" in report
    assert "Details: api-gateway: 'Upstream returned 502' repeated 7 times" in report


def test_optional_fields_omitted():
    report = format_report([spike()])
    assert "Service:" not in report
    assert "Message:" not in report


def test_empty_findings_and_notes():
    report = format_report([], notes=["73 malformed lines skipped"])
    assert "Findings: 0" in report
    assert "No findings detected." in report
    assert "  - 73 malformed lines skipped" in report


def test_report_dict_matches_text_order_and_is_json_safe():
    findings = [repeated("warning"), spike("critical")]
    report = report_to_dict(findings, log_file="data/sample.log", notes=["n"])
    assert json.loads(json.dumps(report)) == report
    assert report["log_file"] == "data/sample.log"
    assert report["finding_count"] == 2
    assert [f["severity"] for f in report["findings"]] == ["critical", "warning"]
    assert report["findings"][0]["started_at"] == "2026-10-19T15:30:00"
    assert report["findings"][0]["count"] == 36
    assert report["findings"][1]["service"] == "api-gateway"
    assert report["notes"] == ["n"]
    assert report["summary"] is None


def test_empty_report_dict():
    report = report_to_dict([], summary="text")
    assert report["log_file"] is None
    assert report["finding_count"] == 0
    assert report["findings"] == []
    assert report["summary"] == "text"
