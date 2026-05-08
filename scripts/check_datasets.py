"""Pre-commit hook: fail if any staged file in datasets/ is not DVC-tracked."""

from __future__ import annotations

import pathlib
import sys

IGNORED = {".gitkeep", ".dvcignore"}
files = sys.argv[1:]
bad = [
    f
    for f in files
    if pathlib.Path(f).parts[0] == "datasets"
    and pathlib.Path(f).name not in IGNORED
    and not f.endswith(".dvc")
]
if bad:
    print(f"ERROR: Raw dataset files staged — use `dvc add` first: {bad}")
    sys.exit(1)
