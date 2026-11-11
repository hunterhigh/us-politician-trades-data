"""Compatibility import for the frozen paper implementation."""
import sys
from .frozen_paper import reviewed_house_paper as _implementation

sys.modules[__name__] = _implementation
