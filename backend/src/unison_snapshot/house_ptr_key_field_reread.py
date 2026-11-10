"""Compatibility import for the frozen paper implementation."""
import sys
from .frozen_paper import house_ptr_key_field_reread as _implementation

if __name__ == "__main__":
    _implementation.main()
else:
    sys.modules[__name__] = _implementation
