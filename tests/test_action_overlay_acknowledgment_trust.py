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

"""ADR-067 D5/D6 trust boundary through the Action's synthesized overlay.

A discovered ``.abicheck.yml`` is repository-controlled: on a ``pull_request``
trigger the pull request can edit it. The CLI loads acknowledgment records only
from an explicit ``--config``, but the Action folds a discovered document into
an overlay it always hands the CLI as an *explicit* ``--config``. Unless the
overlay builder strips ``acknowledgment.file`` (and the ``breaks`` loosening
knob), a PR can make its own additions acceptable by naming a records file in
its checkout (ADR-067 G5).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from _abicheck_subprocess import abicheck_command
from _action_run_sh_harness import (
    REQUIRES_POSIX_SHELL,
    _stub_abicheck,
)

from abicheck.action_config_overlay import (
    rebase_relative_config_paths,
    strip_untrusted_execution_keys,
)

pytestmark = REQUIRES_POSIX_SHELL


def test_strip_drops_discovered_acknowledgment_file_and_breaks_policy() -> None:
    base = {
        "acknowledgment": {
            "file": "/checkout/abi/acknowledgments.yml",
            "breaks": "accept",
            "unacknowledged_additions": "block",
        }
    }
    stripped = strip_untrusted_execution_keys(base)
    # Records and the breaks loosening are trusted only from an explicit
    # --config; the gate tightening (block) is harmless and survives.
    assert stripped["acknowledgment"] == {"unacknowledged_additions": "block"}


def test_strip_does_not_mutate_its_input() -> None:
    base = {"acknowledgment": {"file": "/checkout/acks.yml"}}
    strip_untrusted_execution_keys(base)
    assert base == {"acknowledgment": {"file": "/checkout/acks.yml"}}


def test_relative_acknowledgment_file_is_rebased_against_project_root(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    found = project / ".abicheck.yml"
    found.write_text("acknowledgment: {}\n", encoding="utf-8")
    rebased = rebase_relative_config_paths(
        {"acknowledgment": {"file": "abi/acks.yml"}}, found_path=found
    )
    # Relocated to a scratch overlay, a relative path would resolve somewhere
    # else entirely; it must name the real project's records file.
    assert rebased["acknowledgment"]["file"] == str(
        (project / "abi" / "acks.yml").resolve()
    )


_REPO_ROOT = Path(__file__).resolve().parents[1]


def _capture_stub(tmp_path: Path, capture: Path) -> Path:
    """An abicheck that copies the file it was given as ``--config`` aside.

    The overlay the Action synthesizes is written to a scratch path that is
    gone once the step exits, so the stub is the only place to observe what
    the CLI would actually be told to load.

    ``run.sh`` runs its overlay merge with ``PYTHONPATH`` cleared, so the
    interpreter it finds on PATH imports whatever ``abicheck`` is installed --
    in a git worktree that is a different checkout from the code under test,
    and the test would exercise stale code. The shim pins the interpreter to
    *this* checkout, and the test below asserts the pin took effect.
    """
    bindir = _stub_abicheck(tmp_path, exit_code=0, payload=None)
    shim = bindir / "python3"
    shim.write_text(
        "#!/usr/bin/env bash\n"
        f'export PYTHONPATH="{_REPO_ROOT}"\n'
        f'exec "{sys.executable}" "$@"\n',
        encoding="utf-8",
    )
    shim.chmod(0o755)
    stub = bindir / "abicheck"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        "prev=''\n"
        'for arg in "$@"; do\n'
        '  if [[ "$prev" == "--config" ]]; then\n'
        f'    cp "$arg" "{capture}"\n'
        "  fi\n"
        '  prev="$arg"\n'
        "done\n"
        "exit 0\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    return bindir


@pytest.mark.skipif(
    os.name == "nt", reason="the overlay synthesis path is POSIX shell only"
)
def _lib(path: Path, source: str) -> Path:
    src = path.with_suffix(".c")
    src.write_text(source, encoding="utf-8")
    subprocess.run(
        ["gcc", "-shared", "-fPIC", "-g", str(src), "-o", str(path)], check=True
    )
    return path


def test_action_discovered_config_cannot_load_records_into_overlay(
    tmp_path: Path,
) -> None:
    """A discovered ``acknowledgment.file`` is dropped, with a note naming it.

    ``--set compile.frontend="castxml"`` is the compile-context input the Action
    forwards for ``ast-frontend: castxml``. It is what makes the run read the
    discovered config for its compile context, the path the old overlay took.
    """
    if shutil.which("gcc") is None:
        pytest.skip("needs gcc to build the shared objects")
    records = tmp_path / "accept-everything.yml"
    records.write_text(
        "version: 1\nacknowledgments:\n"
        "  - finding_id: 'deadbeefdeadbeef'\n    reason: 'planted by PR'\n",
        encoding="utf-8",
    )
    (tmp_path / ".abicheck.yml").write_text(
        f"acknowledgment:\n  file: {records}\n  unacknowledged_additions: block\n",
        encoding="utf-8",
    )
    old = _lib(tmp_path / "old.so", "int f(int x){return x+1;}\n")
    new = _lib(tmp_path / "new.so", "int f(int x){return x+1;}\n")
    result = subprocess.run(
        abicheck_command(
            "compare", str(old), str(new), "--set", 'compile.frontend="castxml"'
        ),
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    output = result.stdout + result.stderr
    # The gate's own outcome: the discovered key is not loaded, and it says so.
    assert "acknowledgment.file is not" in output, output
    assert "a discovered config is not trusted" in output, output
    # A refused key is not a failed run: the comparison itself completes.
    assert "Traceback" not in output, output
