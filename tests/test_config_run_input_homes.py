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

"""The config homes of ``--debug-info``/``--follow-deps``/``--search-path``/
``--ld-library-path`` (ADR-091 D4, 2026-10-31 amendment).

Each concept has three spellings on the CLI -- the flag, ``.abicheck.yml``
(``debug.info``, ``deps.follow``, ``deps.search_paths``,
``deps.ld_library_path``) and ``--set`` -- and one on the typed request
(``config_overrides``). The oracle is the *effective request* each spelling
produces at the boundary the front end hands to the workflow: the single-pair
session request for ``compare``, the planner's ``DumpRequest`` for ``dump``.
Every spelling of one value must produce the same effective request, and the
precedence is flag > ``--set`` > file > default.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from abicheck.cli import main
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.serialization import snapshot_to_json
from abicheck.workflows.request_inputs import InputSpec
from abicheck.workflows.service_requests import CompareRequest, DumpRequest


class _Captured(Exception):
    def __init__(self, value: Any) -> None:
        super().__init__("captured")
        self.value = value


def _snapshot(path: Path, version: str) -> Path:
    fn = Function(
        name="f", mangled="f", return_type="int", visibility=Visibility.PUBLIC
    )
    snap = AbiSnapshot(
        library="libfoo.so", version=version, functions=[fn], from_headers=True
    )
    path.write_text(snapshot_to_json(snap), encoding="utf-8")
    return path


@pytest.fixture
def capture_compare(monkeypatch: pytest.MonkeyPatch) -> None:
    import abicheck.workflows.compare.session as session

    def fake(request: Any, host: Any) -> Any:
        raise _Captured(request)

    monkeypatch.setattr(session, "run_pair_session", fake)


@pytest.fixture
def capture_dump(monkeypatch: pytest.MonkeyPatch) -> None:
    from abicheck.workflows.plan import AnalysisPlanner

    def fake(request: Any, *args: Any, **kwargs: Any) -> Any:
        raise _Captured(request)

    monkeypatch.setattr(AnalysisPlanner, "resolve", staticmethod(fake))


@pytest.fixture(autouse=True)
def _in_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run from *tmp_path*, so no enclosing ``.abicheck.yml`` is discovered."""
    monkeypatch.chdir(tmp_path)


def _run(args: list[str], cwd: Path) -> Any:
    """Invoke the real CLI; the captured effective request, or fail."""
    result = CliRunner().invoke(main, args, catch_exceptions=True)
    if isinstance(result.exception, _Captured):
        return result.exception.value
    raise AssertionError(
        f"no request reached the workflow (exit {result.exit_code}):\n{result.output}"
    )


def _refused(args: list[str], cwd: Path) -> str:
    result = CliRunner().invoke(main, args, catch_exceptions=True)
    assert result.exit_code == 64, (result.exit_code, result.output, result.exception)
    return result.output


def _config(tmp_path: Path, body: str, name: str = "cfg") -> Path:
    directory = tmp_path / name
    directory.mkdir(exist_ok=True)
    path = directory / ".abicheck.yml"
    path.write_text(body, encoding="utf-8")
    return path


@pytest.fixture
def dirs(tmp_path: Path) -> dict[str, Path]:
    out = {}
    for name in ("libs_a", "libs_b", "dbg_old", "dbg_new", "dbg_both"):
        path = tmp_path / name
        path.mkdir()
        out[name] = path
    return out


def _compare_view(request: Any) -> dict[str, Any]:
    """The effective values of the four concepts in a compare session request."""
    ev = request.evidence
    return {
        "follow": ev.follow_deps,
        "search_paths": tuple(Path(p) for p in ev.search_paths),
        "ld_library_path": ev.ld_library_path,
        "old_debug": tuple(ev.old_debug_roots),
        "new_debug": tuple(ev.new_debug_roots),
    }


def _dump_view(request: Any) -> dict[str, Any]:
    return {
        "follow": request.follow_deps,
        "search_paths": tuple(Path(p) for p in request.dependency_search_paths),
        "ld_library_path": request.ld_library_path,
        "debug": tuple(request.input.debug_roots),
    }


# One case per concept: (flag argv, config YAML, --set argv, expected view
# fields). Builders take the fixture directories so every spelling names the
# same absolute paths.
_Case = Callable[[dict[str, Path]], tuple[list[str], str, list[str], dict[str, Any]]]

_COMPARE_CASES: dict[str, _Case] = {
    "deps.follow": lambda d: (
        ["--follow-deps"],
        "deps:\n  follow: true\n",
        ["--set", "deps.follow=true"],
        {"follow": True},
    ),
    "deps.search_paths": lambda d: (
        ["--search-path", str(d["libs_a"]), "--search-path", str(d["libs_b"])],
        f"deps:\n  search_paths: [{d['libs_a']}, {d['libs_b']}]\n",
        ["--set", f"deps.search_paths=[{d['libs_a']}, {d['libs_b']}]"],
        {"search_paths": (d["libs_a"], d["libs_b"])},
    ),
    "deps.ld_library_path": lambda d: (
        ["--ld-library-path", "/opt/x/lib:/opt/y/lib"],
        "deps:\n  ld_library_path: /opt/x/lib:/opt/y/lib\n",
        ["--set", "deps.ld_library_path=/opt/x/lib:/opt/y/lib"],
        {"ld_library_path": "/opt/x/lib:/opt/y/lib"},
    ),
    "debug.info/sided": lambda d: (
        ["--debug-info", f"old={d['dbg_old']}", "--debug-info", f"new={d['dbg_new']}"],
        f"debug:\n  info: ['old={d['dbg_old']}', 'new={d['dbg_new']}']\n",
        ["--set", f"debug.info=['old={d['dbg_old']}', 'new={d['dbg_new']}']"],
        {"old_debug": (d["dbg_old"],), "new_debug": (d["dbg_new"],)},
    ),
    "debug.info/shared+override": lambda d: (
        ["--debug-info", str(d["dbg_both"]), "--debug-info", f"new={d['dbg_new']}"],
        f"debug:\n  info: ['{d['dbg_both']}', 'new={d['dbg_new']}']\n",
        ["--set", f"debug.info=['{d['dbg_both']}', 'new={d['dbg_new']}']"],
        {"old_debug": (d["dbg_both"],), "new_debug": (d["dbg_new"],)},
    ),
}

_DEFAULT_COMPARE = {
    "follow": False,
    "search_paths": (),
    "ld_library_path": "",
    "old_debug": (),
    "new_debug": (),
}


@pytest.mark.usefixtures("capture_compare")
@pytest.mark.parametrize("case", sorted(_COMPARE_CASES))
def test_compare_flag_config_and_set_resolve_to_one_request(
    case: str, tmp_path: Path, dirs: dict[str, Path]
) -> None:
    old = _snapshot(tmp_path / "old.json", "1")
    new = _snapshot(tmp_path / "new.json", "2")
    flag, yaml_body, set_args, expected = _COMPARE_CASES[case](dirs)
    empty = _config(tmp_path, "version: 1\n", "empty")
    cfg = _config(tmp_path, yaml_body)
    base = ["compare", str(old), str(new)]

    default = _compare_view(_run([*base, "--config", str(empty)], tmp_path))
    by_flag = _compare_view(_run([*base, "--config", str(empty), *flag], tmp_path))
    by_file = _compare_view(_run([*base, "--config", str(cfg)], tmp_path))
    by_set = _compare_view(_run([*base, "--config", str(empty), *set_args], tmp_path))

    assert default == _DEFAULT_COMPARE
    assert by_flag == {**_DEFAULT_COMPARE, **expected}
    assert by_file == by_flag
    assert by_set == by_flag


@pytest.mark.usefixtures("capture_compare")
def test_compare_precedence_is_flag_then_set_then_file(
    tmp_path: Path, dirs: dict[str, Path]
) -> None:
    old = _snapshot(tmp_path / "old.json", "1")
    new = _snapshot(tmp_path / "new.json", "2")
    cfg = _config(
        tmp_path,
        f"deps:\n  search_paths: [{dirs['libs_a']}]\n  ld_library_path: /file\n"
        f"debug:\n  info: ['{dirs['dbg_old']}']\n",
    )
    base = ["compare", str(old), str(new), "--config", str(cfg)]
    set_args = [
        "--set", f"deps.search_paths=[{dirs['libs_b']}]",
        "--set", "deps.ld_library_path=/set",
        "--set", f"debug.info=['{dirs['dbg_new']}']",
    ]  # fmt: skip

    file_only = _compare_view(_run(base, tmp_path))
    with_set = _compare_view(_run([*base, *set_args], tmp_path))
    with_flag = _compare_view(
        _run(
            [
                *base,
                *set_args,
                "--search-path", str(dirs["dbg_both"]),
                "--ld-library-path", "/flag",
                "--debug-info", str(dirs["dbg_both"]),
            ],
            tmp_path,
        )
    )  # fmt: skip

    assert file_only["search_paths"] == (dirs["libs_a"],)
    assert file_only["ld_library_path"] == "/file"
    assert file_only["old_debug"] == file_only["new_debug"] == (dirs["dbg_old"],)
    assert with_set["search_paths"] == (dirs["libs_b"],)
    assert with_set["ld_library_path"] == "/set"
    assert with_set["old_debug"] == with_set["new_debug"] == (dirs["dbg_new"],)
    # The flag replaces the configured value; it is never merged with it.
    assert with_flag["search_paths"] == (dirs["dbg_both"],)
    assert with_flag["ld_library_path"] == "/flag"
    assert with_flag["old_debug"] == with_flag["new_debug"] == (dirs["dbg_both"],)


@pytest.mark.usefixtures("capture_compare")
def test_set_turns_a_configured_follow_off(tmp_path: Path) -> None:
    old = _snapshot(tmp_path / "old.json", "1")
    new = _snapshot(tmp_path / "new.json", "2")
    cfg = _config(tmp_path, "deps:\n  follow: true\n")
    base = ["compare", str(old), str(new), "--config", str(cfg)]
    assert _compare_view(_run(base, tmp_path))["follow"] is True
    off = _run([*base, "--set", "deps.follow=false"], tmp_path)
    assert _compare_view(off)["follow"] is False


@pytest.mark.usefixtures("capture_compare")
@pytest.mark.parametrize(
    ("yaml_body", "key"),
    [
        ("deps:\n  search_paths: [libs]\n", "search_paths"),
        ("debug:\n  info: ['old=dbg', 'new=../other/dbg2']\n", "debug"),
    ],
)
def test_relative_config_paths_resolve_against_the_config_file(
    tmp_path: Path, yaml_body: str, key: str
) -> None:
    old = _snapshot(tmp_path / "old.json", "1")
    new = _snapshot(tmp_path / "new.json", "2")
    cfg = _config(tmp_path, yaml_body, "project")
    (cfg.parent / "libs").mkdir()
    view = _compare_view(
        _run(["compare", str(old), str(new), "--config", str(cfg)], tmp_path)
    )
    if key == "search_paths":
        assert view["search_paths"] == (cfg.parent / "libs",)
    else:
        assert view["old_debug"] == (cfg.parent / "dbg",)
        assert view["new_debug"] == (cfg.parent / "../other/dbg2",)


def test_compare_refuses_a_configured_search_path_that_does_not_exist(
    tmp_path: Path,
) -> None:
    old = _snapshot(tmp_path / "old.json", "1")
    new = _snapshot(tmp_path / "new.json", "2")
    cfg = _config(tmp_path, f"deps:\n  search_paths: [{tmp_path / 'nope'}]\n")
    out = _refused(["compare", str(old), str(new), "--config", str(cfg)], tmp_path)
    assert "deps.search_paths" in out


@pytest.mark.parametrize(
    "body",
    [
        "deps:\n  follow: true\n",
        "deps:\n  ld_library_path: /x\n",
        "debug:\n  info: [dbg]\n",
    ],
)
def test_no_baseline_refuses_the_config_keys_by_name(tmp_path: Path, body: str) -> None:
    new = _snapshot(tmp_path / "new.json", "2")
    cfg = _config(tmp_path, body)
    out = _refused(
        ["compare", "--no-baseline", str(new), "--config", str(cfg)], tmp_path
    )
    key = body.split(":")[0] + "." + body.split("\n")[1].strip().split(":")[0]
    assert key in out
    assert "--no-baseline" in out


def test_config_rejects_a_debug_info_entry_without_a_path(tmp_path: Path) -> None:
    old = _snapshot(tmp_path / "old.json", "1")
    new = _snapshot(tmp_path / "new.json", "2")
    cfg = _config(tmp_path, "debug:\n  info: ['old=']\n")
    out = _refused(["compare", str(old), str(new), "--config", str(cfg)], tmp_path)
    assert "debug.info" in out


# ── typed API parity ────────────────────────────────────────────────────────


@pytest.mark.parametrize("case", sorted(_COMPARE_CASES))
def test_typed_compare_overrides_match_the_cli_request(
    case: str,
    tmp_path: Path,
    dirs: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import abicheck.workflows.compare.execute as execute
    import abicheck.workflows.compare.session as session

    old = _snapshot(tmp_path / "old.json", "1")
    new = _snapshot(tmp_path / "new.json", "2")
    _flag, _yaml, set_args, _expected = _COMPARE_CASES[case](dirs)
    monkeypatch.setattr(
        session, "run_pair_session", lambda r, h: (_ for _ in ()).throw(_Captured(r))
    )
    empty = _config(tmp_path, "version: 1\n", "empty")
    cli = _compare_view(
        _run(
            ["compare", str(old), str(new), "--config", str(empty), *set_args], tmp_path
        )
    )

    import yaml

    path, _, raw = set_args[1].partition("=")
    request = CompareRequest(
        old=InputSpec(path=old),
        new=InputSpec(path=new),
        config_overrides={path: yaml.safe_load(raw)},
    )

    def capture(req: Any) -> Any:
        raise _Captured(req)

    monkeypatch.setattr(execute, "resolve_compare_request", capture)
    with pytest.raises(_Captured) as excinfo:
        execute.run_compare_request(request)
    typed = excinfo.value.value
    assert {
        "follow": typed.follow_deps,
        "search_paths": tuple(typed.dependency_search_paths),
        "ld_library_path": typed.ld_library_path,
        "old_debug": tuple(typed.old.debug_roots),
        "new_debug": tuple(typed.new.debug_roots),
    } == cli


def test_typed_argument_beats_the_override(
    tmp_path: Path, dirs: dict[str, Path]
) -> None:
    from abicheck.workflows.compare.execute import _with_config_overrides

    request = CompareRequest(
        old=InputSpec(path=tmp_path / "a.json", debug_roots=(dirs["dbg_both"],)),
        new=InputSpec(path=tmp_path / "b.json"),
        ld_library_path="/typed",
        config_overrides={
            "deps.ld_library_path": "/set",
            "debug.info": [str(dirs["dbg_old"])],
        },
    )
    out = _with_config_overrides(request)
    assert out.ld_library_path == "/typed"
    assert out.old.debug_roots == (dirs["dbg_both"],)
    assert out.new.debug_roots == (dirs["dbg_old"],)


# ── dump ────────────────────────────────────────────────────────────────────

_DUMP_CASES: dict[str, _Case] = {
    "deps.follow": _COMPARE_CASES["deps.follow"],
    "deps.search_paths": _COMPARE_CASES["deps.search_paths"],
    "deps.ld_library_path": _COMPARE_CASES["deps.ld_library_path"],
    "debug.info": lambda d: (
        ["--debug-info", str(d["dbg_both"])],
        f"debug:\n  info: ['{d['dbg_both']}']\n",
        ["--set", f"debug.info=['{d['dbg_both']}']"],
        {"debug": (d["dbg_both"],)},
    ),
}

_DEFAULT_DUMP = {
    "follow": False,
    "search_paths": (),
    "ld_library_path": "",
    "debug": (),
}


@pytest.mark.usefixtures("capture_dump")
@pytest.mark.parametrize("case", sorted(_DUMP_CASES))
def test_dump_flag_config_set_and_typed_resolve_to_one_request(
    case: str, tmp_path: Path, dirs: dict[str, Path]
) -> None:
    from abicheck.workflows.dump.request import run_dump_request

    binary = tmp_path / "libfoo.so"
    binary.write_bytes(b"\x7fELF" + b"\0" * 60)
    flag, yaml_body, set_args, expected = _DUMP_CASES[case](dirs)
    empty = _config(tmp_path, "version: 1\n", "empty")
    cfg = _config(tmp_path, yaml_body)
    base = ["dump", str(binary), "-o", str(tmp_path / "out.json")]

    default = _dump_view(_run([*base, "--config", str(empty)], tmp_path))
    by_flag = _dump_view(_run([*base, "--config", str(empty), *flag], tmp_path))
    by_file = _dump_view(_run([*base, "--config", str(cfg)], tmp_path))
    by_set = _dump_view(_run([*base, "--config", str(empty), *set_args], tmp_path))

    import yaml

    path, _, raw = set_args[1].partition("=")
    with pytest.raises(_Captured) as excinfo:
        run_dump_request(
            DumpRequest(
                input=InputSpec(path=binary),
                config_overrides={path: yaml.safe_load(raw)},
            )
        )
    typed = _dump_view(excinfo.value.value)

    assert default == _DEFAULT_DUMP
    assert by_flag == {**_DEFAULT_DUMP, **expected}
    assert by_file == by_flag
    assert by_set == by_flag
    assert typed == by_flag


def test_dump_refuses_a_side_scoped_debug_info_entry(tmp_path: Path) -> None:
    binary = tmp_path / "libfoo.so"
    binary.write_bytes(b"\x7fELF" + b"\0" * 60)
    cfg = _config(tmp_path, "debug:\n  info: ['old=dbg']\n")
    out = _refused(["dump", str(binary), "--config", str(cfg)], tmp_path)
    assert "debug.info" in out
    assert "old=" in out
