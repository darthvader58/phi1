"""Calibration result types, shared by the offline fitter and the runtime loader.

Deliberately free of numpy/scipy: backend/data/calibration.py (the fitter)
and backend/data/calibration_store.py (the runtime reader) both need these
shapes, but only the fitter needs numpy/scipy. Defining them here means
importing the runtime reader never drags the fitting module — and its heavy
dependencies — in behind it.
"""

from dataclasses import dataclass
from typing import Dict


@dataclass
class TyreDegParams:
    """Fitted parameters for a single compound at a single track."""
    compound: str
    track: str
    alpha: float   # Base compound offset (seconds from track base time)
    k: float       # Degradation rate coefficient
    e: float       # Degradation exponent
    r_squared: float  # Goodness of fit
    n_samples: int    # Number of data points used
    base_lap_time: float  # Estimated base fuel-corrected lap time


@dataclass
class TrackCalibration:
    """Full calibration result for a track."""
    track: str
    base_lap_time: float
    pit_loss_seconds: float
    compounds: Dict[str, TyreDegParams]
