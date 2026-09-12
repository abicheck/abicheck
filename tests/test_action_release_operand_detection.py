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

import subprocess
import sys
from pathlib import Path

from _package_fixtures import _make_conda_v2, _make_tar_mode, _make_wheel
from _workflow_exec import bash_executable, require_bash

from abicheck.package import is_package

RUN_SH = Path(__file__).resolve().parents[1] / "action" / "run.sh"

_FN_START = "_is_release_style_operand() {"
_FN_END = "\n}\n"


def _function_source() -> str:
    """The real definition, parsed out of `run.sh` rather than restated."""
    text = RUN_SH.read_text(encoding="utf-8")
    start = text.index(_FN_START)
    end = text.index(_FN_END, start) + len(_FN_END)
    return text[start:end]


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
        f'_PY_BIN_HAS_ABICHECK=true\n_PY_BIN={sys.executable}\n_PY_SAFE_DIR={cwd}\n'
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

    def test_a_directory_is_a_release_operand_either_way(
        self, tmp_path: Path
    ) -> None:
        """`is_package` answers False for a directory by contract (ADR-065
        D2: a directory proves no container completeness), so this one rule
        stays in the shell -- and must survive the probe being consulted."""
        d = tmp_path / "release"
        d.mkdir()
        assert not is_package(d)
        assert _ask(d, abicheck_available=True, cwd=tmp_path)
        assert _ask(d, abicheck_available=False, cwd=tmp_path)


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
        assert _ask(
            "operand", abicheck_available=True, cwd=safe_dir, workdir=workdir
        )

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

    def test_the_fallback_is_what_the_probe_improves_on(
        self, tmp_path: Path
    ) -> None:
        """States the gap rather than hiding it: the table cannot see a
        content-routed package, which is exactly why the probe exists. If
        this ever starts passing, the fallback grew content detection and
        this test should become an agreement assertion instead."""
        renamed = _make_tar_mode(tmp_path / "operand", "w:gz")
        assert is_package(renamed)
        assert not _ask(renamed, abicheck_available=False, cwd=tmp_path)
        assert _ask(renamed, abicheck_available=True, cwd=tmp_path)
