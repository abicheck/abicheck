"""Public-interface tests for :mod:`abicheck.extract.compiler_driver`.

The driver×flags matrix below states the SYCL-mode and clang-binary decisions
against an oracle written from the documented rules (the module docstrings),
not from the module's own private helpers. Nothing here runs a real compiler:
the single-pass probe and executable identity are answered through the public
seams in :mod:`abicheck.extract.dpcpp_jobs`, and ``clang`` availability is
patched on the module that asks for it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from abicheck.errors import AstContextMissingError, SnapshotError
from abicheck.extract import compiler_driver as driver
from abicheck.extract.compiler_driver import (
    needs_sycl_host_only,
    resolve_dpcpp_acquisition,
)

# Driver spellings the decisions must cover: plain clang, versioned clang,
# CL-style clang, Intel's oneAPI family (all four aliases, one of them with a
# path), legacy dpcpp (SYCL-on by default), GNU compilers and a non-clang name.
DRIVERS = [
    "clang",
    "clang++",
    "clang-18",
    "clang-cl",
    "icx",
    "icpx",
    "/opt/intel/oneapi/bin/icpx",
    "dpcpp",
    "dpcpp-cl",
    "gcc",
    "g++",
]

# Caller flags, covering every last-flag-wins combination and the
# single-pass pins that suppress the host-only collapse.
FLAGS = [
    [],
    ["-fsycl"],
    ["-fno-sycl"],
    ["-fsycl", "-fno-sycl"],
    ["-fno-sycl", "-fsycl"],
    ["-fsycl", "-fsycl"],
    ["-fsycl-host-only"],
    ["-fsycl-device-only"],
    ["-fsycl", "-fsycl-host-only"],
    ["-DX=1"],
]

_INTEL_STEMS = {"icx", "icpx", "dpcpp", "dpcpp-cl"}
_SYCL_ON_BY_DEFAULT_STEMS = {"dpcpp", "dpcpp-cl"}


def _stem(path: str) -> str:
    return Path(path).stem.lower()


def _oracle_intel(driver_name: str) -> bool:
    return _stem(driver_name) in _INTEL_STEMS


def _oracle_sycl_enabled(driver_name: str, flags: list[str]) -> bool:
    enabled = _stem(driver_name) in _SYCL_ON_BY_DEFAULT_STEMS
    for tok in flags:
        if tok == "-fsycl":
            enabled = True
        elif tok == "-fno-sycl":
            enabled = False
    return enabled


def _oracle_needs_host_only(driver_name: str, flags: list[str]) -> bool:
    if not _oracle_intel(driver_name):
        return False
    if "-fsycl-host-only" in flags or "-fsycl-device-only" in flags:
        return False
    return _oracle_sycl_enabled(driver_name, flags)


@pytest.fixture
def probe_answers(monkeypatch: pytest.MonkeyPatch):
    """Return a setter that fixes the single-pass probe's answer, with no
    driver executed and no executable identity read."""

    def _set(answer: bool) -> None:
        monkeypatch.setattr(
            "abicheck.extract.dpcpp_jobs.executable_revision",
            lambda clang_bin: f"rev-{clang_bin}",
        )
        monkeypatch.setattr(
            "abicheck.extract.dpcpp_jobs.host_only_request_usable",
            lambda *_a: answer,
        )

    return _set


@pytest.fixture
def clang_on_path(monkeypatch: pytest.MonkeyPatch):
    """Return a setter that decides whether ``clang_available`` finds the
    binary, so resolution is tested without touching ``PATH``."""

    def _set(found: bool) -> None:
        monkeypatch.setattr(driver, "clang_available", lambda *_a, **_k: found)

    return _set


# ── SYCL mode: driver × flags ───────────────────────────────────────────────


@pytest.mark.parametrize("flags", FLAGS, ids=lambda f: " ".join(f) or "no-flags")
@pytest.mark.parametrize("driver_name", DRIVERS)
def test_needs_sycl_host_only_matrix(driver_name: str, flags: list[str]) -> None:
    assert driver.needs_sycl_host_only(driver_name, flags) is (
        _oracle_needs_host_only(driver_name, flags)
    )


@pytest.mark.parametrize("flags", FLAGS, ids=lambda f: " ".join(f) or "no-flags")
@pytest.mark.parametrize("driver_name", DRIVERS)
@pytest.mark.parametrize("frontend_context", ["host", "device"])
@pytest.mark.parametrize("probe", [False, True], ids=["probe-no", "probe-yes"])
def test_resolve_dpcpp_acquisition_matrix(
    probe_answers,
    driver_name: str,
    flags: list[str],
    frontend_context: str,
    probe: bool,
) -> None:
    probe_answers(probe)
    gcc_option_tokens = tuple(flags)
    is_intel = _oracle_intel(driver_name)
    # A non-host context needs a DPC++ driver, and an explicit last -fno-sycl
    # on one forbids that context (ADR-050 D5); both are refused, not resolved.
    if frontend_context != "host" and (not is_intel or _user_disabled_last(flags)):
        with pytest.raises(AstContextMissingError):
            driver.resolve_dpcpp_acquisition(
                driver_name, frontend_context, None, gcc_option_tokens
            )
        return
    multi = is_intel and not _user_disabled_last(flags)
    direct_host = (
        multi
        and frontend_context == "host"
        and "-fsycl-device-only" not in flags
        and probe
    )
    assert driver.resolve_dpcpp_acquisition(
        driver_name, frontend_context, None, gcc_option_tokens
    ) == (multi and not direct_host, direct_host)


def _user_disabled_last(flags: list[str]) -> bool:
    last = None
    for tok in flags:
        if tok in ("-fsycl", "-fno-sycl"):
            last = tok
    return last == "-fno-sycl"


def test_gcc_option_string_and_tokens_are_both_seen_by_acquisition(
    probe_answers,
) -> None:
    # --gcc-options and the already-split tokens are one flag stream: an
    # explicit -fno-sycl in either place disables SYCL for a non-host context.
    probe_answers(False)
    with pytest.raises(AstContextMissingError, match="-fno-sycl"):
        driver.resolve_dpcpp_acquisition("icpx", "device", "-fno-sycl", ())
    with pytest.raises(AstContextMissingError, match="-fno-sycl"):
        driver.resolve_dpcpp_acquisition("icpx", "device", None, ("-fno-sycl",))


# ── clang binary: driver × compiler × override ──────────────────────────────


@pytest.mark.parametrize("compiler", ["c++", "g++", "clang++", "gcc", "clang"])
@pytest.mark.parametrize(
    "gcc_path",
    [None, "clang++", "/usr/bin/clang-18", "clang-cl", "icpx", "gcc", "/usr/bin/g++"],
)
@pytest.mark.parametrize("gcc_prefix", [None, "aarch64-linux-gnu-"])
def test_resolve_clang_bin_matrix(
    clang_on_path, compiler: str, gcc_path: str | None, gcc_prefix: str | None
) -> None:
    clang_on_path(True)
    cxx = compiler in ("c++", "g++", "clang++")
    family_path = gcc_path is not None and (
        "clang" in _stem(gcc_path)
        or _stem(gcc_path) in {"icx", "icpx", "dpcpp", "dpcpp-cl"}
    )
    if family_path:
        expected = gcc_path
    elif gcc_prefix:
        expected = f"{gcc_prefix}{'clang++' if cxx else 'clang'}"
    else:
        expected = "clang++" if cxx else "clang"
    assert driver.resolve_clang_bin(compiler, gcc_path, gcc_prefix) == expected
    assert driver.clang_bin_is_explicitly_configured(gcc_path, gcc_prefix) is (
        family_path or gcc_prefix is not None
    )


def test_resolve_clang_bin_raises_when_the_resolved_binary_is_absent(
    clang_on_path,
) -> None:
    clang_on_path(False)
    with pytest.raises(SnapshotError, match="not found in PATH"):
        driver.resolve_clang_bin("c++", None, None)


@pytest.mark.parametrize(
    "gcc_path,exclude_cl_style,expected",
    [
        ("clang++", True, "clang++"),
        ("clang++", False, "clang++"),
        # A CL-mode driver is refused by the S2 pre-scan (GNU-only flags) but
        # kept by L4 replay, which re-drives it with --driver-mode=cl.
        ("/usr/bin/clang-cl", True, "clang"),
        ("/usr/bin/clang-cl", False, "/usr/bin/clang-cl"),
        ("dpcpp-cl", True, "clang"),
        ("icpx", True, "icpx"),
        ("gcc", True, "clang"),
    ],
)
def test_source_frontend_excludes_cl_style_only_when_asked(
    monkeypatch: pytest.MonkeyPatch,
    gcc_path: str,
    exclude_cl_style: bool,
    expected: str,
) -> None:
    monkeypatch.setattr(
        "abicheck.extract.compiler_driver.shutil.which", lambda _p: None
    )
    assert (
        driver.resolve_source_frontend_clang_bin(
            gcc_path, None, exclude_cl_style=exclude_cl_style
        )
        == expected
    )


def test_source_frontend_prefix_only_when_the_prefixed_binary_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "abicheck.extract.compiler_driver.shutil.which",
        lambda name: "/cross/bin/aarch64-clang" if name == "aarch64-clang" else None,
    )
    assert (
        driver.resolve_source_frontend_clang_bin(None, "aarch64-", fallback="clang")
        == "aarch64-clang"
    )
    assert (
        driver.resolve_source_frontend_clang_bin(None, "riscv-", fallback="clang")
        == "clang"
    )


@pytest.mark.parametrize(
    "clang_bin,compiler,expected",
    [
        ("clang++", "c++", True),
        ("/usr/bin/clang", "c++", False),
        ("clang-18", "clang", True),
        ("aarch64-linux-gnu-clang", "c++", False),
        ("clang-18++", "c++", False),
    ],
)
def test_is_default_clang_bin_compares_the_invocation_basename(
    clang_bin: str, compiler: str, expected: bool
) -> None:
    assert driver.is_default_clang_bin(clang_bin, compiler) is expected


@pytest.fixture
def no_host_only_probe(probe_answers) -> None:
    """The single-pass probe answers False: acquisition reports only the
    multi-context decision."""
    probe_answers(False)


@pytest.mark.parametrize(
    "cc_bin,expected",
    [
        ("dpcpp", True),
        ("dpcpp-cl", True),
        ("/opt/oneapi/bin/dpcpp", True),
        ("DPCPP", True),
        ("dpcpp.exe", True),
        ("icx", False),
        ("icpx", False),
        ("clang", False),
        ("clang++", False),
        ("gcc", False),
    ],
)
def test_dpcpp_defaults_sycl_on(cc_bin: str, expected: bool) -> None:
    # With no -fsycl/-fno-sycl token, only a legacy dpcpp driver name starts
    # with SYCL on; needs_sycl_host_only is that default observed end to end.
    assert needs_sycl_host_only(cc_bin, []) is expected


@pytest.mark.parametrize(
    "tokens,expected",
    [
        ([], False),
        (["-fsycl"], False),
        (["-fno-sycl"], True),
        (["-fsycl", "-fno-sycl"], True),
        # Last-flag-wins: a later -fsycl re-enables it (Codex review, P2).
        (["-fno-sycl", "-fsycl"], False),
        (["-fsycl", "-fno-sycl", "-fsycl"], False),
        (["-DFOO=1"], False),
    ],
)
def test_user_explicitly_disabled_sycl(tokens: list[str], expected: bool) -> None:
    # On a dpcpp driver SYCL defaults on, so an explicit last-flag -fno-sycl is
    # exactly the case where the single-pass pin is withheld.
    assert needs_sycl_host_only("dpcpp", tokens) is (not expected)


@pytest.mark.parametrize(
    "clang_bin,frontend_context,tokens,expected",
    [
        ("icpx", "host", [], True),
        ("icpx", "host", ["-fno-sycl"], False),
        ("clang++", "host", [], False),
        ("icpx", "device", [], True),
    ],
)
def test_resolve_dpcpp_multi_context(
    no_host_only_probe,
    clang_bin: str,
    frontend_context: str,
    tokens: list[str],
    expected: bool,
) -> None:
    # The multi-context decision is the pre-direct-host half of the acquisition
    # result; with the single-pass probe answering False, direct_host is False
    # and the multi-context flag is exactly the first element.
    multi, direct_host = resolve_dpcpp_acquisition(
        clang_bin, frontend_context, None, tuple(tokens)
    )
    assert direct_host is False
    assert multi is expected


def test_resolve_dpcpp_multi_context_device_on_plain_clang_raises() -> None:
    from abicheck.errors import AstContextMissingError

    with pytest.raises(AstContextMissingError, match="DPC\\+\\+-capable"):
        resolve_dpcpp_acquisition("clang++", "device", None, ())


def test_resolve_dpcpp_multi_context_device_with_fno_sycl_raises() -> None:
    from abicheck.errors import AstContextMissingError

    with pytest.raises(AstContextMissingError, match="-fno-sycl"):
        resolve_dpcpp_acquisition("icpx", "device", None, ("-fno-sycl",))
