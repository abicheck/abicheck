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

"""``_gated_build_query_inputs`` is the one place a caller's "may this input
resolve an executable ``build.query``" decision is enforced (Codex review,
two rounds). A follow-up round found that blanket-nulling ``build_config``'s
bare *presence* whenever ``allow_build_query`` is not exactly ``True`` was a
real regression for ``scan``: its own CLI-side consent gate
(``cli_scan_helpers.resolve_effective_allow_query``, ADR-037 D4) only ever
authorizes the config's *executable* ``build.query`` field, never its bare
presence -- and ``build_config``'s own query field is already, correctly,
gated downstream (``collect_inline_pack``'s presence-based
``build_config_trusted_for_query``) regardless of this function. So an
ordinary ``scan --config <path>`` whose config sets only passive settings
(``build.compile_db``, ...) and declares no ``build.query`` at all -- the
common case -- silently lost the entire config once this migration started
routing scan through this gate too.

These are primitive-level tests on the gate itself (this repo's own
"Primitive-level property tests" convention) plus one end-to-end test
proving ``scan_engine._build_new_snapshot`` forwards ``build_config``
through ungated even when ``allow_build_query`` is falsy."""

from __future__ import annotations

from pathlib import Path

from abicheck.service_input_resolution import _gated_build_query_inputs


class TestGatedBuildQueryInputsDefault:
    """Default behavior (``build_config_locally_trusted=False``) — unchanged
    from before this parameter existed, matching ``dump``/``compare``'s
    typed-API contract."""

    def test_both_nulled_when_query_not_allowed(self):
        config = Path("/tmp/.abicheck.yml")
        got_config, got_query = _gated_build_query_inputs(
            config, "cmake --build .", allow_build_query=False
        )
        assert got_config is None
        assert got_query is None

    def test_both_pass_through_when_query_allowed(self):
        config = Path("/tmp/.abicheck.yml")
        got_config, got_query = _gated_build_query_inputs(
            config, "cmake --build .", allow_build_query=True
        )
        assert got_config == config
        assert got_query == "cmake --build ."

    def test_none_inputs_stay_none_either_way(self):
        assert _gated_build_query_inputs(None, None, allow_build_query=False) == (
            None,
            None,
        )
        assert _gated_build_query_inputs(None, None, allow_build_query=True) == (
            None,
            None,
        )


class TestGatedBuildQueryInputsLocallyTrustedConfig:
    """``build_config_locally_trusted=True`` — scan's own opt-in, restoring
    its pre-migration ungated pass-through of ``build_config``."""

    def test_config_survives_when_query_not_allowed(self):
        config = Path("/tmp/.abicheck.yml")
        got_config, got_query = _gated_build_query_inputs(
            config,
            None,
            allow_build_query=False,
            build_config_locally_trusted=True,
        )
        assert got_config == config
        assert got_query is None

    def test_query_string_itself_still_gated(self):
        """The bare executable command string has no downstream gate of its
        own (unlike build_config's query *field*, which
        collect_inline_pack's build_config_trusted_for_query enforces) — it
        must stay nulled regardless of build_config_locally_trusted."""
        config = Path("/tmp/.abicheck.yml")
        got_config, got_query = _gated_build_query_inputs(
            config,
            "cmake --build .",
            allow_build_query=False,
            build_config_locally_trusted=True,
        )
        assert got_config == config
        assert got_query is None

    def test_both_pass_through_when_query_also_allowed(self):
        config = Path("/tmp/.abicheck.yml")
        got_config, got_query = _gated_build_query_inputs(
            config,
            "cmake --build .",
            allow_build_query=True,
            build_config_locally_trusted=True,
        )
        assert got_config == config
        assert got_query == "cmake --build ."

    def test_none_config_stays_none(self):
        got_config, got_query = _gated_build_query_inputs(
            None,
            None,
            allow_build_query=False,
            build_config_locally_trusted=True,
        )
        assert got_config is None
        assert got_query is None


def test_scan_build_new_snapshot_forwards_build_config_ungated(monkeypatch, tmp_path):
    """End-to-end: scan_engine._build_new_snapshot must not lose an ordinary
    --config file's passive settings merely because allow_build_query is
    falsy (the common case — cli_scan_helpers.resolve_effective_allow_query
    only ever authorizes a config that itself declares build.query AND an
    explicitly-pinned deep level)."""
    import abicheck.scan_engine as scan_engine
    from abicheck.compile_context import CompileContext
    from abicheck.model import AbiSnapshot

    binary = tmp_path / "lib.so"
    binary.write_bytes(b"")
    config = tmp_path / ".abicheck.yml"
    config.write_text("build:\n  compile_db: compile_commands.json\n")

    captured: dict = {}

    def fake_impl(side, evidence, **kwargs):
        captured.update(kwargs)
        captured["side"] = side
        from abicheck.service_input_resolution import SideResolution

        return SideResolution(
            snapshot=AbiSnapshot(library="lib.so", version="1.0"),
            effective_includes=[],
            effective_compile_context=None,
            baseline_compile_context=None,
        )

    # ADR-061 Phase 3: `scan_engine` imports this from its owner
    # (`workflows.artifact.execute`), so the patch has to land there --
    # patching the `service_input_resolution` facade rebinds a name nothing
    # reads and leaves the real resolver running.
    monkeypatch.setattr(
        "abicheck.workflows.artifact.execute._resolve_side_snapshot_impl", fake_impl
    )

    scan_engine._build_new_snapshot(
        binary,
        headers=(),
        includes=(),
        sources=None,
        build_info=None,
        build_config=config,
        build_targets=(),
        collect_mode="off",
        compile_context=CompileContext(),
        lang="c++",
        public_headers=None,
        public_header_dirs=None,
        symbols_only=False,
        debug_presence_only=False,
        changed_paths=(),
        allow_build_query=False,
        baseline_reuse_hint=None,
        defer_cleanup=None,
        include_dependencies=True,
    )

    assert captured["build_config"] == config
    assert captured["allow_build_query"] is False
    assert captured["build_config_locally_trusted"] is True
