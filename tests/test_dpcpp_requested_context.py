# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""DPC++ requested-context acquisition avoids unused complete AST documents."""

from pathlib import Path

from abicheck.dumper_ast_config import _build_clang_header_command
from abicheck.dumper_clang import _resolve_dpcpp_acquisition


def test_host_context_uses_one_direct_sycl_pass() -> None:
    assert _resolve_dpcpp_acquisition("icpx", "host", None, ()) == (False, True)


def test_device_context_retains_correlated_multi_pass() -> None:
    assert _resolve_dpcpp_acquisition("icpx", "device", None, ()) == (True, False)


def test_explicit_device_only_is_not_overridden_for_host() -> None:
    assert _resolve_dpcpp_acquisition(
        "icpx", "host", None, ("-fsycl-device-only",)
    ) == (True, False)


def test_direct_host_command_does_not_request_context_inventory(tmp_path: Path) -> None:
    cmd = _build_clang_header_command(
        "icpx",
        "gnu",
        [],
        tmp_path / "aggregate.hpp",
        force_cpp=True,
        dpcpp_host_context=True,
    )
    assert "-fsycl" in cmd
    assert "-fsycl-host-only" in cmd
    assert "-v" not in cmd
