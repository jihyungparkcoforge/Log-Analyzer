from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from log_analyzer.models import LogEntry


@dataclass(frozen=True, slots=True)
class DetectionInput:
    """Only the parsed entries are required; timestamps come from the parser."""

    entries: tuple[LogEntry, ...]

    def __post_init__(self) -> None:
        # Snapshot caller-owned collections before running either detector.
        object.__setattr__(self, "entries", tuple(self.entries))


@dataclass(frozen=True, slots=True)
class Finding:
    """Shared detector output, with optional statistical evidence for summaries.

    count belongs to this finding; overlapping findings must not be summed.
    started_at is the window start for spikes and the first event for repetitions.
    """

    detector: str
    description: str
    started_at: datetime
    severity: Literal["warning", "critical"]
    count: int
    service: str | None = None
    message: str | None = None
    expected_errors: float | None = None
    threshold: int | None = None
    baseline_windows: int | None = None


class BaseDetector(ABC):
    """Require every detector to expose the same input/output interface.

    New subclasses implement detect(); callers can then use them in the same loop.
    """

    @abstractmethod
    def detect(self, data: DetectionInput) -> tuple[Finding, ...]:
        """Return findings without retaining state between calls."""
