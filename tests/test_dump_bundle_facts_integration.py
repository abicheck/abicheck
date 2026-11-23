"""``dump --bundle-facts-out`` on real gcc-built libraries.

The JSON-snapshot parity cases live in ``tests/test_dump_bundle_facts.py``.
These add what those cannot reach: members dumped from ELF binaries (with
and without headers), and the round trip through the stored driver, which
pairs a stored OLD side with a live directory of binaries.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.cli import main
from abicheck.storage.evidence_digest import VOLATILE_CAPTURE_FIELDS
from abicheck.storage.snapshot_encode import sectioned_document_for_write

#: What a member snapshot records about its *capture* rather than the
#: binary: the evidence digest's volatile set, plus the file size the
#: comparison's member path stamps beside the mtime (a scalar `dump` stamps
#: neither).
_CAPTURE_FIELDS = VOLATILE_CAPTURE_FIELDS | {"source_size"}


def _without_capture(doc: dict, fields: frozenset[str] | set[str]) -> dict:
    """Strip only capture fields, preserving section headers and extension facts."""
    if "sections" not in doc:
        return {k: v for k, v in doc.items() if k not in fields}
    return {
        **doc,
        "sections": {
            name: {
                **section,
                "payload": {
                    k: v for k, v in section["payload"].items() if k not in fields
                },
            }
            if isinstance(section.get("payload"), dict)
            else section
            for name, section in doc["sections"].items()
        },
    }


def _evidence(doc: dict) -> dict:
    members = {
        key: _without_capture(snap, _CAPTURE_FIELDS)
        for key, snap in doc["per_library_snapshots"].items()
    }
    return {**doc, "per_library_snapshots": members}


pytestmark = [
    pytest.mark.skipif(
        sys.platform != "linux",
        reason="GNU ld -soname and ELF members; bundle facts are ELF/Linux-only "
        "(ADR-018/ADR-023).",
    ),
]

#: (library, OLD source, NEW source): a removal, an addition, a return-type
#: change, and an unchanged library.
_LIBS = (
    (
        "liba",
        "int a(void){return 1;}\nint b(void){return 2;}\n",
        "int a(void){return 1;}\n",
    ),
    ("libb", "int v = 1;\n", "int v = 1;\nint w = 2;\n"),
    ("libc", "int c(void){return 3;}\n", "int c(void){return 3;}\n"),
)


def _gcc() -> str:
    gcc = shutil.which("gcc")
    if gcc is None:
        pytest.skip("gcc not available")
    return gcc


def _build(root: Path, side: int) -> tuple[Path, Path]:
    libs, inc = root / "lib", root / "include"
    libs.mkdir(parents=True)
    inc.mkdir(parents=True)
    for name, *sources in _LIBS:
        src = root / f"{name}.c"
        src.write_text(sources[side], encoding="utf-8")
        decls = "".join(
            f"{line.split('{')[0].strip()};\n"
            if "(" in line
            else f"extern {line.split('=')[0].strip()};\n"
            for line in sources[side].splitlines()
        )
        (inc / f"{name}.h").write_text(decls, encoding="utf-8")
        res = subprocess.run(
            [
                _gcc(),
                "-shared",
                "-fPIC",
                "-g",
                "-O0",
                str(src),
                "-o",
                str(libs / f"{name}.so"),
                f"-Wl,-soname,{name}.so",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode != 0:
            pytest.fail(f"gcc failed for {name}: {res.stderr}")
    return libs, inc


def _invoke(*args: str):
    return CliRunner().invoke(main, list(args))


@pytest.mark.parametrize("with_headers", [False, True], ids=["binary", "headers"])
@pytest.mark.integration
def test_dump_matches_compare_and_round_trips(
    tmp_path: Path, with_headers: bool
) -> None:
    if with_headers and shutil.which("castxml") is None:
        pytest.skip("castxml not available")
    old_libs, old_inc = _build(tmp_path / "old", 0)
    new_libs, new_inc = _build(tmp_path / "new", 1)
    old_h = ["-H", str(old_inc)] if with_headers else []
    sided = ["-H", f"old={old_inc}", "-H", f"new={new_inc}"] if with_headers else []
    dumped, compared = tmp_path / "dumped.json", tmp_path / "compared.json"

    # `--label old`: compare's own OLD-side default label, the one field a
    # capture of the same release legitimately differs in.
    result = _invoke(
        "dump",
        str(old_libs),
        *old_h,
        "--label",
        "old",
        "--bundle-facts-out",
        str(dumped),
    )
    assert result.exit_code == 0, result.output
    live = _invoke(
        "compare",
        str(old_libs),
        str(new_libs),
        *sided,
        "--bundle-facts-out",
        str(compared),
        "-o",
        "json=-",
    )
    assert live.exit_code in (0, 1, 2, 4), live.output
    dumped_doc = json.loads(dumped.read_text(encoding="utf-8"))
    compared_doc = json.loads(compared.read_text(encoding="utf-8"))
    assert _evidence(dumped_doc) == _evidence(compared_doc)
    assert sorted(dumped_doc["per_library_snapshots"]) == [
        "liba.so",
        "libb.so",
        "libc.so",
    ]
    assert dumped_doc["degraded_members"] == {}

    new_h = ["-H", f"new={new_inc}"] if with_headers else []
    stored = _invoke("compare", str(dumped), str(new_libs), *new_h, "-o", "json=-")
    assert stored.exit_code == live.exit_code, stored.output
    live_doc, stored_doc = json.loads(live.stdout), json.loads(stored.stdout)
    assert stored_doc["verdict"] == live_doc["verdict"]
    assert {lib["library"]: lib["verdict"] for lib in stored_doc["libraries"]} == {
        lib["library"]: lib["verdict"] for lib in live_doc["libraries"]
    }


@pytest.mark.integration
def test_the_label_names_every_member(tmp_path: Path) -> None:
    old_libs, _inc = _build(tmp_path / "old", 0)
    dumped = tmp_path / "dumped.json"
    result = _invoke(
        "dump", str(old_libs), "--label", "9.9", "--bundle-facts-out", str(dumped)
    )
    assert result.exit_code == 0, result.output
    from abicheck.serialization import load_bundle_facts

    facts = load_bundle_facts(dumped)
    assert {s.version for s in facts.per_library_snapshots.values()} == {"9.9"}


@pytest.mark.parametrize("with_headers", [False, True], ids=["binary", "headers"])
@pytest.mark.integration
def test_each_member_is_the_libraries_own_dump(
    tmp_path: Path, with_headers: bool
) -> None:
    """Independent oracle: every captured member equals what a standalone
    `abicheck dump <library>` writes for it under the same inputs -- a
    document the capture's producer never touched -- apart from capture
    metadata."""
    from abicheck.serialization import (
        load_bundle_facts,
        load_snapshot,
    )

    if with_headers and shutil.which("castxml") is None:
        pytest.skip("castxml not available")
    old_libs, old_inc = _build(tmp_path / "old", 0)
    headers = ["-H", str(old_inc)] if with_headers else []
    dumped = tmp_path / "dumped.json"
    result = _invoke(
        "dump",
        str(old_libs),
        *headers,
        "--label",
        "9.9",
        "--bundle-facts-out",
        str(dumped),
    )
    assert result.exit_code == 0, result.output
    facts = load_bundle_facts(dumped)

    # A standalone `dump` also stamps the run's provenance (git commit,
    # `dump_provenance`, `workflows.provenance.stamp_provenance`); a release
    # capture's members carry none, the same as `compare`'s.
    stamped = _CAPTURE_FIELDS | {"git_commit", "git_tag", "dump_provenance"}

    def _strip(snapshot) -> dict:
        return _without_capture(sectioned_document_for_write(snapshot), stamped)

    assert sorted(facts.per_library_snapshots) == [f"{name}.so" for name, *_ in _LIBS]
    for key, member in facts.per_library_snapshots.items():
        alone = tmp_path / f"{key}.alone.json"
        single = _invoke(
            "dump", str(old_libs / key), *headers, "--label", "9.9", "-o", str(alone)
        )
        assert single.exit_code == 0, single.output
        assert _strip(member) == _strip(load_snapshot(alone)), key


@pytest.mark.parametrize("project_config", [False, True])
def test_capture_and_compare_apply_member_contexts(tmp_path: Path, project_config: bool) -> None:
    from abicheck.serialization import load_bundle_facts

    clang = shutil.which("clang++")
    if clang is None:
        pytest.skip("requires clang++ for both header contexts")
    libs, _ = _build(tmp_path, 1)
    (tmp_path / "api.hpp").write_text(
        "#if PROFILE == 1\nextern int cpu_only();\n"
        "#elif PROFILE == 2\nextern int queue_only();\n#else\n#error missing profile\n#endif\n"
    )
    manifest = tmp_path / "members.json"
    manifest.write_text(
        json.dumps(
            {
                f"lib{lib}.so": {
                    "headers": ["api.hpp"],
                    "frontend": "clang",
                    "gcc_path": clang,
                    "gcc_options": [f"-DPROFILE={profile}"],
                }
                for lib, profile in [("a", 1), ("b", 2)]
            }
        )
    )
    baseline = tmp_path / "base.json"
    flags = ["--bundle-facts-library-manifest", str(manifest)]
    if project_config:
        config = tmp_path / ".abicheck.yml"
        config.write_text(
            json.dumps(
                {
                    "profiles": {
                        lib: {
                            "compile": {
                                "frontend": "clang",
                                "binding": "llvm",
                                "abi_macros": {"PROFILE": str(profile)},
                            }
                        }
                        for lib, profile in [("a", 1), ("b", 2)]
                    },
                    "release": {
                        "bindings": {"llvm": clang},
                        "libraries": {
                            f"lib{lib}.so": {"headers": ["api.hpp"], "profiles": [lib]}
                            for lib in ("a", "b")
                        },
                    },
                }
            )
        )
        (libs / "api.hpp").write_bytes((tmp_path / "api.hpp").read_bytes())
        flags = ["--config", str(config)]
    captured = _invoke("dump", str(libs), "--bundle-facts-out", str(baseline), *flags)
    assert captured.exit_code == 0, captured.output
    facts = load_bundle_facts(baseline)
    assert not facts.degraded_members
    for key, expected in [
        ("liba.so", {"cpu_only"}),
        ("libb.so", {"queue_only"}),
        ("libc.so", set()),
    ]:
        found = {
            f.name for f in facts.per_library_snapshots[key].declarations.functions
        }
        assert found & {"cpu_only", "queue_only"} == expected
    report = tmp_path / "report.json"
    compared = _invoke(
        "compare", str(baseline), str(libs), *flags, "-o", f"json={report}"
    )
    assert compared.exit_code == 0, compared.output
    result = json.loads(report.read_text())
    assert len(result["libraries"]) == 3
    assert {member["verdict"] for member in result["libraries"]} == {"NO_CHANGE"}
    assert not result["extraction_failures"]
