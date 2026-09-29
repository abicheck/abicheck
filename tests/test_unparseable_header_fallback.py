"""Unparseable-header fallback for a multi-header L2 parse.

oneDNN validation: ``-H include/`` where ``dnnl_sycl.hpp`` raises ``#error
"Unsupported compiler"`` under castxml sank the whole directory. The parse
now drops exactly the headers whose include chains fail, records them, and
retries -- deterministically, and never by guessing.
"""

from __future__ import annotations

import random
import shutil
import subprocess
from pathlib import Path

import pytest

from abicheck.errors import HeaderToolchainError, SnapshotError
from abicheck.extract.unparseable_header_fallback import (
    attribute_failing_headers,
    parse_excluding_unparseable_headers,
)
from abicheck.model.header_exclusion_record import (
    EXCLUDED_HEADERS_TOOLCHAIN_KEY,
    excluded_headers_from_toolchain,
    unparseable_header_warnings,
)

AGG = "/tmp/abicheck-agg-1234.hpp"


def _headers(n: int) -> list[Path]:
    return [Path(f"/inc/h{i}.h") for i in range(n)]


def _stderr_for(active: list[Path], bad: set[Path], style: int) -> str:
    """castxml/clang-shaped diagnostics for each *bad* header still active."""
    out: list[str] = []
    for i, h in enumerate(active):
        if h not in bad:
            continue
        if style == 0:  # #error directly in the header, reached from the aggregate
            out += [
                f"In file included from {AGG}:{i + 1}:",
                f"{h}:3:2: error: Unsupported compiler",
                '    3 | #error "Unsupported compiler"',
            ]
        elif style == 1:  # error in a private transitive include
            out += [
                f"In file included from {AGG}:{i + 1}:",
                f"In file included from {h}:7:",
                "/inc/detail/x.h:9:1: error: unknown type name 'foo'",
            ]
        else:  # fatal error located in the header itself, no chain
            out += [f"{h}:1:10: fatal error: 'sycl/sycl.hpp' file not found"]
        out.append("1 error generated.")
    return "\n".join(out)


def _fake_castxml(bad: set[Path], style: int, calls: list[list[Path]]):
    def attempt(active: list[Path]) -> str:
        calls.append(list(active))
        if any(h in bad for h in active):
            exc = SnapshotError("castxml failed (exit 1):\n...truncated")
            setattr(exc, "stderr", _stderr_for(active, bad, style))
            raise exc
        return "xml"

    return attempt


@pytest.mark.parametrize("seed", range(40))
def test_drops_exactly_the_failing_headers(seed):
    rng = random.Random(seed)
    headers = _headers(rng.randint(2, 12))
    bad = set(rng.sample(headers, rng.randint(1, len(headers) - 1)))
    style = seed % 3
    calls: list[list[Path]] = []
    result, excluded = parse_excluding_unparseable_headers(
        headers, _fake_castxml(bad, style, calls)
    )
    assert result == "xml"
    # Oracle: the bad set, in input order; survivors keep input order.
    assert excluded == [h for h in headers if h in bad]
    assert calls[-1] == [h for h in headers if h not in bad]
    # Deterministic: a second run makes the identical sequence of attempts.
    calls2: list[list[Path]] = []
    assert (
        parse_excluding_unparseable_headers(headers, _fake_castxml(bad, style, calls2))[
            1
        ]
        == excluded
    )
    assert calls2 == calls


def test_every_header_failing_reraises_original():
    headers = _headers(3)
    with pytest.raises(SnapshotError):
        parse_excluding_unparseable_headers(headers, _fake_castxml(set(headers), 0, []))


def test_single_header_is_never_reduced():
    headers = _headers(1)
    calls: list[list[Path]] = []
    with pytest.raises(SnapshotError):
        parse_excluding_unparseable_headers(
            headers, _fake_castxml(set(headers), 0, calls)
        )
    assert len(calls) == 1


def test_unattributable_error_reraises_without_retry():
    calls: list[list[Path]] = []

    def attempt(active):
        calls.append(active)
        exc = SnapshotError("castxml failed")
        setattr(exc, "stderr", f"{AGG}:1:1: error: something in the aggregate itself")
        raise exc

    with pytest.raises(SnapshotError):
        parse_excluding_unparseable_headers(_headers(4), attempt)
    assert len(calls) == 1


def test_toolchain_failure_is_not_header_specific():
    calls: list[list[Path]] = []

    def attempt(active):
        calls.append(active)
        exc = HeaderToolchainError("castxml too old")
        setattr(exc, "stderr", _stderr_for(active, {active[0]}, 0))
        raise exc

    with pytest.raises(HeaderToolchainError):
        parse_excluding_unparseable_headers(
            _headers(3),
            attempt,
            is_header_specific=lambda e: not isinstance(e, HeaderToolchainError),
        )
    assert len(calls) == 1


def test_attribution_ignores_warnings():
    headers = _headers(2)
    stderr = f"In file included from {AGG}:2:\n{headers[1]}:1:1: warning: deprecated"
    assert attribute_failing_headers(stderr, headers) == set()


def test_recorded_exclusions_become_warnings():
    from abicheck.model import AbiSnapshot

    old = AbiSnapshot(library="l", version="1")
    new = AbiSnapshot(library="l", version="2")
    new.ast_toolchain = {EXCLUDED_HEADERS_TOOLCHAIN_KEY: '["/n/inc/dnnl_sycl.hpp"]'}
    assert excluded_headers_from_toolchain(new.ast_toolchain) == [
        "/n/inc/dnnl_sycl.hpp"
    ]
    warnings = unparseable_header_warnings(old, new)
    assert any("dnnl_sycl.hpp" in w and "reduced evidence" in w for w in warnings)
    assert any("differ between the two sides" in w for w in warnings)
    old.ast_toolchain = {EXCLUDED_HEADERS_TOOLCHAIN_KEY: '["/o/inc/dnnl_sycl.hpp"]'}
    assert not any("differ" in w for w in unparseable_header_warnings(old, new))


@pytest.mark.integration
@pytest.mark.skipif(
    shutil.which("castxml") is None or shutil.which("gcc") is None,
    reason="needs castxml + gcc",
)
def test_real_castxml_directory_with_unparseable_header(tmp_path):
    from abicheck.dumper import dump

    inc = tmp_path / "include"
    inc.mkdir()
    (inc / "good.h").write_text("#pragma once\nint good_fn(int);\n")
    (inc / "bad_sycl.h").write_text(
        '#pragma once\n#error "Unsupported compiler"\nint bad_fn(void);\n'
    )
    src = tmp_path / "lib.c"
    src.write_text("int good_fn(int x){return x;}\nint bad_fn(void){return 0;}\n")
    lib = tmp_path / "libx.so"
    subprocess.run(["gcc", "-shared", "-fPIC", "-o", str(lib), str(src)], check=True)
    snap = dump(
        lib,
        [inc / "bad_sycl.h", inc / "good.h"],
        [inc],
        lang="c",
        header_backend="castxml",
    )
    names = {f.name for f in snap.functions if f.source_header}
    assert "good_fn" in names
    recorded = excluded_headers_from_toolchain(snap.ast_toolchain)
    assert [Path(p).name for p in recorded] == ["bad_sycl.h"]
