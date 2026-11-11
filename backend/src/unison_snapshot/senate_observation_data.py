"""Compatibility import for the frozen paper implementation."""
import sys
from .frozen_paper import senate_observation_data as _implementation

sys.modules[__name__] = _implementation
