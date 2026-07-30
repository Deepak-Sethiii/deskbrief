"""LLM commentary via Groq, with a deterministic fallback that always works.

THE CONTRACT: generate_commentary() never raises and never returns None. A
market-research pipeline that fails to produce a workbook because a language
model returned prose instead of JSON is a broken pipeline. The model is an
enhancement to the report, not a dependency of it.

Failure ladder, in order:
  1. call Groq, parse strict JSON, validate with pydantic  -> use it
  2. anything at all goes wrong -> retry ONCE with a blunter instruction
  3. still bad, or no API key at all -> deterministic template summary built
     from the comps table itself, clearly labelled as such in the output
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Mapping

import pandas as pd
from pydantic import BaseModel, Field, ValidationError, field_validator

log = logging.getLogger(__name__)

MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
MAX_HEADLINES_IN_PROMPT = 25
REQUEST_TIMEOUT = 45.0

SYSTEM_PROMPT = (
    "You are a sell-side equity research assistant covering Indian large caps. "
    "You will be given a comps table and today's headlines. "
    "Reply with a single JSON object and nothing else. No markdown, no code "
    "fences, no preamble, no trailing commentary.\n"
    "Schema, exactly these three keys:\n"
    '{"market_tone": "<one short sentence>", '
    '"bullets": ["<3 to 5 factual observations>"], '
    '"watch_items": ["<2 to 4 things to watch next session>"]}\n'
    "Ground every statement in the numbers or headlines supplied. Do not invent "
    "figures, price targets, or recommendations."
)


class Commentary(BaseModel):
    """Strict shape we demand back from the model."""

    market_tone: str = Field(min_length=1)
    bullets: list[str] = Field(min_length=1)
    watch_items: list[str] = Field(default_factory=list)

    @field_validator("market_tone")
    @classmethod
    def _tidy_tone(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("market_tone is empty")
        return cleaned

    @field_validator("bullets", "watch_items", mode="before")
    @classmethod
    def _coerce_list(cls, value: Any) -> Any:
        # Models sometimes send a single string, or a newline-delimited blob.
        if isinstance(value, str):
            return [line.strip(" -*\t") for line in value.splitlines() if line.strip()]
        return value

    @field_validator("bullets", "watch_items")
    @classmethod
    def _tidy_items(cls, value: list[str]) -> list[str]:
        items = [" ".join(str(v).split()) for v in value]
        return [item for item in items if item]


def extract_json(text: str) -> dict[str, Any]:
    """Pull a JSON object out of a model response that may be wrapped in prose.

    Tries the whole string first, then a ```json fence, then the outermost
    brace-balanced span. Raises ValueError if none of that yields an object.
    """
    if not text or not text.strip():
        raise ValueError("empty response")

    candidates: list[str] = [text.strip()]

    fence = re.search(r"```(?:json)?\s*(.+?)\s*```", text, re.DOTALL | re.IGNORECASE)
    if fence:
        candidates.append(fence.group(1).strip())

    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed

    raise ValueError("no JSON object found in response")


def format_comps_for_prompt(comps: pd.DataFrame) -> str:
    """Compact, readable rendering of the comps table for the prompt."""
    if comps is None or comps.empty:
        return "(no comps data)"

    lines = ["ticker | sector | last | 1d% | 5d% | 21d% | vol30 | vs52wHigh | P/E"]
    for ticker, row in comps.iterrows():
        def pct(key: str) -> str:
            value = row.get(key)
            return "n/a" if pd.isna(value) else f"{value * 100:+.2f}%"

        def num(key: str, fmt: str = ".1f") -> str:
            value = row.get(key)
            return "n/a" if pd.isna(value) else format(value, fmt)

        lines.append(
            f"{ticker} | {row.get('sector') or 'n/a'} | {num('last_close', ',.2f')} | "
            f"{pct('ret_1d')} | {pct('ret_5d')} | {pct('ret_21d')} | "
            f"{num('realised_vol_30d', '.1%')} | {pct('pct_from_52w_high')} | "
            f"{num('pe')}"
        )
    return "\n".join(lines)


def format_headlines_for_prompt(headlines: pd.DataFrame | None) -> str:
    if headlines is None or headlines.empty:
        return "(no headlines today)"

    lines = []
    for _, row in headlines.head(MAX_HEADLINES_IN_PROMPT).iterrows():
        tag = row.get("ticker") or "-"
        lines.append(f"[{tag}] {row.get('title')}")
    return "\n".join(lines)


def build_user_prompt(comps: pd.DataFrame, headlines: pd.DataFrame | None) -> str:
    return (
        "COMPS TABLE (returns are fractions shown as percentages):\n"
        f"{format_comps_for_prompt(comps)}\n\n"
        "TODAY'S HEADLINES (ticker tags are approximate substring matches, "
        "not verified entity links - treat them as hints):\n"
        f"{format_headlines_for_prompt(headlines)}\n\n"
        "Return the JSON object now."
    )


def _call_groq(client: Any, user_prompt: str, *, blunt: bool = False) -> str:
    system = SYSTEM_PROMPT
    if blunt:
        system += (
            "\n\nYOUR PREVIOUS REPLY WAS NOT VALID JSON. Output must start with "
            "the character { and end with the character }. Nothing before, "
            "nothing after."
        )

    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.2,  # low: this is summarisation, not ideation
        max_tokens=900,
        # Groq honours this for the llama-3.3 models and it removes most of the
        # prose-instead-of-JSON failures at the source.
        response_format={"type": "json_object"},
    )
    return response.choices[0].message.content or ""


def _make_client() -> Any | None:
    api_key = os.environ.get("GROQ_API_KEY", "").strip()
    if not api_key:
        return None
    try:
        from groq import Groq

        return Groq(api_key=api_key, timeout=REQUEST_TIMEOUT)
    except Exception as exc:  # noqa: BLE001
        log.warning("could not construct the Groq client: %s", exc)
        return None


def fallback_commentary(comps: pd.DataFrame, headlines: pd.DataFrame | None) -> dict[str, Any]:
    """Deterministic summary computed from the comps table. No model involved.

    Deliberately plain and factual. It should read as obviously mechanical, so
    nobody mistakes it for the model's work.
    """
    result: dict[str, Any] = {
        "market_tone": "No commentary model available; summary generated from the data.",
        "bullets": [],
        "watch_items": [],
        "source": "deterministic-fallback",
    }
    if comps is None or comps.empty:
        result["bullets"] = ["No comparable data was available for this run."]
        return result

    day = comps["ret_1d"].dropna() if "ret_1d" in comps.columns else pd.Series(dtype=float)
    bullets: list[str] = []

    if not day.empty:
        advancers = int((day > 0).sum())
        decliners = int((day < 0).sum())
        breadth = "risk-on" if advancers > decliners else (
            "risk-off" if decliners > advancers else "mixed"
        )
        result["market_tone"] = (
            f"Breadth was {breadth}: {advancers} of {len(day)} names advanced on the day."
        )
        best, worst = day.idxmax(), day.idxmin()
        bullets.append(f"Best performer on the day: {best} at {day[best] * 100:+.2f}%.")
        bullets.append(f"Weakest on the day: {worst} at {day[worst] * 100:+.2f}%.")
        bullets.append(f"Median one-day move across the watchlist: {day.median() * 100:+.2f}%.")

    if "pct_from_52w_high" in comps.columns:
        gap = comps["pct_from_52w_high"].dropna()
        if not gap.empty:
            furthest = gap.idxmin()
            bullets.append(
                f"{furthest} sits furthest below its 52-week high, at "
                f"{gap[furthest] * 100:.1f}%."
            )
            near = gap[gap > -0.05]
            if len(near):
                bullets.append(
                    f"{len(near)} name(s) within 5% of the 52-week high: "
                    f"{', '.join(near.index)}."
                )

    if "realised_vol_30d" in comps.columns:
        vol = comps["realised_vol_30d"].dropna()
        if not vol.empty:
            result["watch_items"].append(
                f"Highest 30-day realised volatility: {vol.idxmax()} at "
                f"{vol.max() * 100:.1f}% annualised."
            )

    if headlines is not None and not headlines.empty:
        tagged = headlines["ticker"].notna().sum() if "ticker" in headlines.columns else 0
        result["watch_items"].append(
            f"{len(headlines)} headlines collected, {tagged} tagged to a watchlist name "
            "by substring match (unverified)."
        )

    result["watch_items"].append(
        "Commentary model was unavailable, so no qualitative read is included."
    )
    result["bullets"] = bullets or ["No derived observations were available."]
    return result


def generate_commentary(
    comps: pd.DataFrame,
    headlines: pd.DataFrame | None = None,
    *,
    client: Any | None = None,
) -> dict[str, Any]:
    """Best-effort commentary. Always returns a usable dict; never raises."""
    client = client or _make_client()
    if client is None:
        log.warning("GROQ_API_KEY not set -- using the deterministic fallback summary")
        result = fallback_commentary(comps, headlines)
        result["source"] = "deterministic-fallback (no GROQ_API_KEY)"
        return result

    user_prompt = build_user_prompt(comps, headlines)

    for attempt, blunt in enumerate((False, True), start=1):
        try:
            raw = _call_groq(client, user_prompt, blunt=blunt)
            payload = extract_json(raw)
            commentary = Commentary.model_validate(payload)
            log.info("commentary generated by %s (attempt %d)", MODEL, attempt)
            result = commentary.model_dump()
            result["source"] = f"groq:{MODEL}"
            return result
        except (ValueError, ValidationError) as exc:
            log.warning("commentary attempt %d returned unusable output: %s", attempt, exc)
        except Exception as exc:  # noqa: BLE001 - network, auth, rate limit, anything
            log.warning("commentary attempt %d failed: %s", attempt, exc)

    log.error("commentary model failed twice -- falling back to the deterministic summary")
    result = fallback_commentary(comps, headlines)
    result["source"] = "deterministic-fallback (model returned unusable output twice)"
    return result
