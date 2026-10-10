"""abicheck code identity, and the disk caches whose keys must carry it.

Bug class: a disk cache storing something abicheck *computed* was keyed by its
inputs plus a hand-bumped version, so an extraction change with no matching
bump served the previous code's answer for byte-identical inputs (the example
catalog's debug builds are reproducible byte for byte, so this reached CI).
Three layers of tests:

* ``TestComputeCodeIdentity`` -- the primitive's contract over generated
  trees, against an independent oracle (the set of ``(path, content)`` pairs).
* ``TestCachesMissAcrossCodeIdentity`` -- every computed-output cache misses
  when only the code identity changes, and hits again when it changes back.
* ``test_every_disk_cache_is_classified`` -- a ``DiskCache`` added later must
  be classified here, so a new computed-output cache cannot silently skip the
  identity (a missing entry fails, rather than passing everything).
"""

from __future__ import annotations

import ast
import json
import os
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from abicheck.storage import code_identity
from abicheck.storage.code_identity import compute_code_fingerprint

REPO = Path(__file__).resolve().parent.parent

# Files always end in .py/.pyi and directories never contain a dot, so a file
# and a directory can never collide on one path.
_names = st.from_regex(r"[a-z]{1,6}(/[a-z]{1,6}){0,2}\.pyi?", fullmatch=True)
_trees = st.dictionaries(_names, st.binary(max_size=40), min_size=1, max_size=8)


def _write(root: Path, tree: dict[str, bytes], order: list[str] | None = None) -> Path:
    root.mkdir(parents=True)
    for rel in order or list(tree):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(tree[rel])
    return root


def _sources_of(tree: dict[str, bytes]) -> frozenset[tuple[str, bytes]]:
    """Oracle: two trees are the same code iff these sets are equal."""
    return frozenset(tree.items())


class TestComputeCodeIdentity:
    @settings(
        max_examples=60,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(a=_trees, b=_trees)
    def test_identity_equal_iff_sources_equal(self, tmp_path_factory, a, b) -> None:
        ra = _write(tmp_path_factory.mktemp("a") / "pkg", a)
        rb = _write(tmp_path_factory.mktemp("b") / "pkg", b)
        same = compute_code_fingerprint(ra) == compute_code_fingerprint(rb)
        assert same == (_sources_of(a) == _sources_of(b))

    @settings(
        max_examples=40,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(tree=_trees, data=st.data())
    def test_any_single_edit_changes_identity(
        self, tmp_path_factory, tree, data
    ) -> None:
        names = list(tree)
        root = _write(tmp_path_factory.mktemp("t") / "pkg", tree)
        before = compute_code_fingerprint(root)
        victim = data.draw(st.sampled_from(sorted(names)))
        kind = data.draw(st.sampled_from(["content", "rename", "delete"]))
        path = root / victim
        if kind == "content":
            path.write_bytes(tree[victim] + b"#")
        elif kind == "rename":
            path.rename(path.with_name("zz_" + path.name))
        else:
            path.unlink()
        assert compute_code_fingerprint(root) != before

    def test_ignores_listing_order_mtime_bytecode_and_non_sources(
        self, tmp_path: Path
    ) -> None:
        tree = {"a.py": b"x = 1\n", "sub/b.py": b"y = 2\n", "sub/c.pyi": b"z: int\n"}
        r1 = _write(tmp_path / "one", tree)
        r2 = _write(tmp_path / "two", tree, order=list(reversed(list(tree))))
        os.utime(r2 / "a.py", (0, 0))
        (r2 / "__pycache__").mkdir()
        (r2 / "__pycache__" / "a.cpython-313.pyc").write_bytes(b"\0bytecode")
        (r2 / "sub" / "notes.txt").write_text("not code")
        (r2 / "sub" / "data.json").write_text("{}")
        assert compute_code_fingerprint(r1) == compute_code_fingerprint(r2)

    def test_real_package_identity_is_stable_and_memoized(self) -> None:
        first = code_identity.abicheck_code_fingerprint()
        assert first == code_identity.abicheck_code_fingerprint()
        assert first == compute_code_fingerprint(code_identity.PACKAGE_ROOT)
        assert len(first) == 64


# -- every computed-output cache misses when only the code changed ------------


@pytest.fixture
def identity(monkeypatch: pytest.MonkeyPatch):
    """Set the code identity every consumer sees (each imported it by name)."""
    import abicheck.buildsource.build_cache as build_cache
    import abicheck.buildsource.source_replay as source_replay
    import abicheck.snapshot_cache as snapshot_cache

    def set_to(value: str) -> None:
        for mod in (snapshot_cache, build_cache, source_replay):
            monkeypatch.setattr(mod, "abicheck_code_fingerprint", lambda v=value: v)

    return set_to


class TestCachesMissAcrossCodeIdentity:
    def test_snapshot_cache(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, identity
    ) -> None:
        import abicheck.snapshot_cache as sc
        from abicheck.model import AbiSnapshot

        monkeypatch.setattr(sc, "_CACHE_DIR", tmp_path / "cache")
        binary = tmp_path / "lib.so"
        binary.write_bytes(b"reproducible build output")

        identity("code-A")
        sc.store(
            AbiSnapshot(library="libfoo.so", version="1.0"),
            binary,
            [],
            [],
            "1.0",
            "c++",
        )
        assert sc.lookup(binary, [], [], "1.0", "c++") is not None
        identity("code-B")
        assert sc.lookup(binary, [], [], "1.0", "c++") is None
        identity("code-A")
        assert sc.lookup(binary, [], [], "1.0", "c++") is not None

    def test_build_evidence_cache_key(self, tmp_path: Path, identity) -> None:
        from abicheck.buildsource.build_cache import compute_build_cache_key

        db = tmp_path / "compile_commands.json"
        db.write_text(
            json.dumps(
                [
                    {
                        "file": "f.c",
                        "arguments": ["cc", "-c", "f.c"],
                        "directory": str(tmp_path),
                    }
                ]
            )
        )
        identity("code-A")
        a = compute_build_cache_key(db, "generic")
        identity("code-B")
        b = compute_build_cache_key(db, "generic")
        identity("code-A")
        assert a and b and a != b
        assert compute_build_cache_key(db, "generic") == a

    def test_source_abi_tu_cache_key(self, tmp_path: Path, identity) -> None:
        from abicheck.buildsource.build_evidence import CompileUnit
        from abicheck.buildsource.source_replay import compute_tu_cache_key

        src = tmp_path / "f.cpp"
        src.write_text("int f();\n")
        cu = CompileUnit(
            id="cu://f",
            source=str(src),
            target_id="",
            language="CXX",
            directory=str(tmp_path),
        )

        def key() -> str | None:
            return compute_tu_cache_key(
                extractor_name="clang-source",
                extractor_version="1",
                compile_unit=cu,
                public_header_roots=[],
            )

        identity("code-A")
        a = key()
        identity("code-B")
        b = key()
        identity("code-A")
        assert a and b and a != b
        assert key() == a


# -- exhaustiveness: no disk cache escapes classification ---------------------

#: Every ``DiskCache`` in the package, by registered name. ``"code-identity"``
#: means its entries are abicheck-computed output and its key folds
#: :func:`abicheck_code_fingerprint` (proved per cache above); anything else names
#: why the identity does not apply.
DISK_CACHE_CLASSIFICATION = {
    "abicheck.snapshot_cache.disk": "code-identity",
    "abicheck.buildsource.build_cache.disk": "code-identity",
    "abicheck.buildsource.source_replay.disk": "code-identity",
    "abicheck.storage.header_ast_cache.ast_disk": (
        "external-tool output: keyed by the exact aggregate header and command "
        "line abicheck generates, plus the source of the modules that shape a "
        "stored clang entry (tests/test_header_ast_cache_key_inputs.py)"
    ),
}
_PROVED = {
    "abicheck.snapshot_cache.disk": "test_snapshot_cache",
    "abicheck.buildsource.build_cache.disk": "test_build_evidence_cache_key",
    "abicheck.buildsource.source_replay.disk": "test_source_abi_tu_cache_key",
}


def _disk_cache_names_in_package() -> set[str]:
    names: set[str] = set()
    for path in (REPO / "abicheck").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and getattr(node.func, "id", getattr(node.func, "attr", None))
                == "DiskCache"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                names.add(node.args[0].value)
    return names


def test_every_disk_cache_is_classified() -> None:
    found = _disk_cache_names_in_package()
    assert found, "scanner found no DiskCache at all -- the scan itself is broken"
    assert found == set(DISK_CACHE_CLASSIFICATION), (
        "classify every DiskCache in DISK_CACHE_CLASSIFICATION: a cache of "
        "abicheck-computed output must fold abicheck_code_fingerprint() into its key"
    )
    keyed = {n for n, c in DISK_CACHE_CLASSIFICATION.items() if c == "code-identity"}
    assert keyed == set(_PROVED)
    for test_name in _PROVED.values():
        assert hasattr(TestCachesMissAcrossCodeIdentity, test_name)


# -- edge branches of the primitives ------------------------------------------


class TestModuleFingerprintEdges:
    def test_module_source_path_resolves_root_package_module_and_package(
        self, tmp_path: Path
    ) -> None:
        root = _write(
            tmp_path / "pkg",
            {"__init__.py": b"", "a.py": b"", "sub/__init__.py": b""},
        )
        assert (
            code_identity.module_source_path(root, "abicheck") == root / "__init__.py"
        )
        assert code_identity.module_source_path(root, "abicheck.a") == root / "a.py"
        assert (
            code_identity.module_source_path(root, "abicheck.sub")
            == root / "sub" / "__init__.py"
        )
        assert code_identity.module_source_path(root, "abicheck.nope") is None

    def test_missing_module_is_a_distinct_marker_not_dropped(
        self, tmp_path: Path
    ) -> None:
        root = _write(tmp_path / "pkg", {"a.py": b"x = 1\n"})
        fp = code_identity.compute_modules_fingerprint
        only_a = fp(root, ("abicheck.a",))
        with_gone = fp(root, ("abicheck.a", "abicheck.gone"))
        with_other_gone = fp(root, ("abicheck.a", "abicheck.other"))
        # A missing name changes the fingerprint, and which name is missing matters.
        assert len({only_a, with_gone, with_other_gone}) == 3
        # Listing order and duplicates are irrelevant.
        assert fp(root, ("abicheck.gone", "abicheck.a", "abicheck.a")) == with_gone

    def test_unreadable_file_hashes_as_marker(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = _write(tmp_path / "pkg", {"a.py": b"x = 1\n"})
        before = compute_code_fingerprint(root)
        real = Path.read_bytes

        def boom(self: Path) -> bytes:
            if self.name == "a.py":
                raise OSError("unreadable")
            return real(self)

        monkeypatch.setattr(Path, "read_bytes", boom)
        unreadable = compute_code_fingerprint(root)
        assert unreadable != before
        assert unreadable == compute_code_fingerprint(root)


def test_running_package_modules_fingerprint_matches_primitive() -> None:
    mods = ("abicheck.storage.code_identity",)
    assert code_identity.abicheck_modules_fingerprint(
        mods
    ) == code_identity.compute_modules_fingerprint(code_identity.PACKAGE_ROOT, mods)
