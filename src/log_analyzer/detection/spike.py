"""Error-count spike detection using only timestamped parser entries."""

from collections import Counter, deque
from math import ceil, isfinite
from statistics import median

from log_analyzer.detection._windows import errors, validate_window, window_start
from log_analyzer.detection.base import BaseDetector, DetectionInput, Finding


class SpikeDetector(BaseDetector):
    """Compare each window with recent non-alert windows from the same input.

    Threshold = max(min_errors, ceil(median + sensitivity * robust_scale)).
    robust_scale = 1.4826 * max(MAD, 1). MAD is the median absolute deviation.
    No earlier windows means a median of zero and the minimum-count threshold.
    This detects error events, not failed requests or request failure rates.
    """

    def __init__(
        self,
        window_minutes: int = 5,
        baseline_windows: int = 12,
        min_errors: int = 10,
        sensitivity: float = 3.0,
    ):
        validate_window(window_minutes)
        if type(baseline_windows) is not int or baseline_windows < 1:
            raise ValueError("baseline_windows must be a positive integer")
        if type(min_errors) is not int or min_errors < 1:
            raise ValueError("min_errors must be a positive integer")
        if not isfinite(sensitivity) or sensitivity <= 0:
            raise ValueError("sensitivity must be positive and finite")
        self.window_minutes = window_minutes
        self.baseline_windows = baseline_windows
        self.min_errors = min_errors
        self.sensitivity = sensitivity

    def detect(self, data: DetectionInput) -> tuple[Finding, ...]:
        error_entries = errors(data, self.window_minutes)
        if not data.entries:
            return ()
        # INFO/WARNING events establish the observed timeline, including quiet slots.
        occupied = sorted(
            {window_start(e.timestamp, self.window_minutes) for e in data.entries}
        )
        counts = Counter(
            window_start(e.timestamp, self.window_minutes) for e in error_entries
        )
        baseline: deque[int] = deque(maxlen=self.baseline_windows)
        findings = []
        previous = None
        for start in occupied:
            if previous is not None:
                gap = (
                    int(
                        (start - previous).total_seconds() // (60 * self.window_minutes)
                    )
                    - 1
                )
                # Missing slots inside the input span count as zero observed errors.
                # Cap the work for long gaps: older values cannot survive the deque.
                baseline.extend([0] * min(gap, self.baseline_windows))
            previous = start
            center = float(median(baseline)) if baseline else 0.0
            mad = median(abs(n - center) for n in baseline) if baseline else 0.0
            threshold = max(
                self.min_errors, ceil(center + self.sensitivity * 1.4826 * max(mad, 1))
            )
            count = counts[start]
            if count < threshold:
                baseline.append(count)
                continue
            # Exclude alerts from the baseline so a sustained spike is not learned
            # as normal. All state is per-call, and no future window is consulted.
            findings.append(
                Finding(
                    detector="spike",
                    started_at=start,
                    count=count,
                    severity="critical" if count >= 2 * threshold else "warning",
                    expected_errors=center,
                    threshold=threshold,
                    baseline_windows=len(baseline),
                    description=f"{count} ERROR events in the {self.window_minutes}-minute "
                    f"window starting {start.isoformat()}; threshold {threshold}, "
                    f"baseline median {center:g} from {len(baseline)} prior non-alert windows.",
                )
            )
        return tuple(findings)
