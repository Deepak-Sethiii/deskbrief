"""Every filesystem location DeskBrief uses, defined once and imported everywhere.

Two different anchoring strategies here, deliberately:

* PROJECT_ROOT is anchored to *this file*, so config, templates and the SQLite
  database resolve against the repo no matter what the working directory is.
  Those are inputs; they live where the code lives.

* OUTPUT_ROOT defaults to the RELATIVE path "output", resolved against the
  current working directory, and is overridable with DESKBRIEF_OUTPUT. That lets
  CI redirect every generated artefact (DESKBRIEF_OUTPUT=public) without a code
  change. run_refresh.bat cds to the repo root before running anything, so the
  default resolves exactly where it always has.

The env var is read once at import time. Every consumer of it is a process
launched with the variable already set, which is the only usage that matters;
tests pass an explicit output_dir instead of mutating the environment.
"""

from __future__ import annotations

import os
from pathlib import Path

# --- inputs: anchored to the repo -----------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[1]

CONFIG_DIR = PROJECT_ROOT / "config"
DATA_DIR = PROJECT_ROOT / "data"
TEMPLATES_DIR = PROJECT_ROOT / "templates"
LOGS_DIR = PROJECT_ROOT / "logs"

DEFAULT_DB_PATH = DATA_DIR / "deskbrief.db"
DEFAULT_WATCHLIST = CONFIG_DIR / "watchlist.yaml"
TEMPLATE_PATH = TEMPLATES_DIR / "deskbrief_template.xlsm"

# Chart PNGs are intermediate build products consumed by the workbook and the
# deck, not deliverables, so they stay beside the repo rather than moving with
# OUTPUT_ROOT. The web export copies the ones it needs into the output root.
ASSETS_DIR = PROJECT_ROOT / "assets"

# --- outputs: redirectable, but still repo-anchored ------------------------
# A relative DESKBRIEF_OUTPUT (or none at all) resolves against PROJECT_ROOT,
# not the working directory: making it CWD-relative would silently move the
# artefacts depending on where the process happened to be started from. An
# absolute value is honoured verbatim, which is what a CI job or a container
# mount needs.
_out = os.getenv("DESKBRIEF_OUTPUT")
OUTPUT_ROOT = (
    Path(_out) if _out and Path(_out).is_absolute()
    else PROJECT_ROOT / (_out or "output")
)

# The web bundle is deliberately NOT under OUTPUT_ROOT. Routing it through the
# same knob meant pointing DESKBRIEF_OUTPUT at public/ to publish, which also
# dropped the timestamped .xlsm and .pptx in there -- deployable files mixed in
# with pipeline artefacts. Fixed location, always; --web writes here and
# nowhere else.
WEB_DIR = PROJECT_ROOT / "public"

# The macro reads this pointer to find the workbook it should open. Only the
# filename is fixed; it is always written beside the workbook it points at.
LATEST_POINTER_NAME = "latest.txt"
