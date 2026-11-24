"""Preserve the selected Intel offload driver and pass through extraction."""

from pathlib import Path

import pytest

from abicheck.buildsource.adapters.adapter_shared import extract_abi_relevant_flags
from abicheck.buildsource.build_evidence import CompileUnit
from abicheck.buildsource.source_extractors import (
    build_clang_command,
    build_clang_macro_command,
)
from abicheck.extract.headers.clang_header_argv import (
    HeaderCompileContext,
    render_clang_header_argv,
)


@pytest.mark.parametrize(
    "selector", [None, "--offload-host-only", "--offload-device-only"]
)
def test_source_replay_preserves_offload_context(selector):
    flags = ["-fsycl", "--offload-new-driver"]
    if selector:
        flags.append(selector)
    argv = ["icpx", *flags, "-c", "a.cpp"]
    unit = CompileUnit(
        id="cu://a.cpp",
        source="a.cpp",
        language="CXX",
        argv=argv,
        abi_relevant_flags=extract_abi_relevant_flags(argv),
    )
    for build in (build_clang_command, build_clang_macro_command):
        command = build(unit, Path("a.cpp"), clang_bin="icpx")
        assert "--offload-new-driver" in command
        assert command.count(selector or "--offload-host-only") == 1
        assert "-fsycl-host-only" not in command
        if selector == "--offload-device-only":
            assert "--offload-host-only" not in command


@pytest.mark.parametrize(
    "flags,expected",
    [
        (["--offload-new-driver"], "--offload-host-only"),
        (["--offload-new-driver", "--no-offload-new-driver"], "-fsycl-host-only"),
    ],
)
def test_l2_host_uses_the_selected_driver(flags, expected):
    context = HeaderCompileContext(
        cc_bin="icpx", dpcpp_host_context=True, gcc_option_tokens=tuple(flags)
    )
    assert expected in render_clang_header_argv(context, Path("headers.hpp"))
