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

"""`dump`'s CLI and its typed `DumpRequest` must resolve one collect mode.

CLI cleanup phase two, PR 3A blocker 5 sub-issue 2. `dump`'s CLI resolves
`--depth` through `cli_dump_helpers.resolve_dump_collect_context`; the typed
`DumpRequest` path resolves it through
`service_compare_evidence.resolve_dump_request_evidence`. They genuinely
disagreed for one input shape (`--build-info` with no `--depth`:
"source-target" on the CLI, "build" through the typed path), which meant the
same invocation attempted L4 replay in one front end and stopped at L3 in the
other. The CLI's default is canonical;
`service_compare_evidence.dump_collect_mode_for` is the typed path's mirror of
it.

The grid below is checked against the *real* CLI resolver rather than a
restatement of its rule, so a future change to either side fails here rather
than diverging silently again.

Deliberately a separate module from `tests/test_dump_cli_typed_api_parity.py`,
which asks the same class of question: every test there is
`pytest.mark.integration` and skips without a real castxml/g++ toolchain, while
this is pure resolution logic that must gate the default fast/PR unit lane.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from abicheck.api_types import DumpRequest, InputSpec

_ALL_DEPTHS: tuple[str | None, ...] = (None, "binary", "headers", "build", "source")


def _cli_collect_mode(
    depth: str | None, *, sources: Path | None, build_info: Path | None
) -> str:
    from abicheck.cli_dump_helpers import resolve_dump_collect_context

    collect_mode, _headers = resolve_dump_collect_context(
        depth, None, sources, build_info, ()
    )
    return collect_mode


def _typed_collect_mode(
    depth: str | None, *, sources: Path | None, build_info: Path | None
) -> str:
    from abicheck.service_compare_evidence import resolve_dump_request_evidence

    request = DumpRequest(
        input=InputSpec.of(Path("libfoo.so"), sources=sources, build_info=build_info),
        depth=depth,
    )
    return resolve_dump_request_evidence(request).collect_mode


class TestCollectModeParity:
    @pytest.mark.parametrize("depth", _ALL_DEPTHS)
    @pytest.mark.parametrize("has_sources", [False, True])
    @pytest.mark.parametrize("has_build_info", [False, True])
    def test_cli_and_typed_agree_for_every_input_shape(
        self, tmp_path: Path, depth: str | None, has_sources: bool, has_build_info: bool
    ) -> None:
        sources = tmp_path / "src" if has_sources else None
        build_info = tmp_path / "compile_commands.json" if has_build_info else None
        cli_mode = _cli_collect_mode(depth, sources=sources, build_info=build_info)
        typed_mode = _typed_collect_mode(depth, sources=sources, build_info=build_info)
        assert cli_mode == typed_mode, (depth, has_sources, has_build_info)

    def test_build_info_without_depth_is_source_target_on_both(
        self, tmp_path: Path
    ) -> None:
        """The one shape that used to disagree, pinned by name.

        Pre-fix the typed side answered "build" here; this is the executable
        record of which default won.
        """
        build_info = tmp_path / "compile_commands.json"
        assert (
            _cli_collect_mode(None, sources=None, build_info=build_info)
            == "source-target"
        )
        assert (
            _typed_collect_mode(None, sources=None, build_info=build_info)
            == "source-target"
        )

    def test_resolved_collect_mode_wins_over_the_depth_derivation(
        self, tmp_path: Path
    ) -> None:
        """`compare`'s implicit-dump path forwards an already-resolved mode.

        Codex review on #814: ``cli_compare_helpers._embed_inline_source_side``
        calls ``ctx.invoke(dump_cmd, ..., _resolved_collect_mode=collect_mode)``
        with no ``depth=`` kwarg at all (Click's own default, ``None``, wins)
        -- the real run's collect mode comes entirely from the pair-resolved
        override, not from ``depth``. A ``DumpRequest`` built for that same
        invocation (e.g. by ``--dry-run``) must record that override rather
        than let ``resolve_dump_request_evidence`` re-derive a *different*
        mode from the absent ``depth`` -- exactly the "preview describes a
        run that never happened" hazard this whole request object exists to
        foreclose.
        """
        from abicheck.service_compare_evidence import resolve_dump_request_evidence

        # depth=None alone would resolve to "source-target" (the test above
        # pins this) -- a value distinct from "build" is what proves the
        # override, not the derivation, decided the outcome.
        request = DumpRequest(
            input=InputSpec.of(Path("libfoo.so")),
            depth=None,
            resolved_collect_mode="build",
        )
        assert resolve_dump_request_evidence(request).collect_mode == "build"

    def test_resolved_collect_mode_rejects_an_unrecognized_value(self) -> None:
        """An out-of-vocabulary override must fail validation, not reach
        `collection_for_ci_mode()`'s own silent ``.get(mode, ())`` fallback.

        Codex review on #814: `resolved_collect_mode` is never user-typed --
        it is always set by another resolver's own already-computed value --
        but a typo or drift in a future caller must still be caught rather
        than silently behaving as "off".
        """
        request = DumpRequest(
            input=InputSpec.of(Path("libfoo.so")),
            depth=None,
            resolved_collect_mode="SOURCE-TARGET",
        )
        errors = request.validation_errors()
        assert any("resolved_collect_mode" in e for e in errors), errors

    def test_resolved_collect_mode_accepts_every_real_ci_mode(self) -> None:
        from abicheck.buildsource.source_replay import CI_MODE_TO_SCOPE

        for mode in CI_MODE_TO_SCOPE:
            request = DumpRequest(
                input=InputSpec.of(Path("libfoo.so")),
                depth=None,
                resolved_collect_mode=mode,
            )
            errors = [e for e in request.validation_errors() if "resolved_collect_mode" in e]
            assert errors == [], (mode, errors)

    def test_source_only_binary_depth_is_rejected(self, tmp_path: Path) -> None:
        """Mirror `dump_cmd`'s own `so_path is None and depth == "binary"` check.

        Codex review on #814: a source-only request (`InputSpec.path is
        None`, allowed since PR 3A blocker 5) has no native artifact to
        report `--depth binary` evidence from -- the CLI already raises a
        `UsageError` for this exact shape, but the typed preflight silently
        approved it (a resolved, empty, `collect_mode="off"` request) before
        this fix. Lives here rather than in `test_typed_dump_request.py`,
        which is at the AI-readiness 2000-line hard cap.
        """
        spec = InputSpec.of(path=None, sources=tmp_path)
        errors = DumpRequest(input=spec, depth="binary").validation_errors()
        assert any("--depth binary requires a native artifact" in e for e in errors), errors

    def test_source_only_non_binary_depth_is_unaffected(self, tmp_path: Path) -> None:
        spec = InputSpec.of(path=None, sources=tmp_path)
        for depth in ("headers", "build", "source"):
            errors = DumpRequest(input=spec, depth=depth).validation_errors()
            assert not any("--depth binary" in e for e in errors), (depth, errors)

    def test_binary_depth_with_a_real_path_is_unaffected(self, tmp_path: Path) -> None:
        # The ordinary, valid case this check must never touch.
        so_path = tmp_path / "libfoo.so"
        so_path.write_bytes(b"")
        request = DumpRequest(input=InputSpec.of(so_path), depth="binary")
        assert request.validation_errors() == []

    def test_compare_keeps_its_own_inference_rule(self, tmp_path: Path) -> None:
        """`compare`'s rule is deliberately NOT changed by the dump fix.

        `collect_mode_for` mirrors `cli_compare_helpers.
        _resolve_compare_collect_mode`, where an omitted depth is *inferred*
        from the supplied inputs and defaults to "off" -- correct for that
        front end, and a different question from `dump`'s fixed default.
        """
        from abicheck.service_compare_evidence import collect_mode_for

        side = InputSpec.of(Path("libfoo.so"))
        assert collect_mode_for(None, side, side) == "off"
        with_build = InputSpec.of(
            Path("libfoo.so"), build_info=tmp_path / "compile_commands.json"
        )
        assert collect_mode_for(None, side, with_build) == "build"
