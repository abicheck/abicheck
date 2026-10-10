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
from abicheck.extract.castxml_header_compat import write_castxml_aggregate
from abicheck.extract.unparseable_header_fallback import (
    _scan_diagnostics,
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


def _agg_line(active: list[Path], h: Path) -> int:
    """Line on which the real castxml aggregate writer includes *h*.

    Read from a file ``write_castxml_aggregate`` actually wrote, never from a
    formula: the fixtures must reproduce the layout castxml really sees (a
    preamble include precedes the headers), or they would only prove the
    attribution agrees with a re-statement of itself.
    """
    agg = write_castxml_aggregate(active, ".hpp")
    try:
        target = f'#include "{h.resolve()}"'
        for n, line in enumerate(agg.read_text().splitlines(), start=1):
            if line == target:
                return n
        raise AssertionError(f"{h} is not included by the aggregate")
    finally:
        shutil.rmtree(agg.parent)


def _stderr_for(active: list[Path], bad: set[Path], style: int) -> str:
    """castxml/clang-shaped diagnostics for each *bad* header still active."""
    out: list[str] = []
    for h in active:
        if h not in bad:
            continue
        if style == 0:  # #error directly in the header, reached from the aggregate
            out += [
                f"In file included from {AGG}:{_agg_line(active, h)}:",
                f"{h}:3:2: error: Unsupported compiler",
                '    3 | #error "Unsupported compiler"',
            ]
        elif style == 1:  # error in a private transitive include
            out += [
                f"In file included from {AGG}:{_agg_line(active, h)}:",
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


@pytest.mark.parametrize("seed", range(30))
def test_transitive_listed_include_drops_only_the_aggregate_input(seed):
    """Listed A includes listed B; B fails only under a macro A sets. The
    aggregate frame names A as the input, so only A is dropped -- B, which
    parses fine on its own, is kept (a retry must not exclude it)."""
    rng = random.Random(1000 + seed)
    headers = _headers(rng.randint(3, 10))
    a, b = rng.sample(headers, 2)
    calls: list[list[Path]] = []

    def attempt(active: list[Path]) -> str:
        calls.append(list(active))
        if a in active:
            exc = SnapshotError("castxml failed")
            setattr(
                exc,
                "stderr",
                "\n".join(
                    [
                        f"In file included from {AGG}:{_agg_line(active, a)}:",
                        f"In file included from {a}:5:",
                        f"{b}:3:2: error: MODE_FROM_A requires C++",
                    ]
                ),
            )
            raise exc
        return "xml"

    result, excluded = parse_excluding_unparseable_headers(headers, attempt)
    assert result == "xml"
    assert excluded == [a]
    assert b in calls[-1]
    assert len(calls) == 2


def test_inner_listed_header_used_without_aggregate_frame():
    headers = _headers(3)
    stderr = f"In file included from {headers[0]}:4:\n{headers[2]}:1:1: error: x"
    # No aggregate frame: the outermost frame is itself listed, so the
    # innermost listed file in the chain is the attribution.
    assert _scan_diagnostics(stderr, headers)[0] == {2}


_VERSION_TEXT = "error: unknown type name '_Float128'"
_PLAIN_TEXT = "error: Unsupported compiler"


@pytest.mark.parametrize("cls", ["snapshot", "toolchain", "unsupported"])
@pytest.mark.parametrize("retry_failed", [False, True])
@pytest.mark.parametrize("msg_version", [False, True])
@pytest.mark.parametrize("retry_version", [False, True, None])
def test_castxml_retry_classification_matrix(
    cls, retry_failed, msg_version, retry_version
):
    """Exhaustive small domain. Oracle: a castxml frontend-version signature
    (in the message or in the language retry's diagnostics) or an
    unsupported castxml is never reducible; a HeaderToolchainError is
    reducible only when it is a failed language-mode retry; any other
    SnapshotError is reducible."""
    from abicheck.errors import UnsupportedCastxmlVersionError
    from abicheck.extract.headers.castxml.probe import (
        castxml_failure_is_header_specific,
    )

    ctor = {
        "snapshot": SnapshotError,
        "toolchain": HeaderToolchainError,
        "unsupported": UnsupportedCastxmlVersionError,
    }[cls]
    exc = ctor(_VERSION_TEXT if msg_version else _PLAIN_TEXT)
    exc.language_retry_failed = retry_failed
    if retry_version is not None:
        exc.attribution_stderr = _VERSION_TEXT if retry_version else _PLAIN_TEXT
    version = msg_version or bool(retry_version)
    if cls == "unsupported" or version:
        expected = False
    elif cls == "toolchain":
        expected = retry_failed
    else:
        expected = True
    assert castxml_failure_is_header_specific(exc) is expected


def test_failed_lang_retry_excludes_header_via_retry_diagnostics():
    """--lang c over a C++ header set where one header #errors in both modes:
    the C-mode HeaderToolchainError that dumper re-raises (marked as a failed
    language retry) is reduced using the C++ retry's diagnostics, which
    implicate only the failing header -- not every C++ header the C-mode
    errors hit."""
    from abicheck.extract.headers.castxml.probe import (
        castxml_failure_is_header_specific,
    )

    headers = _headers(4)
    bad = headers[2]
    calls: list[list[Path]] = []

    def attempt(active: list[Path]) -> str:
        calls.append(list(active))
        if bad not in active:
            return "xml"
        exc = HeaderToolchainError("castxml failed (C mode)")
        # C-mode diagnostics implicate every header (C++ syntax everywhere).
        exc.stderr = _stderr_for(active, set(active), 0)
        exc.language_retry_failed = True
        exc.attribution_stderr = _stderr_for(active, {bad}, 0)
        raise exc

    result, excluded = parse_excluding_unparseable_headers(
        headers, attempt, is_header_specific=castxml_failure_is_header_specific
    )
    assert (result, excluded) == ("xml", [bad])
    assert calls[-1] == [h for h in headers if h != bad]


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
    assert _scan_diagnostics(stderr, headers)[0] == set()


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
    names = {f.name for f in snap.declarations.functions if f.source_header}
    assert "good_fn" in names
    recorded = excluded_headers_from_toolchain(snap.ast_toolchain)
    assert [Path(p).name for p in recorded] == ["bad_sycl.h"]


def _conflict_stderr(active: list[Path], pairs: list[tuple[Path, Path]]) -> str:
    """clang-shaped redefinition diagnostics: error in *b*, note in *a*."""
    out: list[str] = []
    for a, b in pairs:
        if a not in active or b not in active:
            continue
        out += [
            f"In file included from {AGG}:{_agg_line(active, b)}:",
            f"{b}:1:16: error: typedef redefinition with different types",
            "    1 | typedef double clashing_t;",
            f"{a}:1:13: note: previous definition is here",
            "    1 | typedef int clashing_t;",
        ]
    out.append("1 error generated.")
    return "\n".join(out)


@pytest.mark.parametrize("seed", range(40))
def test_cross_header_conflict_is_never_resolved_by_dropping(seed):
    """A clash between two listed headers is not attributable to either one:
    the fallback must re-raise the original failure (no arbitrary drop), even
    when self-contained failures are mixed in -- the conflict wins, since the
    user's ``--exclude-header`` is the only non-arbitrary resolution."""
    rng = random.Random(seed)
    headers = _headers(rng.randint(3, 12))
    a, b = rng.sample(headers, 2)
    others = [h for h in headers if h not in (a, b)]
    self_bad = set(rng.sample(others, rng.randint(0, len(others) - 1)))
    calls: list[list[Path]] = []

    def attempt(active: list[Path]) -> str:
        calls.append(list(active))
        exc = SnapshotError("castxml failed")
        setattr(
            exc,
            "stderr",
            _stderr_for(active, self_bad, seed % 3)
            + "\n"
            + _conflict_stderr(active, [(a, b)]),
        )
        raise exc

    with pytest.raises(SnapshotError):
        parse_excluding_unparseable_headers(headers, attempt)
    assert calls == [headers]  # no reduced retry was attempted
    stderr = _conflict_stderr(headers, [(a, b)])
    assert _scan_diagnostics(stderr, headers)[1] == {headers.index(b)}


@pytest.mark.parametrize("seed", range(20))
def test_note_in_same_or_unlisted_header_stays_self_contained(seed):
    """Notes pointing into the failing header itself or into a non-listed
    (toolchain/private) file do not make an error a conflict."""
    rng = random.Random(seed)
    headers = _headers(rng.randint(2, 8))
    h = rng.choice(headers)
    i = headers.index(h)
    line = _agg_line(headers, h)
    note_file = h if seed % 2 else Path("/usr/include/c++/foo.h")
    stderr = "\n".join(
        [
            f"In file included from {AGG}:{line}:",
            f"{h}:4:1: error: no matching function",
            f"{note_file}:2:1: note: candidate function not viable",
        ]
    )
    assert _scan_diagnostics(stderr, headers)[0] == {i}
    assert _scan_diagnostics(stderr, headers)[1] == set()


@pytest.mark.skipif(shutil.which("castxml") is None, reason="needs castxml")
def test_real_castxml_redefinition_is_a_conflict(tmp_path):
    a = tmp_path / "a.h"
    b = tmp_path / "b.h"
    a.write_text("typedef int clashing_t;\n")
    b.write_text("typedef double clashing_t;\n")
    agg = tmp_path / "agg.cpp"
    agg.write_text(f'#include "{a}"\n#include "{b}"\n')
    proc = subprocess.run(
        ["castxml", "--castxml-output=1", "-o", str(tmp_path / "o.xml"), str(agg)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode != 0
    assert _scan_diagnostics(proc.stderr, [a, b])[1] == {1}


def _chain_lines(frames: list[tuple[str, int]], style: str) -> list[str]:
    """*frames* outermost-first, rendered as clang (one line per level,
    outermost first) or GCC (one group, innermost first, continuation lines)."""
    if style == "clang":
        return [f"In file included from {f}:{n}:" for f, n in frames]
    inner_first = list(reversed(frames))
    out = []
    for k, (f, n) in enumerate(inner_first):
        end = ":" if k == len(inner_first) - 1 else ","
        prefix = "In file included from " if k == 0 else " " * 17 + "from "
        out.append(f"{prefix}{f}:{n}{end}")
    return out


@pytest.mark.parametrize("style", ["clang", "gcc"])
@pytest.mark.parametrize("seed", range(30))
def test_multi_level_chains_attribute_to_the_aggregate_input(seed, style):
    """Oracle: the input the aggregate frame includes -- the chain's next
    entry -- is the one dropped, regardless of how deep the chain is,
    whether intermediate files are listed, or which compiler's frame layout
    is used. Chains are shaped as a compiler prints them: the aggregate's
    line includes the target, the target includes the rest."""
    rng = random.Random(seed)
    headers = _headers(rng.randint(2, 10))
    target = rng.randrange(len(headers))
    depth = rng.randint(0, 4)
    # Below the top-level input, any file may include any other.
    inner = [
        (
            str(rng.choice(headers)) if rng.random() < 0.5 else f"/inc/detail/d{j}.h",
            j + 3,
        )
        for j in range(depth)
    ]
    agg_frame = (AGG, _agg_line(headers, headers[target]))
    frames = [agg_frame, (str(headers[target]), 2), *inner] if depth else [agg_frame]
    err_file = f"/inc/detail/leaf{seed}.h" if depth else str(headers[target])
    stderr = "\n".join(
        [
            "some preamble",
            *_chain_lines(frames, style),
            f"{err_file}:1:2: error: boom",
            "    1 | #error boom",
            "1 error generated.",
        ]
    )
    assert _scan_diagnostics(stderr, headers)[0] == {target}


@pytest.mark.parametrize("style", ["clang", "gcc"])
@pytest.mark.parametrize("seed", range(40))
def test_attribution_is_independent_of_the_aggregate_layout(seed, style):
    """The aggregate's line numbers carry no meaning to the attribution.

    Whoever writes the aggregate owns its layout (a preamble include, blank
    lines, any header order); a rule that inverted it arithmetically blamed
    the healthy neighbour of the failing header as soon as a preamble line
    was added. Here every bad header sits on an arbitrary line of an
    arbitrary layout -- including lines no header occupies and line numbers
    past the header count -- and the oracle is the generator's own choice of
    bad headers, not any line formula.
    """
    rng = random.Random(7000 + seed)
    headers = _headers(rng.randint(1, 9))
    bad = set(rng.sample(headers, rng.randint(1, len(headers))))
    order = rng.sample(headers, len(headers))
    lines = rng.sample(range(1, 4 * len(headers) + 6), len(headers))
    line_of = dict(zip(order, sorted(lines)))
    out: list[str] = []
    for h in headers:
        if h not in bad:
            continue
        frames = [(AGG, line_of[h])]
        if rng.random() < 0.5:  # the error sits deeper, in a private include
            frames.append((str(h), rng.randint(1, 30)))
            leaf = f"/inc/detail/{h.stem}_impl.h"
        else:
            leaf = str(h)
        out += [*_chain_lines(frames, style), f"{leaf}:2:1: error: boom"]
    expected = {headers.index(h) for h in bad}
    assert _scan_diagnostics("\n".join(out), headers)[0] == expected


def test_an_error_in_the_aggregate_preamble_is_attributed_to_no_header():
    """An error whose chain runs through the writer's own preamble names no
    input: the preamble is not one of the headers, so nothing is dropped and
    the caller re-raises the original failure instead of blaming a header."""
    headers = _headers(3)
    agg = write_castxml_aggregate(headers, ".hpp")
    try:
        preamble = agg.read_text().splitlines()[0].split('"')[1]
    finally:
        shutil.rmtree(agg.parent)
    stderr = "\n".join(
        [f"In file included from {AGG}:1:", f"{preamble}:3:1: error: boom"]
    )
    assert _scan_diagnostics(stderr, headers)[0] == set()


def test_gcc_group_does_not_leak_into_the_next_diagnostic():
    headers = _headers(3)
    stderr = "\n".join(
        [
            *_chain_lines(
                [(AGG, _agg_line(headers, headers[0])), (str(headers[0]), 2)], "gcc"
            ),
            "/inc/detail/b.h:1:1: error: first",
            "    1 | x",
            f"{headers[2]}:1:1: error: second",
        ]
    )
    assert _scan_diagnostics(stderr, headers)[0] == {0, 2}


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("castxml") is None, reason="needs castxml")
@pytest.mark.parametrize(
    ("n", "bad"), [(n, bad) for n in (2, 3, 4) for bad in range(n)]
)
def test_real_castxml_attributes_the_failing_header_at_every_position(tmp_path, n, bad):
    """The aggregate header castxml parses must keep header ``i`` on line ``i+1``.

    Bug class: anything the aggregate writer emits ahead of the listed headers
    (the ``_Float128`` preamble include did) shifts every line by one, so the
    fallback blamed the *next* header -- or none -- and the whole dump failed.
    Enumerates every failing position over 2-4 headers, through real castxml;
    the oracle is the index the test chose, not the attribution arithmetic.
    """
    from abicheck.extract.headers.castxml.backend import castxml_dump as _castxml_dump
    from abicheck.extract.headers.castxml.probe import (
        castxml_dump_excluding_unparseable,
    )

    headers = []
    for i in range(n):
        h = tmp_path / f"h{i}.h"
        body = '#error "unparseable"\n' if i == bad else ""
        h.write_text(f"#pragma once\n{body}int fn{i}(int);\n")
        headers.append(h)
    _root, excluded = castxml_dump_excluding_unparseable(
        _castxml_dump, headers, [tmp_path], "cc", lang="c"
    )
    assert excluded == [headers[bad]]
