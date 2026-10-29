"""ADR-085 Remaining item 4: function-template default arguments.

``template<class T = int> void f();`` records its spelled defaults on the
function pattern (``Function.template_param_defaults_fact``, schema v64), on
the clang backend only. A header-only change to one is
``TEMPLATE_PARAM_DEFAULT_CHANGED`` (``API_BREAK``), the same contract as a
class template. Each case goes through the public ``abicheck dump`` /
``abicheck compare`` workflow.

Marked ``integration``: it shells out to clang and castxml.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from abicheck.model import AbiSnapshot, Fact, Function
from abicheck.model.availability import FactStatus
from abicheck.serialization import snapshot_from_dict, snapshot_to_dict
from tests._abicheck_subprocess import abicheck_command

pytestmark = pytest.mark.integration

_KIND = "template_param_default_changed"

_SINGLE = """#pragma once
namespace mylib {{
template <typename T = {default}> void f();
}}  // namespace mylib
"""

_UNDEFAULTED = """#pragma once
namespace mylib {
template <typename T> void f();
}  // namespace mylib
"""

_DEFAULTED = """#pragma once
namespace mylib {
template <typename T = int> void f();
}  // namespace mylib
"""

_REMOVED_DEFAULT = """#pragma once
namespace mylib {
template <typename T> void f();
}  // namespace mylib
"""

_OVERLOADS = """#pragma once
namespace mylib {{
template <typename T = {t}> void f(T);
template <int N = 2> void f();
}}  // namespace mylib
"""


def _require_clang() -> None:
    if shutil.which("clang++") is None:
        pytest.skip("clang++ not installed")


def _require_castxml() -> None:
    if shutil.which("castxml") is None:
        pytest.skip("castxml not installed")


def _run_abicheck(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        abicheck_command(*args),
        capture_output=True,
        text=True,
        check=False,
    )


def _dump(tmp: Path, header: Path, tag: str, frontend: str) -> Path:
    cfg = tmp / f"{frontend}.yml"
    cfg.write_text(f"compile:\n  frontend: {frontend}\n  lang: c++\n")
    out = tmp / f"{tag}-{frontend}.json"
    result = _run_abicheck(
        "dump",
        "-H",
        str(header),
        "--config",
        str(cfg),
        "--depth",
        "headers",
        "-o",
        str(out),
    )
    assert result.returncode == 0, result.stderr
    return out


def _compare(old: Path, new: Path, tmp: Path, tag: str) -> dict[str, Any]:
    out = tmp / f"cmp-{tag}.json"
    _run_abicheck("compare", str(old), str(new), "-o", f"json={out}")
    return json.loads(out.read_text())


def _changes(report: dict[str, Any]) -> list[dict[str, Any]]:
    return [c for c in report.get("changes", []) if c["kind"] == _KIND]


def _pair(tmp: Path, old_text: str, new_text: str, frontend: str) -> dict[str, Any]:
    old_h = tmp / "old.h"
    new_h = tmp / "new.h"
    old_h.write_text(old_text)
    new_h.write_text(new_text)
    old = _dump(tmp, old_h, "old", frontend)
    new = _dump(tmp, new_h, "new", frontend)
    return _compare(old, new, tmp, frontend)


@pytest.mark.parametrize(
    ("old_text", "new_text", "old_value", "new_value"),
    [
        pytest.param(
            _SINGLE.format(default="int"),
            _SINGLE.format(default="char"),
            "('int',)",
            "('char',)",
            id="changed",
        ),
        pytest.param(
            _UNDEFAULTED,
            _DEFAULTED,
            "(None,)",
            "('int',)",
            id="added",
        ),
        pytest.param(
            _DEFAULTED,
            _REMOVED_DEFAULT,
            "('int',)",
            "(None,)",
            id="removed",
        ),
    ],
)
def test_function_default_change_is_api_break_on_clang(
    tmp_path: Path, old_text: str, new_text: str, old_value: str, new_value: str
) -> None:
    _require_clang()
    report = _pair(tmp_path, old_text, new_text, "clang")
    assert report["verdict"] == "API_BREAK"
    (change,) = _changes(report)
    assert change["old_value"] == old_value
    assert change["new_value"] == new_value
    assert change["symbol"] == "mylib::f()"


def test_unchanged_function_default_reads_no_change_on_clang(tmp_path: Path) -> None:
    _require_clang()
    report = _pair(
        tmp_path, _SINGLE.format(default="int"), _SINGLE.format(default="int"), "clang"
    )
    assert report["verdict"] == "NO_CHANGE"
    assert _changes(report) == []


def test_overloaded_templates_pair_by_identity_not_by_name(tmp_path: Path) -> None:
    # Only the ``f(T)`` overload's default changes. The ``f<int N>()`` overload
    # shares the name ``mylib::f`` and must not be paired with it.
    _require_clang()
    report = _pair(
        tmp_path,
        _OVERLOADS.format(t="int"),
        _OVERLOADS.format(t="char"),
        "clang",
    )
    (change,) = _changes(report)
    assert change["symbol"] == "mylib::f(T)"
    assert change["old_value"] == "('int',)"
    assert change["new_value"] == "('char',)"


_DECLARED_THEN_DEFINED = """#pragma once
namespace mylib {{
template <typename T = {default}> void f();
template <typename T> void f() {{}}
}}  // namespace mylib
"""


def test_declared_then_defined_template_default_change_is_reported(
    tmp_path: Path,
) -> None:
    # A declaration and its later definition are two pattern declarations with
    # one identity. Their recorded defaults agree, so the change is kept rather
    # than dropped as an ambiguous identity.
    _require_clang()
    report = _pair(
        tmp_path,
        _DECLARED_THEN_DEFINED.format(default="int"),
        _DECLARED_THEN_DEFINED.format(default="char"),
        "clang",
    )
    assert report["verdict"] == "API_BREAK"
    (change,) = _changes(report)
    assert change["symbol"] == "mylib::f()"
    assert change["old_value"] == "('int',)"
    assert change["new_value"] == "('char',)"


def test_castxml_function_default_change_is_a_named_gap_not_a_verdict(
    tmp_path: Path,
) -> None:
    # castxml records no template defaults. The header scan names the gap, the
    # verdict stays NO_CHANGE, and the run reads partial.
    _require_castxml()
    report = _pair(
        tmp_path,
        _SINGLE.format(default="int"),
        _SINGLE.format(default="char"),
        "castxml",
    )
    assert report["verdict"] == "NO_CHANGE"
    assert _changes(report) == []
    assert report["analysis_assurance"]["status"] == "partial"
    gap_notes = [
        n
        for n in report["analysis_assurance"]["notes"]
        if "template-parameter defaults (castxml cannot record them)" in n
    ]
    assert len(gap_notes) == 2


def test_function_defaults_round_trip_through_v64_snapshot() -> None:
    fn = Function(
        name="f",
        mangled="f",
        return_type="void",
        template_param_defaults_fact=Fact.present(("int", None)),
    )
    snap = AbiSnapshot(library="libx.so", version="1")
    snap.declarations.functions.append(fn)
    back = snapshot_from_dict(snapshot_to_dict(snap))
    (decoded,) = back.declarations.functions
    assert decoded.template_param_defaults_fact is not None
    assert decoded.template_param_defaults_fact.value == ("int", None)


def test_function_without_recorded_defaults_reads_not_collected() -> None:
    # Written only when captured: a function from any other producer encodes
    # without the key, and reads back as NOT_COLLECTED at v64, never as "no
    # defaults".
    snap = AbiSnapshot(library="libx.so", version="1")
    snap.declarations.functions.append(
        Function(name="g", mangled="g", return_type="void")
    )
    doc = snapshot_to_dict(snap)
    (raw,) = doc["functions"]
    assert "template_param_defaults_fact" not in raw
    (decoded,) = snapshot_from_dict(doc).declarations.functions
    assert decoded.template_param_defaults_fact is not None
    assert decoded.template_param_defaults_fact.status is FactStatus.NOT_COLLECTED
