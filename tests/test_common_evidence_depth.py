"""A pair whose operands reach different evidence rungs is judged at the rung
both reach (``policy/common_evidence_depth.py``), so headers given for one side
only never read as a library change.

The oracle: the two operands describe the *same* library (one side merely
carries its header declarations too), so no comparison of them may report a
removal, an addition or a break.
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck import checker
from abicheck.cli import main
from abicheck.model import (
    AbiSnapshot,
    Function,
    RecordType,
    ScopeOrigin,
    TypeField,
    Visibility,
)
from abicheck.model.elf_facts import ElfMetadata, ElfSymbol
from abicheck.policy.common_evidence_depth import (
    asymmetric_header_evidence_warning,
    common_evidence_depth,
    effective_compare_depth,
)
from abicheck.policy.depth_projection import project_pair_to_depth
from abicheck.serialization import save_snapshot

_EXPORTS = ("_ZN3KLUD2Ev", "_ZN3KLUD1Ev", "_Z7klu_getP3KLD")


def _snap(*, headers: bool, version: str = "1") -> AbiSnapshot:
    """The same library, with (header-parsed) or without its declarations.

    A header parse names the destructor ``~KLU`` and sees ``KLD``; the
    header-less dump of the same binary has only the exported symbols.
    """
    functions = (
        [
            Function(
                name="KLU::~KLU",
                mangled="~KLU",
                return_type="void",
                visibility=Visibility.PUBLIC,
                origin=ScopeOrigin.PUBLIC_HEADER,
            ),
            Function(
                name="klu_get",
                mangled="_Z7klu_getP3KLD",
                return_type="int",
                visibility=Visibility.PUBLIC,
                origin=ScopeOrigin.PUBLIC_HEADER,
            ),
        ]
        if headers
        else []
    )
    types = (
        [
            RecordType(
                name="KLD",
                kind="struct",
                size_bits=192,
                fields=[TypeField(name="d", type="int")],
                origin=ScopeOrigin.PUBLIC_HEADER,
            )
        ]
        if headers
        else []
    )
    snap = AbiSnapshot(
        library="libk.so.1",
        version=version,
        functions=functions,
        types=types,
        from_headers=headers,
        elf_only_mode=not headers,
    )
    snap.elf = ElfMetadata(
        soname="libk.so.1", symbols=[ElfSymbol(name=n) for n in _EXPORTS]
    )
    return snap


@pytest.mark.parametrize(
    ("old_headers", "new_headers"), list(itertools.product((False, True), repeat=2))
)
def test_common_depth_is_binary_exactly_when_one_side_has_headers(
    old_headers: bool, new_headers: bool
) -> None:
    got = common_evidence_depth(_snap(headers=old_headers), _snap(headers=new_headers))
    assert got == ("binary" if old_headers != new_headers else None)


@pytest.mark.parametrize("requested", [None, "binary", "headers", "build", "source"])
@pytest.mark.parametrize(
    ("old_headers", "new_headers"), list(itertools.product((False, True), repeat=2))
)
def test_an_explicit_depth_always_wins(
    requested: str | None, old_headers: bool, new_headers: bool
) -> None:
    old, new = _snap(headers=old_headers), _snap(headers=new_headers)
    expected = requested if requested is not None else common_evidence_depth(old, new)
    assert effective_compare_depth(requested, old, new) == expected


@pytest.mark.parametrize("headers_side", ["old", "new"])
def test_the_warning_names_the_side_that_had_headers(headers_side: str) -> None:
    old = _snap(headers=headers_side == "old")
    text = asymmetric_header_evidence_warning(old)
    other = "new" if headers_side == "old" else "old"
    assert text.startswith(f"headers were given for the {headers_side} side only")
    assert f"the {other} side has no header-level evidence" in text


@pytest.mark.parametrize("headers_side", ["old", "new"])
@pytest.mark.parametrize("prior_compare", [False, True])
def test_projected_asymmetric_pair_reports_nothing(
    headers_side: str, prior_compare: bool
) -> None:
    old = _snap(headers=headers_side == "old")
    new = _snap(headers=headers_side == "new", version="2")
    if prior_compare:
        # A compare builds the snapshot's lookup indexes; a projection made
        # afterwards must not keep serving the stripped declarations.
        checker.compare(old, old)
        checker.compare(new, new)
    o, n = project_pair_to_depth(old, new, common_evidence_depth(old, new))
    result = checker.compare(o, n)
    assert result.verdict == checker.Verdict.NO_CHANGE, [
        (c.kind.value, c.symbol) for c in result.changes
    ]


@pytest.mark.parametrize("headers_side", ["old", "new"])
def test_cli_compare_of_an_asymmetric_pair_is_not_a_break(
    tmp_path: Path, headers_side: str
) -> None:
    old_p, new_p, out = (
        tmp_path / "old.json",
        tmp_path / "new.json",
        tmp_path / "r.json",
    )
    save_snapshot(_snap(headers=headers_side == "old"), old_p)
    save_snapshot(_snap(headers=headers_side == "new", version="2"), new_p)
    result = CliRunner().invoke(
        main, ["compare", str(old_p), str(new_p), "-o", f"json={out}"]
    )
    report = json.loads(out.read_text())
    # No break and no removal. Without DWARF the binary-depth comparison
    # cannot confirm layout, so it reads UNCONFIRMED (exit 1, ADR-090), never
    # a break (it was BREAKING, exit 4, before).
    assert (report["verdict"], result.exit_code) == ("UNCONFIRMED", 1), result.output
    assert not any(
        c["kind"] in {"func_removed", "func_added", "type_removed"}
        for c in report["changes"]
    )
    assert any(
        w.startswith(f"headers were given for the {headers_side} side only")
        for w in report["coverage_warnings"]
    )


_HEADER = "struct KLU { virtual ~KLU(); int a; char c; };\nstruct KLD : KLU { int d; };\nint klu_get(KLD* p);\n"
_SOURCE = (
    '#include "a.h"\nKLU::~KLU() {}\nint klu_get(KLD* p) { return p->a + p->d; }\n'
)


@pytest.mark.integration
@pytest.mark.parametrize("headers_side", ["old", "new"])
def test_real_library_with_headers_on_one_side_is_unchanged(
    tmp_path: Path, headers_side: str
) -> None:
    import shutil
    import subprocess

    if shutil.which("g++") is None or shutil.which("castxml") is None:
        pytest.skip("needs g++ and castxml")
    (tmp_path / "a.h").write_text(_HEADER)
    (tmp_path / "a.cpp").write_text(_SOURCE)
    so = tmp_path / "libk.so"
    subprocess.run(
        ["g++", "-g", "-shared", "-fPIC", "-o", str(so), str(tmp_path / "a.cpp")],
        check=True,
    )
    out = tmp_path / "r.json"
    result = CliRunner().invoke(
        main,
        [
            "compare",
            str(so),
            str(so),
            "--header",
            f"{headers_side}={tmp_path / 'a.h'}",
            "-o",
            f"json={out}",
        ],
    )
    report = json.loads(out.read_text())
    assert (report["verdict"], result.exit_code) == ("NO_CHANGE", 0), [
        (c["kind"], c.get("symbol")) for c in report["changes"]
    ]


@pytest.mark.parametrize("blocked_side", ["old", "new"])
@pytest.mark.parametrize("headers_side", ["old", "new"])
def test_an_unreadable_debug_section_is_no_evidence_of_asymmetry(
    blocked_side: str, headers_side: str
) -> None:
    import dataclasses

    snaps = {s: _snap(headers=s == headers_side) for s in ("old", "new")}
    # ``from_headers`` lives in the debug section: unreadable, it is a default.
    snaps[blocked_side] = dataclasses.replace(
        snaps[blocked_side], unreadable_sections=(("debug", "failed"),)
    )
    assert common_evidence_depth(snaps["old"], snaps["new"]) is None
