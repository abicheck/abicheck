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

"""Every report-output spelling the CLI accepts is recognised by the Action.

``action/run.sh`` reads the CLI's one report-output flag (``-o FORMAT=DEST``)
in two places. ``_extra_args_exports`` reads the caller's ``extra-args``
(tokenized by ``_extra_args_options``), and ``_build_json_cmd`` reads the
assembled ``CMD`` array when it rebuilds the run for the PR comment. Both now
classify each token with one matcher, ``_export_flag_spelling``.

Before that, ``_build_json_cmd`` recognised only the separate-token
``-o``/``--output`` forms. ``--output=json=X`` and ``-ojson=X`` therefore
survived into the rerun next to its own ``-o json=$PR_JSON``, which wrote the
caller's file a second time.

Nothing here is a hand-maintained spelling list. The derivation is:

* the option names come from Click's own ``-o/--output`` definition
  (``param.opts``) on every command that carries the export option;
* the formats come from that option's ``export_formats``;
* the forms (bare, ``=``-attached, short-attached, boolean cluster) are
  generated from those names, and each one is then checked against Click's
  parser -- a form only counts when the CLI really accepts it as an export
  with that operand.

So a spelling Click gains (an alias, a new cluster shape) appears in the
generated matrix and fails the test until the matcher handles it.

The matrix runs the real shell functions in one bash process per ``MODE``,
with the live CLI-derived value-option table (``_cli_value_options_init``),
the same way production runs them. Value-option cases also check the reverse
direction: a non-export option's own value is never misread as an export.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import click
import pytest
from _workflow_exec import bash_executable, require_bash

from abicheck.cli import main as abicheck_main

RUN_SH = Path(__file__).resolve().parents[1] / "action" / "run.sh"
_HELPERS_MARKER = "# Build the abicheck command"
_BUILD_JSON_CMD_START = "_build_json_cmd() {"
_BUILD_JSON_CMD_END = "\n}\n"

#: The rerun's destination. `_build_json_cmd` appends `-o json=$PR_JSON`.
_PR_JSON = "pr-sentinel.json"
#: What the compare rerun pins after the kept tokens: an empty `output.view`,
#: so a config file's `show=` display filter cannot hide gated changes from
#: the PR comment (ADR-091 Phase 5). The deps commands take no `--set`.
_VIEW_PIN = {"compare": ("--set", "output.view=[]")}
#: The primary run's own argv before its export flags.
_PRIMARY = ("abicheck", "compare", "old.json", "new.json")
#: A value-taking option's probe value: spelled like an export, so it would be
#: stripped if a value were ever misread as one.
_VALUE_PROBE = ("-o", "json=probe.out")

#: run.sh's MODE -> the CLI command whose export option its rerun builds.
MODE_TO_COMMAND: dict[str, tuple[str, ...]] = {
    "compare": ("compare",),
    "deps-tree": ("deps", "tree"),
    "deps-compare": ("deps", "compare"),
}

_PRELUDE = """
MODE={mode}
_PY_BIN={py_bin}
_PY_SAFE_DIR="$(mktemp -d)"
_PY_BIN_HAS_ABICHECK=true
PR_JSON={pr_json}
trap 'rm -rf "$_PY_SAFE_DIR"' EXIT
"""

# Bash helpers that report one case each: the rerun argv after
# `_build_json_cmd` (STRIP) and the extra-args exports (EXPORT). Written with
# raw strings so the escapes reach bash unchanged.
_DRIVER_FUNCTIONS = r"""
_emit_strip() {
  local _cid="$1" _joined
  _joined="$(IFS=$'\037'; printf '%s' "${PR_CMD_JSON[*]}")"
  printf 'STRIP\t%s\t%s\n' "$_cid" "$_joined"
}

_dump_exports() {
  local _cid="$1" _f _d
  while IFS=$'\t' read -r _f _d; do
    [[ -n "$_f$_d" ]] || continue
    printf 'EXPORT\t%s\t%s\t%s\n' "$_cid" "$_f" "$_d"
  done <<<"$(_extra_args_exports)"
}
"""


@dataclass(frozen=True)
class _Case:
    """One probe of the Action's matcher: the tokens the user (or a PR run's
    CMD) carries, and what the rerun and extra-args must each conclude."""

    cid: str
    mode: str
    kind: str  # "spelling" or "value-option"
    label: str
    tokens: tuple[str, ...]
    expected_strip: tuple[str, ...]
    expected_exports: tuple[tuple[str, str], ...]
    fmt: str | None = None
    dest: str | None = None


def _resolve_command(path: tuple[str, ...]) -> click.Command:
    node: click.Command = abicheck_main
    for segment in path:
        assert isinstance(node, click.Group), f"{path} is not a group path"
        resolved = node.commands.get(segment)
        assert resolved is not None, f"no such command: {' '.join(path)}"
        node = resolved
    return node


def _export_param(cmd: click.Command) -> click.Option:
    """The one option carrying `export_formats` -- the CLI's `-o/--output`."""
    found = [
        p
        for p in cmd.params
        if isinstance(p, click.Option)
        and getattr(p, "export_formats", None) is not None
    ]
    assert len(found) == 1, f"expected one export option, found {len(found)}"
    return found[0]


def _value_options(cmd: click.Command) -> set[str]:
    return {
        opt
        for p in cmd.params
        if isinstance(p, click.Option) and not p.is_flag and p.nargs != 0
        for opt in p.opts
    }


def _boolean_short_letters(cmd: click.Command) -> list[str]:
    """Single-letter boolean flags, e.g. `v` for `-v/--verbose`."""
    return sorted(
        {
            opt[1]
            for p in cmd.params
            if isinstance(p, click.Option) and p.is_flag
            for opt in p.opts
            if len(opt) == 2 and opt.startswith("-") and not opt.startswith("--")
        }
    )


def _spelling_forms(
    names: list[str], booleans: list[str], operand: str
) -> list[tuple[str, tuple[str, ...]]]:
    """Every form Click's grammar gives an export, from its option names.

    Bare forms carry the operand in the next token; attached forms carry it
    in the same token. Short names also appear after each boolean-flag
    cluster (`-vo`, `-vvo`), which Click reads as `-v -o`.
    """
    forms: list[tuple[str, tuple[str, ...]]] = []
    for name in names:
        if name.startswith("--"):
            forms.append((f"{name} VALUE", (name, operand)))
            forms.append((f"{name}=VALUE", (f"{name}={operand}",)))
            continue
        letter = name[1:]
        forms.append((f"{name} VALUE", (name, operand)))
        forms.append((f"{name}VALUE", (f"{name}{operand}",)))
        for flag in booleans:
            for count in (1, 2):
                cluster = "-" + flag * count + letter
                forms.append((f"{cluster} VALUE", (cluster, operand)))
                forms.append((f"{cluster}VALUE", (f"{cluster}{operand}",)))
    return forms


def _click_export_operands(mode: str, tokens: tuple[str, ...]) -> tuple[str, ...]:
    """What Click's own parser makes of *tokens* for the mode's command: the
    export operands it collected, or a `click.UsageError` if it rejects them.
    """
    cmd = _resolve_command(MODE_TO_COMMAND[mode])
    parser = cmd.make_parser(click.Context(cmd))
    opts, _largs, _order = parser.parse_args(args=list(tokens))
    return tuple(opts.get(_export_param(cmd).name, ()))


@cache
def _cases(mode: str) -> tuple[_Case, ...]:
    cmd = _resolve_command(MODE_TO_COMMAND[mode])
    param = _export_param(cmd)
    names = list(param.opts) + list(param.secondary_opts)
    booleans = _boolean_short_letters(cmd)
    cases: list[_Case] = []
    for fmt in param.export_formats:
        dest = f"report-{fmt}.out"
        operand = f"{fmt}={dest}"
        for label, tokens in _spelling_forms(names, booleans, operand):
            cases.append(
                _Case(
                    cid=f"{mode}|{label}|{fmt}",
                    mode=mode,
                    kind="spelling",
                    label=label,
                    tokens=tokens,
                    expected_strip=(
                        *_PRIMARY,
                        *_VIEW_PIN.get(mode, ()),
                        "-o",
                        f"json={_PR_JSON}",
                    ),
                    expected_exports=((fmt, dest),),
                    fmt=fmt,
                    dest=dest,
                )
            )
    # Every value-taking option that is not an export: its own value (here
    # `-o`) must be kept, never read as the export it looks like.
    export_names = set(names)
    for opt in sorted(_value_options(cmd) - export_names):
        spellings = [(opt, (opt,))]
        if opt.startswith("-") and not opt.startswith("--"):
            spellings += [
                (f"-{flag}{opt[1:]}", (f"-{flag}{opt[1:]}",)) for flag in booleans
            ]
        for label, head in spellings:
            tokens = (*head, *_VALUE_PROBE)
            cases.append(
                _Case(
                    cid=f"{mode}|value {label}",
                    mode=mode,
                    kind="value-option",
                    label=label,
                    tokens=tokens,
                    expected_strip=(
                        *_PRIMARY,
                        *tokens,
                        *_VIEW_PIN.get(mode, ()),
                        "-o",
                        f"json={_PR_JSON}",
                    ),
                    expected_exports=(),
                )
            )
    return tuple(cases)


def _prelude(mode: str) -> str:
    return _PRELUDE.format(
        mode=mode, py_bin=shlex.quote(sys.executable), pr_json=_PR_JSON
    )


def _case_lines(case: _Case) -> str:
    argv = " ".join(shlex.quote(tok) for tok in case.tokens)
    extra = shlex.quote(" ".join(case.tokens))
    return "\n".join(
        [
            f"CMD=({' '.join(_PRIMARY)} {argv})",
            "_build_json_cmd",
            f"_emit_strip {shlex.quote(case.cid)}",
            f"INPUT_EXTRA_ARGS={extra}",
            f"_dump_exports {shlex.quote(case.cid)}",
        ]
    )


def _parse_matrix_output(
    stdout: str,
) -> tuple[dict[str, tuple[str, ...]], dict[str, list[tuple[str, str]]]]:
    strip: dict[str, tuple[str, ...]] = {}
    exports: dict[str, list[tuple[str, str]]] = {}
    for line in stdout.splitlines():
        kind, _, rest = line.partition("\t")
        if kind == "STRIP":
            cid, _, joined = rest.partition("\t")
            strip[cid] = tuple(joined.split("\x1f")) if joined else ()
        elif kind == "EXPORT":
            cid, fmt, dest = rest.split("\t", 2)
            exports.setdefault(cid, []).append((fmt, dest))
    return strip, exports


@cache
def _matrix_results(
    mode: str,
) -> tuple[dict[str, tuple[str, ...]], dict[str, list[tuple[str, str]]]]:
    """Run every case for *mode* through the real `_build_json_cmd` and
    `_extra_args_exports` in one bash process, and return what each reports.
    """
    require_bash()
    cases = _cases(mode)
    text = RUN_SH.read_text(encoding="utf-8")
    helpers = text[: text.index(_HELPERS_MARKER)]
    start = text.index(_BUILD_JSON_CMD_START)
    build = text[
        start : text.index(_BUILD_JSON_CMD_END, start) + len(_BUILD_JSON_CMD_END)
    ]
    # run.sh derives the CLI value-option table eagerly at top level, so every
    # `$(...)` subshell inherits it. Do the same here; otherwise each subshell
    # re-derives it with its own Python call (correct, but ~1s per case).
    body = "\n".join(
        ["_cli_value_options_init"] + [_case_lines(case) for case in cases]
    )
    script = "\n".join([_prelude(mode), helpers, build, _DRIVER_FUNCTIONS, body, ""])
    with tempfile.NamedTemporaryFile(
        "w", suffix=".sh", delete=False, encoding="utf-8", newline="\n"
    ) as handle:
        handle.write(script)
        path = handle.name
    try:
        proc = subprocess.run(
            [bash_executable(), path],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    finally:
        os.unlink(path)
    assert proc.returncode == 0, (
        f"matrix harness failed for mode {mode} (exit {proc.returncode})\n"
        f"--- stderr ---\n{proc.stderr}"
    )
    strip, exports = _parse_matrix_output(proc.stdout)
    missing = [case.cid for case in cases if case.cid not in strip]
    assert not missing, f"harness reported no STRIP line for {missing[:5]}"
    return strip, exports


def _spelling_params(kind: str) -> list[object]:
    return [
        pytest.param(case, id=case.cid)
        for mode in sorted(MODE_TO_COMMAND)
        for case in _cases(mode)
        if case.kind == kind
    ]


pytestmark = pytest.mark.skipif(not RUN_SH.is_file(), reason="action/run.sh not found")


class TestCliDerivation:
    """The matrix is built from the CLI, so the CLI side must be non-empty
    and the spellings must be ones Click really parses."""

    def test_export_option_is_found_with_its_formats(self) -> None:
        for mode, path in MODE_TO_COMMAND.items():
            param = _export_param(_resolve_command(path))
            assert {"-o", "--output"} <= set(param.opts), mode
            assert param.export_formats, mode

    @pytest.mark.parametrize("case", _spelling_params("spelling"))
    def test_click_accepts_the_spelling_as_this_export(self, case: _Case) -> None:
        operands = _click_export_operands(case.mode, case.tokens)
        expected = f"{case.fmt}={case.dest}"
        assert operands == (expected,), (
            f"Click reads {case.tokens!r} as {operands!r}, not as the export "
            f"{expected!r} the matrix assumes"
        )

    @pytest.mark.parametrize("spelling", ["--output-file", "--out", "--outp"])
    def test_click_rejects_the_near_misses_so_they_are_not_spellings(
        self, spelling: str
    ) -> None:
        """`--output-file` was once listed as a survivor of the rerun. Click
        rejects it, so the primary run fails before any rerun, and it is not
        a spelling for the matcher to recognise."""
        with pytest.raises(click.UsageError):
            _click_export_operands("compare", (spelling, "json=x.out"))


class TestBuildJsonCmdStripsEverySpelling:
    @pytest.mark.parametrize("case", _spelling_params("spelling"))
    def test_rerun_drops_the_export_and_keeps_one_sidecar(self, case: _Case) -> None:
        strip, _ = _matrix_results(case.mode)
        assert strip[case.cid] == case.expected_strip


class TestExtraArgsExportsParseEverySpelling:
    @pytest.mark.parametrize("case", _spelling_params("spelling"))
    def test_extra_args_reads_the_export_back(self, case: _Case) -> None:
        _, exports = _matrix_results(case.mode)
        assert tuple(exports.get(case.cid, [])) == case.expected_exports


def _value_option_params() -> list[object]:
    return [
        pytest.param(case, id=case.cid)
        for mode in sorted(MODE_TO_COMMAND)
        for case in _cases(mode)
        if case.kind == "value-option"
    ]


class TestNonExportValueIsNeverAnExport:
    """The reverse direction of parity: a value-taking option that is not an
    export keeps its own value even when that value is spelled like one."""

    @pytest.mark.parametrize("case", _value_option_params())
    def test_rerun_keeps_the_value_verbatim(self, case: _Case) -> None:
        strip, _ = _matrix_results(case.mode)
        assert strip[case.cid] == case.expected_strip

    @pytest.mark.parametrize("case", _value_option_params())
    def test_extra_args_reports_no_export(self, case: _Case) -> None:
        _, exports = _matrix_results(case.mode)
        assert tuple(exports.get(case.cid, [])) == case.expected_exports
