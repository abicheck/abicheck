# Copyright 2026 Nikolay Petrov
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

"""Security regression (Codex review on abicheck/abicheck#1089): `compare`'s
inline `--sources old=`/`new=` collection may trust only the operator's
*explicit* `--config`, never `compare`'s own auto-discovered project
`.abicheck.yml`.

`embed_build_source` runs ``build.query`` only from a config the caller marks
trusted (ADR-032 D5; see
``tests/test_build_source_cli.py::test_auto_discovered_build_query_is_not_executed``
for the primitive's own trust boundary). An untrusted, PR-controlled
``.abicheck.yml`` -- sitting in the ``--sources`` tree being compared, or
merely discovered from the working directory -- must never authorize its own
``build.query`` subprocess on a plain ``compare --sources old=<tree>`` with no
``--config``. ``compare`` states that trust on the side's
``InputSpec.execution`` (``build_config_explicit``); these cells read it off
the request the real CLI hands the shared resolution."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.cli import main


def _side_execution_of_compare(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    extra_args: list[str],
):
    """Drive `compare --sources old=<tree>` through the real CLI up to the
    shared resolution, returning the old side's execution plan."""
    import abicheck.workflows.compare.resolve as resolve_mod

    src = tmp_path / "src"
    src.mkdir(exist_ok=True)
    lib = tmp_path / "libfoo.so"
    lib.write_bytes(b"\x7fELF\x02\x01\x01" + b"\0" * 57)

    captured: list = []

    class _Stop(Exception):
        pass

    def _capture(request, *args: object, **kwargs: object):
        captured.append(request)
        raise _Stop

    monkeypatch.setattr(resolve_mod, "resolve_compare_request", _capture)

    with pytest.raises(_Stop):
        CliRunner().invoke(
            main,
            [
                "compare",
                str(lib),
                str(lib),
                "--sources",
                f"old={src}",
                "--depth",
                "build",
                *extra_args,
            ],
            catch_exceptions=False,
        )
    (request,) = captured
    assert request.old.execution is not None
    return request.old.execution


@pytest.mark.parametrize("where", ["cwd", "tree"])
def test_an_auto_discovered_config_is_not_trusted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    where: str,
) -> None:
    """No ``--config`` given, but a `.abicheck.yml` is discoverable (from the
    working directory, or at the ``--sources`` root): its passive settings
    apply, but it is never trusted -- exactly the untrusted case ADR-032 D5
    distinguishes from an operator-supplied one."""
    root = tmp_path / "src" if where == "tree" else tmp_path
    root.mkdir(exist_ok=True)
    (root / ".abicheck.yml").write_text(
        "severity:\n  addition: warning\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    execution = _side_execution_of_compare(tmp_path, monkeypatch, extra_args=[])

    assert execution.build_config == root / ".abicheck.yml"
    assert execution.build_config_explicit is False


def test_explicit_config_is_trusted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Positive control: an operator-supplied ``--config`` is the side's
    config, and trusted -- so the fix for the finding above doesn't overshoot
    into never trusting a config at all."""
    monkeypatch.chdir(tmp_path)
    explicit_cfg = tmp_path / "explicit.yml"
    explicit_cfg.write_text("severity:\n  addition: warning\n", encoding="utf-8")

    execution = _side_execution_of_compare(
        tmp_path,
        monkeypatch,
        extra_args=["--config", str(explicit_cfg)],
    )

    assert execution.build_config == explicit_cfg
    assert execution.build_config_explicit is True
