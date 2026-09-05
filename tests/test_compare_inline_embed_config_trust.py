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
inline `--sources old=`/`new=` embed path must forward only the operator's
*explicit* `--config` to its nested `dump_cmd` invocation, never `compare`'s
own auto-discovered project `.abicheck.yml`.

`embed_build_source` treats a non-``None`` ``build_config`` as operator
authorization to execute ``build.query`` (ADR-032 D5; see
``tests/test_build_source_cli.py::test_auto_discovered_build_query_is_not_executed``
for the primitive's own trust boundary). CLI cleanup phase two's Block 7 (PR
C's tail) added a request-level ``InputSpec.build_config`` seam so ``dump``/
``compare``'s pre-flight bazel-target-scoping check could see an explicit
``--config`` -- but the first version of that fix forwarded `compare`'s
already-resolved ``cfg_path`` (which conflates an explicit ``--config`` with
`_resolve_compare_config`'s own auto-discovery fallback,
``discover_project_config()``) into the nested ``dump_cmd`` invocation's
``build_config`` parameter instead of the raw, explicit-only ``--config``
value. That would have let an untrusted, PR-controlled ``.abicheck.yml`` --
either sitting in the ``--sources`` tree being compared, or merely discovered
from the working directory -- silently authorize its own ``build.query``
subprocess execution on a plain ``compare --sources old=<tree>`` invocation
with no ``--config`` at all. This module is a dedicated home for that one
security property, split out from ``tests/test_build_source_cli.py`` rather
than grown into it (that file already sits at its `no_growth` architecture
debt baseline -- see `architecture/debt.yaml`)."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.cli import main


def _invoke_compare_inline_embed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, extra_args: list[str],
) -> dict:
    """Drive `compare --sources old=<tree>` through the real CLI up to (but
    not through) its nested `dump_cmd` invocation, returning the kwargs that
    invocation would have received.

    `_normalize_binary_input` is mocked (module-scoped, matching
    `tests/test_compare_dispatch.py`'s own established pattern for this
    exact function) so a placeholder `old.so`/`new.so` is classified as a
    native ELF operand without needing a real binary -- the inline-embed
    dispatch only needs the *format*, not real content, to decide a raw
    `--sources` tree needs collecting. `_embed_inline_source_sides` is
    replaced with a capture-and-stop stub so the real (heavier, and for a
    placeholder ELF, doomed-to-fail) dump/diff machinery never runs -- this
    test is only about what reaches that one call, not what happens after.
    """
    import abicheck.cli_compare_helpers as cch
    import abicheck.frontends.cli.commands.compare as climod

    src = tmp_path / "src"
    src.mkdir(exist_ok=True)
    old_so, new_so = tmp_path / "old.so", tmp_path / "new.so"
    old_so.write_bytes(b"")
    new_so.write_bytes(b"")
    monkeypatch.setattr(climod, "_normalize_binary_input", lambda p: (p, "elf"))

    captured: dict = {}

    class _Stop(Exception):
        pass

    def _fake_embed_sides(*args: object, **kwargs: object) -> None:
        captured.update(kwargs)
        raise _Stop

    monkeypatch.setattr(cch, "_embed_inline_source_sides", _fake_embed_sides)

    with pytest.raises(_Stop):
        CliRunner().invoke(
            main,
            [
                "compare", str(old_so), str(new_so),
                "--sources", f"old={src}", "--depth", "build",
                *extra_args,
            ],
            catch_exceptions=False,
        )
    return captured


def test_auto_discovered_config_is_not_forwarded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No ``--config`` given, but a `.abicheck.yml` is auto-discoverable from
    cwd: the nested invocation's ``build_config`` must stay ``None`` -- an
    auto-discovered config is exactly the untrusted case ADR-032 D5 exists
    to distinguish from an operator-supplied one."""
    (tmp_path / ".abicheck.yml").write_text(
        "severity:\n  addition: warning\n", encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    captured = _invoke_compare_inline_embed(tmp_path, monkeypatch, extra_args=[])

    assert captured.get("build_config") is None


def test_explicit_config_is_forwarded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Positive control: an operator-supplied ``--config`` must still reach
    the nested invocation -- Block 7's own parity goal -- so the fix for the
    finding above doesn't overshoot into never forwarding a config at all."""
    explicit_cfg = tmp_path / "explicit.yml"
    explicit_cfg.write_text("severity:\n  addition: warning\n", encoding="utf-8")

    captured = _invoke_compare_inline_embed(
        tmp_path, monkeypatch, extra_args=["--config", str(explicit_cfg)],
    )

    assert captured.get("build_config") == explicit_cfg
