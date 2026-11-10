"""Compatibility import for the frozen paper implementation."""
import sys
from .frozen_paper import senate_paper_accounts as _implementation

sys.modules[__name__] = _implementation
