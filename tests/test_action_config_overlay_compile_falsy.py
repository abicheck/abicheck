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

"""Direct unit tests for ``abicheck.action_config_overlay_compile``'s
``_COMPILE_EMPTY_STRING_UNSET_KEYS`` handling in ``merge_compile_block``
(via ``action_config_overlay.apply_sources_root_config_blocks``, its
public entry point).

Split out of ``tests/test_action_config_overlay.py`` (which was already at
its architecture-gate line cap) rather than grown in place, per this
repo's own precedent of splitting a test module once a sibling gate is hit
(see ``tests/test_mutation_per_module_scoping.py``'s own docstring for the
same pattern).

P2 finding (Codex review, PR #1222, "Treat an empty sysroot as unset
during overlay merge"): a checkout document validly spelling
``compile.sysroot: ""`` was preserved by a raw key-presence test (``key
not in checkout_blk``), permanently blocking a sources-root
``compile.sysroot`` from ever applying. The real ``cli_options.
merge_compile_config`` never does this -- its own ``sysroot = cli_ctx.
sysroot if cli_ctx.sysroot is not None else (Path(bc.compile_sysroot) if
bc.compile_sysroot else None)`` treats a falsy (empty-string)
``compile_sysroot`` exactly like an absent key, so a real two-stage run
lets the sources-root value win. ``compiler`` has the identical falsy-vs-
presence shape one level down (``if gcc_path is None and gcc_prefix is
None and bc.compile_compiler:``), so it gets the same fix and the same
kind of test here.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from abicheck.action_config_overlay import apply_sources_root_config_blocks
from abicheck.cli_options import merge_compile_config
from abicheck.dry_run_estimate import CompileContext


def _real_two_stage_sysroot(
    tmp_path: Path,
    checkout_compile: dict[str, object],
    sources_compile: dict[str, object],
) -> str | None:
    """Run the REAL two-stage ``merge_compile_config`` fold ``compare``'s
    own per-side implicit dump performs and return the resulting
    ``sysroot`` as a string (or ``None``) -- the oracle this module's
    sysroot test checks the overlay's merged document against."""
    checkout_cfg = tmp_path / "checkout_sysroot.abicheck.yml"
    checkout_cfg.write_text(yaml.safe_dump({"compile": checkout_compile}))
    sources_dir = tmp_path / "sources_root_sysroot"
    sources_dir.mkdir(exist_ok=True)
    (sources_dir / ".abicheck.yml").write_text(
        yaml.safe_dump({"compile": sources_compile})
    )
    ctx1, includes1 = merge_compile_config(CompileContext(), (), checkout_cfg, None)
    ctx2, _ = merge_compile_config(ctx1, includes1, None, sources_dir)
    return str(ctx2.sysroot) if ctx2.sysroot is not None else None


def _real_two_stage_gcc_path(
    tmp_path: Path,
    checkout_compile: dict[str, object],
    sources_compile: dict[str, object],
) -> str | None:
    """Same as :func:`_real_two_stage_sysroot`, but for the resulting
    ``gcc_path`` (``compile.compiler``). ``config_explicit=True`` on both
    folds matches how the overlay's own merged document is ultimately
    consumed downstream: the nested "Run analysis" invocation re-parses it
    as a single, explicitly-trusted ``--build-config`` document (one
    already-merged document, not two separately-trust-gated ones), which
    is the trust level this overlay's own output is meant to reproduce."""
    checkout_cfg = tmp_path / "checkout_compiler.abicheck.yml"
    checkout_cfg.write_text(yaml.safe_dump({"compile": checkout_compile}))
    sources_dir = tmp_path / "sources_root_compiler"
    sources_dir.mkdir(exist_ok=True)
    (sources_dir / ".abicheck.yml").write_text(
        yaml.safe_dump({"compile": sources_compile})
    )
    ctx1, includes1 = merge_compile_config(
        CompileContext(), (), checkout_cfg, None, config_explicit=True
    )
    ctx2, _ = merge_compile_config(
        ctx1, includes1, None, sources_dir, config_explicit=True
    )
    return ctx2.gcc_path


class TestCompileEmptyStringTreatedAsUnset:
    def test_empty_checkout_sysroot_is_treated_as_unset(self, tmp_path: Path) -> None:
        checkout_compile = {"sysroot": ""}
        sources_compile = {"sysroot": "/real/path"}
        out = apply_sources_root_config_blocks(
            {"compile": checkout_compile},
            {"compile": sources_compile},
            blocks=("compile",),
            merge_compile=True,
        )
        expected = _real_two_stage_sysroot(tmp_path, checkout_compile, sources_compile)
        assert expected == "/real/path"
        assert out["compile"]["sysroot"] == expected

    def test_empty_checkout_compiler_is_treated_as_unset(self, tmp_path: Path) -> None:
        checkout_compile = {"compiler": ""}
        sources_compile = {"compiler": "/usr/bin/real-gcc"}
        out = apply_sources_root_config_blocks(
            {"compile": checkout_compile},
            {"compile": sources_compile},
            blocks=("compile",),
            merge_compile=True,
        )
        expected = _real_two_stage_gcc_path(tmp_path, checkout_compile, sources_compile)
        assert expected == "/usr/bin/real-gcc"
        assert out["compile"]["compiler"] == expected

    def test_none_checkout_sysroot_is_also_treated_as_unset(
        self, tmp_path: Path
    ) -> None:
        """A checkout document with the key present but explicitly ``null``
        (``compile.sysroot: null`` -> Python ``None``) is just as falsy as
        ``""`` and must resolve the same way."""
        checkout_compile: dict[str, object] = {"sysroot": None}
        sources_compile = {"sysroot": "/real/path"}
        out = apply_sources_root_config_blocks(
            {"compile": checkout_compile},
            {"compile": sources_compile},
            blocks=("compile",),
            merge_compile=True,
        )
        assert out["compile"]["sysroot"] == "/real/path"

    def test_nonempty_checkout_sysroot_still_blocks_sources_root(
        self, tmp_path: Path
    ) -> None:
        """Negative control: a genuinely non-empty checkout ``sysroot``
        still wins over a sources-root one -- the fix must only change the
        falsy-checkout-value case, not the ordinary conflict case."""
        checkout_compile = {"sysroot": "/checkout/sysroot"}
        sources_compile = {"sysroot": "/sources/sysroot"}
        out = apply_sources_root_config_blocks(
            {"compile": checkout_compile},
            {"compile": sources_compile},
            blocks=("compile",),
            merge_compile=True,
        )
        expected = _real_two_stage_sysroot(tmp_path, checkout_compile, sources_compile)
        assert expected == "/checkout/sysroot"
        assert out["compile"]["sysroot"] == expected

    def test_nonempty_checkout_compiler_still_blocks_sources_root(
        self, tmp_path: Path
    ) -> None:
        checkout_compile = {"compiler": "/checkout/gcc"}
        sources_compile = {"compiler": "/sources/gcc"}
        out = apply_sources_root_config_blocks(
            {"compile": checkout_compile},
            {"compile": sources_compile},
            blocks=("compile",),
            merge_compile=True,
        )
        expected = _real_two_stage_gcc_path(tmp_path, checkout_compile, sources_compile)
        assert expected == "/checkout/gcc"
        assert out["compile"]["compiler"] == expected

    def test_both_empty_leaves_the_field_unset(self, tmp_path: Path) -> None:
        checkout_compile = {"sysroot": ""}
        sources_compile: dict[str, object] = {"sysroot": ""}
        out = apply_sources_root_config_blocks(
            {"compile": checkout_compile},
            {"compile": sources_compile},
            blocks=("compile",),
            merge_compile=True,
        )
        assert not out["compile"].get("sysroot")
