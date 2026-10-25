"""The release HTML's dependency graph for PE/COFF and Mach-O members, through
the CLI, with the release JSON as oracle.

Each fixture release is written as snapshot JSON (the same files ``dump``
writes), so no toolchain is needed. The expected edges are derived here from
the JSON document alone, by a procedure separate from the graph module's
matching: a needed entry names a member when its final path component equals
that member's (or its install name's), compared case-insensitively for PE.
"""

from __future__ import annotations

import json
import re
from pathlib import Path, PurePosixPath

import pytest
from test_release_html_report import _parse, _run

from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.model.macho_facts import MachoMetadata
from abicheck.model.pe_facts import PeMetadata
from abicheck.serialization import snapshot_to_json


def _func(name: str) -> Function:
    return Function(
        name=name, mangled=name, return_type="int", visibility=Visibility.PUBLIC
    )


def _pe_release() -> dict[str, AbiSnapshot]:
    """PE members; ``legacy.dll`` predates the delay-load tri-state, and
    ``bare.dll`` carries no format metadata at all."""
    return {
        "app.dll": AbiSnapshot(
            library="app.dll",
            version="1",
            functions=[_func("app")],
            pe=PeMetadata(
                imports={"foo.dll": ["f"], "KERNEL32.dll": ["Sleep"]},
                delay_imports={"ole32.dll": ["Co"]},
            ),
        ),
        "foo.dll": AbiSnapshot(
            library="foo.dll",
            version="1",
            functions=[_func("f")],
            pe=PeMetadata(imports={"KERNEL32.dll": ["Sleep"]}, delay_imports={}),
        ),
        "legacy.dll": AbiSnapshot(
            library="legacy.dll",
            version="1",
            functions=[_func("legacy")],
            pe=PeMetadata(imports={"FOO.DLL": ["f"]}, delay_imports=None),
        ),
        "bare.dll": AbiSnapshot(
            library="bare.dll", version="1", functions=[_func("bare")]
        ),
    }


def _macho_release() -> dict[str, AbiSnapshot]:
    """Mach-O members; ``libold.dylib`` predates the weak-load field."""
    return {
        "libtop.dylib": AbiSnapshot(
            library="libtop.dylib",
            version="1",
            functions=[_func("top")],
            macho=MachoMetadata(
                install_name="@rpath/libtop.dylib",
                dependent_libs=["/usr/lib/libSystem.B.dylib"],
                weak_dependent_libs=["@rpath/libmid.1.dylib"],
                reexported_libs=["@rpath/libre.dylib"],
            ),
        ),
        "libmid.dylib": AbiSnapshot(
            library="libmid.dylib",
            version="1",
            functions=[_func("mid")],
            macho=MachoMetadata(
                install_name="@rpath/libmid.1.dylib",
                dependent_libs=["/usr/lib/libSystem.B.dylib"],
                weak_dependent_libs=[],
            ),
        ),
        "libold.dylib": AbiSnapshot(
            library="libold.dylib",
            version="1",
            functions=[_func("old")],
            macho=MachoMetadata(
                install_name="@rpath/libold.dylib",
                dependent_libs=["@rpath/libmid.1.dylib"],
                weak_dependent_libs=None,
            ),
        ),
        "libre.dylib": AbiSnapshot(
            library="libre.dylib",
            version="1",
            functions=[_func("re")],
            macho=MachoMetadata(
                install_name="@rpath/libre.dylib",
                dependent_libs=["/usr/lib/libSystem.B.dylib"],
                weak_dependent_libs=[],
            ),
        ),
    }


def _write(tmp_path: Path, release: dict[str, AbiSnapshot]) -> tuple[Path, Path]:
    """Each member is written under its own library file name (``app.dll``,
    ``libtop.dylib``), as a real release directory names it, so the member
    identity is the name a PE import or a Mach-O install name refers to."""
    old_dir, new_dir = tmp_path / "old", tmp_path / "new"
    for d in (old_dir, new_dir):
        d.mkdir()
        for member, snap in release.items():
            (d / member).write_text(snapshot_to_json(snap), encoding="utf-8")
    return old_dir, new_dir


def _oracle_edges(doc: dict) -> set[tuple[str, str, str]]:
    """(source member, drawn target, sides) read off the release JSON."""
    members = {m["library"]: m for m in doc["libraries"]}

    def key(name: str, fmt: str) -> str:
        base = PurePosixPath(name.replace("\\", "/")).name
        return base.lower() if fmt == "pe" else base

    provider: dict[str, str] = {}
    for name, m in members.items():
        for facts in (m.get("dependencies") or {}).values():
            if "needed" not in facts:
                continue
            fmt = facts["format"]
            provider.setdefault(key(name, fmt), name)
            if facts.get("soname"):
                provider.setdefault(key(facts["soname"], fmt), name)

    seen: dict[tuple[str, str], set[str]] = {}
    for name, m in members.items():
        for side, facts in (m.get("dependencies") or {}).items():
            if "needed" not in facts:
                continue
            for lib in facts["needed"]:
                target = provider.get(key(lib, facts["format"]), lib)
                seen.setdefault((name, target), set()).add(side)
    return {
        (source, target, "old+new" if len(sides) == 2 else next(iter(sides)))
        for (source, target), sides in seen.items()
    }


@pytest.mark.parametrize(
    ("release_factory", "required_edge"),
    [
        (_pe_release, ("app.dll", "foo.dll", "old+new")),
        (_macho_release, ("libtop.dylib", "libmid.dylib", "old+new")),
    ],
    ids=["pe", "macho"],
)
def test_html_edges_equal_the_release_json_edges(
    tmp_path: Path, release_factory, required_edge
) -> None:
    old_dir, new_dir = _write(tmp_path, release_factory())
    out_json, out_html = tmp_path / "r.json", tmp_path / "r.html"
    _run(
        "compare",
        str(old_dir),
        str(new_dir),
        "-o",
        f"json={out_json}",
        "-o",
        f"html={out_html}",
    )
    doc = json.loads(out_json.read_text())
    parsed = _parse(out_html.read_text())

    expected = _oracle_edges(doc)
    assert expected, "fixture must record dependency edges"
    # A fixed edge the fixture states by construction, so a fact dropped from
    # the JSON cannot shrink the expected set and the drawn set together.
    assert required_edge in expected
    drawn = {(r[0], r[1], r[3]) for r in parsed.rows["dependency-edges"]}
    assert drawn == expected
    titles = {
        re.match(r"(.+) needs (.+) \(recorded on (.+)\)", t).groups()
        for t in parsed.edge_titles
    }
    assert titles == expected
    for member in doc["libraries"]:
        assert member["library"] in parsed.node_names


def test_states_the_release_json_records_are_disclosed_in_the_html(
    tmp_path: Path,
) -> None:
    release = {**_pe_release(), **_macho_release()}
    old_dir, new_dir = _write(tmp_path, release)
    out_json, out_html = tmp_path / "r.json", tmp_path / "r.html"
    _run(
        "compare",
        str(old_dir),
        str(new_dir),
        "-o",
        f"json={out_json}",
        "-o",
        f"html={out_html}",
    )
    doc = json.loads(out_json.read_text())
    by_name = {m["library"]: m for m in doc["libraries"]}
    assert set(by_name) == set(release)

    # Recorded per side, with the container format named.
    assert by_name["app.dll"]["dependencies"]["old"]["format"] == "pe"
    assert by_name["app.dll"]["dependencies"]["new"]["format"] == "pe"
    assert by_name["libtop.dylib"]["dependencies"]["old"]["format"] == "macho"
    # A legacy side names the fact family it did not capture; it is not
    # silently complete.
    assert by_name["legacy.dll"]["dependencies"]["old"]["incomplete"] == [
        "delay_imports"
    ]
    assert by_name["libold.dylib"]["dependencies"]["old"]["incomplete"] == [
        "weak_dependent_libs"
    ]
    # No format metadata is an explicit state, never an empty list.
    assert by_name["bare.dll"]["dependencies"]["old"]["state"] == "not_evaluated"

    page = out_html.read_text()
    assert "1 member(s) have dependencies not evaluated" in page
    assert "2 member(s) recorded only part of their facts" in page
    assert "PE import tables, Mach-O load commands" in page
