"""A bounded, in-memory history of routing decisions - what the dashboard's
decision feed renders (FR4's "log of RL path-selection decisions ... with
reasoning/state snapshot").

Pure: no Ryu, no Flask. The controller writes records here as decisions
happen; the dashboard reads them.
"""

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional

DEFAULT_HISTORY = 200


@dataclass(frozen=True)
class CandidateSummary:
    """One candidate path and the live state that made it look good or bad -
    this is the "reasoning" half of a decision record."""

    path: List[int]
    bottleneck_utilization: float
    total_delay_ms: float
    max_loss_pct: float
    min_trust: float
    chosen: bool


@dataclass(frozen=True)
class DecisionRecord:
    timestamp: float
    src_mac: str
    dst_mac: str
    path: List[int]
    mode_used: str                       # "rl" or "dijkstra"
    event: str                           # "new_flow" or "reroute"
    fallback_reason: Optional[str] = None
    candidates: List[CandidateSummary] = field(default_factory=list)

    def as_dict(self) -> Dict:
        return {
            "timestamp": self.timestamp,
            "src_mac": self.src_mac,
            "dst_mac": self.dst_mac,
            "path": list(self.path),
            "mode_used": self.mode_used,
            "event": self.event,
            "fallback_reason": self.fallback_reason,
            "candidates": [
                {
                    "path": list(c.path),
                    "bottleneck_utilization": round(c.bottleneck_utilization, 4),
                    "total_delay_ms": round(c.total_delay_ms, 2),
                    "max_loss_pct": round(c.max_loss_pct, 3),
                    "min_trust": round(c.min_trust, 3),
                    "chosen": c.chosen,
                }
                for c in self.candidates
            ],
        }


class DecisionLog:
    def __init__(self, max_entries: int = DEFAULT_HISTORY):
        self._lock = threading.Lock()
        self._entries: Deque[DecisionRecord] = deque(maxlen=max_entries)

    def record(self, record: DecisionRecord) -> None:
        with self._lock:
            self._entries.append(record)

    def recent(self, limit: int = 25) -> List[DecisionRecord]:
        """Newest first - the order the dashboard feed renders them in."""
        with self._lock:
            entries = list(self._entries)
        return list(reversed(entries))[:limit]

    def counts_by_mode(self) -> Dict[str, int]:
        with self._lock:
            entries = list(self._entries)
        counts: Dict[str, int] = {}
        for entry in entries:
            counts[entry.mode_used] = counts.get(entry.mode_used, 0) + 1
        return counts

    def fallback_count(self) -> int:
        with self._lock:
            return sum(1 for e in self._entries if e.fallback_reason)

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)


def build_record(
    src_mac: str,
    dst_mac: str,
    decision,
    event: str = "new_flow",
    timestamp: Optional[float] = None,
) -> DecisionRecord:
    """Turns a routing/decision_engine.RoutingDecision into a log record,
    pairing each candidate path with the features that were observed for it."""
    candidates = []
    chosen_path = list(decision.path)
    for path, features in zip(getattr(decision, "candidates", []), getattr(decision, "candidate_features", [])):
        candidates.append(
            CandidateSummary(
                path=list(path),
                bottleneck_utilization=features.bottleneck_utilization,
                total_delay_ms=features.total_delay_ms,
                max_loss_pct=features.max_loss_pct,
                min_trust=features.min_trust,
                chosen=list(path) == chosen_path,
            )
        )
    return DecisionRecord(
        timestamp=timestamp if timestamp is not None else time.time(),
        src_mac=src_mac,
        dst_mac=dst_mac,
        path=chosen_path,
        mode_used=decision.mode_used,
        event=event,
        fallback_reason=decision.fallback_reason,
        candidates=candidates,
    )
