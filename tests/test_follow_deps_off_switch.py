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

"""``--follow-deps/--no-follow-deps``: the run's dependency walk, flag over config.

``deps.follow`` (ADR-091 D4, 2026-10-31 amendment) backs a flag that used to be
on-only, so a configured ``deps.follow: true`` could not be turned off for one
run. The flag is now a boolean pair. Unset defers to ``deps.follow``, then to
the built-in off; either stated half replaces the configured value.

The oracle is :data:`_EXPECTED`, the precedence table written out by hand for
every (flag, config) cell. It is not the resolver. Each cell runs through the
real CLI, and the value checked is the effective request the front end hands to
the workflow (the pair-session request for ``compare``, the planner's
``DumpRequest`` for ``dump``).
"""

from __future__ import annotations

import itertools
import os
from pathlib import Path
from typing import Any

import pytest
from _action_run_sh_harness import _compare_env, _lib, _run_action, _stub_abicheck
from click.testing import CliRunner

from abicheck.cli import main
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.serialization import snapshot_to_json

#: The command-line spelling of each flag state.
_FLAG_ARGS: dict[str, list[str]] = {
    "unset": [],
    "on": ["--follow-deps"],
    "off": ["--no-follow-deps"],
}

#: The ``.abicheck.yml`` for each config state; ``None`` writes no file at all.
_CONFIG_BODY: dict[str, str | None] = {
    "absent": None,
    "true": "version: 1\ndeps:\n  follow: true\n",
    "false": "version: 1\ndeps:\n  follow: false\n",
}

#: Whether the walk runs, per (flag, config): a stated flag wins, an unset one
#: takes the config, and neither means off.
_EXPECTED: dict[tuple[str, str], bool] = {
    ("unset", "absent"): False,
    ("unset", "true"): True,
    ("unset", "false"): False,
    ("on", "absent"): True,
    ("on", "true"): True,
    ("on", "false"): True,
    ("off", "absent"): False,
    ("off", "true"): False,
    ("off", "false"): False,
}

_CELLS = sorted(itertools.product(_FLAG_ARGS, _CONFIG_BODY))


class _Captured(Exception):
    def __init__(self, value: Any) -> None:
        super().__init__("captured")
        self.value = value


@pytest.fixture(autouse=True)
def _in_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run from *tmp_path*, so the only config is the one a cell writes."""
    monkeypatch.chdir(tmp_path)


def _snapshot(path: Path, version: str) -> Path:
    fn = Function(
        name="f", mangled="f", return_type="int", visibility=Visibility.PUBLIC
    )
    snap = AbiSnapshot(
        library="libfoo.so", version=version, functions=[fn], from_headers=True
    )
    path.write_text(snapshot_to_json(snap), encoding="utf-8")
    return path


def _config_args(tmp_path: Path, config: str) -> list[str]:
    body = _CONFIG_BODY[config]
    if body is None:
        return []
    path = tmp_path / "cfg" / ".abicheck.yml"
    path.parent.mkdir(exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return ["--config", str(path)]


def _captured(args: list[str]) -> Any:
    result = CliRunner().invoke(main, args, catch_exceptions=True)
    if isinstance(result.exception, _Captured):
        return result.exception.value
    raise AssertionError(
        f"no request reached the workflow (exit {result.exit_code}):\n{result.output}"
    )


def test_the_table_covers_every_cell() -> None:
    assert set(_EXPECTED) == set(_CELLS)
    assert len(_CELLS) == 9


@pytest.mark.parametrize(("flag", "config"), _CELLS)
def test_compare_resolves_the_walk_flag_over_config(
    flag: str, config: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import abicheck.workflows.compare.session as session

    def fake(request: Any, host: Any) -> Any:
        raise _Captured(request)

    monkeypatch.setattr(session, "run_pair_session", fake)
    old = _snapshot(tmp_path / "old.json", "1")
    new = _snapshot(tmp_path / "new.json", "2")
    request = _captured(
        [
            "compare",
            str(old),
            str(new),
            *_config_args(tmp_path, config),
            *_FLAG_ARGS[flag],
        ]
    )
    assert request.evidence.follow_deps is _EXPECTED[(flag, config)]


@pytest.mark.parametrize(("flag", "config"), _CELLS)
def test_dump_resolves_the_walk_flag_over_config(
    flag: str, config: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from abicheck.workflows.plan import AnalysisPlanner

    def fake(request: Any, *args: Any, **kwargs: Any) -> Any:
        raise _Captured(request)

    monkeypatch.setattr(AnalysisPlanner, "resolve", staticmethod(fake))
    binary = tmp_path / "libfoo.so"
    binary.write_bytes(b"\x7fELF" + b"\0" * 60)
    request = _captured(
        [
            "dump",
            str(binary),
            "-o",
            str(tmp_path / "out.json"),
            *_config_args(tmp_path, config),
            *_FLAG_ARGS[flag],
        ]
    )
    assert request.follow_deps is _EXPECTED[(flag, config)]


@pytest.mark.parametrize(
    "body",
    [
        "deps:\n  follow: true\n",
        "deps:\n  follow: true\n  ld_library_path: /x\n",
    ],
)
def test_no_baseline_accepts_a_configured_walk_turned_off(
    tmp_path: Path, body: str
) -> None:
    """``--no-baseline`` refuses ``deps.*`` because it cannot run the walk.
    ``--no-follow-deps`` turns the walk off, so there is nothing to refuse;
    with the walk still on (unset or ``--follow-deps``) the refusal stands."""
    new = _snapshot(tmp_path / "new.json", "2")
    cfg = tmp_path / "cfg" / ".abicheck.yml"
    cfg.parent.mkdir()
    cfg.write_text("version: 1\n" + body, encoding="utf-8")
    base = ["compare", "--no-baseline", str(new), "--config", str(cfg)]
    runner = CliRunner()
    refused = runner.invoke(main, base)
    assert refused.exit_code == 64, refused.output
    assert "deps.follow" in refused.output
    accepted = runner.invoke(main, [*base, "--no-follow-deps"])
    assert accepted.exit_code != 64, accepted.output
    assert "deps." not in accepted.output


# --- Action: the `follow-deps` input ---------------------------------------


def _argv_capture_stub(tmp_path: Path, capture: Path) -> Path:
    bindir = _stub_abicheck(tmp_path, exit_code=0, payload=None)
    stub = bindir / "abicheck"
    stub.write_text(
        f'#!/usr/bin/env bash\nprintf "%s\\n" "$@" > "{capture}"\nexit 0\n',
        encoding="utf-8",
    )
    stub.chmod(0o755)
    return bindir


#: The flags the Action forwards for each `follow-deps` value; empty is the
#: input's default and leaves the decision to `.abicheck.yml`.
_ACTION_FORWARDS: dict[str | None, set[str]] = {
    None: set(),
    "": set(),
    "true": {"--follow-deps"},
    "false": {"--no-follow-deps"},
}


@pytest.mark.skipif(os.name == "nt", reason="POSIX shell harness")
@pytest.mark.parametrize("mode", ["compare", "dump"])
@pytest.mark.parametrize("value", list(_ACTION_FORWARDS))
def test_action_forwards_an_explicit_false_as_no_follow_deps(
    tmp_path: Path, mode: str, value: str | None
) -> None:
    capture = tmp_path / "argv.txt"
    bindir = _argv_capture_stub(tmp_path, capture)
    if mode == "compare":
        env = _compare_env(tmp_path)
    else:
        env = {"INPUT_MODE": "dump", "INPUT_NEW_LIBRARY": _lib(tmp_path, "libnew.so")}
    if value is not None:
        env["INPUT_FOLLOW_DEPS"] = value
    _run_action(tmp_path, env, bindir)
    argv = capture.read_text(encoding="utf-8").splitlines()
    assert argv[0] == mode
    walk_flags = {a for a in argv if a in ("--follow-deps", "--no-follow-deps")}
    assert walk_flags == _ACTION_FORWARDS[value]


@pytest.mark.skipif(os.name == "nt", reason="POSIX shell harness")
@pytest.mark.parametrize(
    "hostile",
    [
        "false; touch {sentinel}",
        "$(touch {sentinel})",
        "true`touch {sentinel}`",
        "--no-follow-deps --set=deps.follow=true",
    ],
)
def test_action_a_hostile_follow_deps_value_forwards_nothing_and_runs_nothing(
    tmp_path: Path, hostile: str
) -> None:
    """Only the exact strings ``true``/``false`` forward a flag; anything else
    is neither executed nor passed through as argv."""
    sentinel = tmp_path / "pwned"
    capture = tmp_path / "argv.txt"
    bindir = _argv_capture_stub(tmp_path, capture)
    env = {
        **_compare_env(tmp_path),
        "INPUT_FOLLOW_DEPS": hostile.format(sentinel=sentinel),
    }
    _run_action(tmp_path, env, bindir)
    argv = capture.read_text(encoding="utf-8").splitlines()
    assert argv[0] == "compare"
    assert not {a for a in argv if "follow-deps" in a or a.startswith("--set")}
    assert not sentinel.exists()
