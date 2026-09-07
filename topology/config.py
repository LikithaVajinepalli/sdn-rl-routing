"""Configuration for the parameterized ring-with-chords Mininet topology.

Kept as plain dataclasses (no Mininet/Ryu imports) so both the topology
builder and the test suite can import this on any platform.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Tuple

# NFR2: must operate reliably on 6-10 switch topologies.
MIN_SWITCHES = 6
MAX_SWITCHES = 10


@dataclass
class LinkProfile:
    """Default electrical characteristics for a link, used by Mininet's TCLink."""

    bw_mbps: float = 10.0       # link bandwidth/capacity, used for utilization %
    delay_ms: float = 5.0       # base one-way delay (tc netem) - the "ground truth"
    loss_pct: float = 0.0       # base packet loss %, usually 0 until injected


@dataclass
class TopologyConfig:
    """Parameters for RingChordTopo. Nothing here is hardcoded into the topo class."""

    num_switches: int = 8
    hosts_per_switch: int = 1
    # Circulant-graph chord offsets in addition to the ring's offset-1 edges.
    # E.g. [3] adds an edge between switch i and switch (i+3) mod n for every i,
    # giving each switch 4 neighbours total (2 ring + 2 chord) and shorter
    # alternate paths for the RL agent to choose between.
    chord_offsets: List[int] = field(default_factory=lambda: [3])
    default_link: LinkProfile = field(default_factory=LinkProfile)
    # Optional per-edge overrides, keyed by a frozenset({switch_i, switch_j})
    # switch index pair, falling back to default_link when absent.
    link_overrides: Dict[Tuple[int, int], LinkProfile] = field(default_factory=dict)

    def __post_init__(self):
        if not (MIN_SWITCHES <= self.num_switches <= MAX_SWITCHES):
            raise ValueError(
                f"num_switches={self.num_switches} outside supported range "
                f"[{MIN_SWITCHES}, {MAX_SWITCHES}] (NFR2)"
            )
        if self.hosts_per_switch < 1:
            raise ValueError("hosts_per_switch must be >= 1")
        max_offset = self.num_switches // 2
        for off in self.chord_offsets:
            if not (1 < off <= max_offset):
                raise ValueError(
                    f"chord_offset={off} invalid for num_switches={self.num_switches} "
                    f"(must satisfy 1 < offset <= {max_offset})"
                )

    def link_profile(self, i: int, j: int) -> LinkProfile:
        return self.link_overrides.get((min(i, j), max(i, j)), self.default_link)

    def circulant_offsets(self) -> List[int]:
        """Full set of offsets defining the switch-level graph: ring (1) + chords."""
        return [1] + list(self.chord_offsets)
