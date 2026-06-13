# Copyright 2026 Nikolay Petrov
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""Unit tests for the probe-harness YAML parser and matrix bookkeeping.

Compilation tests are deferred to integration tests (need a real C++
compiler on PATH).
"""
from __future__ import annotations

import json

import pytest

from abicheck.probe_harness import (
    MatrixSnapshot,
    Probe,
    ProbeConfiguration,
    ProbeResult,
    ProbeSpec,
    _parse_cxx_std,
    parse_probe_spec,
)


class TestParseCxxStd:
    @pytest.mark.parametrize("flags, expected", [
        (["-std=c++17"], 17),
        (["-O2", "-std=c++20"], 20),
        (["-Wall"], None),
        ([], None),
        (["-std=c++23a"], None),  # invalid suffix
    ])
    def test_parse(self, flags: list[str], expected: int | None) -> None:
        assert _parse_cxx_std(flags) == expected


class TestParseProbeSpec:
    def test_minimal_valid(self) -> None:
        spec = parse_probe_spec({
            "name": "test",
            "configurations": [
                {"id": "gcc13", "compiler": "g++-13",
                 "flags": ["-std=c++20", "-O0"]},
            ],
            "probes": [
                {"name": "p1", "headers": ["<vector>"], "body": "int main() {}"},
            ],
        })
        assert spec.name == "test"
        assert len(spec.configurations) == 1
        assert spec.configurations[0].cxx_std == 20
        assert spec.configurations[0].id == "gcc13"
        assert len(spec.probes) == 1
        assert spec.probes[0].headers == ("<vector>",)

    def test_missing_required_raises(self) -> None:
        with pytest.raises(ValueError, match="missing required"):
            parse_probe_spec({"name": "x", "configurations": []})

    @pytest.mark.parametrize("compiler", ["/bin/sh", "python3", "sh"])
    def test_rejects_non_compiler_executables(self, compiler: str) -> None:
        with pytest.raises(ValueError, match="compiler"):
            parse_probe_spec({
                "name": "test",
                "configurations": [
                    {"id": "c1", "compiler": compiler, "flags": ["-std=c++20"]},
                ],
                "probes": [{"name": "p", "headers": [], "body": ""}],
            })

    @pytest.mark.parametrize("flag", ["-c", "-wrapper", "-fplugin=evil.so", "-Xclang"])
    def test_rejects_command_execution_flags(self, flag: str) -> None:
        with pytest.raises(ValueError, match="flag"):
            parse_probe_spec({
                "name": "test",
                "configurations": [
                    {"id": "c1", "compiler": "g++", "flags": [flag]},
                ],
                "probes": [{"name": "p", "headers": [], "body": ""}],
            })

    @pytest.mark.parametrize("identifier", ["../escape", "subdir/p", ".", "..", ""])
    def test_rejects_path_like_generated_file_identifiers(
        self, identifier: str
    ) -> None:
        with pytest.raises(ValueError, match="identifier|must not"):
            parse_probe_spec({
                "name": "test",
                "configurations": [
                    {"id": identifier, "compiler": "g++", "flags": ["-std=c++20"]},
                ],
                "probes": [{"name": "p", "headers": [], "body": ""}],
            })

    def test_defines_and_includes(self) -> None:
        spec = parse_probe_spec({
            "name": "test",
            "configurations": [{
                "id": "c1",
                "compiler": "g++",
                "flags": ["-std=c++17"],
                "defines": {"FOO": "1", "BAR": ""},
                "include_dirs": ["/opt/inc", "/usr/local/inc"],
            }],
            "probes": [{"name": "p", "headers": [], "body": ""}],
        })
        cfg = spec.configurations[0]
        args = cfg.as_command_args()
        assert "-DFOO=1" in args
        assert "-DBAR" in args
        assert "-I/opt/inc" in args
        assert "-I/usr/local/inc" in args


    @pytest.mark.parametrize("bad_cfg_id", ["../x", "x/y", ""])
    def test_invalid_configuration_id_rejected(self, bad_cfg_id: str) -> None:
        with pytest.raises(ValueError, match="configuration id"):
            parse_probe_spec({
                "name": "test",
                "configurations": [{"id": bad_cfg_id, "compiler": "g++"}],
                "probes": [{"name": "p", "body": ""}],
            })

    @pytest.mark.parametrize("bad_probe_name", ["../p", "p/q", ""])
    def test_invalid_probe_name_rejected(self, bad_probe_name: str) -> None:
        with pytest.raises(ValueError, match="probe name"):
            parse_probe_spec({
                "name": "test",
                "configurations": [{"id": "cfg", "compiler": "g++"}],
                "probes": [{"name": bad_probe_name, "body": ""}],
            })

    @pytest.mark.parametrize("bad_compiler", ["/bin/sh", "../g++", "-Wl,foo", "", "sh", "bash", "rm"])
    def test_invalid_compiler_rejected(self, bad_compiler: str) -> None:
        with pytest.raises(ValueError, match="compiler"):
            parse_probe_spec({
                "name": "test",
                "configurations": [{"id": "cfg", "compiler": bad_compiler}],
                "probes": [{"name": "p", "body": ""}],
            })

    @pytest.mark.parametrize("bad_flag", ["-c", "-o", "-x", "--", "-MD", "-MMD", "-MF/tmp/evil.d", "-MT/target", "-MQ/target", "-o/tmp/out"])
    def test_disallowed_flags_rejected(self, bad_flag: str) -> None:
        with pytest.raises(ValueError, match="disallowed"):
            parse_probe_spec({
                "name": "test",
                "configurations": [{"id": "cfg", "compiler": "g++", "flags": [bad_flag]}],
                "probes": [{"name": "p", "body": ""}],
            })

    def test_unknown_keys_ignored(self) -> None:
        spec = parse_probe_spec({
            "name": "t",
            "configurations": [
                {"id": "a", "compiler": "g++", "future_field": "x"},
            ],
            "probes": [{"name": "p", "body": "", "future_field": True}],
        })
        assert spec.configurations[0].id == "a"


class TestProbeRender:
    def test_renders_angle_includes(self) -> None:
        p = Probe(
            name="p",
            headers=("<vector>", "<string>"),
            body="int x = 0;",
        )
        out = p.render()
        assert "#include <vector>" in out
        assert "#include <string>" in out
        assert "int x = 0;" in out

    def test_renders_quoted_includes(self) -> None:
        p = Probe(name="p", headers=('"my.h"',), body="")
        assert '#include "my.h"' in p.render()

    def test_bare_header_string_wrapped(self) -> None:
        p = Probe(name="p", headers=("string",), body="")
        assert "#include <string>" in p.render()


class TestMatrixSnapshot:
    def test_by_configuration(self) -> None:
        m = MatrixSnapshot(
            library="lib", version="1", spec_name="s",
            results=[
                ProbeResult(configuration_id="a", probe_id="p1"),
                ProbeResult(configuration_id="a", probe_id="p2"),
                ProbeResult(configuration_id="b", probe_id="p1"),
            ],
        )
        idx = m.by_configuration()
        assert set(idx.keys()) == {"a", "b"}
        assert len(idx["a"]) == 2
        assert len(idx["b"]) == 1

    def test_roundtrip_json_no_snapshot(self) -> None:
        m = MatrixSnapshot(
            library="lib", version="1", spec_name="s",
            cxx_stds={"a": 20, "b": 17},
            defaults={"backend": "tbb"},
            results=[
                ProbeResult(
                    configuration_id="a", probe_id="p1",
                    object_path="build/a__p1.o", error=None,
                ),
                ProbeResult(
                    configuration_id="b", probe_id="p1",
                    error="compiler not found",
                ),
            ],
        )
        roundtrip = MatrixSnapshot.from_dict(json.loads(m.to_json()))
        assert roundtrip.library == "lib"
        assert roundtrip.cxx_stds == {"a": 20, "b": 17}
        assert roundtrip.defaults == {"backend": "tbb"}
        assert len(roundtrip.results) == 2
        assert roundtrip.results[1].error == "compiler not found"


class TestProbeSpecAsCommandArgs:
    def test_flags_first_then_defines_then_includes(self) -> None:
        cfg = ProbeConfiguration(
            id="c", compiler="g++",
            flags=("-std=c++20", "-O0"),
            defines={"X": "1"},
            include_dirs=("/inc",),
        )
        args = cfg.as_command_args()
        # Compiler first, then flags, then -D, then -I.
        assert args[0] == "g++"
        assert args.index("-std=c++20") < args.index("-DX=1")
        assert args.index("-DX=1") < args.index("-I/inc")


def test_probespec_is_frozen() -> None:
    # frozen=True dataclass — assignment must fail to ensure spec
    # can be safely shared / hashed.
    spec = ProbeSpec(name="x", configurations=(), probes=())
    with pytest.raises(Exception):  # FrozenInstanceError or AttributeError
        spec.name = "y"  # type: ignore[misc]
