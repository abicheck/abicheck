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

"""P0.2 Bazel root-target scoping -- the L2 seed's own ``collect_inline_pack``
call (Codex review, PR #788).

``embed_build_source`` (the L3/L4/L5 embed step) has threaded ``build_targets``
through since this same PR added `scan --build-target`. But `dump`/`scan` both
run a SEPARATE, earlier `collect_inline_pack()` call first --
``l2_seed.seed_includes_and_fold_compile_context`` -- to seed L2 include dirs
and fold the P0.3 L3->L2 compile-context. That call never threaded
``build_targets`` at all, on EITHER command (a pre-existing gap, not
introduced by this PR's scan changes) -- so in a multi-target Bazel workspace,
an explicit `--build-target //:math` scoped L3/L4/L5 evidence collection but
left the L2 seed unscoped, letting an unrelated target's include dirs or
conflicting dialect flags leak into the header parse despite target-scoped L3
evidence.

Covers, bottom-up:

* ``l2_seed._l2_seed_config`` -- folds ``build_targets`` into the effective
  ``BuildConfig.targets``, CLI override winning over `.abicheck.yml`'s own
  ``build.targets`` (mirrors ``embed_build_source``'s identical override).
* ``l2_seed.seed_includes_and_fold_compile_context`` -- the scoped
  ``BuildConfig`` reaches ``collect_inline_pack``'s own ``build_config``
  argument (which drives ``bazel_targets=tuple(cfg.targets)``).
* ``service_input_resolution._seeded_includes_and_compile_context`` -- the
  typed API's ``InputSpec.build_targets`` reaches the same seed via the
  identical ``l2_seed.seed_includes_and_fold_compile_context`` call (PR C,
  typed dump/scan convergence, merged this module's own former two-call
  shape into the one the other three CLI-side resolvers already used).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from abicheck.buildsource.build_config_io import BuildConfig
from abicheck.buildsource.l2_seed import (
    _l2_seed_config,
    seed_includes_and_fold_compile_context,
)


def test_l2_seed_config_folds_explicit_build_targets(tmp_path: Path) -> None:
    cfg = _l2_seed_config(None, tmp_path, None, None, ("//:math", "//:util"))
    assert cfg is not None
    assert cfg.targets == ["//:math", "//:util"]


def test_l2_seed_config_cli_build_targets_override_config(tmp_path: Path) -> None:
    (tmp_path / ".abicheck.yml").write_text(
        "build:\n  system: bazel\n  targets:\n    - //:from_config\n",
        encoding="utf-8",
    )
    cfg = _l2_seed_config(None, tmp_path, None, None, ("//:from_cli",))
    assert cfg is not None
    assert cfg.targets == ["//:from_cli"]


def test_l2_seed_config_no_build_targets_keeps_config_value(tmp_path: Path) -> None:
    (tmp_path / ".abicheck.yml").write_text(
        "build:\n  system: bazel\n  targets:\n    - //:from_config\n",
        encoding="utf-8",
    )
    cfg = _l2_seed_config(None, tmp_path, None, None, ())
    assert cfg is not None
    assert cfg.targets == ["//:from_config"]


def test_seed_includes_and_fold_compile_context_scopes_collect_inline_pack(
    monkeypatch, tmp_path: Path
) -> None:
    """The actual gap Codex flagged: build_targets must reach the
    collect_inline_pack() call this function makes, not just the later,
    separate embed_build_source() call."""
    import abicheck.buildsource.l2_seed as l2_seed_mod

    src = tmp_path / "src"
    src.mkdir()
    header = src / "api.h"
    header.write_text("void f();\n", encoding="utf-8")

    captured: dict = {}

    def _fake_collect_inline_pack(*, build_config, **kwargs):
        captured["build_config"] = build_config
        # The real function wraps this whole section in a best-effort
        # try/except (same "degrade, never fatal" contract as
        # derive_l2_include_dirs) -- raising here is caught and degrades
        # the return value rather than propagating, which is fine: this
        # test only needs the call to have happened with the right args.
        raise RuntimeError("stop before any real collection")

    monkeypatch.setattr(l2_seed_mod, "collect_inline_pack", _fake_collect_inline_pack)

    seed_includes_and_fold_compile_context(
        headers=[header],
        includes=[],
        sources=src,
        build_info=None,
        build_config=None,
        build_query=None,
        build_compile_db=None,
        build_targets=("//:math",),
        collect_mode="build",
        gcc_path=None,
        gcc_prefix=None,
        gcc_options=None,
        gcc_option_tokens=(),
        sysroot=None,
        nostdinc=False,
        frontend="auto",
        frontend_context="host",
        lang="c++",
        lang_explicit=False,
        pending_cleanups=[],
    )

    assert isinstance(captured["build_config"], BuildConfig)
    assert captured["build_config"].targets == ["//:math"]


def test_seeded_includes_and_compile_context_forwards_input_spec_build_targets(
    monkeypatch, tmp_path: Path
) -> None:
    """The typed API side: InputSpec.build_targets reaches the combined
    seed_includes_and_fold_compile_context call (and therefore its single
    collect_inline_pack scoping) through
    service_input_resolution._seeded_includes_and_compile_context.

    PR C (typed dump/scan convergence) merged the typed API's own two
    separate call paths (``_seeded_includes`` -> ``seed_l2_includes`` ->
    ``derive_l2_include_dirs``, and ``_seeded_compile_context`` ->
    ``derive_l2_compile_context``) into this one combined call -- see
    ``_seeded_includes_and_compile_context``'s own docstring for why running
    ``collect_inline_pack`` twice per side was a latent self-deadlock risk,
    the same one already fixed for `dump`/`scan`'s three CLI-side resolvers.
    This test used to be two (one per now-removed helper); both exercised
    the identical `build_targets` gap through the same underlying
    `collect_inline_pack` call, so one test on the combined function covers
    what both did.
    """
    from abicheck.api_types import InputSpec
    from abicheck.service_compare_evidence import SideEvidence
    from abicheck.service_input_resolution import (
        _seeded_includes_and_compile_context,
    )
    from abicheck.service_scan import CompileContext

    captured: dict = {}

    def _fake_collect_inline_pack(*, build_config, **kwargs):
        captured["build_config"] = build_config
        raise RuntimeError("stop before any real collection")

    import abicheck.buildsource.l2_seed as l2_seed_mod

    monkeypatch.setattr(l2_seed_mod, "collect_inline_pack", _fake_collect_inline_pack)

    src = tmp_path / "src"
    src.mkdir()
    header = src / "api.h"
    header.write_text("void f();\n", encoding="utf-8")

    side = InputSpec(path=src / "lib.so", sources=src, build_targets=("//:math",))
    evidence = SideEvidence(
        headers=[header],
        compile=CompileContext(),
        collect_mode="build",
        dump_manifest=None,
    )
    includes, ctx, applied, cleanups = _seeded_includes_and_compile_context(
        side, evidence
    )
    assert captured["build_config"].targets == ["//:math"]
    # The best-effort try/except inside seed_includes_and_fold_compile_context
    # (same "degrade, never fatal" contract as derive_l2_include_dirs/
    # derive_l2_compile_context) swallows the injected RuntimeError and
    # degrades to the no-op result -- this test only needs the call to have
    # happened with the right build_config, same as its combined-function
    # sibling above.
    assert (includes, ctx, applied, cleanups) == ([], evidence.compile, False, [])


# ── ADR-063 Phase 4 (Codex review): a deliberate usage error must not be ────
# swallowed by the "best-effort" contract above ──────────────────────────────
#
# The RuntimeError test above confirms an ordinary collection failure (a
# missing compiler, a corrupt pack, ...) correctly degrades to a no-op --
# that is this layer's own documented contract (_l2_seed_config's docstring:
# "a malformed/invalid config surfaces loudly elsewhere ... this is a
# best-effort include-dir hint, so it degrades ... rather than raising
# through"). But a Codex review round found that assumption false for one
# specific input: at a depth whose collect_mode is "off" (e.g. `--depth
# headers`), `embed_build_source` never even runs (it no-ops before ever
# calling collect_inline_pack), so this L2 seed's own collect_inline_pack
# call is the ONLY place `_maybe_collect_bazel_build_info`'s ValidationError
# (--build-target/.abicheck.yml `build.targets` vs. a pre-captured Bazel
# jsonproto) can fire at all -- and the broad `except Exception` here
# swallowed it, so the run proceeded with no diagnostic and without the
# build-derived context it should have had. Fixed by carving ValidationError
# out of the broad except, mirroring the pre-existing
# HeaderCompileContextAmbiguousError carve-out for the identical reason: a
# deliberate usage error is not "best-effort collection failed."


def _write_bazel_aquery(tmp_path: Path) -> Path:
    import json

    path = tmp_path / "aquery.json"
    path.write_text(
        json.dumps({"actions": [], "pathFragments": [], "artifacts": [], "targets": []})
    )
    return path


def test_seed_includes_and_fold_compile_context_raises_on_bazel_scoping_mismatch(
    tmp_path: Path,
) -> None:
    """The real (unmocked) reproduction: build_targets sourced only from
    .abicheck.yml (no explicit --build-target), combined with a pre-captured
    Bazel aquery jsonproto -- must raise ValidationError, not silently
    degrade to a no-op with no diagnostic."""
    from abicheck.errors import ValidationError

    src = tmp_path / "src"
    src.mkdir()
    header = src / "api.h"
    header.write_text("void f();\n", encoding="utf-8")
    (src / ".abicheck.yml").write_text(
        "build:\n  system: bazel\n  targets:\n    - //:math\n", encoding="utf-8"
    )
    aquery = _write_bazel_aquery(tmp_path)

    with pytest.raises(ValidationError, match="pre-captured Bazel aquery"):
        seed_includes_and_fold_compile_context(
            headers=[header],
            includes=[],
            sources=src,
            build_info=aquery,
            build_config=None,
            build_query=None,
            build_compile_db=None,
            build_targets=(),
            collect_mode="off",
            gcc_path=None,
            gcc_prefix=None,
            gcc_options=None,
            gcc_option_tokens=(),
            sysroot=None,
            nostdinc=False,
            frontend="auto",
            frontend_context="host",
            lang="c++",
            lang_explicit=False,
            pending_cleanups=[],
        )
