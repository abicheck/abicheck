"""Tests for scripts/baseline_loader.py and the five gates that read a baseline.

The shared loader refuses ``NaN``/``Infinity`` and otherwise returns exactly what
``json.loads`` returns. Each gate must then treat a poisoned baseline the way it
already treats a malformed one (fail, or report the baseline unusable), never
gate against it silently.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import baseline_loader as bl  # noqa: E402
import check_header_graph_perf as hg  # noqa: E402
import l2_cli_gating as l2  # noqa: E402
import mutation_scope as ms  # noqa: E402
import perf_antipatterns as pa  # noqa: E402
import perf_baseline as pb  # noqa: E402

VALID_DOCS = [
    '{"scenarios": {"a": {"points": [{"size": 10, "seconds": 1.5}]}}}',
    '{"sites": {}}',
    '{"x": [1, -2.5e3, true, false, null, "NaN is text", "Infinity"]}',
    '{"name": "caf\\u00e9"}',
]


# --- valid input -------------------------------------------------------------


@pytest.mark.parametrize("text", VALID_DOCS)
def test_valid_input_matches_json_loads(text: str) -> None:
    assert bl.parse_baseline(text) == json.loads(text)


def test_read_baseline_reads_utf8_file(tmp_path: Path) -> None:
    path = tmp_path / "b.json"
    path.write_text('{"name": "café"}', encoding="utf-8")
    assert bl.read_baseline(path) == {"name": "café"}


# --- NaN and Infinity --------------------------------------------------------


@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity"])
def test_non_finite_constant_is_refused(token: str) -> None:
    with pytest.raises(bl.BaselineFormatError, match=token) as info:
        bl.parse_baseline(f'{{"v": {token}}}')
    # Same family as malformed JSON, so existing `except` clauses still cover it.
    assert isinstance(info.value, json.JSONDecodeError)
    assert isinstance(info.value, ValueError)


@pytest.mark.parametrize("token", ["NaN", "Infinity"])
def test_non_finite_constant_is_refused_when_nested(token: str) -> None:
    with pytest.raises(bl.BaselineFormatError):
        bl.parse_baseline(f'{{"a": [{{"b": {{"c": [1, {token}]}}}}]}}')


def test_non_finite_constant_in_file_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "b.json"
    path.write_text('{"points": [{"seconds": NaN}]}', encoding="utf-8")
    with pytest.raises(bl.BaselineFormatError):
        bl.read_baseline(path)


def test_the_tokens_inside_strings_are_not_refused() -> None:
    assert bl.parse_baseline('{"v": "NaN and Infinity"}') == {"v": "NaN and Infinity"}


def test_malformed_json_is_still_a_plain_decode_error() -> None:
    with pytest.raises(json.JSONDecodeError) as info:
        bl.parse_baseline('{"a": ')
    assert not isinstance(info.value, bl.BaselineFormatError)


# --- missing file -------------------------------------------------------------


def test_missing_file_raises_oserror(tmp_path: Path) -> None:
    with pytest.raises(OSError):
        bl.read_baseline(tmp_path / "absent.json")


# --- non-object root ----------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [("[1, 2]", [1, 2]), ('"x"', "x"), ("5", 5), ("null", None)],
)
def test_non_object_root_is_returned_as_json_loads_returns_it(
    tmp_path: Path, text: str, expected: object
) -> None:
    # The loader does not validate the root; each caller does, as before.
    path = tmp_path / "b.json"
    path.write_text(text, encoding="utf-8")
    assert bl.read_baseline(path) == expected


def test_non_finite_constant_under_a_non_object_root_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "b.json"
    path.write_text("[NaN]", encoding="utf-8")
    with pytest.raises(bl.BaselineFormatError):
        bl.read_baseline(path)


# --- each gate refuses a poisoned baseline ------------------------------------


def _poisoned(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "baseline.json"
    path.write_text(text, encoding="utf-8")
    return path


def test_perf_antipatterns_refuses_nan_site_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # NaN used to compare as "not above baseline" and pass the gate silently.
    monkeypatch.setattr(
        pa, "BASELINE_FILE", _poisoned(tmp_path, '{"sites": {"k": NaN}}')
    )
    with pytest.raises(json.JSONDecodeError):
        pa.load_baseline()


def test_perf_baseline_reports_nan_point_as_unusable_baseline(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    text = '{"scenarios": {"a": {"points": [{"size": 10, "seconds": Infinity}]}}}'
    assert pb.load_baseline(_poisoned(tmp_path, text), None) == {}
    assert "could not load baseline" in capsys.readouterr().out


def test_check_header_graph_perf_refuses_nan_point(tmp_path: Path) -> None:
    with pytest.raises(bl.BaselineFormatError):
        hg._load_baseline(
            _poisoned(tmp_path, '{"points": [{"size": 10, "attach_ms": NaN}]}')
        )


def test_l2_cli_gating_refuses_infinite_step(tmp_path: Path) -> None:
    text = (
        '{"scenarios": [{"id": "s1", "status": "ok", "steps": '
        '[{"name": "full", "scope": "full_cli", "gated": true, "wall_seconds": Infinity}]}]}'
    )
    with pytest.raises(bl.BaselineFormatError):
        l2.load_baseline(_poisoned(tmp_path, text))


def test_mutation_scope_refuses_nan_module_count_rather_than_reading_none(
    tmp_path: Path,
) -> None:
    # None would mean "no per-module baseline" and silently skip the drift check.
    path = _poisoned(tmp_path, '{"modules": {"abicheck/x.py": {"survivors": NaN}}}')
    with pytest.raises(bl.BaselineFormatError):
        ms.load_baseline(path)


@pytest.mark.parametrize(
    "text",
    [
        '{"modules": {"m.py": {"functions": {"f": Infinity}}}}',
        '{"modules": {"m.py": {"functions": {"f": -Infinity}}}}',
    ],
)
def test_mutation_scope_refuses_infinite_function_count(
    tmp_path: Path, text: str
) -> None:
    with pytest.raises(bl.BaselineFormatError):
        ms.load_function_baseline(_poisoned(tmp_path, text))


def test_mutation_scope_still_treats_a_corrupt_file_as_absent(tmp_path: Path) -> None:
    path = _poisoned(tmp_path, '{"modules": ')
    assert ms.load_baseline(path) is None
    assert ms.load_function_baseline(path) == {}


def test_valid_mutation_baseline_still_loads(tmp_path: Path) -> None:
    path = _poisoned(
        tmp_path,
        '{"modules": {"abicheck/x.py": {"survivors": 3, "functions": {"f": 2}}}}',
    )
    assert ms.load_baseline(path) == {"abicheck/x.py": 3}
    assert ms.load_function_baseline(path) == {("abicheck/x.py", "f"): 2}
