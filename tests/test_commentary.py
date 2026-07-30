"""Stage 5 coverage.

The point of these tests is the negative path. The pipeline must survive a model
that returns prose, half-JSON, the wrong schema, or nothing at all -- and it
must never raise. Fake clients stand in for Groq so nothing here touches the
network or needs an API key.
"""

from __future__ import annotations

import json

import pandas as pd
import pytest

from src.report.commentary import (
    Commentary,
    extract_json,
    fallback_commentary,
    generate_commentary,
)

GOOD_PAYLOAD = {
    "market_tone": "Constructive, led by index heavyweights.",
    "bullets": ["TCS led the tape.", "Banks lagged.", "Breadth was positive."],
    "watch_items": ["ITC results tomorrow.", "US CPI print."],
}


class FakeClient:
    """Minimal stand-in for groq.Groq with a scripted sequence of replies."""

    def __init__(self, *replies: str | Exception):
        self._replies = list(replies)
        self.calls: list[dict] = []
        self.chat = self  # groq's shape is client.chat.completions.create
        self.completions = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        reply = self._replies.pop(0) if self._replies else ""
        if isinstance(reply, Exception):
            raise reply

        class Message:
            content = reply

        class Choice:
            message = Message()

        class Response:
            choices = [Choice()]

        return Response()


@pytest.fixture()
def comps():
    return pd.DataFrame(
        {
            "sector": ["Technology", "Financial Services", "Energy"],
            "last_close": [2446.6, 748.2, 1275.9],
            "ret_1d": [0.0203, -0.0174, 0.0065],
            "ret_5d": [0.1079, -0.0066, -0.0099],
            "ret_21d": [0.2109, -0.0623, -0.0139],
            "realised_vol_30d": [0.3536, 0.2537, 0.175],
            "pct_from_52w_high": [-0.2365, -0.2491, -0.0195],
            "pe": [17.65, 16.49, 23.4],
        },
        index=pd.Index(["TCS.NS", "HDFCBANK.NS", "RELIANCE.NS"], name="ticker"),
    )


@pytest.fixture()
def headlines():
    return pd.DataFrame(
        [
            {"ticker": "TCS.NS", "title": "TCS wins large deal", "source": "Wire"},
            {"ticker": None, "title": "Broad market rallies", "source": "Wire"},
        ]
    )


# --------------------------------------------------------------------------
# extract_json
# --------------------------------------------------------------------------

def test_extract_json_plain_object():
    assert extract_json(json.dumps(GOOD_PAYLOAD)) == GOOD_PAYLOAD


def test_extract_json_from_markdown_fence():
    wrapped = f"```json\n{json.dumps(GOOD_PAYLOAD)}\n```"
    assert extract_json(wrapped) == GOOD_PAYLOAD


def test_extract_json_with_prose_on_both_sides():
    noisy = (
        "Sure! Here is the analysis you asked for:\n"
        f"{json.dumps(GOOD_PAYLOAD)}\n"
        "Let me know if you would like anything expanded."
    )
    assert extract_json(noisy) == GOOD_PAYLOAD


@pytest.mark.parametrize("text", ["", "   ", "no json here at all", "[1, 2, 3]", "{oops"])
def test_extract_json_rejects_unusable_text(text):
    with pytest.raises(ValueError):
        extract_json(text)


# --------------------------------------------------------------------------
# schema validation
# --------------------------------------------------------------------------

def test_commentary_accepts_a_string_instead_of_a_list():
    model = Commentary.model_validate(
        {"market_tone": "Flat", "bullets": "- one\n- two\n", "watch_items": []}
    )
    assert model.bullets == ["one", "two"]


def test_commentary_rejects_missing_bullets():
    with pytest.raises(Exception):
        Commentary.model_validate({"market_tone": "Flat", "bullets": []})


def test_commentary_rejects_blank_tone():
    with pytest.raises(Exception):
        Commentary.model_validate({"market_tone": "   ", "bullets": ["x"]})


# --------------------------------------------------------------------------
# generate_commentary: the failure ladder
# --------------------------------------------------------------------------

def test_happy_path_uses_the_model(comps, headlines):
    client = FakeClient(json.dumps(GOOD_PAYLOAD))
    result = generate_commentary(comps, headlines, client=client)
    assert result["market_tone"] == GOOD_PAYLOAD["market_tone"]
    assert result["bullets"] == GOOD_PAYLOAD["bullets"]
    assert result["source"].startswith("groq:")
    assert len(client.calls) == 1


def test_prose_reply_triggers_exactly_one_retry_then_succeeds(comps, headlines):
    client = FakeClient("I'd be happy to help! Here's my read...", json.dumps(GOOD_PAYLOAD))
    result = generate_commentary(comps, headlines, client=client)
    assert result["source"].startswith("groq:")
    assert len(client.calls) == 2
    # the retry must actually escalate the instruction, not just repeat it
    assert "NOT VALID JSON" in client.calls[1]["messages"][0]["content"]


def test_two_bad_replies_fall_back_and_do_not_raise(comps, headlines):
    client = FakeClient("still prose", "more prose")
    result = generate_commentary(comps, headlines, client=client)
    assert result["source"].startswith("deterministic-fallback")
    assert result["bullets"]
    assert len(client.calls) == 2  # tried twice, never a third time


def test_network_exception_falls_back_and_does_not_raise(comps, headlines):
    client = FakeClient(RuntimeError("connection reset"), RuntimeError("rate limited"))
    result = generate_commentary(comps, headlines, client=client)
    assert result["source"].startswith("deterministic-fallback")


def test_wrong_schema_falls_back(comps, headlines):
    client = FakeClient(json.dumps({"summary": "wrong keys"}), json.dumps({"nope": 1}))
    result = generate_commentary(comps, headlines, client=client)
    assert result["source"].startswith("deterministic-fallback")


def test_no_api_key_uses_fallback_without_calling_anything(comps, headlines, monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    result = generate_commentary(comps, headlines)
    assert result["source"] == "deterministic-fallback (no GROQ_API_KEY)"
    assert result["bullets"]


def test_generate_commentary_never_raises_on_empty_inputs():
    result = generate_commentary(pd.DataFrame(), None, client=FakeClient("garbage", "garbage"))
    assert result["market_tone"]
    assert result["bullets"]


# --------------------------------------------------------------------------
# the deterministic fallback itself
# --------------------------------------------------------------------------

def test_fallback_reports_real_breadth_and_extremes(comps, headlines):
    result = fallback_commentary(comps, headlines)
    assert "2 of 3 names advanced" in result["market_tone"]
    joined = " ".join(result["bullets"])
    assert "TCS.NS" in joined and "+2.03%" in joined      # best performer
    assert "HDFCBANK.NS" in joined and "-1.74%" in joined  # worst performer


def test_fallback_flags_the_widest_gap_to_the_52w_high(comps, headlines):
    joined = " ".join(fallback_commentary(comps, headlines)["bullets"])
    assert "HDFCBANK.NS sits furthest below its 52-week high" in joined


def test_fallback_is_honest_that_no_model_ran(comps, headlines):
    joined = " ".join(fallback_commentary(comps, headlines)["watch_items"])
    assert "model was unavailable" in joined


def test_fallback_survives_an_all_nan_column(headlines):
    comps = pd.DataFrame(
        {"ret_1d": [float("nan")], "pct_from_52w_high": [float("nan")],
         "realised_vol_30d": [float("nan")]},
        index=pd.Index(["A"], name="ticker"),
    )
    result = fallback_commentary(comps, headlines)
    assert result["bullets"]
