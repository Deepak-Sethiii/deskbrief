"""Flag surface of the report/refresh commands.

The contract that matters: every optional stage defaults to OFF, so a bare
`refresh` behaves exactly as it did before any of these flags existed.
"""

from __future__ import annotations

import pytest

from src.cli import _commentary_source_kind, build_parser, main

FLAG_COMMANDS = ("report", "refresh")
OPTIONAL_STAGE_FLAGS = ("no_commentary", "no_chart", "no_deck", "skip_workbook", "web")


@pytest.mark.parametrize("command", FLAG_COMMANDS)
def test_every_optional_stage_flag_defaults_off(command):
    args = build_parser().parse_args([command])
    for flag in OPTIONAL_STAGE_FLAGS:
        assert getattr(args, flag) is False, f"{command} --{flag} must default off"


@pytest.mark.parametrize("command", FLAG_COMMANDS)
def test_skip_workbook_is_accepted_and_sets_the_flag(command):
    args = build_parser().parse_args([command, "--skip-workbook"])
    assert args.skip_workbook is True
    # and it must not disturb the other stages
    assert (args.no_commentary, args.no_chart, args.no_deck) == (False, False, False)


@pytest.mark.parametrize("command", FLAG_COMMANDS)
def test_flags_are_independent(command):
    args = build_parser().parse_args([command, "--skip-workbook", "--no-deck"])
    assert args.skip_workbook is True and args.no_deck is True
    assert args.no_commentary is False and args.no_chart is False


def test_commands_without_the_flag_loop_do_not_gain_the_flags():
    args = build_parser().parse_args(["comps"])
    assert not hasattr(args, "skip_workbook")
    assert not hasattr(args, "web")
    # cmd_report reads these with getattr(..., False) precisely for that reason


@pytest.mark.parametrize("command", FLAG_COMMANDS)
def test_web_with_no_commentary_is_refused(command, capsys):
    """A page with an empty commentary block reads as a broken deploy."""
    with pytest.raises(SystemExit) as excinfo:
        main([command, "--web", "--no-commentary"])
    assert excinfo.value.code == 2  # argparse usage error, not a crash
    message = capsys.readouterr().err
    assert "--web cannot be combined with --no-commentary" in message


@pytest.mark.parametrize("command", FLAG_COMMANDS)
def test_web_alone_and_no_commentary_alone_are_both_allowed(command):
    assert build_parser().parse_args([command, "--web"]).web is True
    assert build_parser().parse_args([command, "--no-commentary"]).no_commentary is True


# --------------------------------------------------------------------------
# commentary source badge
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "commentary, expected",
    [
        ({"source": "groq:llama-3.3-70b-versatile"}, "llm"),
        ({"source": "deterministic-fallback (no GROQ_API_KEY)"}, "fallback"),
        ({"source": "deterministic-fallback (model returned unusable output twice)"},
         "fallback"),
        ({"source_kind": "llm", "source": "anything"}, "llm"),
        ({"source_kind": "fallback", "source": "groq:whatever"}, "fallback"),
        # absent commentary must never be reported as model-written
        (None, "fallback"),
        ({}, "fallback"),
    ],
)
def test_commentary_source_kind(commentary, expected):
    assert _commentary_source_kind(commentary) == expected


def test_source_kind_key_wins_over_the_derived_prefix():
    """The explicit key is the live path; the prefix derivation is only a guard."""
    conflicting = {"source_kind": "llm", "source": "deterministic-fallback (stale)"}
    assert _commentary_source_kind(conflicting) == "llm"


def test_generate_commentary_always_supplies_the_key_the_helper_prefers():
    """Ties the two modules together: the derived branch is dead in normal use."""
    import pandas as pd

    from src.report.commentary import fallback_commentary

    produced = fallback_commentary(
        pd.DataFrame({"ret_1d": [0.01]}, index=pd.Index(["A"], name="ticker")), None
    )
    assert "source_kind" in produced
    assert _commentary_source_kind(produced) == produced["source_kind"]
