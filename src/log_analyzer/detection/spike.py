from collections import Counter, defaultdict
from datetime import time
from math import exp, fsum, isfinite, lgamma, log, log1p

from log_analyzer.detection._windows import errors, validate_window, window_start
from log_analyzer.detection.base import (
    BaseDetector,
    DetectionInput,
    Finding,
    TrafficWindow,
    validate_count,
)


class InsufficientHistoryError(ValueError):
    """An error-containing window lacks enough prior days with traffic."""


def _binomial_tail(n: int, p: float, observed: int) -> float:
    """P(X >= observed), using the smaller tail and a stable PMF recurrence."""
    if observed == 0:
        return 1.0
    upper = observed > n * p
    k = observed if upper else observed - 1
    term = exp(
        lgamma(n + 1)
        - lgamma(k + 1)
        - lgamma(n - k + 1)
        + k * log(p)
        + (n - k) * log1p(-p)
    )
    terms = [term]
    total = term
    while k < n if upper else k > 0:
        if upper:
            term *= (n - k) / (k + 1) * p / (1 - p)
            k += 1
        else:
            term *= k / (n - k + 1) * (1 - p) / p
            k -= 1
        terms.append(term)
        total += term
        if term <= total * 1e-16:
            break
    probability = fsum(terms)
    return min(1.0, max(0.0, probability if upper else 1 - probability))


class SpikeDetector(BaseDetector):
    """Detect unusually many failures given traffic and same-slot history.

    Daily budgets are nominal decision levels, not guaranteed false-alarm rates:
    the failure probability is estimated, and real requests may be correlated.
    """

    def __init__(
        self,
        window_minutes: int = 5,
        history_days: int = 7,
        warning_daily_budget: float = 0.05,
        critical_daily_budget: float = 0.005,
    ) -> None:
        validate_window(window_minutes)
        if type(history_days) is not int or history_days < 1:
            raise ValueError("history_days must be a positive integer")
        if (
            not isfinite(warning_daily_budget)
            or not isfinite(critical_daily_budget)
            or not 0 < critical_daily_budget < warning_daily_budget < 1
        ):
            raise ValueError("require 0 < critical budget < warning budget < 1")
        self.window_minutes = window_minutes
        self.history_days = history_days
        windows_per_day = 1440 // window_minutes
        self.warning_cutoff = warning_daily_budget / windows_per_day
        self.critical_cutoff = critical_daily_budget / windows_per_day

    def detect(self, data: DetectionInput) -> tuple[Finding, ...]:
        counts = Counter(
            window_start(e.timestamp, self.window_minutes)
            for e in errors(data, self.window_minutes)
        )
        for start, requests in data.request_counts.items():
            validate_count(requests, "request count")
            if start != window_start(start, self.window_minutes):
                raise ValueError("request-count keys must be aligned window starts")
        history_by_slot: dict[time, list[TrafficWindow]] = defaultdict(list)
        seen = set()
        for item in data.history:
            if item.started_at != window_start(item.started_at, self.window_minutes):
                raise ValueError("history must use aligned window starts")
            if item.started_at in seen:
                raise ValueError("duplicate historical window")
            seen.add(item.started_at)
            history_by_slot[item.started_at.time()].append(item)
        findings = []
        for start, count in sorted(counts.items()):
            if start not in data.request_counts:
                raise ValueError(f"missing request count for {start.isoformat()}")
            requests = data.request_counts[start]
            if count > requests:
                raise ValueError("ERROR count cannot exceed completed requests")
            prior = sorted(
                (
                    h
                    for h in history_by_slot[start.time()]
                    if h.started_at.date() < start.date() and h.requests > 0
                ),
                key=lambda h: h.started_at,
            )[-self.history_days :]
            if len(prior) < self.history_days:
                raise InsufficientHistoryError(
                    f"{start.isoformat()}: need {self.history_days} prior days with traffic; "
                    f"found {len(prior)}"
                )
            probability = (sum(h.failed_requests for h in prior) + 1) / (
                sum(h.requests for h in prior) + 2
            )
            tail = _binomial_tail(requests, probability, count)
            if tail > self.warning_cutoff:
                continue
            expected = requests * probability
            findings.append(
                Finding(
                    detector="spike",
                    started_at=start,
                    count=count,
                    severity="critical" if tail <= self.critical_cutoff else "warning",
                    description=f"{count} failures / {requests} requests in the "
                    f"{self.window_minutes}-minute window starting {start.isoformat()}; "
                    f"expected {expected:.2f}, upper-tail probability {tail:.6g}",
                    request_count=requests,
                    expected_errors=expected,
                    tail_probability=tail,
                )
            )
        return tuple(findings)
