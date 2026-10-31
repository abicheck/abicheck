"""``abicheck dump RELEASE --bundle-facts-out PATH``: a release's BundleFacts
document captured without a comparison.

Known gap "``compare --bundle-facts-out`` stays a ``compare`` flag because
``dump`` has no release fan-out" (closed 2026-10-31). The capture composes
the comparison's own owners (the one-sided release resolver, the member-dump
resolver, ``write_bundle_facts_out``), so the oracle is the comparison:

* **Parity.** For every release shape below, ``dump OLD --bundle-facts-out``
  writes the document ``compare OLD NEW --bundle-facts-out`` writes for its
  OLD side, whatever NEW is -- byte-for-byte as JSON.
* **Round trip** (``tests/test_dump_bundle_facts_integration.py``, real
  libraries): ``compare DUMPED NEW`` reaches the verdict ``compare OLD NEW``
  reaches live.
* **Every key is a member.** The capture's library keys are exactly the
  release's member keys. Writing the parity test found ``compare
  --bundle-facts-out`` persisting a JSON-snapshot member twice, once under
  its file name and once under the snapshot's ``library`` name.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.cli import main
from abicheck.model import AbiSnapshot, Function, Variable, Visibility
from abicheck.serialization import snapshot_to_json


def _fn(name: str, ret: str = "int") -> Function:
    return Function(
        name=name, mangled=name, return_type=ret, visibility=Visibility.PUBLIC
    )


def _var(name: str) -> Variable:
    return Variable(name=name, mangled=name, type="int", visibility=Visibility.PUBLIC)


def _write(root: Path, members: dict[str, AbiSnapshot]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for name, snap in members.items():
        (root / f"{name}.json").write_text(snapshot_to_json(snap), encoding="utf-8")
    return root


def _snap(lib: str, version: str, *, functions=(), variables=()) -> AbiSnapshot:
    return AbiSnapshot(
        library=f"{lib}.so",
        version=version,
        functions=list(functions),
        variables=list(variables),
        from_headers=True,
    )


#: (old members, new members) -- one library, several, a member only OLD
#: has, a member only NEW has, and an unchanged release.
_RELEASES = {
    "one-member": (
        {"liba": _snap("liba", "1", functions=[_fn("a"), _fn("b")])},
        {"liba": _snap("liba", "2", functions=[_fn("a")])},
    ),
    "three-members": (
        {
            "liba": _snap("liba", "1", functions=[_fn("a")]),
            "libb": _snap("libb", "1", variables=[_var("v")]),
            "libc": _snap("libc", "1", functions=[_fn("c", "long")]),
        },
        {
            "liba": _snap("liba", "2", functions=[_fn("a"), _fn("a2")]),
            "libb": _snap("libb", "2", variables=[_var("v"), _var("w")]),
            "libc": _snap("libc", "2", functions=[_fn("c")]),
        },
    ),
    "old-only-member": (
        {
            "liba": _snap("liba", "1", functions=[_fn("a")]),
            "libgone": _snap("libgone", "1", functions=[_fn("g")]),
        },
        {"liba": _snap("liba", "2", functions=[_fn("a")])},
    ),
    "new-only-member": (
        {"liba": _snap("liba", "1", functions=[_fn("a")])},
        {
            "liba": _snap("liba", "2", functions=[_fn("a")]),
            "libnew": _snap("libnew", "2", functions=[_fn("n")]),
        },
    ),
    "unchanged": (
        {"liba": _snap("liba", "1", functions=[_fn("a")])},
        {"liba": _snap("liba", "1", functions=[_fn("a")])},
    ),
}


def _invoke(*args: str):
    return CliRunner().invoke(main, list(args))


def _release(tmp_path: Path, shape: str) -> tuple[Path, Path]:
    old, new = _RELEASES[shape]
    return _write(tmp_path / "old", old), _write(tmp_path / "new", new)


@pytest.mark.parametrize("shape", sorted(_RELEASES))
def test_dump_writes_the_document_compare_writes(tmp_path: Path, shape: str) -> None:
    old, new = _release(tmp_path, shape)
    dumped, compared = tmp_path / "dumped.json", tmp_path / "compared.json"
    result = _invoke("dump", str(old), "--bundle-facts-out", str(dumped))
    assert result.exit_code == 0, result.output
    assert f"BundleFacts written to {dumped}" in result.stderr
    compare = _invoke(
        "compare",
        str(old),
        str(new),
        "--bundle-facts-out",
        str(compared),
        "-o",
        f"json={tmp_path / 'report.json'}",
    )
    assert compare.exit_code in (0, 1, 2, 4), compare.output
    dumped_doc = json.loads(dumped.read_text(encoding="utf-8"))
    assert dumped_doc == json.loads(compared.read_text(encoding="utf-8"))
    members = sorted(f"{name}.json" for name in _RELEASES[shape][0])
    assert sorted(dumped_doc["per_library_snapshots"]) == members
    assert sorted(dumped_doc["library_filenames"]) == members


@pytest.mark.parametrize(
    ("extra", "named"),
    [
        (["-o", "x.json"], "-o/--output"),
        (["--dry-run"], "--dry-run"),
        (["--follow-deps"], "--follow-deps"),
        (["--provenance", "git_tag=v1"], "--provenance"),
    ],
)
def test_an_input_the_capture_does_not_apply_is_refused(
    tmp_path: Path, extra: list[str], named: str
) -> None:
    old, _new = _release(tmp_path, "one-member")
    out = tmp_path / "dumped.json"
    result = _invoke("dump", str(old), "--bundle-facts-out", str(out), *extra)
    assert result.exit_code == 64, result.output
    assert named in result.output
    assert not out.exists()


def test_a_single_library_is_not_a_release(tmp_path: Path) -> None:
    old, _new = _release(tmp_path, "one-member")
    out = tmp_path / "dumped.json"
    result = _invoke("dump", str(old / "liba.json"), "--bundle-facts-out", str(out))
    assert result.exit_code == 64, result.output
    assert "directory or package release" in result.output
    assert not out.exists()


def test_an_empty_release_is_an_error_not_an_empty_baseline(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    out = tmp_path / "dumped.json"
    result = _invoke("dump", str(empty), "--bundle-facts-out", str(out))
    assert result.exit_code != 0, result.output
    assert not out.exists()


def test_compare_keys_json_snapshot_members_once(tmp_path: Path) -> None:
    """The producer bug the parity test found: every key is a member key."""
    old, new = _release(tmp_path, "three-members")
    out = tmp_path / "compared.json"
    result = _invoke(
        "compare",
        str(old),
        str(new),
        "--bundle-facts-out",
        str(out),
        "-o",
        f"json={tmp_path / 'report.json'}",
    )
    assert result.exit_code in (0, 1, 2, 4), result.output
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert sorted(doc["per_library_snapshots"]) == [
        "liba.json",
        "libb.json",
        "libc.json",
    ]
