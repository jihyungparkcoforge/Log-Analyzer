from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Literal

from log_analyzer.models import LogEntry


def validate_count(value: int, name: str) -> None:
    # Reject bool explicitly: Python otherwise treats True as the integer 1.
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class TrafficWindow:
    """Historical completed/failed requests for one dated time window."""

    started_at: datetime
    requests: int
    failed_requests: int

    def __post_init__(self) -> None:
        # Enforce the request-outcome contract before history reaches a detector.
        validate_count(self.requests, "requests")
        validate_count(self.failed_requests, "failed_requests")
        if self.failed_requests > self.requests:
            raise ValueError("failed_requests cannot exceed requests")


@dataclass(frozen=True, slots=True)
class DetectionInput:
    """Parser entries plus optional traffic metadata for spike detection.

    Each ERROR entry must represent one failed request when used by SpikeDetector.
    History and request-count keys must use the detector's window duration.
    """

    entries: tuple[LogEntry, ...]
    request_counts: Mapping[datetime, int] = field(default_factory=dict)
    history: tuple[TrafficWindow, ...] = ()

    def __post_init__(self) -> None:
        # Snapshot caller-owned collections so later mutations cannot change input.
        # object.__setattr__ is needed while initializing this frozen dataclass.
        object.__setattr__(self, "entries", tuple(self.entries))
        object.__setattr__(self, "history", tuple(self.history))
        object.__setattr__(
            self, "request_counts", MappingProxyType(dict(self.request_counts))
        )


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
    request_count: int | None = None
    expected_errors: float | None = None
    tail_probability: float | None = None


class BaseDetector(ABC):
    """Require every detector to expose the same input/output interface.

    New subclasses implement detect(); callers can then use them in the same loop.
    """
    @abstractmethod
    def detect(self, data: DetectionInput) -> tuple[Finding, ...]:
        """Return findings without retaining state between calls."""
