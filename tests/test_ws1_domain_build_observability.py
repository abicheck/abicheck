"""ADR-093 oracle for the domain-family ``observable_at`` declarations (WS-1).

Each declaration in ``model/change_catalog/build.py`` (numpy_*, wheel_*, and the
bundle_* kinds it carries) is checked against the real public workflow: real
shared objects built with gcc, packaged as real wheels or library directories,
run through ``python -m abicheck compare``. The assertions encode what the
oracle showed for each evidence configuration:

* ``B``  -- stripped binaries, no headers, no DWARF;
* ``D``  -- the same binaries built with ``-g`` (DWARF present), no headers.

Package-scope kinds (wheel archive, bundle artifact set, instantiation
manifest) are emitted only when that scope is the run's subject; the
binary fact they report is read from ``B``. ``test_bare_binary_does_not_
emit_package_scope_kinds`` pins that the scope, not the binary, is what the
kind needs.
"""

from __future__ import annotations

import json
import subprocess
import zipfile
from pathlib import Path

import pytest

from abicheck.model.change_catalog.assembled import REGISTRY
from abicheck.model.change_catalog.observability import UNCLASSIFIED
from tests._abicheck_subprocess import abicheck_command

pytestmark = pytest.mark.integration

NUMPY_TARGET_1_20 = (
    b"_ARRAY_API is NULL pointer\0"
    b"_ARRAY_API is not PyCapsule object\0"
    b"module was compiled against NumPy C-API version 0x%x (NumPy 1.20) but\0"
)
NUMPY_TARGET_2_0 = NUMPY_TARGET_1_20.replace(b"1.20", b"2.0")
NUMPY_TARGET_1_23 = NUMPY_TARGET_1_20.replace(b"1.20", b"1.23")


def _emit_c_binary(
    out: Path,
    source: str,
    *,
    dbg: bool = False,
    extra: tuple[str, ...] = (),
) -> None:
    src = out.with_suffix(".c")
    src.write_text(source)
    cmd = [
        "gcc",
        "-shared",
        "-fPIC",
        "-O1",
        "-g" if dbg else "-s",
        str(src),
        "-o",
        str(out),
    ]
    cmd += list(extra)
    subprocess.run(cmd, check=True, capture_output=True)


def _marker_source(payload: bytes, name: str) -> str:
    """A C file whose .rodata carries *payload* (literal strings survive ``strip``)."""
    body = ", ".join(str(b) for b in payload)
    return (
        f"const unsigned char {name}_marker[] = {{ {body} }};\n"
        f"int {name}_fn(void) {{ return (int)sizeof({name}_marker); }}\n"
    )


def _compare_kinds(
    old: Path, new: Path, tmp: Path, extra: tuple[str, ...] = ()
) -> set[str]:
    out = tmp / "report.json"
    subprocess.run(
        abicheck_command("compare", str(old), str(new), "-o", f"json={out}", *extra),
        capture_output=True,
        text=True,
    )
    data = json.loads(out.read_text())
    kinds: set[str] = set()

    def walk(node: object) -> None:
        if isinstance(node, dict):
            value = node.get("kind")
            if isinstance(value, str) and value in REGISTRY.entries:
                kinds.add(value)
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(data)
    return kinds


# -- declarations -----------------------------------------------------------

_DECLARED = {
    "numpy_capi_consumption_added",
    "numpy_capi_consumption_removed",
    "numpy_target_floor_raised",
    "numpy_metadata_understates_required_version",
    "numpy_abi_major_incompatible",
    "wheel_rpath_not_portable",
    "wheel_closure_dependency_violation",
    "wheel_tag_architecture_mismatch",
    "bundle_intra_dep_removed",
    "bundle_intra_dep_signature_unverified",
    "bundle_intra_dep_signature_changed",
    "bundle_intra_type_changed",
    "bundle_provider_changed",
    "bundle_manifest_instantiation_added",
    "bundle_manifest_instantiation_removed",
}


def test_declared_kinds_are_no_longer_unclassified() -> None:
    for kind in _DECLARED:
        assert REGISTRY.entries[kind].observable_at is not UNCLASSIFIED, kind


def test_binary_alone_observes_numpy_c_api_kinds(tmp_path: Path) -> None:
    """{B}: the marker bytes are read from a stripped binary, no headers, no DWARF."""
    for d in ("old", "new"):
        (tmp_path / d).mkdir()
    plain = tmp_path / "old" / "ext.so"
    consumer = tmp_path / "new" / "ext.so"
    _emit_c_binary(plain, _marker_source(b"no numpy here\0", "plain"))
    _emit_c_binary(consumer, _marker_source(NUMPY_TARGET_1_20, "consumer"))
    assert "numpy_capi_consumption_added" in _compare_kinds(plain, consumer, tmp_path)
    assert "numpy_capi_consumption_removed" in _compare_kinds(consumer, plain, tmp_path)


def test_binary_alone_observes_numpy_target_floor_raised(tmp_path: Path) -> None:
    for d in ("old", "new"):
        (tmp_path / d).mkdir()
    _emit_c_binary(
        tmp_path / "old" / "ext.so", _marker_source(NUMPY_TARGET_1_20, "ext")
    )
    _emit_c_binary(
        tmp_path / "new" / "ext.so", _marker_source(NUMPY_TARGET_1_23, "ext")
    )
    kinds = _compare_kinds(
        tmp_path / "old" / "ext.so", tmp_path / "new" / "ext.so", tmp_path
    )
    assert "numpy_target_floor_raised" in kinds


# -- wheel scope ------------------------------------------------------------


def _make_wheel(
    dest: Path,
    so: Path,
    *,
    tag: str,
    requires: str,
    version: str = "1.0",
) -> Path:
    name = f"nparr-{version}-cp313-cp313-{tag}.whl"
    path = dest / name
    module = "nparr.cpython-313-x86_64-linux-gnu.so"
    metadata = (
        "Metadata-Version: 2.1\nName: nparr\n"
        f"Version: {version}\nRequires-Dist: {requires}\n"
    )
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(module, so.read_bytes())
        z.writestr("nparr-1.0.dist-info/METADATA", metadata)
        z.writestr("nparr-1.0.dist-info/WHEEL", "Wheel-Version: 1.0\n")
        z.writestr("nparr-1.0.dist-info/RECORD", "")
    return path


def _wheel_pair(
    tmp: Path,
    old_so: Path,
    new_so: Path,
    *,
    old_tag: str,
    new_tag: str,
    old_req: str,
    new_req: str,
) -> tuple[Path, Path]:
    tmp.mkdir(exist_ok=True)
    return (
        _make_wheel(tmp, old_so, tag=old_tag, requires=old_req, version="1.0"),
        _make_wheel(tmp, new_so, tag=new_tag, requires=new_req, version="1.1"),
    )


def test_wheel_rpath_not_portable_needs_wheel_scope(tmp_path: Path) -> None:
    rel = tmp_path / "rel"
    rel.mkdir()
    _emit_c_binary(rel / "nparr", _marker_source(NUMPY_TARGET_1_20, "w"))
    absolute = tmp_path / "abs"
    absolute.mkdir()
    _emit_c_binary(
        absolute / "nparr",
        _marker_source(NUMPY_TARGET_1_20, "w"),
        extra=("-Wl,-rpath,/opt/vendor/lib",),
    )
    old_w, new_w = _wheel_pair(
        tmp_path / "wheels",
        rel / "nparr",
        absolute / "nparr",
        old_tag="linux_x86_64",
        new_tag="linux_x86_64",
        old_req="numpy>=1.23",
        new_req="numpy>=1.23",
    )
    kinds = _compare_kinds(old_w, new_w, tmp_path)
    assert "wheel_rpath_not_portable" in kinds

    # The same binary as a bare shared object, with no wheel scope: not emitted.
    bare = _compare_kinds(rel / "nparr", absolute / "nparr", tmp_path)
    assert "wheel_rpath_not_portable" not in bare


def test_wheel_tag_architecture_mismatch(tmp_path: Path) -> None:
    so = tmp_path / "nparr"
    _emit_c_binary(so, _marker_source(NUMPY_TARGET_1_20, "w"))
    old_w, new_w = _wheel_pair(
        tmp_path / "wheels",
        so,
        so,
        old_tag="linux_x86_64",
        new_tag="manylinux_2_17_aarch64",
        old_req="numpy>=1.23",
        new_req="numpy>=1.23",
    )
    kinds = _compare_kinds(old_w, new_w, tmp_path)
    assert "wheel_tag_architecture_mismatch" in kinds


def test_wheel_closure_dependency_violation(tmp_path: Path) -> None:
    vend_dir = tmp_path / "vend"
    vend_dir.mkdir()
    vend = vend_dir / "libvend-0123abcd.so.1"
    _emit_c_binary(
        vend,
        "int vend_fn(int x) { return x + 5; }\n",
        extra=("-Wl,-soname,libvend-0123abcd.so.1",),
    )
    plain = tmp_path / "plain"
    plain.mkdir()
    _emit_c_binary(plain / "nparr", _marker_source(NUMPY_TARGET_1_20, "w"))
    vended = tmp_path / "vended"
    vended.mkdir()
    (vended / "nparr_src.c").write_text(
        "extern int vend_fn(int);\n"
        + _marker_source(NUMPY_TARGET_1_20, "w")
        + "int use_vend(void) { return vend_fn(1); }\n"
    )
    subprocess.run(
        [
            "gcc",
            "-shared",
            "-fPIC",
            "-O1",
            "-s",
            str(vended / "nparr_src.c"),
            "-o",
            str(vended / "nparr"),
            str(vend),
        ],
        check=True,
        capture_output=True,
    )
    old_w, new_w = _wheel_pair(
        tmp_path / "wheels",
        plain / "nparr",
        vended / "nparr",
        old_tag="linux_x86_64",
        new_tag="linux_x86_64",
        old_req="numpy>=1.23",
        new_req="numpy>=1.23",
    )
    kinds = _compare_kinds(old_w, new_w, tmp_path)
    assert "wheel_closure_dependency_violation" in kinds


def test_numpy_metadata_understated_by_declared_requirement(tmp_path: Path) -> None:
    binary_2_0 = tmp_path / "bin"
    binary_2_0.mkdir()
    _emit_c_binary(binary_2_0 / "nparr", _marker_source(NUMPY_TARGET_2_0, "w"))
    old_w, new_w = _wheel_pair(
        tmp_path / "wheels",
        binary_2_0 / "nparr",
        binary_2_0 / "nparr",
        old_tag="linux_x86_64",
        new_tag="linux_x86_64",
        old_req="numpy>=2.0",
        new_req="numpy>=1.23",
    )
    kinds = _compare_kinds(old_w, new_w, tmp_path)
    assert "numpy_metadata_understates_required_version" in kinds
    assert "numpy_abi_major_incompatible" in kinds


# -- bundle scope -----------------------------------------------------------

CORE_OLD = (
    "int core_api(int x) { return x + 1; }\nint core_other(int x) { return x * 2; }\n"
)
CORE_NEW = "int core_other(int x) { return x * 2; }\n"
USE = "int core_api(int);\nint use_it(int x) { return core_api(x) + 3; }\n"
CORE_SIG = (
    "long core_api(long x) { return x + 1; }\nint core_other(int x) { return x * 2; }\n"
)
USE_LONG = "long core_api(long);\nlong use_it(long x) { return core_api(x) + 3; }\n"


def _library_dir(
    root: Path, libs: list[tuple[str, str, list[str]]], *, dbg: bool
) -> Path:
    """Build each (name, source, link-deps) library into *root*, in order."""
    root.mkdir(parents=True, exist_ok=True)
    for name, source, deps in libs:
        src = root / f"{name}.c"
        src.write_text(source)
        cmd = [
            "gcc",
            "-shared",
            "-fPIC",
            "-O1",
            "-g" if dbg else "-s",
            str(src),
            "-o",
            str(root / f"{name}.so"),
            f"-Wl,-soname,{name}.so",
            "-Wl,-rpath,$ORIGIN",
            "-L",
            str(root),
        ]
        cmd += [f"-l{d[3:]}" for d in deps]
        subprocess.run(cmd, check=True, capture_output=True)
    return root


def _bundle_kinds(
    tmp: Path, tag: str, old_libs, new_libs, *, dbg: bool, extra: tuple[str, ...] = ()
) -> set[str]:
    old = _library_dir(tmp / f"{tag}_old_{'D' if dbg else 'B'}", old_libs, dbg=dbg)
    new = _library_dir(tmp / f"{tag}_new_{'D' if dbg else 'B'}", new_libs, dbg=dbg)
    return _compare_kinds(old, new, tmp, extra)


def test_bundle_intra_dep_removed_from_stripped_members(tmp_path: Path) -> None:
    old = [("libcore", CORE_OLD, []), ("libuse", USE, ["libcore"])]
    new = [("libcore", CORE_NEW, []), ("libuse", USE, ["libcore"])]
    for dbg in (False, True):
        kinds = _bundle_kinds(tmp_path, "removed", old, new, dbg=dbg)
        assert "bundle_intra_dep_removed" in kinds, dbg


def test_bundle_signature_unverified_from_binary_only(tmp_path: Path) -> None:
    old = [("libcore", CORE_OLD, []), ("libuse", USE, ["libcore"])]
    new = [("libcore", CORE_SIG, []), ("libuse", USE, ["libcore"])]
    kinds = _bundle_kinds(tmp_path, "sig", old, new, dbg=False)
    assert "bundle_intra_dep_signature_unverified" in kinds
    assert "bundle_intra_dep_signature_changed" not in kinds


def test_bundle_signature_changed_needs_dwarf(tmp_path: Path) -> None:
    old = [("libcore", CORE_OLD, []), ("libuse", USE, ["libcore"])]
    new = [("libcore", CORE_SIG, []), ("libuse", USE_LONG, ["libcore"])]
    assert "bundle_intra_dep_signature_changed" in _bundle_kinds(
        tmp_path, "sigd", old, new, dbg=True
    )
    assert "bundle_intra_dep_signature_changed" not in _bundle_kinds(
        tmp_path, "sigb", old, new, dbg=False
    )


def test_bundle_provider_changed_needs_dwarf(tmp_path: Path) -> None:
    other = "int other_fn(int x) { return x; }\n"
    old = [
        ("libcore", CORE_OLD, []),
        ("libother", other, []),
        ("libuse", USE, ["libcore"]),
    ]
    new = [
        ("libcore", CORE_NEW, []),
        ("libother", other + "int core_api(int x){return x+1;}\n", []),
        ("libuse", USE, ["libother"]),
    ]
    assert "bundle_provider_changed" in _bundle_kinds(
        tmp_path, "prov", old, new, dbg=True
    )
    assert "bundle_provider_changed" not in _bundle_kinds(
        tmp_path, "provb", old, new, dbg=False
    )


def test_bundle_intra_type_changed_needs_dwarf(tmp_path: Path) -> None:
    old = [
        (
            "libcore",
            "struct cfg { int a; int b; };\nint core_cfg(struct cfg *c){return c->a;}\n",
            [],
        ),
        (
            "libuse",
            "struct cfg { int a; int b; };\nint use_cfg(struct cfg *c){return c->a;}\n",
            ["libcore"],
        ),
    ]
    new = [
        (
            "libcore",
            "struct cfg { long a; int b; };\nint core_cfg(struct cfg *c){return (int)c->a;}\n",
            [],
        ),
        (
            "libuse",
            "struct cfg { long a; int b; };\nint use_cfg(struct cfg *c){return (int)c->a;}\n",
            ["libcore"],
        ),
    ]
    assert "bundle_intra_type_changed" in _bundle_kinds(
        tmp_path, "type", old, new, dbg=True
    )
    assert "bundle_intra_type_changed" not in _bundle_kinds(
        tmp_path, "typeb", old, new, dbg=False
    )


def test_bundle_manifest_instantiation_removed_and_added(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text("provides:\n  - symbol: core_api\n    library: libcore.so\n")
    old = [("libcore", CORE_OLD, []), ("libuse", USE, ["libcore"])]
    new = [("libcore", CORE_NEW, []), ("libuse", USE, ["libcore"])]
    removed = _bundle_kinds(
        tmp_path,
        "mrem",
        old,
        new,
        dbg=False,
        extra=("--instantiation-manifest", str(manifest)),
    )
    assert "bundle_manifest_instantiation_removed" in removed

    manifest.write_text("provides:\n  - symbol: core_new\n    library: libcore.so\n")
    old2 = [("libcore", CORE_OLD, [])]
    new2 = [("libcore", CORE_OLD + "int core_new(int x){return x;}\n", [])]
    added = _bundle_kinds(
        tmp_path,
        "madd",
        old2,
        new2,
        dbg=False,
        extra=("--instantiation-manifest", str(manifest)),
    )
    assert "bundle_manifest_instantiation_added" in added


def test_bare_binary_does_not_emit_package_scope_kinds(tmp_path: Path) -> None:
    """A bare pair of shared objects, no wheel or bundle scope, emits no bundle kind."""
    old = _library_dir(tmp_path / "bo", [("libcore", CORE_OLD, [])], dbg=False)
    new = _library_dir(tmp_path / "bn", [("libcore", CORE_NEW, [])], dbg=False)
    kinds = _compare_kinds(old / "libcore.so", new / "libcore.so", tmp_path)
    assert not {k for k in kinds if k.startswith("bundle_")}
