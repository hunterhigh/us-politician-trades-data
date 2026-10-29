"""Portable saved-input preparation reuses real PDF and existing saved fixtures."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from test_oge_cover_consumption import fixture, write, DOCUMENT_ID
from test_oge_content_relations import column_read
from unison_snapshot.oge_278t_fields import observe_content_rows, observe_field_shapes
from unison_snapshot.oge_saved_observations import MAX_JSON_BYTES, load_saved_oge_observations


ROOT = Path(__file__).resolve().parents[2]


def directory_alias(link, target):
    if os.name == 'nt':
        subprocess.run(['cmd.exe', '/c', 'mklink', '/J', str(link), str(target)],
                       check=True, capture_output=True)
    else:
        link.symlink_to(target, target_is_directory=True)




if __name__ == '__main__':
    unittest.main()
