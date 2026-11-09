"""ADR-090 end to end: evidence combinations x change kinds, through the CLI.

Real libraries built with gcc, compared with the public ``abicheck compare``
command, judged on all three user-facing surfaces: the process exit code, the
JSON ``verdict``, and the terminal and Markdown headlines.

Each side is built in one of three shapes:

* ``dwarf``    -- ``-g``, unstripped: DWARF present;
* ``symtab``   -- no ``-g``, unstripped: a full symbol table, no DWARF;
* ``stripped`` -- ``-g`` then ``strip``: only ``.dynsym`` remains.

Each pair is compared with no headers, or with per-side headers.

The oracle is written from what each evidence shape can observe, not from the
implementation. A side has type-level evidence iff it has DWARF or a header.
A change visible in the dynamic symbol table (a function removal) is always a
break (exit 4). A change that lives only in types (a parameter added to a C
function, a struct's layout behind an exported function) is a break when both
sides have type evidence; otherwise it is either still observed as a break or
reported ``UNCONFIRMED`` with exit 1, and never a clean claim with exit 0. A
no-change pair is clean (exit 0) only when both sides have type evidence, or
when the two libraries are byte-identical and neither side has a header
(ADR-090 D1(c): one input, which can carry no trace of a type-only change).
"""

from __future__ import annotations

import filecmp
import itertools
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from tests.oracles.absence import CLEAN_CLAIM_PHRASES, COMPATIBILITY_CLAIMS

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not sys.platform.startswith("linux")
        or shutil.which("gcc") is None
        or shutil.which("strip") is None,
        reason="builds ELF libraries with gcc and strip on Linux",
    ),
]

#: change kind -> (v1 source, v2 source, v1 header, v2 header, symbol-visible?)
CHANGES: dict[str, tuple[str, str, str, str, bool]] = {
    "func_removed": (
        "int add(int a, int b) { return a + b; }\nint keep(void) { return 1; }\n",
        "int keep(void) { return 1; }\n",
        "int add(int a, int b);\nint keep(void);\n",
        "int keep(void);\n",
        True,
    ),
    "param_added": (
        "int add(int a, int b) { return a + b; }\nint keep(void) { return 1; }\n",
        "int add(int a, int b, int c) { return a + b + c; }\nint keep(void) { return 1; }\n",
        "int add(int a, int b);\nint keep(void);\n",
        "int add(int a, int b, int c);\nint keep(void);\n",
        False,
    ),
    "struct_layout": (
        '#include "lib.h"\nint get(struct P *p) { return p->x; }\n',
        '#include "lib.h"\nint get(struct P *p) { return p->x; }\n',
        "struct P { int x; int y; };\nint get(struct P *p);\n",
        "struct P { int x; long z; int y; };\nint get(struct P *p);\n",
        False,
    ),
    "no_change": (
        "static int helper(void) { return 2; }\nint keep(void) { return helper(); }\n",
        "static int helper(void) { return 3; }\nint keep(void) { return helper(); }\n",
        "int keep(void);\n",
        "int keep(void);\n",
        False,
    ),
}

SHAPES = ("dwarf", "symtab", "stripped")
PAIRS = [(o, n) for o, n in itertools.product(SHAPES, SHAPES)]
CELLS = [
    (kind, old, new, headers)
    for kind in CHANGES
    for old, new in PAIRS
    for headers in (False, True)
]


def _has_types(shape: str, headers: bool) -> bool:
    return headers or shape == "dwarf"


def _build(root: Path, kind: str, side: str, shape: str) -> tuple[Path, Path]:
    v1, v2, h1, h2, _ = CHANGES[kind]
    src, hdr = (v1, h1) if side == "v1" else (v2, h2)
    d = root / kind / side
    d.mkdir(parents=True, exist_ok=True)
    (d / "lib.h").write_text(hdr)
    (d / "lib.c").write_text(src)
    lib = d / f"lib_{shape}.so"
    if not lib.exists():
        flags = [] if shape == "symtab" else ["-g"]
        subprocess.run(
            [
                "gcc",
                "-shared",
                "-fPIC",
                "-O0",
                *flags,
                "-o",
                str(lib),
                str(d / "lib.c"),
            ],
            check=True,
            capture_output=True,
        )
        if shape == "stripped":
            subprocess.run(["strip", str(lib)], check=True)
    return lib, d / "lib.h"


def _compare(
    tmp: Path, old: Path, new: Path, headers: tuple[Path, Path] | None, tag: str
):
    from abicheck.cli import main

    out_json, out_md = tmp / f"{tag}.json", tmp / f"{tag}.md"
    args = [
        "compare",
        str(old),
        str(new),
        "-o",
        f"json={out_json}",
        "-o",
        f"markdown={out_md}",
        "-o",
        "terminal=-",
    ]
    if headers is not None:
        args += ["--header", f"old={headers[0]}", "--header", f"new={headers[1]}"]
    res = CliRunner().invoke(main, args, catch_exceptions=False)
    report = json.loads(out_json.read_text(encoding="utf-8"))
    return (
        res.exit_code,
        report,
        {"terminal": res.output, "markdown": out_md.read_text(encoding="utf-8")},
    )


@pytest.fixture(scope="module")
def lab(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("adr090_matrix")


def _expected(
    kind: str, old: str, new: str, headers: bool, identical: bool
) -> tuple[frozenset[int], frozenset[str]]:
    """The oracle: the exit codes and verdicts each cell may produce.

    A symbol-table change is always a break. Byte-identical libraries with no
    header on either side are one input (D1(c)), clean by construction. With
    type evidence on both sides, a type-only change is an observed break and
    no change is a clean claim. Otherwise no change is UNCONFIRMED, and a
    type-only change is either still observed or UNCONFIRMED -- never clean.
    """
    if CHANGES[kind][4]:
        return frozenset({4}), frozenset({"BREAKING"})
    typed = [_has_types(side, headers) for side in (old, new)]
    if identical and not any(typed):
        return frozenset({0}), frozenset({"NO_CHANGE"})
    if all(typed):
        if kind == "no_change":
            return frozenset({0}), COMPATIBILITY_CLAIMS
        return frozenset({2, 4}), frozenset({"API_BREAK", "BREAKING"})
    if kind == "no_change":
        return frozenset({1}), frozenset({"UNCONFIRMED"})
    return frozenset({1, 2, 4}), frozenset({"UNCONFIRMED", "API_BREAK", "BREAKING"})


def _assert_surfaces_agree(report: dict, rendered: dict[str, str], tag: str) -> None:
    """UNCONFIRMED on every surface, or on none; a triggered run states no
    clean claim anywhere."""
    unconfirmed = report["verdict"] == "UNCONFIRMED"
    assert (report["run_outcome"]["compatibility"] == "UNCONFIRMED") is unconfirmed, tag
    assert ("Compatibility: UNCONFIRMED" in rendered["terminal"]) is unconfirmed, tag
    assert ("`UNCONFIRMED`" in rendered["markdown"]) is unconfirmed, tag
    if not unconfirmed:
        return
    assert report["unconfirmed"]["exit_contribution"] == 1, tag
    assert report["release_recommendation"]["version_bump"] == "undetermined", tag
    leaks = [
        (name, phrase)
        for name, text in rendered.items()
        for phrase in CLEAN_CLAIM_PHRASES
        if phrase in text
    ]
    assert leaks == [], tag


@pytest.mark.parametrize(("kind", "old", "new", "headers"), CELLS)
def test_evidence_matrix_never_reads_clean_without_type_evidence(
    lab: Path, kind: str, old: str, new: str, headers: bool
) -> None:
    old_lib, old_h = _build(lab, kind, "v1", old)
    new_lib, new_h = _build(lab, kind, "v2", new)
    tag = f"{kind}-{old}-{new}-{int(headers)}"
    code, report, rendered = _compare(
        lab, old_lib, new_lib, (old_h, new_h) if headers else None, tag
    )
    # D1(c), decided here from the files themselves: a type-only C change
    # built without -g can compile to byte-identical libraries, which carry
    # no trace of it at all.
    identical = filecmp.cmp(old_lib, new_lib, shallow=False)
    codes, verdicts = _expected(kind, old, new, headers, identical)
    assert code in codes, (tag, code)
    assert report["verdict"] in verdicts, (tag, report["verdict"])
    # Exit 1 and UNCONFIRMED imply each other on every cell.
    assert (code == 1) is (report["verdict"] == "UNCONFIRMED"), tag
    _assert_surfaces_agree(report, rendered, tag)


@pytest.mark.parametrize("shape", SHAPES)
def test_byte_identical_pair_is_exempt(lab: Path, shape: str) -> None:
    """D1(c): one input compared with itself is NO_CHANGE by construction."""
    lib, _ = _build(lab, "no_change", "v1", shape)
    copy = lib.with_name(f"copy_{lib.name}")
    shutil.copy(lib, copy)
    code, report, rendered = _compare(lab, lib, copy, None, f"identical-{shape}")
    assert (code, report["verdict"]) == (0, "NO_CHANGE")
    assert "unconfirmed" not in report


def test_byte_identical_pair_with_one_sided_header_is_not_exempt(lab: Path) -> None:
    """ADR-090 matrix row 17: identical bytes, a header on one side only --
    the effective inputs differ, so the one-sided gap is real."""
    lib, hdr = _build(lab, "no_change", "v1", "stripped")
    copy = lib.with_name(f"copy1_{lib.name}")
    shutil.copy(lib, copy)
    from abicheck.cli import main

    out = lab / "row17.json"
    res = CliRunner().invoke(
        main,
        ["compare", str(lib), str(copy), "--header", f"new={hdr}", "-o", f"json={out}"],
        catch_exceptions=False,
    )
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["verdict"] not in COMPATIBILITY_CLAIMS
    assert res.exit_code != 0


def test_stored_snapshots_read_the_same_as_live(lab: Path) -> None:
    """Maintainer answer 3: `dump` then `compare` of stripped sides is
    UNCONFIRMED / 1 exactly like the live compare."""
    from abicheck.cli import main

    paths = []
    for side in ("v1", "v2"):
        lib, _ = _build(lab, "param_added", side, "stripped")
        snap = lab / f"stored-{side}.json"
        CliRunner().invoke(
            main, ["dump", str(lib), "-o", str(snap)], catch_exceptions=False
        )
        paths.append(snap)
    out = lab / "stored.json"
    res = CliRunner().invoke(
        main,
        ["compare", str(paths[0]), str(paths[1]), "-o", f"json={out}"],
        catch_exceptions=False,
    )
    report = json.loads(out.read_text(encoding="utf-8"))
    assert (res.exit_code, report["verdict"]) == (1, "UNCONFIRMED")


def test_warn_mode_keeps_the_label_and_exits_zero(lab: Path, tmp_path: Path) -> None:
    """D4: `assurance.unconfirmed: warn` -- label stays, exit 0."""
    from abicheck.cli import main

    old, _ = _build(lab, "param_added", "v1", "stripped")
    new, _ = _build(lab, "param_added", "v2", "stripped")
    cfg = tmp_path / ".abicheck.yml"
    cfg.write_text("assurance:\n  unconfirmed: warn\n")
    out = tmp_path / "warn.json"
    res = CliRunner().invoke(
        main,
        [
            "compare",
            str(old),
            str(new),
            "--config",
            str(cfg),
            "-o",
            f"json={out}",
            "-o",
            "terminal=-",
        ],
        catch_exceptions=False,
    )
    report = json.loads(out.read_text(encoding="utf-8"))
    assert res.exit_code == 0
    assert report["verdict"] == "UNCONFIRMED"
    assert report["unconfirmed"]["mode"] == "warn"
    assert "Compatibility: UNCONFIRMED" in res.output
