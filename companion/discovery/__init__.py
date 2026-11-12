"""World discovery: a Scanner interface, a fake implementation, and in-game effects.

Real radios are intentionally not used here. Hardware drivers should implement
the Scanner interface in discovery.base later on.
"""

from discovery.base import SIGNAL_KINDS, Scanner, Signal
from discovery.effects import apply_signals
from discovery.simulated import SimulatedScanner

__all__ = [
    "SIGNAL_KINDS",
    "Scanner",
    "Signal",
    "SimulatedScanner",
    "apply_signals",
]
