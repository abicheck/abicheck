# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""A directory/package ``compare`` applies the dependency walk and debug roots.

Bug class: a release member silently disagreeing with the identical
single-pair comparison (``tests/regressions`` ``F2`` route parity). The
directory/package fan-out took no ``follow_deps``, ``search_paths``,
``ld_library_path`` or directory/detached-file debug-root argument, so
``--follow-deps``, ``--search-path``, ``--ld-library-path`` and
``--debug-info DIR`` (and their ``deps.*``/``debug.info`` config homes) were
accepted on a directory operand and did nothing: a false clean.

General invariant (ADR-061/063 "one model, any cardinality"): for every one of
those settings and every spelling of it (flag, ``.abicheck.yml``, ``--set``
for the non-core keys), each member of a release is compared with exactly the
run inputs the scalar ``compare`` of that member's pair uses. The oracle is
the scalar route: the effective request the real CLI hands the pair session
for ``compare OLD_LIB NEW_LIB``, not the release's own forwarding helper. A
one-member and a three-member release are both checked, and the
``integration`` tests below repeat the claim on real compiled ELF libraries
through the rendered JSON report.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from abicheck.cli import main
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.serialization import snapshot_to_json


class _Captured(Exception):
    def __init__(self, value: Any) -> None:
        super().__init__("captured")
        self.value = value


@pytest.fixture(autouse=True)
def _in_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run from *tmp_path*, so the only config is the one a case writes."""
    monkeypatch.chdir(tmp_path)


def _snapshot(path: Path, library: str, version: str) -> Path:
    fn = Function(
        name=f"{library}_f",
        mangled=f"{library}_f",
        return_type="int",
        visibility=Visibility.PUBLIC,
    )
    snap = AbiSnapshot(
        library=f"{library}.so", version=version, functions=[fn], from_headers=True
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(snapshot_to_json(snap), encoding="utf-8")
    return path


@pytest.fixture
def dirs(tmp_path: Path) -> dict[str, Path]:
    out = {}
    for name in ("libs_a", "libs_b", "dbg_old", "dbg_new", "dbg_both"):
        path = tmp_path / name
        path.mkdir()
        out[name] = path
    return out


def _view(follow: Any, search: Any, ld: Any, old_dbg: Any, new_dbg: Any) -> dict:
    return {
        "follow": bool(follow),
        "search_paths": tuple(Path(p) for p in search),
        "ld_library_path": ld,
        "old_debug": tuple(Path(p) for p in old_dbg),
        "new_debug": tuple(Path(p) for p in new_dbg),
    }


def _scalar_view(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, args: list[str]
) -> dict:
    """The run inputs the real CLI hands the scalar pair session."""
    import abicheck.workflows.compare.session as session

    def fake(request: Any, host: Any) -> Any:
        raise _Captured(request)

    with monkeypatch.context() as m:
        m.setattr(session, "run_pair_session", fake)
        result = CliRunner().invoke(main, args, catch_exceptions=True)
    assert isinstance(result.exception, _Captured), (result.exit_code, result.output)
    ev = result.exception.value.evidence
    return _view(
        ev.follow_deps,
        ev.search_paths,
        ev.ld_library_path,
        ev.old_debug_roots,
        ev.new_debug_roots,
    )


def _release_views(monkeypatch: pytest.MonkeyPatch, args: list[str]) -> dict[str, dict]:
    """The run inputs each release member's CompareRequest carries, by library."""
    import abicheck.workflows.member_compare as member_compare

    seen: dict[str, dict] = {}
    real = member_compare.run_compare_request

    def record(request: Any) -> Any:
        seen[Path(request.old.path).name] = _view(
            request.follow_deps,
            request.dependency_search_paths,
            request.ld_library_path,
            request.old.debug_roots,
            request.new.debug_roots,
        )
        return real(request)

    with monkeypatch.context() as m:
        m.setattr(member_compare, "run_compare_request", record)
        # Keep a multi-member pool in this process so the recorder sees every
        # member; the integration test below runs the forked process pool.
        m.setattr(
            "abicheck.workflows.release_member_processes.member_processes_possible",
            lambda: False,
        )
        result = CliRunner().invoke(main, args, catch_exceptions=True)
    assert result.exit_code in (0, 1, 2, 4), (result.exit_code, result.output)
    return seen


# (flag argv, config YAML, --set argv or None for a core key) per concept.
_Case = Callable[[dict[str, Path]], tuple[list[str], str, list[str] | None]]

_CASES: dict[str, _Case] = {
    "deps.follow": lambda d: (["--follow-deps"], "deps:\n  follow: true\n", None),
    "deps.follow/off-over-config": lambda d: (
        ["--no-follow-deps"],
        "deps:\n  follow: false\n",
        None,
    ),
    "deps.search_paths": lambda d: (
        ["--search-path", str(d["libs_a"]), "--search-path", str(d["libs_b"])],
        f"deps:\n  search_paths: [{d['libs_a']}, {d['libs_b']}]\n",
        ["--set", f"deps.search_paths=[{d['libs_a']}, {d['libs_b']}]"],
    ),
    "deps.ld_library_path": lambda d: (
        ["--ld-library-path", "/opt/x/lib:/opt/y/lib"],
        "deps:\n  ld_library_path: /opt/x/lib:/opt/y/lib\n",
        ["--set", "deps.ld_library_path=/opt/x/lib:/opt/y/lib"],
    ),
    "debug.info/sided": lambda d: (
        ["--debug-info", f"old={d['dbg_old']}", "--debug-info", f"new={d['dbg_new']}"],
        f"debug:\n  info: ['old={d['dbg_old']}', 'new={d['dbg_new']}']\n",
        None,
    ),
    "debug.info/shared+override": lambda d: (
        ["--debug-info", str(d["dbg_both"]), "--debug-info", f"new={d['dbg_new']}"],
        f"debug:\n  info: ['{d['dbg_both']}', 'new={d['dbg_new']}']\n",
        None,
    ),
}

_DEFAULT = _view(False, (), "", (), ())


def _write_config(tmp_path: Path, body: str, name: str) -> Path:
    path = tmp_path / name / ".abicheck.yml"
    path.parent.mkdir(exist_ok=True)
    path.write_text("version: 1\n" + body, encoding="utf-8")
    return path


@pytest.mark.parametrize("members", [1, 3])
@pytest.mark.parametrize("case", sorted(_CASES))
def test_every_member_takes_the_scalar_run_inputs(
    case: str,
    members: int,
    tmp_path: Path,
    dirs: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    libs = [f"libm{i}" for i in range(members)]
    for lib in libs:
        _snapshot(tmp_path / "old" / f"{lib}.json", lib, "1")
        _snapshot(tmp_path / "new" / f"{lib}.json", lib, "2")
    flag, body, set_args = _CASES[case](dirs)
    empty = _write_config(tmp_path, "", "empty")
    cfg = _write_config(tmp_path, body, "cfg")
    spellings: dict[str, list[str]] = {
        "flag": ["--config", str(empty), *flag],
        "config": ["--config", str(cfg)],
    }
    if set_args is not None:
        spellings["set"] = ["--config", str(empty), *set_args]

    for spelling, extra in spellings.items():
        release = _release_views(
            monkeypatch,
            ["compare", str(tmp_path / "old"), str(tmp_path / "new"), *extra],
        )
        assert set(release) == {f"{lib}.json" for lib in libs}, spelling
        for lib in libs:
            scalar = _scalar_view(
                tmp_path,
                monkeypatch,
                [
                    "compare",
                    str(tmp_path / "old" / f"{lib}.json"),
                    str(tmp_path / "new" / f"{lib}.json"),
                    *extra,
                ],
            )
            assert release[f"{lib}.json"] == scalar, (spelling, lib)
        if not case.endswith("off-over-config"):
            # The case states a value, so it must differ from the default:
            # an equal pair of defaults would prove nothing.
            assert scalar != _DEFAULT, spelling


def test_no_follow_deps_overrides_a_configured_walk_on_a_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _snapshot(tmp_path / "old" / "libm0.json", "libm0", "1")
    _snapshot(tmp_path / "new" / "libm0.json", "libm0", "2")
    cfg = _write_config(tmp_path, "deps:\n  follow: true\n", "cfg")
    base = [
        "compare",
        str(tmp_path / "old"),
        str(tmp_path / "new"),
        "--config",
        str(cfg),
    ]
    assert _release_views(monkeypatch, base)["libm0.json"]["follow"] is True
    off = _release_views(monkeypatch, [*base, "--no-follow-deps"])
    assert off["libm0.json"]["follow"] is False


# --- real compiled ELF libraries ---------------------------------------------

_TOOLS = ("gcc", "objcopy", "strip", "readelf")


def _missing_tools() -> list[str]:
    if sys.platform != "linux":
        return ["linux"]
    return [t for t in _TOOLS if shutil.which(t) is None]


def _gcc(*args: str) -> None:
    proc = subprocess.run(["gcc", *args], capture_output=True, text=True)
    assert proc.returncode == 0, (args, proc.stderr)


def _split_debug(so: Path, root: Path) -> None:
    """Move *so*'s DWARF into a ``.build-id`` tree under *root*, strip *so*."""
    debug = so.with_suffix(".so.debug")
    subprocess.run(["objcopy", "--only-keep-debug", str(so), str(debug)], check=True)
    notes = subprocess.run(
        ["readelf", "-n", str(so)], capture_output=True, text=True, check=True
    ).stdout
    build_id = next(
        line.rsplit(":", 1)[-1].strip()
        for line in notes.splitlines()
        if "Build ID:" in line
    )
    subprocess.run(["strip", "--strip-debug", "--strip-unneeded", str(so)], check=True)
    target = root / ".build-id" / build_id[:2]
    target.mkdir(parents=True, exist_ok=True)
    shutil.move(str(debug), target / f"{build_id[2:]}.debug")


#: Per member: (old source, new source). Every member links ``libdep.so``,
#: which lives outside the release, so only ``--search-path`` resolves it.
_SOURCES = {
    "libpoint": (
        "int dep(void);\nstruct P { int x; int y; };\n"
        "int add(struct P p) { return p.x + p.y + dep(); }\n",
        "int dep(void);\nstruct P { int x; int y; int z; };\n"
        "int add(struct P p) { return p.x + p.y + p.z + dep(); }\n",
    ),
    "libplain": (
        "int dep(void);\nint plain(void) { return dep(); }\n",
        "int dep(void);\nint plain(void) { return dep(); }\n",
    ),
    "libgrow": (
        "int dep(void);\nint grow(void) { return dep(); }\n",
        "int dep(void);\nint grow(void) { return dep(); }\nint grown(void) { return 2; }\n",
    ),
}


def _build_release(tmp_path: Path, members: list[str]) -> dict[str, Path]:
    deps = tmp_path / "deps"
    deps.mkdir()
    (tmp_path / "dep.c").write_text("int dep(void) { return 1; }\n")
    _gcc("-shared", "-fPIC", "-o", str(deps / "libdep.so"), str(tmp_path / "dep.c"))
    out = {"deps": deps}
    for side, index in (("old", 0), ("new", 1)):
        side_dir = tmp_path / side
        side_dir.mkdir()
        dbg = tmp_path / f"dbg_{side}"
        dbg.mkdir()
        out[side], out[f"dbg_{side}"] = side_dir, dbg
        for name in members:
            src = tmp_path / f"{name}_{side}.c"
            src.write_text(_SOURCES[name][index])
            so = side_dir / f"{name}.so"
            _gcc(
                "-shared", "-fPIC", "-g", "-o", str(so), str(src), f"-L{deps}", "-ldep"
            )
            _split_debug(so, dbg)
    return out


def _json_report(args: list[str], out: Path) -> tuple[int, dict]:
    result = CliRunner().invoke(main, [*args, "-o", f"json={out}"])
    assert out.is_file(), (result.exit_code, result.output)
    return result.exit_code, json.loads(out.read_text(encoding="utf-8"))


def _member_facts(doc: dict) -> dict[str, object]:
    """What the scalar report and a release member entry both state."""
    return {
        "old_dependency_info": doc.get("old_dependency_info"),
        "new_dependency_info": doc.get("new_dependency_info"),
    }


#: The run-input spellings checked on real binaries: (name, argv builder).
_REAL_SETTINGS: dict[str, Callable[[dict[str, Path]], list[str]]] = {
    "follow+search_path": lambda p: ["--follow-deps", "--search-path", str(p["deps"])],
    "follow+ld_library_path": lambda p: [
        "--follow-deps",
        "--ld-library-path",
        str(p["deps"]),
    ],
    "follow, unresolved": lambda p: ["--follow-deps"],
    "debug roots": lambda p: [
        "--debug-info",
        f"old={p['dbg_old']}",
        "--debug-info",
        f"new={p['dbg_new']}",
    ],
}


@pytest.mark.integration
@pytest.mark.skipif(bool(_missing_tools()), reason=f"needs Linux + {_TOOLS}")
@pytest.mark.parametrize(
    "members", [["libpoint"], ["libpoint", "libplain", "libgrow"]], ids=["one", "three"]
)
@pytest.mark.parametrize("setting", sorted(_REAL_SETTINGS))
def test_release_report_equals_each_members_scalar_report(
    setting: str, members: list[str], tmp_path: Path
) -> None:
    paths = _build_release(tmp_path, members)
    extra = _REAL_SETTINGS[setting](paths)
    release_exit, release = _json_report(
        ["compare", str(paths["old"]), str(paths["new"]), *extra],
        tmp_path / "release.json",
    )
    entries = {e["library"]: e for e in release["libraries"]}
    assert set(entries) == {f"{m}.so" for m in members}
    worst = 0
    for name in members:
        so = f"{name}.so"
        scalar_exit, scalar = _json_report(
            ["compare", str(paths["old"] / so), str(paths["new"] / so), *extra],
            tmp_path / f"{name}.json",
        )
        worst = max(worst, scalar_exit)
        entry = entries[so]
        assert entry["verdict"] == scalar["verdict"], (setting, so)
        assert _member_facts(entry) == _member_facts(scalar), (setting, so)
        if "--follow-deps" in extra:
            assert scalar["old_dependency_info"]["nodes"], (setting, so)
    assert release_exit == worst, setting
    if "debug roots" == setting:
        # Without the roots both stripped sides are symbols-only and the
        # struct growth is invisible; with them libpoint breaks.
        assert entries["libpoint.so"]["verdict"] == "BREAKING"


def test_release_member_section_renders_the_scalar_section_body() -> None:
    """A member's Markdown section and the scalar report's section state the
    same graph; only the headings differ (the member's sits one level down)."""
    from abicheck.frontends.render import _render_deps_section_md
    from abicheck.model.extraction_contract import DependencyInfo
    from abicheck.report.dependency_section import (
        dependency_info_fields,
        release_member_dependency_section,
    )

    infos = [
        DependencyInfo(
            nodes=[
                {"soname": "libfoo.so", "depth": 0, "resolution_reason": "root"},
                {"soname": "libdep.so", "depth": 1, "resolution_reason": "runpath"},
            ],
            bindings_summary={"resolved": 3, "missing": 1},
            unresolved=[{"soname": "libgone.so", "consumer": "libfoo.so"}],
            missing_symbols=[{"symbol": f"s{i}", "version": ""} for i in range(12)],
        ),
        DependencyInfo(nodes=[{"soname": "libfoo.so", "resolution_reason": "root"}]),
    ]
    old = AbiSnapshot(library="libfoo.so", version="1")
    new = AbiSnapshot(library="libfoo.so", version="2")
    old.dependency_info, new.dependency_info = infos
    entry = json.loads(
        json.dumps({"library": "libfoo.so", **dependency_info_fields(old, new)})
    )

    def body(text: str) -> list[str]:
        return [line for line in text.splitlines() if not line.startswith("#")]

    member = release_member_dependency_section(entry)
    assert "### Dependency Analysis: libfoo.so" in member
    assert body(member) == body(_render_deps_section_md(old, new))
    assert "libgone.so" in member and "+2 more" in member
    assert release_member_dependency_section({"library": "libfoo.so"}) == ""
