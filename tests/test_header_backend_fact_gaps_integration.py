"""Canaries for header-backend fact gaps the generated edit corpus found.

Each test dumps a tiny real library with one header backend and asserts the
fact that backend currently fails to record (castxml and the native gcc plugin
against the clang backend, which records all of them). Every test is a
``known_gap_canary`` for a ``docs/contribute/known-gaps.md`` entry and for the
strict-xfail ``tests/_edit_corpus_known.py`` entries that name the same gap:
when one fails, the backend learned the fact -- archive the entry, drop the
corpus ``Known`` rows, and turn the canary into a regression test.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from abicheck.dumper import dump
from abicheck.model import AbiSnapshot

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(sys.platform != "linux", reason="builds ELF .so files"),
]


def _build(tmp: Path, header: str, source: str, lang: str) -> tuple[Path, Path]:
    ext = "c" if lang == "c" else "cpp"
    h = tmp / "lib.h"
    h.write_text(
        f"#pragma GCC visibility push(default)\n{header}\n#pragma GCC visibility pop\n"
    )
    src = tmp / f"lib.{ext}"
    src.write_text(f'#include "lib.h"\n{source}\n')
    so = tmp / "libgap.so"
    cc = "gcc" if lang == "c" else "g++"
    std = "-std=c17" if lang == "c" else "-std=c++14"
    subprocess.run(
        [cc, std, "-g", "-shared", "-fPIC", "-o", str(so), str(src)],
        check=True,
        capture_output=True,
        text=True,
    )
    return so, h


def _require_gcc_plugin(lang: str) -> None:
    """Skip only when the optional, build-on-demand plugin is not built; a
    missing compiler skips before probing, and any other probe failure (a
    broken driver, a wrong ``ABICHECK_GCC_PLUGIN``) fails the test."""
    from abicheck.errors import SnapshotError
    from abicheck.extract.headers.gcc.discovery import find_plugin

    compiler = shutil.which("gcc" if lang == "c" else "g++")
    if compiler is None:
        pytest.skip("no gcc driver on PATH")
    try:
        find_plugin(compiler, lang)
    except SnapshotError as exc:
        if not str(exc).startswith("Native GCC plugin is missing"):
            raise
        pytest.skip(f"native gcc plugin not built: {exc}")


def _dump(tmp: Path, header: str, source: str, lang: str, backend: str) -> AbiSnapshot:
    if backend == "gcc":
        _require_gcc_plugin(lang)
    elif backend == "castxml" and shutil.which("castxml") is None:
        pytest.skip("castxml unavailable")
    elif (
        backend == "clang"
        and shutil.which("clang" if lang == "c" else "clang++") is None
    ):
        pytest.skip("clang unavailable")
    tmp.mkdir(parents=True, exist_ok=True)
    so, h = _build(tmp, header, source, lang)
    std = "c17" if lang == "c" else "c++14"
    return dump(
        so,
        [h],
        version="1",
        compiler="cc" if lang == "c" else "c++",
        lang=lang,
        header_backend=backend,
        gcc_options=f"-std={std}",
    )


def _function(snap: AbiSnapshot, name: str):  # type: ignore[no-untyped-def]
    found = [f for f in snap.declarations.functions if f.name == name]
    assert len(found) == 1, [f.name for f in snap.declarations.functions]
    return found[0]


@pytest.mark.known_gap_canary
def test_castxml_records_no_gnu_contract_attribute(tmp_path: Path) -> None:
    """castxml 0.7 writes no ``nonnull``/``returns_nonnull`` on a <Function>,
    so the castxml snapshot has no contract tokens where clang has both."""
    header = "int put(char *p) __attribute__((nonnull(1)));"
    source = "int put(char *p) { return *p; }"
    castxml = _function(_dump(tmp_path / "x", header, source, "c", "castxml"), "put")
    clang = _function(_dump(tmp_path / "c", header, source, "c", "clang"), "put")
    assert not (castxml.contract_attributes or [])
    assert any("nonnull" in t for t in clang.contract_attributes or [])


def _va_list_fact(backend: str, tmp: Path) -> object:
    header = "#include <stdarg.h>\nint vlog(const char *fmt, va_list ap);"
    source = (
        "int vlog(const char *fmt, va_list ap) { return fmt[0] + va_arg(ap, int); }"
    )
    f = _function(_dump(tmp, header, source, "c", backend), "vlog")
    return f.params[1].is_va_list_fact


@pytest.mark.known_gap_canary
@pytest.mark.parametrize("backend", ["castxml", "gcc"])
def test_only_the_clang_backend_marks_a_va_list_parameter(
    tmp_path: Path, backend: str
) -> None:
    """castxml and the native gcc plugin never establish ``Param.is_va_list``;
    clang reads the x86-64 ``__va_list_tag *`` spelling."""
    if sys.maxsize <= 2**32:
        pytest.skip("the clang reading is x86-64 SysV only")
    gapped = _va_list_fact(backend, tmp_path / backend)
    clang = _va_list_fact("clang", tmp_path / "clang")
    assert getattr(gapped, "value", None) is not True
    assert getattr(clang, "value", None) is True


@pytest.mark.known_gap_canary
def test_the_gcc_backend_records_no_dynamic_exception_spec(tmp_path: Path) -> None:
    header = "int thrower(int a) throw(int);"
    source = "int thrower(int a) throw(int) { if (a < 0) throw 1; return a; }"
    gcc = _function(_dump(tmp_path / "g", header, source, "c++", "gcc"), "thrower")
    castxml = _function(
        _dump(tmp_path / "x", header, source, "c++", "castxml"), "thrower"
    )
    assert gcc.exception_spec is None
    assert castxml.exception_spec


@pytest.mark.known_gap_canary
def test_the_gcc_backend_keeps_an_unexported_declaration_public(tmp_path: Path) -> None:
    """A header-declared function the library does not export: castxml and
    clang record it HIDDEN (binary_exported false), the gcc plugin PUBLIC."""
    header = "int late_f(int x);\nint keep(int x);"
    source = "int keep(int x) { return x; }"
    gcc = _function(_dump(tmp_path / "g", header, source, "c", "gcc"), "late_f")
    castxml = _function(_dump(tmp_path / "x", header, source, "c", "castxml"), "late_f")
    assert gcc.binary_exported_fact.value is False
    assert gcc.visibility.name == "PUBLIC"
    assert castxml.visibility.name == "HIDDEN"


def _record(snap: AbiSnapshot, name: str):  # type: ignore[no-untyped-def]
    found = [t for t in snap.declarations.types if t.name.rsplit("::", 1)[-1] == name]
    assert found, [t.name for t in snap.declarations.types]
    return found[0]


@pytest.mark.known_gap_canary
def test_castxml_records_no_standard_layout_or_data_size(tmp_path: Path) -> None:
    """castxml's XML has no data size (dsize) and abicheck derives no
    standard-layout trait from it; the native gcc backend records both."""
    header = "struct TP {\n  int get() const;\nprivate:\n  int a;\n  char b;\n};"
    source = "int TP::get() const { return a + b; }"
    castxml = _record(_dump(tmp_path / "x", header, source, "c++", "castxml"), "TP")
    gcc = _record(_dump(tmp_path / "g", header, source, "c++", "gcc"), "TP")
    assert castxml.is_standard_layout is None
    assert castxml.data_size_bits is None
    assert gcc.is_standard_layout is True
    assert gcc.data_size_bits == 40


def test_castxml_spells_a_std_return_type_by_its_bare_leaf_but_resolves_it(
    tmp_path: Path,
) -> None:
    """castxml writes ``std::string`` as ``string`` and records the resolved
    ``std::`` identity, which the std-by-value check reads (formerly a
    known-gap canary)."""
    header = "#include <string>\nstd::string greet_n(int n);"
    source = "std::string greet_n(int n) { return std::string(n, 'x'); }"
    castxml = _function(
        _dump(tmp_path / "x", header, source, "c++", "castxml"), "greet_n"
    )
    clang = _function(_dump(tmp_path / "c", header, source, "c++", "clang"), "greet_n")
    assert "std::" not in castxml.return_type
    assert "std::" in clang.return_type
    from abicheck.model import ParamKind
    from abicheck.policy.std_by_value import is_std_by_value

    assert is_std_by_value(
        castxml.return_type, 0, ParamKind.VALUE, castxml.return_type_identities_fact
    )


@pytest.mark.known_gap_canary
def test_the_gcc_backend_spells_a_typedef_return_canonically(tmp_path: Path) -> None:
    """The gcc plugin records a function's return type through its typedef
    as the canonical type, so renaming the typedef changes nothing it sees."""
    header = "typedef const char *stamp_1_2_3;\nstamp_1_2_3 vstamp(void);"
    source = 'stamp_1_2_3 vstamp(void) { return "1"; }'
    gcc = _function(_dump(tmp_path / "g", header, source, "c", "gcc"), "vstamp")
    castxml = _function(_dump(tmp_path / "x", header, source, "c", "castxml"), "vstamp")
    assert "stamp_1_2_3" not in gcc.return_type
    assert "stamp_1_2_3" in castxml.return_type


# --- detector gaps: one real old/new pair each, compared like the CLI ---------


def _kinds(
    tmp: Path, old: tuple[str, str], new: tuple[str, str], lang: str, backend: str
) -> tuple[str, set[str]]:
    from abicheck.workflows.compare_policy import compare_snapshots
    from abicheck.workflows.snapshot_factory import finish_provenance

    snaps = []
    for side, (header, source) in (("old", old), ("new", new)):
        snap = _dump(tmp / side, header, source, lang, backend)
        snaps.append(finish_provenance(snap, [tmp / side / "lib.h"], None))
    result = compare_snapshots(snaps[0], snaps[1])
    return result.verdict.name, {c.kind.name for c in result.changes}


_GETV_H = (
    "template <class T> T getv(T a);\nextern template int getv<int>(int);\n"
    "extern template long getv<long>(long);"
)
_GETV_C = (
    "template <class T> T getv(T a) { return a; }\n"
    "template int getv<int>(int);\ntemplate long getv<long>(long);"
)


def test_a_dropped_instantiation_is_reported_under_headers(tmp_path: Path) -> None:
    """getv<long> dropped while getv<int> stays: the stems match although the
    demangled names keep their return types (formerly a known-gap canary)."""
    new = (
        _GETV_H.replace("extern template long getv<long>(long);", ""),
        _GETV_C.replace("template long getv<long>(long);", ""),
    )
    verdict, kinds = _kinds(tmp_path, (_GETV_H, _GETV_C), new, "c++", "castxml")
    assert "FUNC_REMOVED" in kinds
    assert "INSTANTIATION_MISSING_FROM_BINARY" in kinds


_PIMPL = (
    "namespace lib {\nnamespace detail { struct impl { int count_; int cap_; }; }\n"
    "class Desc {\n  detail::impl* p_;\npublic:\n  Desc();\n"
    "  int count() const { return p_->count_; }\n};\n}"
)


def test_a_pimpl_rename_whose_new_field_reads_compatible_is_paired(
    tmp_path: Path,
) -> None:
    """count_ -> n_ moved to the end reads type_field_added_compatible, and the
    inline-body rename check still pairs it (formerly a known-gap canary). The
    gcc backend qualifies the holder; castxml and clang do not (canary below)."""
    old = (_PIMPL, "lib::Desc::Desc() : p_(new detail::impl{3, 8}) {}")
    new = (
        _PIMPL.replace("int count_; int cap_;", "int cap_; int n_;").replace(
            "p_->count_", "p_->n_"
        ),
        "lib::Desc::Desc() : p_(new detail::impl{8, 3}) {}",
    )
    verdict, kinds = _kinds(tmp_path, old, new, "c++", "gcc")
    assert {"TYPE_FIELD_REMOVED", "TYPE_FIELD_ADDED_COMPATIBLE"} <= kinds
    assert "INLINE_BODY_REFERENCES_RENAMED_MEMBER" in kinds


@pytest.mark.known_gap_canary
@pytest.mark.parametrize("backend", ["clang", "gcc"])
def test_a_renamed_tags_instantiation_declaration_is_dropped_in_the_corpus_seed(
    tmp_path: Path, backend: str
) -> None:
    """In the edit corpus's ``cxx_more`` seed, renaming ``lib::fast_tag`` to
    ``quick_tag`` leaves the clang and gcc header snapshots without the
    ``extern template int run<quick_tag>()`` declaration the old side had for
    ``run<fast_tag>``, so tag_type_renamed has no added symbol to pair.
    (castxml keeps it; a two-declaration header keeps it on every backend.)"""
    from tests._edit_corpus_config import default_config
    from tests._edit_corpus_harness import BaselineCache, build_new
    from tests._edit_corpus_operators import OP_BY_NAME
    from tests._edit_corpus_seeds import SEED_BY_NAME
    from tests._edit_corpus_toolchains import build_toolchains

    if backend == "gcc":
        _require_gcc_plugin("c++")
    elif shutil.which("clang++") is None:
        pytest.skip("clang unavailable")
    gcc = {t.family: t for t in build_toolchains()}.get("gcc")
    if gcc is None:
        pytest.skip("no gcc toolchain")
    seed = SEED_BY_NAME["cxx_more"]
    cfg = default_config("c++", gcc).with_(header_backend=backend)
    old = BaselineCache(tmp_path / "old").get(seed, cfg)
    new = build_new(OP_BY_NAME["tag_type_renamed"], seed, cfg, tmp_path / "new")

    def runs(snap: AbiSnapshot) -> set[str]:
        return {f.mangled for f in snap.declarations.functions if "3run" in f.mangled}

    assert "_ZN3lib3runINS_8fast_tagEEEiv" in runs(old.snapshot)
    assert "_ZN3lib3runINS_9quick_tagEEEiv" not in runs(new.snapshot)


@pytest.mark.known_gap_canary
def test_a_return_respelled_through_a_renamed_typedef_reads_breaking(
    tmp_path: Path,
) -> None:
    """``stamp_1_2_3 f()`` -> ``stamp_1_2_4 f()``, both ``const char *``: the
    call is unchanged, but the return reads func_return_changed (BREAKING)."""
    old = (
        "typedef const char *stamp_1_2_3;\nstamp_1_2_3 vstamp(void);",
        'stamp_1_2_3 vstamp(void) { return "1"; }',
    )
    new = tuple(t.replace("1_2_3", "1_2_4") for t in old)
    verdict, kinds = _kinds(tmp_path, old, new, "c", "castxml")  # type: ignore[arg-type]
    assert "FUNC_RETURN_CHANGED" in kinds
    assert verdict == "BREAKING"


def test_an_overload_swapped_for_another_is_reported_rerouted(tmp_path: Path) -> None:
    """route(int)+route(double) -> route(int)+route(float) on real castxml
    snapshots (formerly a known-gap canary)."""
    old = (
        "int route(int a);\nint route(double a);",
        "int route(int a) { return a; }\nint route(double a) { return (int)a; }",
    )
    new = tuple(t.replace("double", "float") for t in old)
    _, kinds = _kinds(tmp_path, old, new, "c++", "castxml")  # type: ignore[arg-type]
    assert {"FUNC_REMOVED", "FUNC_ADDED"} <= kinds
    assert "OVERLOAD_SET_REROUTED" in kinds


_AUTO_OLD = (
    "inline auto make_cb() { return [](int a) { return a + 1; }; }\nint keep(int a);",
    "int keep(int a) { return a; }",
)
_AUTO_NEW = (
    "typedef int (*cb_t)(int);\ncb_t make_cb();\nint keep(int a);",
    "int keep(int a) { return a; }\ncb_t make_cb() { return [](int a) { return a + 1; }; }",
)


@pytest.mark.known_gap_canary
@pytest.mark.parametrize(
    ("backend", "spelling"), [("castxml", "?"), ("clang", "(lambda:")]
)
def test_a_closure_return_spelling_is_read_as_unspecified_on_every_backend(
    tmp_path: Path, backend: str, spelling: str
) -> None:
    """castxml spells a deduced closure return ``?`` and clang ``(lambda:...)``;
    both now read unspecified_return_now_named. The canary half: clang also
    reads func_return_changed (BREAKING) for the formerly inline function."""
    f = _function(_dump(tmp_path / "d", *_AUTO_OLD, "c++", backend), "make_cb")
    assert f.return_type.startswith(spelling)
    verdict, kinds = _kinds(tmp_path / "k", _AUTO_OLD, _AUTO_NEW, "c++", backend)
    assert "UNSPECIFIED_RETURN_NOW_NAMED" in kinds
    assert ("FUNC_RETURN_CHANGED" in kinds) == (backend == "clang")
    assert (verdict == "BREAKING") == (backend == "clang")


_ISA_H = (
    "int k1(int a);\nint k2(int a);\nint k3(int a);\n"
    "int k1_avx2(int a);\nint k2_avx2(int a);\nint k3_avx2(int a);\n"
    "int k1_sse42(int a);\nint k2_sse42(int a);\nint k3_sse42(int a);"
)
_ISA_C = (
    "\n".join(
        f"int k{i}_{isa}(int a) {{ return a + {i}; }}"
        for isa in ("avx2", "sse42")
        for i in (1, 2, 3)
    )
    + "\n"
    + "\n".join(f"int k{i}(int a) {{ return k{i}_sse42(a); }}" for i in (1, 2, 3))
)


@pytest.mark.known_gap_canary
def test_a_declared_isa_tier_removal_reads_as_a_risk(tmp_path: Path) -> None:
    """Dropping the declared, exported k*_avx2 kernels reads
    cpu_dispatch_isa_dropped (COMPATIBLE_WITH_RISK) with no func_removed,
    although a consumer calling k1_avx2 fails to load."""
    new = (
        "\n".join(line for line in _ISA_H.splitlines() if "avx2" not in line),
        "\n".join(line for line in _ISA_C.splitlines() if "avx2" not in line),
    )
    verdict, kinds = _kinds(tmp_path, (_ISA_H, _ISA_C), new, "c", "castxml")
    assert "CPU_DISPATCH_ISA_DROPPED" in kinds
    assert "FUNC_REMOVED" not in kinds
    assert verdict == "COMPATIBLE_WITH_RISK"


@pytest.mark.known_gap_canary
@pytest.mark.parametrize("backend", ["castxml", "clang"])
def test_castxml_and_clang_name_the_corpus_pimpl_holder_by_its_bare_leaf(
    tmp_path: Path, backend: str
) -> None:
    """In the edit corpus's ``cxx_more`` seed the pimpl holder ``lib::Desc`` is
    recorded as ``Desc`` while its inline accessor demangles to ``lib::Desc``,
    so the inline-body rename check finds no holder for it."""
    from tests._edit_corpus_config import default_config
    from tests._edit_corpus_harness import BaselineCache
    from tests._edit_corpus_seeds import SEED_BY_NAME
    from tests._edit_corpus_toolchains import build_toolchains

    gcc = {t.family: t for t in build_toolchains()}.get("gcc")
    if gcc is None:
        pytest.skip("no gcc toolchain")
    cfg = default_config("c++", gcc).with_(header_backend=backend)
    snap = BaselineCache(tmp_path).get(SEED_BY_NAME["cxx_more"], cfg).snapshot
    names = {t.name for t in snap.declarations.types if t.name.endswith("Desc")}
    assert names == {"Desc"}


_BUF_C = "int keep(int x) { return x; }"


@pytest.mark.known_gap_canary
def test_a_template_default_change_reads_out_of_contract_on_clang(
    tmp_path: Path,
) -> None:
    """A public header's class template changes a default argument: the clang
    snapshot reads template_param_default_changed, but the contract evaluator
    places the template pattern PROVEN_OUT_OF_CONTRACT, so the CLI verb rolls
    the run up to NO_CHANGE (castxml and bare ``checker.compare`` say API_BREAK)."""
    old = (
        "template <typename T, int N = 4> struct Buf { T data[N]; };\nint keep(int x);",
        _BUF_C,
    )
    new = (
        "template <typename T, int N = 8> struct Buf { T data[N]; };\nint keep(int x);",
        _BUF_C,
    )
    verdict, kinds = _kinds(tmp_path, old, new, "c++", "clang")
    assert "TEMPLATE_PARAM_DEFAULT_CHANGED" in kinds
    assert verdict == "NO_CHANGE"
