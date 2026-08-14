from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EvaluationPartitions:
    """Non-overlapping research and final-acceptance date boundaries."""

    discovery_start: str
    discovery_end: str
    selection_start: str | None = None
    selection_end: str | None = None
    sealed_start: str | None = None
    sealed_end: str | None = None

    def validate(self) -> None:
        for label, value in self._values().items():
            if value is not None and (len(value) != 8 or not value.isdigit()):
                raise ValueError(f"{label} must be YYYYMMDD")
        if self.discovery_start > self.discovery_end:
            raise ValueError("discovery_start must be <= discovery_end")
        if self.selection_start or self.selection_end:
            if not self.selection_start or not self.selection_end:
                raise ValueError("selection_start and selection_end must be provided together")
            if self.selection_start > self.selection_end:
                raise ValueError("selection_start must be <= selection_end")
            if self.selection_start <= self.discovery_end:
                raise ValueError("selection window must start strictly after discovery_end")
        if self.sealed_start or self.sealed_end:
            if not self.sealed_start or not self.sealed_end:
                raise ValueError("sealed_start and sealed_end must be provided together")
            if self.sealed_start > self.sealed_end:
                raise ValueError("sealed_start must be <= sealed_end")
            preceding = self.selection_end or self.discovery_end
            if self.sealed_start <= preceding:
                raise ValueError("sealed window must start strictly after discovery/selection")

    def as_dict(self) -> dict[str, str | None]:
        self.validate()
        return {
            "discovery_start": self.discovery_start,
            "discovery_end": self.discovery_end,
            "selection_start": self.selection_start,
            "selection_end": self.selection_end,
            "sealed_start": self.sealed_start,
            "sealed_end": self.sealed_end,
        }

    def _values(self) -> dict[str, str | None]:
        return {
            "discovery_start": self.discovery_start,
            "discovery_end": self.discovery_end,
            "selection_start": self.selection_start,
            "selection_end": self.selection_end,
            "sealed_start": self.sealed_start,
            "sealed_end": self.sealed_end,
        }
