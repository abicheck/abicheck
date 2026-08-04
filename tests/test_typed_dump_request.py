# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""G33 Phase 5: ``dump``'s typed request, and the MCP tool parity it unlocks.

Three things are pinned here:

* :class:`DumpRequest` validates by the same rules :class:`CompareRequest`
  does — same messages, so a mistake reads identically whichever command a
  caller reached for;
* :func:`run_dump_request` really applies the four steps that previously
  existed only inside ``cli.py``'s ``dump_cmd`` (collect-mode inference,
  inline build/source embedding, dependency walk, the depth floor);
* the phase's own gate — ``abi_dump``/``abi_scan``'s MCP parameter sets are a
  superset of ``abi_compare``'s for every concept the commands share.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from abicheck.api_types import CompareRequest, DumpRequest, InputSpec
from abicheck.compile_context import CompileContext
from abicheck.errors import SnapshotError, ValidationError
from abicheck.model import AbiSnapshot, Function
from abicheck.serialization import snapshot_to_json


def _snapshot(version: str = "1.0") -> AbiSnapshot:
    return AbiSnapshot(
        library="libfoo.so.1",
        version=version,
        functions=[Function(name="foo", mangled="foo", return_type="void", params=[])],
    )


@pytest.fixture()
def snap_path(tmp_path: Path) -> Path:
    p = tmp_path / "lib.abi.json"
    p.write_text(snapshot_to_json(_snapshot()), encoding="utf-8")
    return p


def _write_yaml(directory: Path, name: str, body: str) -> Path:
    path = directory / name
    path.write_text(body, encoding="utf-8")
    return path


# ===================================================================
# DumpRequest validation
# ===================================================================


class TestDumpRequestValidation:
    def test_defaults_are_valid(self, snap_path: Path):
        assert DumpRequest(input=InputSpec(path=snap_path)).validation_errors() == []

    @pytest.mark.parametrize(
        ("kwargs", "fragment"),
        [
            ({"lang": "rust"}, "unsupported language 'rust'"),
            ({"frontend": "gcc"}, "unsupported AST frontend 'gcc'"),
            ({"debug_format": "stabs"}, "unsupported debug format 'stabs'"),
            ({"depth": "everything"}, "unsupported depth 'everything'"),
            ({"frontend_context": "gpu"}, "unsupported frontend context 'gpu'"),
        ],
    )
    def test_value_errors_match_compare_request(
        self, snap_path: Path, kwargs: dict, fragment: str
    ):
        """The same bad value must produce the same message on both requests.

        This is the point of routing both through one set of module-level
        helpers: front-end parity (ADR-037 D9) stopped at "CLI vs MCP" and now
        also covers "dump vs compare".
        """
        dump_errors = DumpRequest(
            input=InputSpec(path=snap_path), **kwargs
        ).validation_errors()
        compare_errors = CompareRequest(
            old=InputSpec(path=snap_path), new=InputSpec(path=snap_path), **kwargs
        ).validation_errors()
        assert any(fragment in e for e in dump_errors), dump_errors
        assert set(dump_errors) <= set(compare_errors)

    def test_android_frontend_needs_source_inputs(self, snap_path: Path):
        errors = DumpRequest(
            input=InputSpec(path=snap_path), frontend="android"
        ).validation_errors()
        assert any("source-ABI only" in e for e in errors)

    @pytest.mark.parametrize("field", ["sources", "build_info"])
    def test_android_frontend_satisfied_by_either_evidence_field(
        self, snap_path: Path, tmp_path: Path, field: str
    ):
        spec = InputSpec.of(snap_path, **{field: tmp_path})
        assert DumpRequest(input=spec, frontend="android").validation_errors() == []

    def test_android_frontend_satisfied_by_has_sources(self, snap_path: Path):
        request = DumpRequest(
            input=InputSpec(path=snap_path), frontend="android", has_sources=True
        )
        assert request.validation_errors() == []

    def test_dump_manifest_and_headers_are_mutually_exclusive(
        self, snap_path: Path, tmp_path: Path
    ):
        hdr = tmp_path / "api.h"
        hdr.write_text("int foo(void);\n", encoding="utf-8")
        spec = InputSpec(
            path=snap_path,
            headers=(hdr,),
            dump_manifest=object(),  # type: ignore[arg-type]
        )
        errors = DumpRequest(input=spec).validation_errors()
        assert any("mutually exclusive" in e and "input side" in e for e in errors)

    def test_per_input_compile_frontend_context_is_validated(self, snap_path: Path):
        spec = InputSpec(path=snap_path, compile=CompileContext(frontend_context="gpu"))
        errors = DumpRequest(input=spec).validation_errors()
        assert any("unsupported input frontend context 'gpu'" in e for e in errors)

    def test_validate_raises_with_every_problem_joined(self, snap_path: Path):
        request = DumpRequest(
            input=InputSpec(path=snap_path), lang="rust", depth="deep"
        )
        with pytest.raises(ValidationError) as exc:
            request.validate()
        assert "unsupported language" in str(exc.value)
        assert "unsupported depth" in str(exc.value)

    def test_validate_returns_self_for_inline_use(self, snap_path: Path):
        request = DumpRequest(input=InputSpec(path=snap_path))
        assert request.validate() is request

    def test_replace_is_additive(self, snap_path: Path):
        request = DumpRequest(input=InputSpec(path=snap_path))
        assert request.replace(depth="headers").depth == "headers"
        assert request.depth is None

    def test_case_insensitive_values_accepted_like_compare(self, snap_path: Path):
        request = DumpRequest(
            input=InputSpec(path=snap_path),
            lang="C++",
            frontend="CastXML",
            debug_format="DWARF",
            depth="HEADERS",
            frontend_context="DEVICE",
        )
        assert request.validation_errors() == []


# ===================================================================
# run_dump_request
# ===================================================================


class TestRunDumpRequest:
    def test_resolves_a_snapshot_input(self, snap_path: Path):
        from abicheck.service import run_dump_request

        snap = run_dump_request(DumpRequest(input=InputSpec(path=snap_path)))
        assert snap.library == "libfoo.so.1"
        assert [f.name for f in snap.functions] == ["foo"]

    def test_validation_runs_before_any_resolution(self, snap_path: Path, monkeypatch):
        from abicheck import service

        def _boom(*args, **kwargs):  # pragma: no cover - must never run
            raise AssertionError("resolve_input reached despite an invalid request")

        monkeypatch.setattr(service, "resolve_input", _boom)
        with pytest.raises(ValidationError):
            service.run_dump_request(
                DumpRequest(input=InputSpec(path=snap_path), lang="rust")
            )

    def test_explicit_depth_is_a_floor_not_a_hint(self, snap_path: Path):
        """A symbols-only snapshot cannot satisfy ``depth='build'``.

        The Tier-2 twin of ``dump --depth``'s ``DumpDepthNotSatisfiedError``:
        silently returning weaker evidence is a lie a downstream baseline
        consumer cannot detect.
        """
        from abicheck.service import run_dump_request

        with pytest.raises(ValidationError, match="only reached 'binary'"):
            run_dump_request(
                DumpRequest(input=InputSpec(path=snap_path), depth="build")
            )

    def test_binary_depth_is_always_satisfied(self, snap_path: Path):
        from abicheck.service import run_dump_request

        snap = run_dump_request(
            DumpRequest(input=InputSpec(path=snap_path), depth="binary")
        )
        assert snap.library == "libfoo.so.1"

    def test_binary_depth_clears_headers_before_resolving(
        self, snap_path: Path, tmp_path: Path, monkeypatch
    ):
        """``depth='binary'`` must not still run L2 on carried-over headers."""
        from abicheck import service

        hdr = tmp_path / "api.h"
        hdr.write_text("int foo(void);\n", encoding="utf-8")
        captured: dict[str, object] = {}
        original = service.resolve_input

        def _spy(path, headers=None, includes=None, version="", lang="c++", **kwargs):
            captured["headers"] = list(headers or [])
            captured["public_headers"] = list(kwargs.get("public_headers") or [])
            return original(path, headers, includes, version, lang, **kwargs)

        monkeypatch.setattr(service, "resolve_input", _spy)
        service.run_dump_request(
            DumpRequest(input=InputSpec(path=snap_path, headers=(hdr,)), depth="binary")
        )
        assert captured["headers"] == []
        # The public-header sets are cleared too: a headerless dump still
        # fingerprints them, so leaving them populated would make two otherwise
        # identical snapshots disagree on scope.
        assert captured["public_headers"] == []

    def test_headers_double_as_the_public_header_set(
        self, snap_path: Path, tmp_path: Path, monkeypatch
    ):
        from abicheck import service

        hdr = tmp_path / "api.h"
        hdr.write_text("int foo(void);\n", encoding="utf-8")
        pub_dir = tmp_path / "include"
        pub_dir.mkdir()
        captured: dict[str, object] = {}
        original = service.resolve_input

        def _spy(path, headers=None, includes=None, version="", lang="c++", **kwargs):
            captured["public_headers"] = list(kwargs.get("public_headers") or [])
            captured["public_header_dirs"] = list(
                kwargs.get("public_header_dirs") or []
            )
            return original(path, headers, includes, version, lang, **kwargs)

        monkeypatch.setattr(service, "resolve_input", _spy)
        service.run_dump_request(
            DumpRequest(
                input=InputSpec(
                    path=snap_path, headers=(hdr,), public_header_dirs=(pub_dir,)
                )
            )
        )
        assert captured["public_headers"] == [hdr]
        assert captured["public_header_dirs"] == [pub_dir]

    def test_compile_context_and_frontend_reach_resolve_input(
        self, snap_path: Path, monkeypatch
    ):
        from abicheck import service

        captured: dict[str, object] = {}
        original = service.resolve_input

        def _spy(path, headers=None, includes=None, version="", lang="c++", **kwargs):
            captured["compile"] = kwargs.get("compile")
            captured["header_backend"] = kwargs.get("header_backend")
            captured["dwarf_only"] = kwargs.get("dwarf_only")
            captured["include_dependencies"] = kwargs.get("include_dependencies")
            return original(path, headers, includes, version, lang, **kwargs)

        monkeypatch.setattr(service, "resolve_input", _spy)
        service.run_dump_request(
            DumpRequest(
                input=InputSpec(
                    path=snap_path,
                    compile=CompileContext(sysroot=Path("/opt/sysroot")),
                    include_dependencies=False,
                ),
                frontend="clang",
                dwarf_only=True,
                frontend_context="device",
            )
        )
        compile_ctx = captured["compile"]
        assert isinstance(compile_ctx, CompileContext)
        assert compile_ctx.sysroot == Path("/opt/sysroot")
        # The request-level frontend_context is merged onto an input whose own
        # value reads as the class default.
        assert compile_ctx.frontend_context == "device"
        assert captured["header_backend"] == "clang"
        assert captured["dwarf_only"] is True
        assert captured["include_dependencies"] is False

    def test_debug_format_auto_becomes_none(self, snap_path: Path, monkeypatch):
        """ "auto" *is* "no format forced" at the extraction layer."""
        from abicheck import service

        captured: dict[str, object] = {}
        original = service.resolve_input

        def _spy(path, headers=None, includes=None, version="", lang="c++", **kwargs):
            captured["debug_format"] = kwargs.get("debug_format")
            return original(path, headers, includes, version, lang, **kwargs)

        monkeypatch.setattr(service, "resolve_input", _spy)
        service.run_dump_request(
            DumpRequest(input=InputSpec(path=snap_path), debug_format="AUTO")
        )
        assert captured["debug_format"] is None

    def test_debug_format_rejected_for_non_elf_input(
        self, snap_path: Path, monkeypatch
    ):
        from abicheck import service

        monkeypatch.setattr(service, "detect_binary_format", lambda _p: "pe")
        with pytest.raises(ValidationError, match="only supported for ELF"):
            service.run_dump_request(
                DumpRequest(input=InputSpec(path=snap_path), debug_format="dwarf")
            )

    def test_collect_mode_inferred_from_build_info(
        self, tmp_path: Path, snap_path: Path
    ):
        """``depth`` omitted, ``build_info`` set → the "build" collect mode.

        Asserted against the shared resolver rather than through a real
        extraction: the inference is the behaviour, the extractor is not.
        """
        from abicheck.service_compare_evidence import resolve_dump_request_evidence

        evidence = resolve_dump_request_evidence(
            DumpRequest(input=InputSpec(path=snap_path, build_info=tmp_path))
        )
        assert evidence.collect_mode != "off"

    def test_collect_mode_off_without_any_evidence(self, snap_path: Path):
        from abicheck.service_compare_evidence import resolve_dump_request_evidence

        evidence = resolve_dump_request_evidence(
            DumpRequest(input=InputSpec(path=snap_path))
        )
        assert evidence.collect_mode == "off"

    def test_no_pair_wide_dialect_override_for_a_lone_dump(
        self, snap_path: Path, tmp_path: Path
    ):
        """A single dump has no second side to agree with on a C++ standard.

        ``pair_wide_cxx20_std_override`` exists so two *compared* sides cannot
        silently diverge; applying it to one dump would change what ``dump``
        produces today for no benefit, so the resolved compile context stays
        whatever the input asked for.
        """
        from abicheck.service_compare_evidence import resolve_dump_request_evidence

        hdr = tmp_path / "api.h"
        hdr.write_text("template <class T> concept Any = true;\n", encoding="utf-8")
        evidence = resolve_dump_request_evidence(
            DumpRequest(input=InputSpec(path=snap_path, headers=(hdr,)))
        )
        assert evidence.compile is None

    def test_follow_dependencies_is_opt_in(self, snap_path: Path, monkeypatch):
        from abicheck import dependency_info, service

        calls: list[object] = []
        monkeypatch.setattr(
            dependency_info,
            "populate_side_dependency_info",
            lambda *a, **k: calls.append(a),
        )
        service.run_dump_request(DumpRequest(input=InputSpec(path=snap_path)))
        assert calls == []

    def test_follow_dependencies_forwards_search_paths(
        self, snap_path: Path, monkeypatch
    ):
        from abicheck import dependency_info, service

        captured: dict[str, object] = {}

        def _fake(snap, side, fmt, search_paths, ld_library_path):
            captured["search_paths"] = list(search_paths)
            captured["ld_library_path"] = ld_library_path

        monkeypatch.setattr(dependency_info, "populate_side_dependency_info", _fake)
        service.run_dump_request(
            DumpRequest(
                input=InputSpec(path=snap_path),
                follow_dependencies=True,
                dependency_search_paths=(Path("/opt/lib"),),
                ld_library_path="/usr/lib",
            )
        )
        assert captured["search_paths"] == [Path("/opt/lib")]
        assert captured["ld_library_path"] == "/usr/lib"


# ===================================================================
# The Phase 5 gate, as an executable check
# ===================================================================


def _params(func) -> set[str]:
    return set(inspect.signature(func).parameters)


#: Concepts ``abi_compare`` names differently because it has two inputs, or
#: that exist only because it renders and gates a comparison. Everything else
#: in its signature is a concept ``dump``/``scan`` share and must therefore be
#: reachable on those tools too.
_COMPARE_ONLY_PARAMS = {
    # two-sided input naming
    "old_input",
    "new_input",
    "old_headers",
    "new_headers",
    # report rendering — a dump emits a snapshot, a scan its own report
    "output_format",
    "show_only",
    "report_mode",
    "show_impact",
    "stat",
    # gating a comparison's exit code
    "severity_preset",
    "severity_abi_breaking",
    "severity_potential_breaking",
    "severity_quality_issues",
    "severity_addition",
    # ADR-043 app scoping and ADR-050's comparability escape hatch: both are
    # properties of comparing two things
    "used_by",
    "required_symbols",
    "diagnostic_comparison",
}


class TestPhase5ParityGate:
    """G33 Phase 5's gate: ``abi_dump``/``abi_scan`` are supersets of ``abi_compare``.

    Written as a signature check rather than prose because that is the failure
    this phase exists to prevent recurring — ``abi_dump`` sat at a five-argument
    subset of ``abicheck dump`` for several releases without anything noticing.
    """

    def test_abi_scan_covers_every_shared_compare_concept(self):
        from abicheck.mcp_server import abi_compare, abi_scan

        shared = _params(abi_compare) - _COMPARE_ONLY_PARAMS
        missing = shared - _params(abi_scan) - {"headers", "include_dirs", "language"}
        # `binary_path`/`headers`/`include_dirs`/`language` are present under
        # scan's own spelling; everything else must match name-for-name.
        assert missing == set(), f"abi_scan is missing: {sorted(missing)}"

    def test_abi_dump_reaches_every_evidence_concept_the_phase_names(self):
        from abicheck.mcp_server import abi_dump

        params = _params(abi_dump)
        for name in (
            "depth",
            "sources",
            "build_info",
            "dump_manifest",
            "ast_frontend",
            "gcc_path",
            "gcc_prefix",
            "gcc_options",
            "sysroot",
            "nostdinc",
            "frontend_context",
            "public_header_dirs",
            "include_dependencies",
            "dwarf_only",
            "debug_format",
        ):
            assert name in params, f"abi_dump is missing {name}"

    def test_compile_context_family_is_identical_across_dump_and_scan(self):
        """ADR-037 D3 parity, on the MCP surface: one family, both tools."""
        from abicheck.mcp_server import abi_dump, abi_scan

        family = {
            "ast_frontend",
            "gcc_path",
            "gcc_prefix",
            "gcc_options",
            "sysroot",
            "nostdinc",
            "frontend_context",
        }
        assert family <= _params(abi_dump)
        assert family <= _params(abi_scan)


# ===================================================================
# The MCP tools' own forwarding
# ===================================================================


class TestAbiDumpForwarding:
    def test_new_arguments_reach_the_typed_request(
        self, snap_path: Path, tmp_path: Path, monkeypatch
    ):
        from abicheck import service
        from abicheck.mcp_server import abi_dump

        pub = tmp_path / "include"
        pub.mkdir()
        captured: dict[str, object] = {}

        def _fake(request, **kwargs):
            captured["request"] = request
            return _snapshot()

        monkeypatch.setattr(service, "run_dump_request", _fake)
        raw = abi_dump(
            str(snap_path),
            public_header_dirs=[str(pub)],
            depth="binary",
            include_dependencies=False,
            dwarf_only=True,
            ast_frontend="clang",
            gcc_options="-std=c++17",
            nostdinc=True,
            frontend_context="device",
        )
        assert json.loads(raw)["status"] == "ok"
        request = captured["request"]
        assert isinstance(request, DumpRequest)
        assert request.depth == "binary"
        assert request.dwarf_only is True
        assert request.frontend == "clang"
        assert request.frontend_context == "device"
        assert request.input.public_header_dirs == (pub.resolve(),)
        assert request.input.include_dependencies is False
        assert request.input.compile is not None
        assert request.input.compile.gcc_options == "-std=c++17"
        assert request.input.compile.nostdinc is True
        # The linker-script size guard is a request field, not a wrapper concern.
        assert request.input.follow_linker_scripts is False

    def test_untouched_arguments_leave_the_compile_context_unset(
        self, snap_path: Path, monkeypatch
    ):
        """A caller passing none of the new knobs reaches exactly the old resolution."""
        from abicheck import service
        from abicheck.mcp_server import abi_dump

        captured: dict[str, object] = {}

        def _fake(request, **kwargs):
            captured["request"] = request
            return _snapshot()

        monkeypatch.setattr(service, "run_dump_request", _fake)
        abi_dump(str(snap_path))
        request = captured["request"]
        assert isinstance(request, DumpRequest)
        assert request.input.compile is None
        assert request.depth is None
        assert request.input.sources is None and request.input.build_info is None

    def test_unknown_depth_is_a_usage_error(self, snap_path: Path):
        from abicheck.mcp_server import abi_dump

        data = json.loads(abi_dump(str(snap_path), depth="everything"))
        assert data["status"] == "error"
        assert "Unknown depth" in data["error"]

    @pytest.mark.parametrize("depth", ["full", "symbols", "graph"])
    def test_internal_depth_vocabulary_does_not_leak(self, snap_path: Path, depth: str):
        """ADR-043 D2: the public ladder is exactly four rungs, on every tool."""
        from abicheck.mcp_server import abi_dump

        data = json.loads(abi_dump(str(snap_path), depth=depth))
        assert data["status"] == "error"

    def test_public_header_dir_must_be_a_directory(
        self, snap_path: Path, tmp_path: Path
    ):
        from abicheck.mcp_server import abi_dump

        umbrella = tmp_path / "all.hpp"
        umbrella.write_text("// umbrella\n", encoding="utf-8")
        data = json.loads(abi_dump(str(snap_path), public_header_dirs=[str(umbrella)]))
        assert data["status"] == "error"
        assert "must be an existing directory" in data["error"]

    def test_unreached_depth_is_reported_as_an_error_payload(self, snap_path: Path):
        """The depth floor surfaces as this tool's structured error, not a crash."""
        from abicheck.mcp_server import abi_dump

        data = json.loads(abi_dump(str(snap_path), depth="build"))
        assert data["status"] == "error"


class TestAbiScanForwarding:
    def test_new_arguments_reach_the_scan_request(
        self, snap_path: Path, tmp_path: Path, monkeypatch
    ):
        from abicheck import service
        from abicheck.mcp_server import abi_scan

        policy_file = tmp_path / "policy.yml"
        policy_file.write_text("overrides: {}\n", encoding="utf-8")
        build_dir = tmp_path / "build"
        build_dir.mkdir()
        captured: dict[str, object] = {}

        def _fake_subprocess(req, timeout):
            captured["req"] = req
            return {"verdict": "COMPATIBLE", "exit_code": 0}

        monkeypatch.setattr(service, "run_scan_subprocess", _fake_subprocess)
        raw = abi_scan(
            str(snap_path),
            # These are documented "With ``against``" arguments, and an
            # audit-only call now rejects them up front.
            against=str(snap_path),
            build_info=str(build_dir),
            policy="sdk_vendor",
            policy_file=str(policy_file),
            contract_evaluation=True,
            contract_mode="exports",
            ast_frontend="castxml",
            gcc_prefix="aarch64-linux-gnu-",
            frontend_context="device",
        )
        assert json.loads(raw)["status"] == "ok"
        req = captured["req"]
        assert req.build_info == build_dir.resolve()
        assert req.policy == "sdk_vendor"
        assert req.policy_file is not None
        assert req.contract_evaluation is True
        assert req.contract_mode == "exports"
        assert req.compile.frontend == "castxml"
        assert req.compile.gcc_prefix == "aarch64-linux-gnu-"
        assert req.compile.frontend_context == "device"

    def test_suppression_file_is_loaded_once_by_the_service(
        self, snap_path: Path, tmp_path: Path, monkeypatch
    ):
        from abicheck import service
        from abicheck.mcp_server import abi_scan

        suppress = tmp_path / "suppress.yml"
        suppress.write_text(
            "version: 1\nrules:\n  - symbol: foo\n    reason: known\n", encoding="utf-8"
        )
        captured: dict[str, object] = {}

        def _fake_subprocess(req, timeout):
            captured["req"] = req
            return {"verdict": "COMPATIBLE"}

        monkeypatch.setattr(service, "run_scan_subprocess", _fake_subprocess)
        abi_scan(str(snap_path), against=str(snap_path), suppression_file=str(suppress))
        assert captured["req"].suppression is not None

    def test_unknown_policy_is_a_usage_error(self, snap_path: Path):
        from abicheck.mcp_server import abi_scan

        data = json.loads(abi_scan(str(snap_path), policy="nonsense"))
        assert data["status"] == "error"
        assert "Unknown policy" in data["error"]

    def test_policy_file_overrides_the_policy_name_check(
        self, snap_path: Path, tmp_path: Path, monkeypatch
    ):
        """A custom policy file supersedes the base name, as on ``abi_compare``."""
        from abicheck import service
        from abicheck.mcp_server import abi_scan

        policy_file = tmp_path / "policy.yml"
        policy_file.write_text("overrides: {}\n", encoding="utf-8")
        monkeypatch.setattr(
            service,
            "run_scan_subprocess",
            lambda req, timeout: {"verdict": "COMPATIBLE"},
        )
        data = json.loads(
            abi_scan(
                str(snap_path),
                against=str(snap_path),
                policy="nonsense",
                policy_file=str(policy_file),
            )
        )
        assert data["status"] == "ok"


class TestContractModeUsageRules:
    """One implementation of ``--contract``'s two rules, three tools."""

    @pytest.mark.parametrize("tool", ["abi_compare", "abi_scan"])
    def test_contract_mode_requires_contract_evaluation(
        self, snap_path: Path, tool: str
    ):
        import abicheck.mcp_server as mcp_server

        func = getattr(mcp_server, tool)
        args = (
            (str(snap_path), str(snap_path))
            if tool == "abi_compare"
            else (str(snap_path),)
        )
        data = json.loads(func(*args, contract_mode="public"))
        assert data["status"] == "error"
        assert "contract_mode requires contract_evaluation" in data["error"]

    @pytest.mark.parametrize("tool", ["abi_compare", "abi_scan"])
    def test_unknown_contract_mode_is_rejected(self, snap_path: Path, tool: str):
        import abicheck.mcp_server as mcp_server

        func = getattr(mcp_server, tool)
        args = (
            (str(snap_path), str(snap_path))
            if tool == "abi_compare"
            else (str(snap_path),)
        )
        data = json.loads(
            func(*args, contract_evaluation=True, contract_mode="everything")
        )
        assert data["status"] == "error"
        assert "unsupported contract mode" in data["error"]

    def test_contract_mode_reaches_the_compare_request(
        self, snap_path: Path, monkeypatch
    ):
        from abicheck import service
        from abicheck.mcp_server import abi_compare

        captured: dict[str, object] = {}
        original = service.run_compare_request

        def _spy(request):
            captured["contract_mode"] = request.contract_mode
            return original(request)

        monkeypatch.setattr(service, "run_compare_request", _spy)
        abi_compare(
            str(snap_path),
            str(snap_path),
            contract_evaluation=True,
            contract_mode="all",
        )
        assert captured["contract_mode"] == "all"


class TestMissingFileArguments:
    """A missing path names the argument at fault, on both tools.

    ``abi_compare`` gets this from ``CompareRequest.validate()``;
    ``ScanRequest`` has no ``validate()``, so without an explicit check a
    missing file reached the loader and surfaced as a sanitized
    ``FileNotFoundError`` naming nothing.
    """

    def test_missing_dump_manifest(self, snap_path: Path, tmp_path: Path):
        from abicheck.mcp_server import abi_dump

        data = json.loads(
            abi_dump(str(snap_path), dump_manifest=str(tmp_path / "no.yml"))
        )
        assert data["status"] == "error"
        assert "dump_manifest not found" in data["error"]

    @pytest.mark.parametrize("arg", ["policy_file", "suppression_file"])
    def test_missing_scan_config_file(self, snap_path: Path, tmp_path: Path, arg: str):
        from abicheck.mcp_server import abi_scan

        data = json.loads(abi_scan(str(snap_path), **{arg: str(tmp_path / "no.yml")}))
        assert data["status"] == "error"
        assert f"{arg} not found" in data["error"]


class TestAndroidFrontendIsNotAHeaderBackend:
    """``android`` must never reach ``resolve_input`` as the compile context's
    frontend (Codex review, P2).

    It is in ``SUPPORTED_FRONTENDS`` but not ``HEADER_AST_FRONTENDS``: source-ABI
    only, no header-AST path. Both pipelines already map the bare
    ``header_backend`` to ``"auto"`` for it — but ``service._run_dump_uncached``
    gives an explicit ``compile.frontend`` *precedence* over that argument, and
    ``dumper._resolve_header_backend`` raises for anything outside
    castxml/clang/hybrid/auto. So a resolved context still carrying "android"
    failed the whole extraction with "Unknown AST frontend 'android'", before
    any build/source evidence was embedded.
    """

    def _spy_on_compile_context(self, monkeypatch) -> dict[str, object]:
        """Record the ``compile.frontend`` that actually reaches ``resolve_input``."""
        from abicheck import service

        captured: dict[str, object] = {}
        original = service.resolve_input

        def _spy(path, headers=None, includes=None, version="", lang="c++", **kwargs):
            ctx = kwargs.get("compile")
            captured["frontend"] = None if ctx is None else ctx.frontend
            return original(path, headers, includes, version, lang, **kwargs)

        monkeypatch.setattr(service, "resolve_input", _spy)
        return captured

    def test_dump_request_downgrades_android_to_auto(
        self, snap_path: Path, tmp_path: Path, monkeypatch
    ):
        from abicheck import service

        captured = self._spy_on_compile_context(monkeypatch)
        service.run_dump_request(
            DumpRequest(
                input=InputSpec(
                    path=snap_path,
                    build_info=tmp_path,
                    compile=CompileContext(frontend="android"),
                ),
                frontend="android",
            )
        )
        assert captured["frontend"] == "auto"

    def test_compare_request_downgrades_android_to_auto(
        self, snap_path: Path, tmp_path: Path, monkeypatch
    ):
        """The same defect existed for a typed ``compare`` caller, which is why
        the fix lives in the shared resolution rather than at either caller."""
        from abicheck import service
        from abicheck.api_types import CompareRequest

        captured = self._spy_on_compile_context(monkeypatch)
        side = InputSpec(
            path=snap_path,
            build_info=tmp_path,
            compile=CompileContext(frontend="android"),
        )
        service.run_compare_request(
            CompareRequest(old=side, new=side, frontend="android")
        )
        assert captured["frontend"] == "auto"

    def test_a_real_header_frontend_is_left_alone(self, snap_path: Path, monkeypatch):
        from abicheck import service

        captured = self._spy_on_compile_context(monkeypatch)
        service.run_dump_request(
            DumpRequest(
                input=InputSpec(
                    path=snap_path, compile=CompileContext(frontend="clang")
                ),
                frontend="clang",
            )
        )
        assert captured["frontend"] == "clang"

    def test_android_reaches_the_tool_without_failing(
        self, snap_path: Path, tmp_path: Path
    ):
        """End to end: `abi_dump(ast_frontend="android", ...)` used to fail."""
        from abicheck.mcp_server import abi_dump

        data = json.loads(
            abi_dump(str(snap_path), ast_frontend="android", build_info=str(tmp_path))
        )
        assert data["status"] == "ok"


class TestEvidencePathsMustExist:
    """A nonexistent evidence path is a usage error, not a weaker result.

    ``sources``/``build_info``/``compile_db`` infer a collect mode from being
    *set*, and only an explicit ``depth`` arms the floor — so a typo used to
    collect nothing and still answer ``status: "ok"`` (Codex review, P1). The
    CLI declares all of these ``click.Path(exists=True)``.
    """

    @pytest.mark.parametrize("arg", ["sources", "build_info"])
    def test_abi_dump_rejects_a_missing_path(
        self, snap_path: Path, tmp_path: Path, arg: str
    ):
        from abicheck.mcp_server import abi_dump

        data = json.loads(abi_dump(str(snap_path), **{arg: str(tmp_path / "nope")}))
        assert data["status"] == "error"
        assert f"{arg} not found" in data["error"]

    @pytest.mark.parametrize("arg", ["sources", "build_info", "compile_db", "against"])
    def test_abi_scan_rejects_a_missing_path(
        self, snap_path: Path, tmp_path: Path, arg: str
    ):
        from abicheck.mcp_server import abi_scan

        data = json.loads(abi_scan(str(snap_path), **{arg: str(tmp_path / "nope")}))
        assert data["status"] == "error"
        assert f"{arg} not found" in data["error"]

    def test_an_existing_path_still_resolves(
        self, snap_path: Path, tmp_path: Path, monkeypatch
    ):
        from abicheck import service
        from abicheck.mcp_server import abi_dump

        captured: dict[str, object] = {}

        def _fake(request, **kwargs):
            captured["build_info"] = request.input.build_info
            return _snapshot()

        monkeypatch.setattr(service, "run_dump_request", _fake)
        data = json.loads(abi_dump(str(snap_path), build_info=str(tmp_path)))
        assert data["status"] == "ok"
        assert captured["build_info"] == tmp_path.resolve()


class TestCompileContextArgumentValidation:
    """The compile-context knobs are validated where both tools assemble them.

    ``abi_dump`` builds a ``DumpRequest`` whose ``validate()`` would catch a
    typo, but ``abi_scan`` copies straight into a ``ScanRequest``, which has no
    ``validate()`` — so a misspelled frontend or an uppercased ``"DEVICE"``
    survived into the spawned scan worker (Codex review, second round).
    """

    def test_normalizes_frontend_context_case(self):
        from abicheck.mcp_server_inputs import _compile_context_from_args

        ctx = _compile_context_from_args(frontend_context="DEVICE")
        assert ctx is not None
        assert ctx.frontend_context == "device"

    def test_downgrades_a_non_header_ast_frontend(self):
        """``android`` is a legal ``--ast-frontend`` but not a header backend.

        A ``ScanRequest`` has no request-level frontend field to carry it for
        L4, and ``_resolve_header_backend`` raises on it — so the compile
        context gets ``auto``. Here that leaves the context entirely default,
        which is the right answer: nothing was customised.
        """
        from abicheck.mcp_server_inputs import _compile_context_from_args

        assert _compile_context_from_args(ast_frontend="android") is None
        ctx = _compile_context_from_args(ast_frontend="android", nostdinc=True)
        assert ctx is not None
        assert ctx.frontend == "auto"

    @pytest.mark.parametrize(
        ("kwargs", "fragment"),
        [
            ({"ast_frontend": "castxmll"}, "unsupported AST frontend 'castxmll'"),
            ({"frontend_context": "gpu"}, "unsupported frontend context 'gpu'"),
        ],
    )
    def test_rejects_bad_values(self, kwargs: dict, fragment: str):
        from abicheck.mcp_server_inputs import _compile_context_from_args

        with pytest.raises(ValueError, match=fragment):
            _compile_context_from_args(**kwargs)

    @pytest.mark.parametrize("tool", ["abi_dump", "abi_scan"])
    @pytest.mark.parametrize(
        ("kwargs", "fragment"),
        [
            ({"ast_frontend": "castxmll"}, "unsupported AST frontend"),
            ({"frontend_context": "gpu"}, "unsupported frontend context"),
        ],
    )
    def test_both_tools_report_the_same_usage_error(
        self, snap_path: Path, tool: str, kwargs: dict, fragment: str
    ):
        """Identical text on both surfaces — the message helpers come from
        ``api_types``, so this cannot drift from ``DumpRequest.validate()``."""
        import abicheck.mcp_server as mcp_server

        data = json.loads(getattr(mcp_server, tool)(str(snap_path), **kwargs))
        assert data["status"] == "error"
        assert fragment in data["error"]


class TestEvidenceFileSizeGuard:
    """``build_info`` may be a file, so it is held to ``MCP_MAX_FILE_SIZE`` too.

    It accepts a ``compile_commands.json`` (or a Bazel jsonproto), not only a
    directory, and the build-source loader parses it — so without this an
    oversized build-info artifact bypassed the limit every other file-shaped
    input is held to (Codex review, second round).
    """

    @pytest.fixture()
    def small_limit(self, monkeypatch):
        """Shrink the limit on the module object the code under test resolves.

        Not ``from abicheck import mcp_shared`` — under some test-selection
        orders the suite ends up with two distinct ``abicheck.mcp_shared``
        module objects, so patching the one *this file* imports can leave the
        one ``mcp_server_inputs`` holds untouched, and the guard silently
        reads the original 500 MB. Reaching through the module under test is
        identity-proof.
        """
        from abicheck import mcp_server_inputs

        monkeypatch.setattr(mcp_server_inputs.mcp_shared, "MCP_MAX_FILE_SIZE", 4096)

    @pytest.mark.parametrize("tool", ["abi_dump", "abi_scan"])
    def test_oversized_build_info_file_is_rejected(
        self, snap_path: Path, tmp_path: Path, small_limit, tool: str
    ):
        import abicheck.mcp_server as mcp_server

        big = tmp_path / "compile_commands.json"
        big.write_text("[]" + " " * 8192, encoding="utf-8")
        data = json.loads(
            getattr(mcp_server, tool)(str(snap_path), build_info=str(big))
        )
        assert data["status"] == "error"
        assert "build_info is" in data["error"] and "exceeds limit" in data["error"]

    def test_a_directory_build_info_is_not_size_checked(
        self, snap_path: Path, tmp_path: Path, small_limit, monkeypatch
    ):
        """A size limit means nothing for a directory — it must not be applied."""
        from abicheck import service
        from abicheck.mcp_server import abi_dump

        build_dir = tmp_path / "build"
        build_dir.mkdir()
        (build_dir / "compile_commands.json").write_text("[]", encoding="utf-8")
        monkeypatch.setattr(
            service, "run_dump_request", lambda request, **kw: _snapshot()
        )
        data = json.loads(abi_dump(str(snap_path), build_info=str(build_dir)))
        assert data["status"] == "ok"


class TestManifestNamedFilesAreSizeChecked:
    """A `dump_manifest`'s own named headers are held to the same limit.

    Only the manifest YAML went through the size guard, but the document
    names headers the dump pipeline then parses — so a tiny manifest could
    point at a file `headers=` would have rejected outright (Codex review).
    The asymmetry is exact: `dump_manifest.py` calls `roots` "the
    manifest-mode equivalent of `--header`/`-H`", and `abi_dump` size-checks
    every `headers=` entry.
    """

    @pytest.fixture()
    def small_limit(self, monkeypatch):
        """Shrink the limit for *both* paths this class compares.

        `TestEvidenceFileSizeGuard`'s fixture patches
        `mcp_server_inputs.mcp_shared`, which is enough for the manifest
        path. It is not enough here: `mcp_server` imports `_check_file_size`
        as a *function*, and that function reads `MCP_MAX_FILE_SIZE` from its
        own module globals — so under a selection order that leaves two
        distinct `abicheck.mcp_shared` objects (the hazard that fixture
        documents), the `headers=` side silently kept the real 500 MB limit
        and this class's whole point, that the two sides agree, passed
        vacuously. Patching the globals dict each call path actually reads is
        identity-proof.
        """
        from abicheck import mcp_server, mcp_server_inputs

        for globals_dict in (
            mcp_server._check_file_size.__globals__,
            mcp_server_inputs.mcp_shared._check_file_size.__globals__,
        ):
            monkeypatch.setitem(globals_dict, "MCP_MAX_FILE_SIZE", 4096)

    def _manifest(self, tmp_path: Path, body: str) -> Path:
        path = tmp_path / "manifest.yaml"
        path.write_text(body, encoding="utf-8")
        return path

    @pytest.fixture()
    def oversized(self, tmp_path: Path) -> Path:
        big = tmp_path / "huge.h"
        big.write_text("//" + "x" * 8192 + "\n", encoding="utf-8")
        return big

    @pytest.mark.parametrize(
        ("body", "expected"),
        [
            ("roots:\n  - {big}\ntranslation_units:\n  - name: tu\n", "root"),
            (
                "roots:\n  - {ok}\ntranslation_units:\n"
                "  - name: tu\n    forced_includes:\n      - {big}\n",
                "forced include (tu)",
            ),
            (
                "roots:\n  - {ok}\npublic_header_paths:\n  - {big}\n"
                "translation_units:\n  - name: tu\n",
                "public header",
            ),
        ],
        ids=["roots", "forced_includes", "public_header_paths"],
    )
    def test_an_oversized_manifest_header_is_rejected(
        self,
        snap_path: Path,
        tmp_path: Path,
        oversized: Path,
        small_limit,
        body: str,
        expected: str,
    ):
        from abicheck.mcp_server import abi_dump

        ok = tmp_path / "ok.h"
        ok.write_text("// small\n", encoding="utf-8")
        manifest = self._manifest(
            tmp_path, body.format(big=oversized, ok=ok)
        )
        data = json.loads(abi_dump(str(snap_path), dump_manifest=str(manifest)))
        assert data["status"] == "error"
        assert f"dump_manifest {expected} is" in data["error"]
        assert "exceeds limit" in data["error"]

    def test_the_same_file_is_rejected_via_headers(
        self, snap_path: Path, oversized: Path, small_limit
    ):
        # The point of the guard is that these two agree; pinning only the
        # manifest side would not catch the two drifting apart again.
        from abicheck.mcp_server import abi_dump

        data = json.loads(abi_dump(str(snap_path), headers=[str(oversized)]))
        assert data["status"] == "error"
        assert "header is" in data["error"] and "exceeds limit" in data["error"]

    def test_directory_valued_manifest_fields_are_not_size_checked(
        self, snap_path: Path, tmp_path: Path, small_limit, monkeypatch
    ):
        """`public_header_dirs` and a TU's `includes` are -I search roots.

        A size limit means nothing for a directory, so applying one there
        would reject a legitimate manifest.
        """
        from abicheck import service
        from abicheck.mcp_server import abi_dump

        ok = tmp_path / "ok.h"
        ok.write_text("// small\n", encoding="utf-8")
        inc = tmp_path / "inc"
        inc.mkdir()
        manifest = self._manifest(
            tmp_path,
            f"roots:\n  - {ok}\npublic_header_dirs:\n  - {inc}\n"
            f"translation_units:\n  - name: tu\n    includes:\n      - {inc}\n",
        )
        monkeypatch.setattr(
            service, "run_dump_request", lambda request, **kw: _snapshot()
        )
        data = json.loads(abi_dump(str(snap_path), dump_manifest=str(manifest)))
        assert data["status"] == "ok"


class TestBuildDerivedIncludeSeeding:
    """A typed request seeds the build's include dirs, as the CLI does.

    With headers plus ``sources``/``build_info`` but no explicit ``includes``,
    the L2 public-header parse cannot see the include dirs the build already
    knows (public headers reaching into a dependency SDK). The CLI has seeded
    these from the build since ADR-033; the typed path did not, so an identical
    request parsed less than the equivalent CLI invocation (Codex review).
    """

    def test_seeds_when_evidence_is_present_and_includes_are_empty(
        self, snap_path: Path, tmp_path: Path, monkeypatch
    ):
        from abicheck import service

        seen: dict[str, object] = {}
        seeded = tmp_path / "generated-include"
        seeded.mkdir()

        def _fake_seed(**kwargs):
            seen["headers"] = list(kwargs["headers"])
            seen["allow_inferred_build_query"] = kwargs["allow_inferred_build_query"]
            return [seeded], []

        monkeypatch.setattr("abicheck.buildsource.l2_seed.seed_l2_includes", _fake_seed)
        captured: dict[str, object] = {}
        original = service.resolve_input

        def _spy(path, headers=None, includes=None, version="", lang="c++", **kwargs):
            captured["includes"] = list(includes or [])
            return original(path, headers, includes, version, lang, **kwargs)

        monkeypatch.setattr(service, "resolve_input", _spy)
        service.run_dump_request(
            DumpRequest(input=InputSpec(path=snap_path, build_info=tmp_path))
        )
        assert captured["includes"] == [seeded]
        # A Tier-2 call must never *execute* a build system as a side effect of
        # resolving an input — passive discovery only.
        assert seen["allow_inferred_build_query"] is False

    def test_no_evidence_means_no_seeding_call(self, snap_path: Path, monkeypatch):
        """Without sources/build_info the seed is skipped entirely."""
        from abicheck import service

        def _boom(**kwargs):  # pragma: no cover - must never run
            raise AssertionError("seed_l2_includes reached without build evidence")

        monkeypatch.setattr("abicheck.buildsource.l2_seed.seed_l2_includes", _boom)
        snap = service.run_dump_request(DumpRequest(input=InputSpec(path=snap_path)))
        assert snap.library == "libfoo.so.1"

    def test_cleanups_run_after_resolution(
        self, snap_path: Path, tmp_path: Path, monkeypatch
    ):
        """Seeded temp dirs are drained only after the parse consumed them."""
        from abicheck import service

        order: list[str] = []
        monkeypatch.setattr(
            "abicheck.buildsource.l2_seed.seed_l2_includes",
            lambda **kw: ([], [lambda: order.append("cleanup")]),
        )
        original = service.resolve_input

        def _spy(path, headers=None, includes=None, version="", lang="c++", **kwargs):
            order.append("resolve")
            return original(path, headers, includes, version, lang, **kwargs)

        monkeypatch.setattr(service, "resolve_input", _spy)
        service.run_dump_request(
            DumpRequest(input=InputSpec(path=snap_path, build_info=tmp_path))
        )
        assert order == ["resolve", "cleanup"]


class TestContractModeReachesTheReceipt:
    """ADR-049 Phase 6/7: the receipt must name the domain that scored the run.

    `abi_compare` gained `contract_mode`, so a receipt still recording the
    built-in default would misreport the domain that produced the verdict and
    the coverage gate — which is exactly what a replay/audit consumer reads it
    for (Codex review).
    """

    def test_selected_mode_is_recorded(self, snap_path: Path, monkeypatch):
        from abicheck import mcp_compare_receipt

        seen: dict[str, object] = {}
        original = mcp_compare_receipt.resolve_tool_config

        def _spy(**kwargs):
            seen["contract_mode"] = kwargs.get("contract_mode")
            return original(**kwargs)

        monkeypatch.setattr(mcp_compare_receipt, "resolve_tool_config", _spy)
        from abicheck.mcp_server import abi_compare

        abi_compare(
            str(snap_path),
            str(snap_path),
            contract_evaluation=True,
            contract_mode="exports",
        )
        assert seen["contract_mode"] == "exports"

    def test_absent_mode_stays_absent(self, snap_path: Path, monkeypatch):
        from abicheck import mcp_compare_receipt

        seen: dict[str, object] = {}
        original = mcp_compare_receipt.resolve_tool_config

        def _spy(**kwargs):
            seen["contract_mode"] = kwargs.get("contract_mode")
            return original(**kwargs)

        monkeypatch.setattr(mcp_compare_receipt, "resolve_tool_config", _spy)
        from abicheck.mcp_server import abi_compare

        abi_compare(str(snap_path), str(snap_path), contract_evaluation=True)
        assert seen["contract_mode"] is None


class TestSourceAbiOnlyFrontendOnScan:
    """`abi_scan` refuses `android` rather than silently substituting `auto`.

    The compile context downgrades a non-header-AST frontend, which is right
    for `abi_dump` (its `DumpRequest.frontend` still carries the value into
    source replay) but wrong for `abi_scan`: `ScanRequest` has no
    request-level frontend field, so the value would vanish and the scan would
    run an ordinary auto/castxml pass for an argument the tool documents as
    meaningful (Codex review).
    """

    def test_scan_rejects_android(self, snap_path: Path):
        from abicheck.mcp_server import abi_scan

        data = json.loads(abi_scan(str(snap_path), ast_frontend="android"))
        assert data["status"] == "error"
        assert "source-ABI-replay only" in data["error"]
        assert "abi_dump" in data["error"]

    @pytest.mark.parametrize("frontend", ["auto", "castxml", "clang", "hybrid"])
    def test_scan_accepts_every_header_frontend(
        self, snap_path: Path, monkeypatch, frontend: str
    ):
        from abicheck import service
        from abicheck.mcp_server import abi_scan

        monkeypatch.setattr(
            service,
            "run_scan_subprocess",
            lambda req, timeout: {"verdict": "COMPATIBLE"},
        )
        data = json.loads(abi_scan(str(snap_path), ast_frontend=frontend))
        assert data["status"] == "ok"

    def test_dump_still_accepts_android(self, snap_path: Path, tmp_path: Path):
        """The value remains valid where something can act on it."""
        from abicheck.mcp_server import abi_dump

        data = json.loads(
            abi_dump(str(snap_path), ast_frontend="android", build_info=str(tmp_path))
        )
        assert data["status"] == "ok"


class TestRawSourceUnderHybridIsRejected:
    """`depth="source"` + the `hybrid` frontend is a usage error, not a
    silently weaker result — `hybrid` has no real L4 extractor. Mirrors
    `cli.py`'s own `UsageError` for the same combination.
    """

    def _request(self, snap_path: Path, sources: Path, frontend: str) -> DumpRequest:
        return DumpRequest(
            input=InputSpec(path=snap_path, sources=sources),
            frontend=frontend,
            depth="source",
        )

    def test_a_raw_source_tree_under_hybrid_raises(
        self, snap_path: Path, tmp_path: Path
    ):
        from abicheck import service

        sources = tmp_path / "raw"
        sources.mkdir()
        (sources / "a.cpp").write_text("int f() { return 0; }\n", encoding="utf-8")
        with pytest.raises(ValidationError, match="incompatible with the 'hybrid'"):
            service.run_dump_request(self._request(snap_path, sources, "hybrid"))

    def test_a_prebuilt_pack_under_hybrid_is_allowed(
        self, snap_path: Path, tmp_path: Path, monkeypatch
    ):
        # Only a *raw* tree needs real extraction; a prebuilt pack never feeds
        # L4, so it must not be swept up by the same guard.
        from abicheck import cli_buildsource, service

        monkeypatch.setattr(
            cli_buildsource, "embed_build_source", lambda snap, **kwargs: None
        )
        # A real pack is identified by manifest *content* -- the
        # BuildSourcePack version marker -- not by the file merely existing.
        pack = tmp_path / "pack"
        pack.mkdir()
        (pack / "manifest.json").write_text(
            '{"build_source_pack_version": 1}', encoding="utf-8"
        )
        # The stub embeds nothing, so the run still fails -- but on the depth
        # floor, *not* the hybrid guard. That distinction is the assertion:
        # reaching the floor at all proves the pack got past the guard.
        with pytest.raises(ValidationError) as exc:
            service.run_dump_request(self._request(snap_path, pack, "hybrid"))
        assert "only reached 'binary'" in str(exc.value)
        assert "hybrid" not in str(exc.value)


class TestMalformedPackIsTranslated:
    """`embed_build_source` raises `click.ClickException` on a malformed pack —
    a CLI concept with no place in this Tier-2 API's contract, so the
    resolver translates it to `SnapshotError`.
    """

    def test_click_exception_becomes_snapshot_error(
        self, snap_path: Path, tmp_path: Path, monkeypatch
    ):
        import click

        from abicheck import cli_buildsource, service

        def _boom(snap, **kwargs):
            raise click.ClickException("build pack is malformed")

        monkeypatch.setattr(cli_buildsource, "embed_build_source", _boom)
        sources = tmp_path / "src"
        sources.mkdir()
        with pytest.raises(SnapshotError, match="build pack is malformed"):
            service.run_dump_request(
                DumpRequest(input=InputSpec(path=snap_path, sources=sources))
            )


class TestSourceReplayUsesTheSelectedCompiler:
    """L4 source-ABI replay invokes the compiler the request selected.

    `embed_build_source` defaults `clang_bin` to a bare "clang"; the `dump`
    CLI and `scan_engine` both override it from `--gcc-path`/`--gcc-prefix`,
    and the typed path did not. On a hermetic or cross-toolchain host where
    only the requested compiler works, that made an omitted `depth` return a
    weaker snapshot and an explicit `depth="source"` fail, even though the
    caller supplied the right compiler (Codex review).
    """

    @pytest.fixture
    def replayed_with(self, monkeypatch):
        from abicheck import cli_buildsource

        captured: dict[str, object] = {}
        monkeypatch.setattr(
            cli_buildsource,
            "embed_build_source",
            lambda snap, **kwargs: captured.update(kwargs),
        )
        return captured

    def _run(self, snap_path: Path, tmp_path: Path, ctx) -> None:
        from abicheck import service

        sources = tmp_path / "src"
        sources.mkdir(exist_ok=True)
        service.run_dump_request(
            DumpRequest(input=InputSpec(path=snap_path, sources=sources, compile=ctx))
        )

    def test_gcc_path_reaches_source_replay(
        self, snap_path: Path, tmp_path: Path, replayed_with: dict
    ):
        self._run(snap_path, tmp_path, CompileContext(gcc_path="/opt/oneapi/icpx"))
        assert replayed_with["clang_bin"] == "/opt/oneapi/icpx"

    def test_no_compile_context_keeps_the_default(
        self, snap_path: Path, tmp_path: Path, replayed_with: dict
    ):
        self._run(snap_path, tmp_path, None)
        assert replayed_with["clang_bin"] == "clang"

    def test_a_cl_style_driver_is_not_excluded(
        self, snap_path: Path, tmp_path: Path, replayed_with: dict
    ):
        # L4 re-drives a CL compile unit with `--driver-mode=cl` itself, so
        # unlike the S2 pre-scan it must NOT fall back off a CL-mode driver
        # (`exclude_cl_style=False`) -- doing so would replay an Intel DPC++
        # build through a plain clang that cannot parse it.
        self._run(snap_path, tmp_path, CompileContext(gcc_path="/opt/dpcpp-cl"))
        assert replayed_with["clang_bin"] == "/opt/dpcpp-cl"


class TestDependencySysrootIsForwarded:
    """`--follow-deps` under a sysroot searches the target, not the host.

    The only sysroot a typed request can carry is the input's own compile
    context; passing `None` made a cross/sysrooted extraction search host
    defaults and report the target's dependencies unresolved, where the CLI
    (which forwards `--sysroot`) resolved them (Codex review).
    """

    @pytest.fixture
    def resolved_sysroot(self, monkeypatch):
        """Capture the sysroot the dependency resolver is actually given.

        Records it out of `**kwargs` as well as the positional slot, so this
        keeps asserting on the real value rather than raising `TypeError`
        if `populate_side_dependency_info` ever switches to keyword
        arguments (CodeRabbit review).
        """
        from abicheck import dependency_info

        captured: dict[str, object] = {}

        def _record(snap, path, search=None, sysroot=None, ldpath=None, **kwargs):
            captured["sysroot"] = kwargs.get("sysroot", sysroot)

        monkeypatch.setattr(dependency_info, "populate_dependency_info", _record)
        monkeypatch.setattr(
            dependency_info, "_dependency_source", lambda side, fmt: side.path
        )
        return captured

    def test_sysroot_comes_from_the_input_compile_context(
        self, snap_path: Path, resolved_sysroot: dict
    ):
        from abicheck import service

        service.run_dump_request(
            DumpRequest(
                input=InputSpec(
                    path=snap_path, compile=CompileContext(sysroot=Path("/opt/sysroot"))
                ),
                follow_dependencies=True,
            )
        )
        assert resolved_sysroot["sysroot"] == Path("/opt/sysroot")

    def test_no_compile_context_means_no_sysroot(
        self, snap_path: Path, resolved_sysroot: dict
    ):
        from abicheck import service

        service.run_dump_request(
            DumpRequest(input=InputSpec(path=snap_path), follow_dependencies=True)
        )
        assert resolved_sysroot["sysroot"] is None


class TestAuditOnlyScanRejectsComparisonKnobs:
    """A one-build audit rejects comparison-only arguments up front.

    `run_scan` already rejects them, but inside the spawned worker, where
    `run_scan_subprocess` rethrows as `RuntimeError` and the tool reports a
    sanitized unexpected error — after paying for a process spawn. The check
    now happens before spawning, reusing the engine's own field list (Codex
    review).
    """

    @pytest.mark.parametrize(
        ("build_kwargs", "field"),
        [
            (lambda _tmp: ({"policy": "sdk_vendor"}), "policy"),
            (lambda _tmp: ({"contract_evaluation": True}), "contract_evaluation"),
            # These two are worth pinning separately rather than trusting the
            # first two to stand in for them: the tool reads, size-checks and
            # *parses* both files before reaching the guard, so they take a
            # materially different path to the same rejection. The engine's
            # field list is what the guard reuses, and it names the
            # `ScanRequest` field (`suppression`), not the argument that set
            # it (CodeRabbit review).
            (
                lambda tmp: {
                    "policy_file": str(
                        _write_yaml(tmp, "policy.yml", "base_policy: strict_abi\n")
                    )
                },
                "policy_file",
            ),
            (
                lambda tmp: {
                    "suppression_file": str(
                        _write_yaml(
                            tmp,
                            "suppressions.yaml",
                            "version: 1\nsuppressions: []\n",
                        )
                    )
                },
                "suppression",
            ),
        ],
    )
    def test_rejected_without_against(
        self,
        snap_path: Path,
        tmp_path: Path,
        monkeypatch,
        build_kwargs,
        field: str,
    ):
        kwargs = build_kwargs(tmp_path)
        from abicheck import service
        from abicheck.mcp_server import abi_scan

        def _never(req, timeout):  # pragma: no cover - must never run
            raise AssertionError("spawned a worker for a request rejected up front")

        monkeypatch.setattr(service, "run_scan_subprocess", _never)
        data = json.loads(abi_scan(str(snap_path), **kwargs))
        assert data["status"] == "error"
        assert field in data["error"]
        # The engine's wording is `ScanRequest`-shaped; the tool adds the
        # argument a caller can actually act on.
        assert "`against`" in data["error"]

    def test_accepted_with_against(self, snap_path: Path, monkeypatch):
        from abicheck import service
        from abicheck.mcp_server import abi_scan

        monkeypatch.setattr(
            service,
            "run_scan_subprocess",
            lambda req, timeout: {"verdict": "COMPATIBLE"},
        )
        data = json.loads(
            abi_scan(str(snap_path), against=str(snap_path), policy="sdk_vendor")
        )
        assert data["status"] == "ok"

    def test_plain_audit_still_runs(self, snap_path: Path, monkeypatch):
        """Defaults must not trip the guard."""
        from abicheck import service
        from abicheck.mcp_server import abi_scan

        monkeypatch.setattr(
            service,
            "run_scan_subprocess",
            lambda req, timeout: {"verdict": "COMPATIBLE"},
        )
        assert json.loads(abi_scan(str(snap_path)))["status"] == "ok"


class TestPerInputFrontendIsValidated:
    """A typo in `InputSpec.compile.frontend` is a usage error, not a default run.

    The source-ABI-only downgrade (`android` → `auto`) originally rewrote
    *every* non-header frontend, so `CompileContext(frontend="clnag")` turned
    a typo that used to raise `Unknown AST frontend` into a successful
    default-backend run — trading one bug for a worse one (Codex review).
    Fixed on both halves: the value is validated up front, and the downgrade
    is narrowed to frontends that are actually known-but-header-less.
    """

    @pytest.mark.parametrize("label", ["dump", "compare"])
    def test_typo_is_rejected_by_validate(self, snap_path: Path, label: str):
        spec = InputSpec(path=snap_path, compile=CompileContext(frontend="clnag"))
        request = (
            DumpRequest(input=spec)
            if label == "dump"
            else CompareRequest(old=spec, new=spec)
        )
        errors = request.validation_errors()
        assert any("unsupported" in e and "'clnag'" in e for e in errors), errors

    def test_typo_raises_rather_than_running(self, snap_path: Path):
        from abicheck.service import run_dump_request

        with pytest.raises(ValidationError, match="clnag"):
            run_dump_request(
                DumpRequest(
                    input=InputSpec(
                        path=snap_path, compile=CompileContext(frontend="clnag")
                    )
                )
            )

    def test_downgrade_only_applies_to_a_known_headerless_frontend(self):
        """`android` is downgraded; an unknown value is left for the extraction
        layer to reject, so the narrowing cannot silently resurrect the bug."""
        from abicheck.service_compare_evidence import _header_ast_frontend_only

        assert (
            _header_ast_frontend_only(CompileContext(frontend="android")).frontend
            == "auto"
        )
        assert (
            _header_ast_frontend_only(CompileContext(frontend="clnag")).frontend
            == "clnag"
        )
        assert (
            _header_ast_frontend_only(CompileContext(frontend="clang")).frontend
            == "clang"
        )

    def test_a_valid_per_input_frontend_still_passes(self, snap_path: Path):
        spec = InputSpec(path=snap_path, compile=CompileContext(frontend="castxml"))
        assert DumpRequest(input=spec).validation_errors() == []
