"""Reuse synthetic P1 fixtures without changing application code."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
