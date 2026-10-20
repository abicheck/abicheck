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
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from _action_run_sh_harness import (
    REQUIRES_POSIX_SHELL,
    _compare_env,
    _run_action,
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
def test_action_discovered_config_cannot_load_records_into_overlay(
    tmp_path: Path,
) -> None:
    """A PR-planted ``acknowledgment.file`` in the checkout's own
    ``.abicheck.yml`` must not reach the overlay the CLI reads as explicit.

    ``ast-frontend: castxml`` makes the Action synthesize a compile-context
    overlay from the discovered config, the path G5 names. The
    ``unacknowledged_additions`` setting alongside it must survive so the
    merge is shown to have actually run (a positive control: an empty overlay
    would also lack the records and prove nothing).
    """
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
    capture = tmp_path / "captured-overlay.yml"
    bindir = _capture_stub(tmp_path, capture)
    env = {**_compare_env(tmp_path), "INPUT_AST_FRONTEND": "castxml"}

    _run_action(tmp_path, env, bindir)

    assert capture.is_file(), "the Action never handed the CLI a --config overlay"
    # The overlay merge must have run against this checkout's code, or the
    # assertions below would be checking the installed package instead.
    probe = subprocess.run(
        [sys.executable, "-c", "import abicheck; print(abicheck.__file__)"],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(_REPO_ROOT)},
        capture_output=True,
        text=True,
        check=True,
    )
    assert Path(probe.stdout.strip()).is_relative_to(_REPO_ROOT)
    overlay = yaml.safe_load(capture.read_text(encoding="utf-8"))
    assert overlay["acknowledgment"]["unacknowledged_additions"] == "block"
    assert "file" not in overlay["acknowledgment"], (
        "a discovered .abicheck.yml's acknowledgment.file reached the explicit "
        "--config overlay, so the CLI would load PR-planted records"
    )
