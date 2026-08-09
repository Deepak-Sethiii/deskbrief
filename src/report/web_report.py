"""Serialise a run into the payload the hosted dashboard reads.

Deliberately thin: this module owns the JSON shape and nothing else. All
computation happens upstream in transform/metrics.py, which stays pure.

The `commentary_source` field is not decoration - the dashboard shows it. A
reader should be able to tell at a glance whether the prose came from the model
or from the deterministic fallback.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

SCHEMA_VERSION = 1
MAX_HEADLINES = 25


def _records(df: pd.DataFrame) -> list[dict[str, Any]]:
    """DataFrame to JSON-safe records, with NaN as null rather than the string 'nan'."""
    if df is None or df.empty:
        return []
    return json.loads(df.to_json(orient="records", date_format="iso"))


def export_web(
    comps: pd.DataFrame,
    headlines: pd.DataFrame,
    commentary,
    commentary_source: str,
    out_dir: Path,
    chart_png: Path | None = None,
    deck_pptx: Path | None = None,
) -> Path:
    """Write latest.json (plus a dated archive copy) and stage static assets.

    Returns the path to latest.json.
    """
    out_dir = Path(out_dir)
    archive = out_dir / "archive"
    out_dir.mkdir(parents=True, exist_ok=True)
    archive.mkdir(exist_ok=True)

    now = datetime.now(timezone.utc)
    stamp = now.strftime("%Y%m%d")

    payload = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now.isoformat(),
        "commentary_source": commentary_source,   # "llm" | "fallback"
        "commentary": (
            commentary.model_dump() if hasattr(commentary, "model_dump") else dict(commentary)
        ),
        "comps": _records(comps),
        "headlines": _records(headlines.head(MAX_HEADLINES)),
    }

    latest = out_dir / "latest.json"
    body = json.dumps(payload, indent=2, default=str)
    latest.write_text(body, encoding="utf-8")
    (archive / f"{stamp}.json").write_text(body, encoding="utf-8")

    # Static assets sit next to the JSON so the page can reference them by
    # relative path with no server-side routing.
    if chart_png and Path(chart_png).exists():
        shutil.copy2(chart_png, out_dir / "chart.png")
    if deck_pptx and Path(deck_pptx).exists():
        # Stable name only. The timestamped original stays in output/ (gitignored);
        # archiving a deck per day would bloat the repo for little benefit.
        shutil.copy2(deck_pptx, out_dir / "deskbrief.pptx")

    return latest