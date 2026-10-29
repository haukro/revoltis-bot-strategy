"""Reuse the synthetic P1 fixtures when this subdirectory is collected alone."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
