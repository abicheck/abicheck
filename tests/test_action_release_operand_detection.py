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

"""`run.sh`'s release-operand test agrees with the CLI's own `is_package`.

`_is_release_style_operand` gates whether the Action forwards the
package-only inputs (`devel-pkg1/2`, `debug-info1/2`). It was a suffix
table plus an RPM/Deb magic check, documented as "mirrors `package.py`'s
`is_package()`" -- and plan Phase 7n made the real one content-based, so
the mirror broke: a tar/conda/wheel staged under a nonconventional name
became a release operand to `compare` and stayed a single-file operand
here, silently dropping the headers and debug info from a real release
comparison (Codex review, PR #1253).

That is `cli_surface.copied_option_table_went_stale` one adapter out, so
the oracle here is never this module's own table and never a pinned
constant: it is **`abicheck.package.is_package` itself**, asked of the same
bytes. A test that listed the expected answers would drift exactly the way
the shell table did.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
from _package_fixtures import _make_conda_v2, _make_tar_mode, _make_wheel
from _workflow_exec import bash_executable, require_bash

from abicheck.package import is_package

RUN_SH = Path(__file__).resolve().parents[1] / "action" / "run.sh"

_FN_START = "_is_release_style_operand() {"
_FN_END = "\n}\n"


def _named_function_source(name: str) -> str:
    """The real definition of *name*, parsed out of `run.sh`, not restated."""
    text = RUN_SH.read_text(encoding="utf-8")
    start = text.index(f"{name}() {{")
    end = text.index(_FN_END, start) + len(_FN_END)
    return text[start:end]


def _function_source() -> str:
    """The real `_is_release_style_operand`, plus the helpers it calls."""
    return "\n".join(
        (
            '_RUNNING_ON_WINDOWS="${_RUNNING_ON_WINDOWS:-false}"',
            _named_function_source("_is_path_already_qualified"),
            _named_function_source("_is_release_style_operand"),
        )
    )


def _sh(value: Path | str) -> str:
    """A path as a bash *word*: quoted, and separator-converted on Windows.

    Every path this module injects into a generated script goes through
    here. Interpolating one raw is what turned the windows-latest lane red
    after PR #1259: `sys.executable` and the safe directory arrived as
    `D:\\a\\...`, whose backslashes bash ate as escapes, so `$_PY_BIN`
    named nothing, the probe never ran, and every shape that needs it
    (tar, wheel, conda -- but not rpm/deb, which the magic check answers
    without it) reported "not a package".

    Two separate defects, so two separate fixes: `shlex.quote` for the
    quoting, and `as_posix()` for the separator -- the latter only where
    the separator actually differs, since a backslash is a legal filename
    character on POSIX and rewriting one unconditionally would corrupt a
    real path (`_workflow_exec.py` states the same rule at its own
    `bash <script>` boundary, for the same reason).
    """
    return shlex.quote(
        value.as_posix() if isinstance(value, Path) and os.name == "nt" else str(value)
    )


def _ask(
    path: Path | str,
    *,
    abicheck_available: bool,
    cwd: Path,
    workdir: Path | None = None,
) -> bool:
    """Run the extracted function over *path*, with the probe on or off.

    Shells out through `_workflow_exec`'s resolver and its guard, the one
    sanctioned way in this suite: a bare ``["bash", ...]`` argv resolves to
    Windows' WSL launcher stub and reddens the whole module (see
    `tests/CLAUDE.md`).
    """
    require_bash()
    env_setup = (
        f"_PY_BIN_HAS_ABICHECK=true\n_PY_BIN={_sh(sys.executable)}\n"
        f"_PY_SAFE_DIR={_sh(cwd)}\n"
        if abicheck_available
        else "_PY_BIN_HAS_ABICHECK=false\n"
    )
    script = f'{env_setup}{_function_source()}\n_is_release_style_operand "$1"\n'
    completed = subprocess.run(  # noqa: S603
        [bash_executable(), "-c", script, "bash", str(path)],
        capture_output=True,
        text=True,
        check=False,
        cwd=None if workdir is None else str(workdir),
    )
    assert completed.returncode in (0, 1), completed.stderr
    return completed.returncode == 0


def _shapes(tmp_path: Path) -> dict[str, Path]:
    """One operand per shape, each under a name that says nothing true."""
    rpm = tmp_path / "operand-rpm"
    rpm.write_bytes(b"\xed\xab\xee\xdb" + b"\x00" * 64)
    deb = tmp_path / "operand-deb"
    deb.write_bytes(b"!<arch>\n" + b"\x00" * 64)
    return {
        "rpm": rpm,
        "deb": deb,
        "tar_gz": _make_tar_mode(tmp_path / "operand-targz", "w:gz"),
        "tar_plain": _make_tar_mode(tmp_path / "operand-tar", "w"),
        "wheel": _make_wheel(tmp_path / "operand-whl", {"foo/__init__.py": b""}),
        "conda": _make_conda_v2(tmp_path / "operand-conda", {}),
    }


class TestAgreementWithTheRealPredicate:
    """The whole point: one answer, from one place."""

    def test_every_package_shape_agrees_under_a_nonconventional_name(
        self, tmp_path: Path
    ) -> None:
        disagreements = {
            shape: (is_package(path), _ask(path, abicheck_available=True, cwd=tmp_path))
            for shape, path in _shapes(tmp_path).items()
        }
        assert all(cli == sh for cli, sh in disagreements.values()), disagreements
        # ...and the CLI really does call all of these packages, so the
        # agreement above is not two predicates both answering False.
        assert all(cli for cli, _sh in disagreements.values()), disagreements

    def test_a_non_package_agrees_too(self, tmp_path: Path) -> None:
        """The negative control: agreement is not "say yes to everything"."""
        plain = tmp_path / "libfoo.so"
        plain.write_bytes(b"\x7fELF\x02\x01\x01" + b"\x00" * 64)
        assert not is_package(plain)
        assert not _ask(plain, abicheck_available=True, cwd=tmp_path)

    def test_a_directory_is_a_release_operand_either_way(self, tmp_path: Path) -> None:
        """`is_package` answers False for a directory by contract (ADR-065
        D2: a directory proves no container completeness), so this one rule
        stays in the shell -- and must survive the probe being consulted."""
        d = tmp_path / "release"
        d.mkdir()
        assert not is_package(d)
        assert _ask(d, abicheck_available=True, cwd=tmp_path)
        assert _ask(d, abicheck_available=False, cwd=tmp_path)


class TestEveryInjectedPathSurvivesTheShell:
    """A path interpolated into a generated script is a *bash word*.

    This module builds its script by string interpolation, so every path it
    injects is re-parsed by bash. Raw interpolation reddened windows-latest
    after PR #1259 -- `D:\\a\\...` lost its backslashes to bash's escape
    handling -- but the defect is not Windows-specific: a space, a quote or
    a `$` in the path does the same thing on Linux, and no CI lane would
    have caught it either, because every path in play happened to be tame.

    So the contract is stated over the primitive (`_sh`), against an oracle
    that is not `shlex` -- what bash itself reports receiving -- and swept
    over names chosen to break naive interpolation, rather than pinned to
    the one spelling that broke. The end-to-end case then proves the
    primitive is actually *used* on the path that failed, since a correct
    helper nothing calls fixes nothing.
    """

    AWKWARD = (
        "plain",
        "with space",
        "with'single",
        'with"double',
        "with$dollar",
        "with`backtick",
        "with;semi",
        "with*glob",
        "with\\backslash",
        "with\nnewline",
        "with|pipe",
        "-leading-dash",
    )

    @staticmethod
    def _echo_through_bash(word: str) -> str:
        """What bash actually receives for this word -- the oracle."""
        require_bash()
        completed = subprocess.run(  # noqa: S603
            [bash_executable(), "-c", f"printf '%s' {word}"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
        return completed.stdout

    @pytest.mark.skipif(
        os.name == "nt",
        reason="these names are legal on POSIX; NTFS rejects most of them",
    )
    @pytest.mark.parametrize("name", AWKWARD)
    def test_a_path_round_trips_through_the_generated_script(
        self, tmp_path: Path, name: str
    ) -> None:
        target = tmp_path / name
        assert self._echo_through_bash(_sh(target)) == str(target)

    @pytest.mark.skipif(
        os.name == "nt", reason="POSIX-legal name; NTFS rejects the backslash"
    )
    def test_the_probe_still_answers_from_an_awkwardly_named_safe_dir(
        self, tmp_path: Path
    ) -> None:
        """End to end, on the very path whose raw interpolation broke CI."""
        safe_dir = tmp_path / "safe dir\\with odd $chars"
        safe_dir.mkdir()
        operand = _make_tar_mode(tmp_path / "operand", "w:gz")
        assert is_package(operand)
        assert _ask(operand, abicheck_available=True, cwd=safe_dir)


class TestTheProbeResolvesTheOperandNotTheSafeDirectory:
    """A relative operand is the *normal* Action spelling.

    `old-library`/`new-library` usually arrive relative to the workflow
    directory, and the probe runs from `$_PY_SAFE_DIR` (which exists to keep
    the untrusted checkout off `sys.path`, not to relocate the operand). A
    bare `Path(sys.argv[1])` inside that subshell stats a nonexistent path
    under the temp dir, answers "not a package", and skips the package-only
    inputs for an operand `compare` does fan out (Codex review, PR #1259).

    The working directory and the safe directory are deliberately *distinct*
    here: the first version of this module passed the same `tmp_path` as
    both and used absolute operands, so it could not have caught this.
    """

    def test_a_relative_operand_resolves_from_the_working_directory(
        self, tmp_path: Path
    ) -> None:
        workdir = tmp_path / "workspace"
        workdir.mkdir()
        safe_dir = tmp_path / "safe"
        safe_dir.mkdir()
        _make_tar_mode(workdir / "operand", "w:gz")

        assert is_package(workdir / "operand")
        assert _ask("operand", abicheck_available=True, cwd=safe_dir, workdir=workdir)

    def test_a_relative_non_package_still_answers_no(self, tmp_path: Path) -> None:
        """The control: anchoring must not make everything a package."""
        workdir = tmp_path / "workspace"
        workdir.mkdir()
        safe_dir = tmp_path / "safe"
        safe_dir.mkdir()
        (workdir / "libfoo.so").write_bytes(b"\x7fELF\x02\x01\x01" + b"\x00" * 64)

        assert not _ask(
            "libfoo.so", abicheck_available=True, cwd=safe_dir, workdir=workdir
        )

    def test_a_dotted_relative_operand_resolves_too(self, tmp_path: Path) -> None:
        workdir = tmp_path / "workspace"
        (workdir / "nested").mkdir(parents=True)
        safe_dir = tmp_path / "safe"
        safe_dir.mkdir()
        _make_tar_mode(workdir / "nested" / "operand", "w:gz")

        assert _ask(
            "./nested/operand", abicheck_available=True, cwd=safe_dir, workdir=workdir
        )


class TestTheProbeAnchorsExactlyWhatTheSharedPredicateSays:
    """The anchoring decision has one owner, and it is not this call site.

    The first version of the anchoring fix above carried its *own* regex
    (`^[A-Za-z]:[/\\\\]` plus a POSIX-absolute test). That is the
    `cli_surface.copied_option_table_went_stale` shape inside one file: a
    second, narrower spelling of a question `_is_path_already_qualified`
    already answers, which silently disagreed on UNC (`\\\\server\\share`),
    root-relative (`\\pkg`) and drive-relative (`C:pkg`) paths -- each then
    prefixed with `$PWD`, sending the probe at a path that does not exist
    and withholding the package-only inputs (Codex P2 and CodeRabbit,
    PR #1261).

    So the oracle here is the predicate itself, asked of the same string,
    and the observation is the argv the probe *actually* passes to Python
    -- not the probe's verdict, which collapses two different paths onto
    the same "not a package" answer and would have passed against the bug.
    The spellings are swept from both sides of every branch the predicate
    has, so a future narrowing at either site fails here.
    """

    SPELLINGS = (
        "pkg",
        "./nested/pkg",
        "../pkg",
        "/abs/pkg",
        "C:/pkg",
        "C:\\pkg",
        "C:pkg",
        "\\pkg",
        "\\\\server\\share\\pkg",
        "a:baseline.json",
        "weird name/pkg",
    )

    @staticmethod
    def _probe_argv(spelling: str, *, on_windows: bool, workdir: Path) -> str:
        """The path the probe subprocess is actually handed."""
        require_bash()
        recorder = workdir / "recorded"
        stub = workdir / "py-stub"
        stub.write_text(
            '#!/usr/bin/env bash\nprintf "%s" "${!#}" > "$RECORD"\nexit 3\n',
            encoding="utf-8",
        )
        stub.chmod(0o755)
        script = "\n".join(
            (
                f"_RUNNING_ON_WINDOWS={'true' if on_windows else 'false'}",
                "_PY_BIN_HAS_ABICHECK=true",
                f"_PY_BIN={_sh(stub)}",
                f"_PY_SAFE_DIR={_sh(workdir)}",
                f"RECORD={_sh(recorder)}; export RECORD",
                _function_source(),
                '_is_release_style_operand "$1"',
            )
        )
        subprocess.run(  # noqa: S603
            [bash_executable(), "-c", script, "bash", spelling],
            capture_output=True,
            text=True,
            check=False,
            cwd=str(workdir),
        )
        return recorder.read_text(encoding="utf-8")

    @staticmethod
    def _predicate(spelling: str, *, on_windows: bool) -> bool:
        """The oracle: `run.sh`'s own shared predicate, nothing restated."""
        require_bash()
        script = "\n".join(
            (
                f"_RUNNING_ON_WINDOWS={'true' if on_windows else 'false'}",
                _named_function_source("_is_path_already_qualified"),
                '_is_path_already_qualified "$1"',
            )
        )
        completed = subprocess.run(  # noqa: S603
            [bash_executable(), "-c", script, "bash", spelling],
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode in (0, 1), completed.stderr
        return completed.returncode == 0

    @pytest.mark.parametrize("on_windows", [False, True])
    def test_the_probe_anchors_iff_the_predicate_says_unqualified(
        self, tmp_path: Path, on_windows: bool
    ) -> None:
        disagreements = {}
        for spelling in self.SPELLINGS:
            workdir = tmp_path / f"w{len(disagreements)}-{abs(hash(spelling))}"
            workdir.mkdir()
            qualified = self._predicate(spelling, on_windows=on_windows)
            argv = self._probe_argv(spelling, on_windows=on_windows, workdir=workdir)
            anchored = argv != spelling
            if anchored is qualified:
                disagreements[spelling] = (qualified, argv)
        assert not disagreements, disagreements

    def test_the_sweep_covers_both_answers_on_each_platform(self) -> None:
        """Vacuity guard: a sweep that is all-qualified or all-unqualified
        would pass against a predicate reduced to a constant."""
        for on_windows in (False, True):
            answers = {
                self._predicate(s, on_windows=on_windows) for s in self.SPELLINGS
            }
            assert answers == {True, False}, on_windows


class TestThePreInstallFallback:
    """`validate-inputs.sh` runs before abicheck exists, so the table stays
    -- as a degraded approximation whose limits are stated, not as a copy."""

    def test_conventional_names_still_answer_without_the_probe(
        self, tmp_path: Path
    ) -> None:
        for name in ("libfoo.rpm", "libfoo.deb", "libfoo.tar.gz", "libfoo.whl"):
            path = tmp_path / name
            path.write_bytes(b"\x00" * 32)
            assert _ask(path, abicheck_available=False, cwd=tmp_path), name

    def test_the_fallback_is_what_the_probe_improves_on(self, tmp_path: Path) -> None:
        """States the gap rather than hiding it: the table cannot see a
        content-routed package, which is exactly why the probe exists. If
        this ever starts passing, the fallback grew content detection and
        this test should become an agreement assertion instead."""
        renamed = _make_tar_mode(tmp_path / "operand", "w:gz")
        assert is_package(renamed)
        assert not _ask(renamed, abicheck_available=False, cwd=tmp_path)
        assert _ask(renamed, abicheck_available=True, cwd=tmp_path)
