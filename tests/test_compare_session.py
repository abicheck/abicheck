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

"""The single-pair ``compare`` session (``workflows/compare/session.py``).

The native ``compare`` CLI states one :class:`PairSessionRequest` and renders
the :class:`PairSessionOutcome`; these tests drive that interface directly,
with real snapshot operands wherever the answer depends on them, and pin the
CLI to the same answer through the public command.

Two seams are still faked where the property is about *what reaches them*:
``workflows.input_resolution.resolve_input`` (each side's extraction) and
``workflows.compare.resolve.resolve_compare_request`` (the shared resolution
the typed API and MCP tool run, ADR-055 D1).
"""

from __future__ import annotations

import dataclasses
import json
import time
from pathlib import Path

import click
import pytest
from click.testing import CliRunner

from abicheck.cli import main
from abicheck.cli_compare_options import stated_options
from abicheck.errors import SnapshotError, ValidationError
from abicheck.frontends.cli.run_input_errors import as_click_error
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.serialization import snapshot_to_json
from abicheck.workflows.compare.session import PairNotComparable
from abicheck.workflows.run_input_errors import InputRefusal, RunInputError
from tests._pair_session import pair_session_request, run_session


def _fn(name: str, ret: str = "int") -> Function:
    return Function(
        name=name, mangled=name, return_type=ret, visibility=Visibility.PUBLIC
    )


def _snap(version: str, *funcs: Function, library: str = "libfoo.so") -> AbiSnapshot:
    return AbiSnapshot(
        library=library,
        version=version,
        functions=list(funcs) or [_fn("foo")],
        from_headers=True,
    )


def _write(path: Path, snap: AbiSnapshot) -> Path:
    path.write_text(snapshot_to_json(snap), encoding="utf-8")
    return path


def _operands(tmp_path: Path) -> tuple[Path, Path]:
    return (
        _write(tmp_path / "old.json", _snap("1.0")),
        _write(tmp_path / "new.json", _snap("2.0")),
    )


def _fake_extraction(monkeypatch: pytest.MonkeyPatch, calls: list[dict]) -> None:
    import abicheck.workflows.input_resolution as _input_resolution

    def fake_resolve_input(path, headers, includes, version, lang, **kwargs):
        start = time.monotonic()
        time.sleep(0.02)
        calls.append(
            {
                "path": path,
                "version": version,
                "start": start,
                "end": time.monotonic(),
                **kwargs,
            }
        )
        return _snap(version)

    monkeypatch.setattr(_input_resolution, "resolve_input", fake_resolve_input)


# ── What each side's resolution is handed ──────────────────────────────────


class TestSideResolutionInputs:
    def test_each_sides_header_is_its_public_header(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``--header old=/new=`` is the public-header set for provenance,
        not only AST input (a dropped one left every native compare in
        reduced-confidence "no-provenance" mode)."""
        calls: list[dict] = []
        _fake_extraction(monkeypatch, calls)
        old, new = _operands(tmp_path)
        old_h, new_h = [tmp_path / "old.h"], [tmp_path / "new.h"]
        run_session(
            pair_session_request(
                old,
                new,
                headers={"old_headers": tuple(old_h), "new_headers": tuple(new_h)},
            )
        )
        assert [c["public_headers"] for c in calls] == [old_h, new_h]

    @pytest.mark.parametrize("include_dependencies", [False, True])
    def test_dependency_scope_reaches_both_sides(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        include_dependencies: bool,
    ) -> None:
        """Filtered by default on both sides, exactly as ``dump`` filters, so
        ``dump old.so -o base.json`` then ``compare base.json new.so`` agree."""
        calls: list[dict] = []
        _fake_extraction(monkeypatch, calls)
        old, new = _operands(tmp_path)
        run_session(
            pair_session_request(
                old, new, evidence={"include_dependencies": include_dependencies}
            )
        )
        assert [c["include_dependencies"] for c in calls] == [include_dependencies] * 2

    def test_sides_resolve_one_after_the_other(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ADR-050 D6/G32 Phase E: two manifest-driven dumps starting together
        size two full per-TU pools off one ``MemAvailable`` reading. The CLI
        session resolves sequentially, unconditionally."""
        calls: list[dict] = []
        _fake_extraction(monkeypatch, calls)
        old, new = _operands(tmp_path)
        run_session(pair_session_request(old, new))
        assert [c["version"] for c in calls] == ["old", "new"]
        assert calls[1]["start"] >= calls[0]["end"]

    @pytest.mark.parametrize(
        ("old_backend", "new_backend"),
        [(None, None), ("clang", None), (None, "castxml"), ("clang", "castxml")],
    )
    def test_a_side_backend_overrides_only_its_side(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        old_backend: str | None,
        new_backend: str | None,
    ) -> None:
        """A per-side AST frontend rides on that side's own compile context;
        the other side keeps the both-sides backend."""
        seen = _spy_resolution(monkeypatch)
        old, new = _operands(tmp_path)
        run_session(
            pair_session_request(
                old,
                new,
                compile={
                    "header_backend": "castxml",
                    "old_header_backend": old_backend,
                    "new_header_backend": new_backend,
                },
            )
        )
        request = seen[0][0]
        # The shared context's frontend is neutralised to "auto" so the
        # both-sides backend travels as the request's own frontend.
        assert request.frontend == "castxml"
        assert request.old.compile.frontend == (old_backend or "auto")
        assert request.new.compile.frontend == (new_backend or "auto")


def _spy_resolution(monkeypatch: pytest.MonkeyPatch) -> list[tuple]:
    import abicheck.workflows.compare.resolve as _resolve

    real = _resolve.resolve_compare_request
    seen: list[tuple] = []

    def _spy(request, **kwargs):
        seen.append((request, kwargs))
        return real(request, **kwargs)

    monkeypatch.setattr(_resolve, "resolve_compare_request", _spy)
    return seen


class TestSharedResolution:
    """ADR-055 D1: the native ``compare`` runs the resolution the typed API
    and the MCP tool run; every operand-shaped input becomes request surface."""

    def test_every_input_becomes_request_surface(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen = _spy_resolution(monkeypatch)
        old, new = _operands(tmp_path)
        labels = {tmp_path / "inc": "support"}
        session = run_session(
            pair_session_request(
                old,
                new,
                old_version="1.0",
                new_version="2.0",
                headers={
                    "old_headers": (tmp_path / "old.h",),
                    "include_labels": labels,
                    "exclude_headers": ("private/*",),
                },
                debug={"dwarf_only": True, "format": "dwarf"},
                evidence={
                    "follow_deps": True,
                    "search_paths": (tmp_path / "libs",),
                    "ld_library_path": "/opt/lib",
                },
            )
        )
        assert len(seen) == 1
        request, kwargs = seen[0]
        # The CLI's own concerns: its notifier, and sequential resolution.
        assert kwargs["allow_parallel"] is False
        assert kwargs["notify"] == session.notes.append
        assert request.old.path == old and request.new.version == "2.0"
        assert request.old.headers == (tmp_path / "old.h",)
        assert request.old.exclude_headers == ("private/*",)
        assert request.include_labels == tuple(labels.items())
        assert request.dwarf_only is True and request.debug_format == "dwarf"
        assert request.follow_deps is True
        assert request.dependency_search_paths == (tmp_path / "libs",)
        assert request.ld_library_path == "/opt/lib"

    def test_debug_format_auto_returns_to_detection(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen = _spy_resolution(monkeypatch)
        old, new = _operands(tmp_path)
        run_session(pair_session_request(old, new, debug={"format": "AUTO"}))
        assert seen[0][0].debug_format is None

    @pytest.mark.parametrize(
        ("raised", "refusal"),
        [
            (ValidationError("bad input"), InputRefusal.USAGE),
            (SnapshotError("cannot load"), InputRefusal.OPERATIONAL),
        ],
    )
    def test_resolution_refusals_keep_their_exit_class(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        raised: Exception,
        refusal: InputRefusal,
    ) -> None:
        """``ValidationError`` (unusable input) stays exit 64,
        ``SnapshotError`` (operational) stays exit 1, message unchanged."""
        import abicheck.workflows.compare.resolve as _resolve

        def _raise(request, **kwargs):
            raise raised

        monkeypatch.setattr(_resolve, "resolve_compare_request", _raise)
        old, new = _operands(tmp_path)
        with pytest.raises(RunInputError) as info:
            run_session(pair_session_request(old, new))
        assert info.value.refusal is refusal
        assert str(info.value) == str(raised)


# ── The CLI's one translation of a refusal ─────────────────────────────────


@pytest.mark.parametrize(
    ("error", "expected_type", "exit_code"),
    [
        (RunInputError.usage("u"), click.UsageError, 2),
        (RunInputError.parameter("p", "--policy"), click.BadParameter, 2),
        (RunInputError.parameter("p"), click.BadParameter, 2),
        (RunInputError.operational("o"), click.ClickException, 1),
    ],
)
def test_refusals_translate_to_the_click_error_they_name(
    error: RunInputError, expected_type: type, exit_code: int
) -> None:
    translated = as_click_error(error)
    assert type(translated) is expected_type
    assert translated.message == str(error)
    assert translated.exit_code == exit_code
    if isinstance(translated, click.BadParameter):
        assert translated.param_hint == error.param_hint


def test_stated_options_answer_what_the_parameter_source_answered() -> None:
    """``stated_options(ctx)`` is the Click context's own ``COMMANDLINE``
    answer as a set -- including ``False`` for an undeclared name."""
    command = click.Command(
        "c",
        params=[
            click.Option(["--policy"], default="strict_abi"),
            click.Option(["--nostdinc"], is_flag=True),
            click.Option(["--depth"], default=None),
        ],
    )
    for argv, expected in [
        ([], set()),
        (["--policy", "sdk_vendor"], {"policy"}),
        (["--policy", "strict_abi", "--nostdinc"], {"policy", "nostdinc"}),
        (["--depth", "binary"], {"depth"}),
    ]:
        ctx = command.make_context("c", list(argv))
        stated = stated_options(ctx)
        assert stated == expected
        for name in ("policy", "nostdinc", "depth", "undeclared"):
            assert (name in stated) == (
                ctx.get_parameter_source(name) == click.core.ParameterSource.COMMANDLINE
            )


# ── Outcomes over real operands, and the CLI's agreement with them ─────────


_PAIRS: dict[str, tuple[AbiSnapshot, AbiSnapshot]] = {
    "unchanged": (_snap("1.0", _fn("foo")), _snap("2.0", _fn("foo"))),
    "addition": (_snap("1.0", _fn("foo")), _snap("2.0", _fn("foo"), _fn("bar"))),
    "removal": (_snap("1.0", _fn("foo"), _fn("bar")), _snap("2.0", _fn("foo"))),
    "return_type": (_snap("1.0", _fn("foo")), _snap("2.0", _fn("foo", "long"))),
}


@pytest.mark.parametrize("case", sorted(_PAIRS))
def test_cli_reports_the_sessions_outcome(tmp_path: Path, case: str) -> None:
    """The CLI renders the session's outcome; it decides nothing of its own
    about the pair. Oracle: the set of change kinds and the verdict the
    session classified must be what ``compare -o json=-`` reports."""
    old_snap, new_snap = _PAIRS[case]
    old = _write(tmp_path / "old.json", old_snap)
    new = _write(tmp_path / "new.json", new_snap)
    session = run_session(pair_session_request(old, new))
    cli = CliRunner().invoke(main, ["compare", str(old), str(new), "-o", "json=-"])
    assert cli.exit_code in (0, 1, 2, 4), cli.output
    report = json.loads(cli.stdout)
    assert report["verdict"] == session.outcome.result.verdict.value
    assert sorted(c["kind"] for c in report["changes"]) == sorted(
        c.kind.value for c in session.outcome.result.changes
    )


def test_a_removal_is_breaking_and_an_addition_is_not(tmp_path: Path) -> None:
    verdicts = {}
    for case in ("addition", "removal"):
        old_snap, new_snap = _PAIRS[case]
        old = _write(tmp_path / f"{case}-old.json", old_snap)
        new = _write(tmp_path / f"{case}-new.json", new_snap)
        verdicts[case] = run_session(pair_session_request(old, new)).outcome
    assert verdicts["removal"].result.verdict.value == "BREAKING"
    assert verdicts["addition"].result.verdict.value != "BREAKING"
    # The outcome names the operands it was resolved from.
    assert verdicts["removal"].old_input == tmp_path / "removal-old.json"


def test_snapshot_pair_names_the_extraction_inputs_it_ignores(tmp_path: Path) -> None:
    old, new = _operands(tmp_path)
    session = run_session(
        pair_session_request(
            old,
            new,
            headers={"headers": (tmp_path / "a.h",), "old_includes": (tmp_path,)},
        )
    )
    assert session.notes == [
        "Warning: -H/--header, --include old= ignored when both inputs are snapshots."
    ]


def test_a_malformed_policy_file_is_a_bad_policy_value(tmp_path: Path) -> None:
    old, new = _operands(tmp_path)
    bad = tmp_path / "policy.yaml"
    bad.write_text("overrides: [not, a, mapping]\n", encoding="utf-8")
    with pytest.raises(RunInputError) as info:
        run_session(pair_session_request(old, new, policy={"policy_file_path": bad}))
    assert info.value.refusal is InputRefusal.PARAMETER
    assert info.value.param_hint == "--policy"


@pytest.mark.parametrize(("old_scope", "new_scope"), [("core", "core2"), ("a", "b")])
def test_incomparable_extraction_contracts_are_not_a_verdict(
    tmp_path: Path, old_scope: str, new_scope: str
) -> None:
    """ADR-050 D2: two snapshots dumped under different manifest TU scopes
    never classify; the session hands the pair back for the not-comparable
    report (exit 16), and the CLI renders exactly that."""
    from abicheck.comparability import compute_extraction_contract

    header = tmp_path / "v1" / "a.h"
    header.parent.mkdir(parents=True)
    header.write_text("int g(void);\n", encoding="utf-8")
    sides = []
    for version, scope in (("1.0", old_scope), ("2.0", new_scope)):
        snap = AbiSnapshot(
            library="libtest.so",
            version=version,
            contract=compute_extraction_contract(
                declared_headers=[header],
                manifest_tu_scope=json.dumps([{"name": scope}]),
            ),
        )
        sides.append(_write(tmp_path / f"{version}.json", snap))
    with pytest.raises(PairNotComparable) as info:
        run_session(pair_session_request(*sides))
    assert (info.value.old.version, info.value.new.version) == ("1.0", "2.0")
    cli = CliRunner().invoke(main, ["compare", str(sides[0]), str(sides[1])])
    assert cli.exit_code == 16
    assert str(info.value) in cli.output


# ── Notes the session owes the user ─────────────────────────────────────────


@pytest.mark.parametrize(
    ("old_roots", "new_roots", "debuginfod", "expected"),
    [
        ((), (), False, []),
        (("roots",), (), False, ["old"]),
        ((), ("roots",), False, ["new"]),
        (("roots",), ("roots",), False, ["old", "new"]),
        ((), (), True, ["old", "new"]),
    ],
)
def test_each_side_with_debug_inputs_names_its_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    old_roots: tuple,
    new_roots: tuple,
    debuginfod: bool,
    expected: list[str],
) -> None:
    """A side is looked up only when it is a binary and has debug roots, or
    debuginfod is on; a snapshot side never is."""
    from types import SimpleNamespace

    from abicheck.workflows.compare import session_inputs

    asked: list[Path] = []

    def _resolve_debug_info(binary, **_kw):
        asked.append(binary)
        return SimpleNamespace(source=f"{binary.name}.debug")

    monkeypatch.setattr(
        "abicheck.workflows.extraction.resolve_debug_info", _resolve_debug_info
    )
    monkeypatch.setattr(
        "abicheck.workflows.extraction.detect_binary_format",
        lambda p: "elf" if p.suffix == ".so" else None,
    )
    old_bin, new_bin = tmp_path / "old.so", tmp_path / "new.so"
    snapshot = tmp_path / "snap.json"
    notes: list[str] = []
    request = pair_session_request(
        old_bin,
        new_bin,
        debug={"debuginfod": debuginfod},
        evidence={
            "old_debug_roots": tuple(tmp_path / r for r in old_roots),
            "new_debug_roots": tuple(tmp_path / r for r in new_roots),
        },
    )
    session_inputs.note_debug_resolution(old_bin, new_bin, request, notes.append)
    assert notes == [f"Debug info ({side}): {side}.so.debug" for side in expected]
    notes.clear()
    session_inputs.note_debug_resolution(snapshot, snapshot, request, notes.append)
    assert notes == []


@pytest.mark.parametrize(
    ("old_pkg", "new_pkg", "expected"),
    [
        (None, None, []),
        ("a.rpm", None, [("a.rpm", "old side")]),
        (None, "b.rpm", [("b.rpm", "new side")]),
        ("a.rpm", "a.rpm", [("a.rpm", "both sides")]),
        ("a.rpm", "b.rpm", [("a.rpm", "old side"), ("b.rpm", "new side")]),
    ],
)
def test_development_packages_are_named_once_per_package(
    tmp_path: Path, old_pkg: str | None, new_pkg: str | None, expected: list
) -> None:
    old, new = _operands(tmp_path)
    session = run_session(
        pair_session_request(
            old,
            new,
            evidence={
                "old_devel_package": old_pkg and tmp_path / old_pkg,
                "new_devel_package": new_pkg and tmp_path / new_pkg,
            },
        )
    )
    named = [
        (note.split("'")[1], note.split("ignored for the ")[1].split(":")[0])
        for note in session.notes
        if "development package" in note
    ]
    assert named == [(str(tmp_path / pkg), scope) for pkg, scope in expected]


def test_a_linker_script_operand_is_followed_to_its_library(tmp_path: Path) -> None:
    from abicheck.workflows.extraction import follow_linker_script

    lib = tmp_path / "libfoo.so.1"
    lib.write_bytes(b"\x7fELF\x02\x01\x01" + b"\0" * 57)
    script = tmp_path / "libfoo.so"
    script.write_text("/* GNU ld script */\nINPUT(libfoo.so.1)\n", encoding="utf-8")
    notes: list[str] = []
    assert follow_linker_script(script, notes.append) == (lib, "elf")
    assert notes == [
        f"Note: '{script}' is a GNU ld linker script; following its "
        f"INPUT()/GROUP() directive to '{lib}'."
    ]
    notes.clear()
    assert follow_linker_script(lib, notes.append) == (lib, "elf")
    assert notes == []


@pytest.mark.parametrize(
    "compile_block",
    [
        None,
        "compile:\n  std: c++17\n  defines: [A=1, B]\n  include_dirs: [inc]\n",
        "compile:\n  sysroot: /opt/sysroot\n  nostdinc: true\n  frontend: clang\n",
        "compile:\n  compiler: /opt/cross/bin/gcc\n  options: [-fno-rtti]\n",
    ],
)
@pytest.mark.parametrize("named", [True, False])
@pytest.mark.parametrize("defines", [(), ("A=2", "C")])
def test_dry_run_and_session_resolve_one_compile_context(
    tmp_path: Path, compile_block: str | None, named: bool, defines: tuple
) -> None:
    """The ``--dry-run`` receipt (``cli_options.resolve_compile_context``)
    and the session resolve the L2 context through one function, so for the
    same knobs and config they agree -- including the ``compile.compiler``
    trust gate, which turns on whether the config was named."""
    from abicheck.cli_options import resolve_compile_context as cli_resolve
    from abicheck.workflows.compare.session_inputs import resolve_compile_context

    cfg = None
    if compile_block is not None:
        cfg = tmp_path / ".abicheck.yml"
        cfg.write_text(compile_block, encoding="utf-8")
    includes = (tmp_path / "cli-inc",)
    request = pair_session_request(
        tmp_path / "old.json",
        tmp_path / "new.json",
        config=cfg if named else None,
        headers={"includes": includes},
        compile={"defines": defines},
    )
    if not named:  # a discovered config: selected, but not named
        request = dataclasses.replace(
            request, project=dataclasses.replace(request.project, path=cfg)
        )
    session_notes: list[str] = []
    via_session = resolve_compile_context(request, session_notes.append)
    ctx = click.Context(click.Command("compare"))
    via_cli = cli_resolve(
        ctx,
        sysroot=None,
        nostdinc=False,
        header_backend="auto",
        includes=includes,
        build_config=cfg,
        defines=defines,
        config_explicit=named,
    )
    assert via_session == via_cli
