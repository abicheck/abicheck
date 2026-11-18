"""ADR-093: a declared observable_at must observe every emission it is paired with.

The invariant: if the compare path emits kind K for a run with evidence
configuration C on platform P, then K's ``observable_at`` (the entries whose
condition admits P) must observe C. Emission proves observability, so a
declared value that does not cover an observed emission is wrong.

The oracle is the actual emission of the real ``compare`` path over synthetic
pairs in the shape the dumper produces, not the seeder's decision table.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from abicheck.checker import compare
from abicheck.dumper import (
    _itanium_export_function,
    _itanium_export_variable,
    _msvc_export_function,
)
from abicheck.model import (
    AbiSnapshot,
    Function,
    Param,
    ScopeOrigin,
    Variable,
    Visibility,
)
from abicheck.model.change_catalog.assembled import REGISTRY
from abicheck.model.change_catalog.observability import UNCLASSIFIED, Platform
from abicheck.model.fact import Fact
from tests._abicheck_subprocess import abicheck_command
from tests._elf_capability import elf_capability, skip_reason

PLATFORMS = ["elf", "pe", "macho"]
BINARY = ("B",)
BINARY_AND_HEADERS = ("B", "H")


def _export_snapshot(
    version: str, platform: str, functions: list[str], variables: list[str]
) -> AbiSnapshot:
    """Export-table-only snapshot: the shape the dumper builds in elf_only_mode."""
    make_fn = _msvc_export_function if platform == "pe" else _itanium_export_function
    return AbiSnapshot(
        library="libsynth",
        version=version,
        platform=platform,
        elf_only_mode=True,
        functions=[make_fn(n) for n in functions],
        variables=[_itanium_export_variable(n) for n in variables],
    )


def _header_function(name: str, params: tuple[str, ...] = ()) -> Function:
    return Function(
        name=name,
        mangled=f"_Z{len(name)}{name}",
        return_type="void",
        params=[Param(name=f"a{i}", type=t) for i, t in enumerate(params)],
        visibility=Visibility.PUBLIC,
        origin=ScopeOrigin.PUBLIC_HEADER,
    )


def _header_snapshot(
    version: str, platform: str, functions: list[Function]
) -> AbiSnapshot:
    """Header-backed snapshot: declarations parsed from public headers."""
    return AbiSnapshot(
        library="libsynth",
        version=version,
        platform=platform,
        from_headers=True,
        functions=functions,
    )


def _export_pair(platform: str) -> tuple[AbiSnapshot, AbiSnapshot]:
    old = _export_snapshot(
        "1",
        platform,
        ["_Z3foov", "_Z3barv", "_Z3bazi"],
        ["_ZN1v1xE", "_ZN1v1yE"],
    )
    new = _export_snapshot(
        "2",
        platform,
        ["_Z3barv", "_Z3quxv"],
        ["_ZN1v1yE", "_ZN1v1zE"],
    )
    return old, new


def _header_pair(platform: str) -> tuple[AbiSnapshot, AbiSnapshot]:
    old = _header_snapshot(
        "1",
        platform,
        [_header_function("foo"), _header_function("bar", ("int",))],
    )
    new = _header_snapshot(
        "2",
        platform,
        [_header_function("bar", ("long",)), _header_function("qux")],
    )
    return old, new


def _declared(kind: str) -> str:
    obs = REGISTRY.entries[kind].observable_at
    return "UNCLASSIFIED" if obs is UNCLASSIFIED else repr(obs)


def _observes_on(kind: str, platform: str, config: tuple[str, ...]) -> bool:
    """The oracle-free check: some entry whose condition admits *platform* observes *config*."""
    obs = REGISTRY.entries[kind].observable_at
    applicable = [
        o
        for o in obs
        if o.condition is None or o.condition.matches(Platform(platform), None)
    ]
    return any(o.observes(config) for o in applicable)


def _assert_emissions_observed(
    old: AbiSnapshot, new: AbiSnapshot, platform: str, config: tuple[str, ...]
) -> None:
    emitted = {c.kind.value for c in compare(old, new).changes}
    assert emitted, "the pair emitted nothing, so it proves nothing"
    checked = [
        k
        for k in sorted(emitted)
        if REGISTRY.entries[k].observable_at is not UNCLASSIFIED
    ]
    # An all-UNCLASSIFIED emission set would pass vacuously; refuse that.
    assert checked, (
        f"no emitted kind on {platform} carries a declaration: {sorted(emitted)}"
    )
    failures = [
        f"{kind}: emitted on {platform} at {{{','.join(config)}}}, "
        f"declared {_declared(kind)}"
        for kind in checked
        if not _observes_on(kind, platform, config)
    ]
    assert not failures, "\n".join(failures)


@pytest.mark.parametrize("platform", PLATFORMS)
def test_export_table_only_emissions_are_observed_by_binary_alone(
    platform: str,
) -> None:
    old, new = _export_pair(platform)
    _assert_emissions_observed(old, new, platform, BINARY)


@pytest.mark.parametrize("platform", PLATFORMS)
def test_header_backed_emissions_are_observed_by_binary_and_headers(
    platform: str,
) -> None:
    old, new = _header_pair(platform)
    _assert_emissions_observed(old, new, platform, BINARY_AND_HEADERS)


# The inline twins (func_lost_inline, func_became_inline) share one emitter,
# which reads is_inline only. The DWARF reader does not set it (is_inline_fact
# stays not_collected), so the twins are observable from the public header and
# not from the binary's debug info. Oracle: the real compare path, both
# directions, and the DWARF-only pair must emit neither.
_INLINE_TWINS = ("func_lost_inline", "func_became_inline")


def _inline_function(inline: bool | None) -> Function:
    fn = _header_function("f", ("int",))
    if inline is None:
        # What a DWARF-only dump records: the fact was never collected.
        fn.is_inline_fact = Fact.not_collected("dwarf reader does not set is_inline")
    else:
        fn.is_inline = inline
        fn.is_inline_fact = Fact.present(inline)
    return fn


def _inline_pair(old_inline: bool | None, new_inline: bool | None):
    return (
        _header_snapshot("1", "elf", [_inline_function(old_inline)]),
        _header_snapshot("2", "elf", [_inline_function(new_inline)]),
    )


def test_inline_twins_are_emitted_from_headers_in_both_directions() -> None:
    old, new = _inline_pair(True, False)
    assert "func_lost_inline" in {c.kind.value for c in compare(old, new).changes}
    old, new = _inline_pair(False, True)
    assert "func_became_inline" in {c.kind.value for c in compare(old, new).changes}


@pytest.mark.parametrize("kind", _INLINE_TWINS)
def test_inline_twins_are_observed_by_headers_not_by_the_binary(kind: str) -> None:
    assert REGISTRY.entries[kind].observable_at is not UNCLASSIFIED
    assert _observes_on(kind, "elf", BINARY_AND_HEADERS)
    assert not _observes_on(kind, "elf", BINARY)


def test_dwarf_only_inline_pair_emits_neither_twin() -> None:
    old, new = _inline_pair(None, None)
    emitted = {c.kind.value for c in compare(old, new).changes}
    assert not emitted & set(_INLINE_TWINS), emitted


def test_exported_free_function_overload_is_emitted_from_the_binary_alone() -> None:
    # An overload added to an exported free function reads from the export
    # table alone (the mangled names differ), so the binary observes it.
    old = _export_snapshot("1", "elf", ["_Z1gi"], [])
    new = _export_snapshot("2", "elf", ["_Z1gi", "_Z1gd"], [])
    emitted = {c.kind.value for c in compare(old, new).changes}
    assert "overload_added" in emitted, emitted
    assert _observes_on("overload_added", "elf", BINARY)


def test_pure_virtual_flip_is_emitted_from_headers_and_observed_by_headers() -> None:
    # A concrete virtual that became pure: only the public header declares the
    # member (a pure virtual has no body and no symbol), so the emission is a
    # header-backed one. The binary alone must not observe it.
    concrete = _header_function("f", ("int",))
    concrete.is_virtual = True
    pure = _header_function("f", ("int",))
    pure.is_virtual = True
    pure.is_pure_virtual = True
    old = _header_snapshot("1", "elf", [concrete])
    new = _header_snapshot("2", "elf", [pure])
    emitted = {c.kind.value for c in compare(old, new).changes}
    assert "func_virtual_became_pure" in emitted, emitted
    assert (
        REGISTRY.entries["func_virtual_became_pure"].observable_at is not UNCLASSIFIED
    )
    assert _observes_on("func_virtual_became_pure", "elf", BINARY_AND_HEADERS)
    assert not _observes_on("func_virtual_became_pure", "elf", BINARY)


def test_const_value_change_is_emitted_from_section_bytes_alone() -> None:
    # G7: a binary-only dump records an exported read-only object's own section
    # bytes (Variable.initializer_hex), so a changed initializer is observed
    # without any header or DWARF. Declared {B}; the header-backed value path
    # is unchanged and still emits as well.
    old = AbiSnapshot(
        library="libsynth",
        version="1",
        platform="elf",
        variables=[
            Variable(
                name="kLimit",
                mangled="kLimit",
                type="?",
                is_const=True,
                initializer_hex="0a000000",
            )
        ],
    )
    new = AbiSnapshot(
        library="libsynth",
        version="2",
        platform="elf",
        variables=[
            Variable(
                name="kLimit",
                mangled="kLimit",
                type="?",
                is_const=True,
                initializer_hex="14000000",
            )
        ],
    )
    emitted = {c.kind.value for c in compare(old, new).changes}
    assert "var_value_changed" in emitted, emitted
    assert _observes_on("var_value_changed", "elf", BINARY)


@pytest.mark.integration
def test_exported_symbol_with_a_closure_type_is_emitted_from_the_binary_alone(
    tmp_path: Path,
) -> None:
    # Real-compile oracle: an exported symbol whose mangled name embeds a lambda
    # closure (Ul...E_) is visible in the export table of a build without debug
    # info (no -g), so the binary alone observes the kind. No strip step: GNU
    # strip's --strip-debug is absent from macOS strip, and a build without -g
    # already carries no DWARF.
    # The claim is g++'s linkage: clang gives the closure-typed function internal
    # linkage (not exported), so only a GNU ELF producer exercises it.
    prefix = elf_capability("gcc", "shared")
    if prefix is None:
        pytest.skip(skip_reason("gcc", "shared"))
    old_src = tmp_path / "old.cpp"
    new_src = tmp_path / "new.cpp"
    old_src.write_text(
        'auto lam = []{ return 1; };\nextern "C" int keep(int x){return x;}\n'
    )
    new_src.write_text(
        'auto lam = []{ return 1; };\nextern "C" int keep(int x){return x;}\n'
        "int take(decltype(lam)* p) { return (*p)(); }\n"
    )
    dumps = []
    for name, src in (("old", old_src), ("new", new_src)):
        so = tmp_path / f"lib{name}.so"
        subprocess.run(
            [*prefix, "-std=c++17", "-O0", "-fPIC", "-shared", str(src), "-o", str(so)],
            check=True,
        )
        out = tmp_path / f"{name}.json"
        subprocess.run(
            abicheck_command("dump", str(so), "-o", str(out)),
            check=True,
            capture_output=True,
        )
        dumps.append(out)
    result = subprocess.run(
        abicheck_command("compare", str(dumps[0]), str(dumps[1])),
        capture_output=True,
        text=True,
    )
    assert "unnamed_type_in_public_abi" in result.stdout, result.stdout


_HEADER_PAIRS = {
    "template_param_type_changed": (
        'template <class T> struct Box { T v; };\nextern "C" int h(Box<int> b);\n',
        'template <class T> struct Box { T v; };\nextern "C" int h(Box<long> b);\n',
        'extern "C" int h(Box<int> b){ return b.v; }\n',
        'extern "C" int h(Box<long> b){ return (int)b.v; }\n',
    ),
    "abi_equivalent_type_changed": (
        'extern "C" int h(long x);\n',
        'extern "C" int h(long long x);\n',
        'extern "C" int h(long x){ return (int)x; }\n',
        'extern "C" int h(long long x){ return (int)x; }\n',
    ),
    "typedef_version_sentinel": (
        'typedef int lib_version_1_6_46;\nextern "C" int api(int x);\n',
        'typedef int lib_version_1_6_47;\nextern "C" int api(int x);\n',
        'extern "C" int api(int x){ return x; }\n',
        'extern "C" int api(int x){ return x; }\n',
    ),
    "secondary_vtable_group_changed": (
        'struct Base1 { virtual int a() const; };\nstruct Base2 { int b() const; };\nstruct D : Base1, Base2 { int a() const override; };\nextern "C" D* make_d();\n',
        'struct Base1 { virtual int a() const; };\nstruct Base2 { virtual int b() const; };\nstruct D : Base1, Base2 { int a() const override; };\nextern "C" D* make_d();\n',
        'int Base1::a() const {return 1;}\nint Base2::b() const {return 2;}\nint D::a() const {return 1;}\nextern "C" D* make_d(){ return new D; }\n',
        'int Base1::a() const {return 1;}\nint Base2::b() const {return 2;}\nint D::a() const {return 1;}\nextern "C" D* make_d(){ return new D; }\n',
    ),
    "template_return_type_changed": (
        "template <class T> struct Box { T v; };\nBox<int> g(int x);\n",
        "template <class T> struct Box { T v; };\nBox<long> g(int x);\n",
        "Box<int> g(int x){ return Box<int>{x}; }\n",
        "Box<long> g(int x){ return Box<long>{x}; }\n",
    ),
    "opaque_invariant_broken": (
        "struct Impl;\nImpl* make();\nint use(Impl* p);\n",
        "struct Impl { int x; };\nImpl* make();\nint use(Impl* p);\n",
        "struct Impl { int x; };\nImpl* make(){ static Impl i{1}; return &i; }\nint use(Impl* p){ return p->x; }\n",
        "Impl* make(){ static Impl i{1}; return &i; }\nint use(Impl* p){ return p->x; }\n",
    ),
    "handle_type_changed": (
        "struct HandleA;\ntypedef struct HandleA* Handle;\nHandle make();\nint use(Handle h);\n",
        "struct HandleB;\ntypedef struct HandleB* Handle;\nHandle make();\nint use(Handle h);\n",
        "struct HandleA { int x; };\nHandle make(){ static HandleA a{1}; return &a; }\nint use(Handle h){ return h->x; }\n",
        "struct HandleB { int x; };\nHandle make(){ static HandleB a{1}; return &a; }\nint use(Handle h){ return h->x; }\n",
    ),
    "used_reserved_field": (
        'struct R { int a; int reserved1; };\nextern "C" int rsz(const R* r);\n',
        'struct R { int a; int priority; };\nextern "C" int rsz(const R* r);\n',
        'extern "C" int rsz(const R* r){return r->a;}\n',
        'extern "C" int rsz(const R* r){return r->a;}\n',
    ),
    "tag_type_renamed": (
        "struct Tag {};\ntemplate <class T> int f(T) { return 0; }\nint use_tag(Tag t);\n",
        "struct Tag2 {};\ntemplate <class T> int f(T) { return 0; }\nint use_tag(Tag2 t);\n",
        '#include "h_old.h"\nint use_tag(Tag t){ return f(t); }\n',
        '#include "h_new.h"\nint use_tag(Tag2 t){ return f(t); }\n',
    ),
}


@pytest.mark.integration
@pytest.mark.parametrize("kind", sorted(_HEADER_PAIRS))
def test_header_backed_kind_is_not_observed_from_a_stripped_binary(
    kind: str, tmp_path: Path
) -> None:
    # Real-compile oracle (GNU ELF producer): the kind is declared {B,D} and
    # {B,H}. The header pair emits it, and the same stripped binary pair (no
    # debug info, no header) must not, since the binary has no type for it.
    prefix = elf_capability("gcc", "shared")
    if prefix is None:
        pytest.skip(skip_reason("gcc", "shared"))
    h_old, h_new, c_old, c_new = _HEADER_PAIRS[kind]
    (tmp_path / "h_old.h").write_text("#pragma once\n" + h_old)
    (tmp_path / "h_new.h").write_text("#pragma once\n" + h_new)
    (tmp_path / "old.cpp").write_text(
        c_old if "#include" in c_old else '#include "h_old.h"\n' + c_old
    )
    (tmp_path / "new.cpp").write_text(
        c_new if "#include" in c_new else '#include "h_new.h"\n' + c_new
    )

    def build(name: str, src: str, header: str, strip: bool) -> Path:
        so = tmp_path / f"lib{name}.so"
        subprocess.run(
            [
                *prefix,
                "-O0",
                "-fPIC",
                "-shared",
                "-I",
                str(tmp_path),
                str(tmp_path / src),
                "-o",
                str(so),
            ],
            check=True,
        )
        out = tmp_path / f"{name}.json"
        args = ["dump", str(so), "-o", str(out)]
        if header:
            args += ["-H", str(tmp_path / header), "--depth", "headers"]
        subprocess.run(abicheck_command(*args), check=True, capture_output=True)
        return out

    def report(a: Path, b: Path) -> str:
        # Text and JSON: a kind the text summary describes only as a group line
        # (secondary_vtable_group_changed) is still named in the JSON report.
        out = tmp_path / f"{a.stem}-{b.stem}.json"
        text = subprocess.run(
            abicheck_command("compare", str(a), str(b), "-o", f"json={out}"),
            capture_output=True,
            text=True,
        ).stdout
        return text + (out.read_text() if out.exists() else "")

    hdr = [
        build("ho", "old.cpp", "h_old.h", False),
        build("hn", "new.cpp", "h_new.h", False),
    ]
    assert kind in report(hdr[0], hdr[1])
    bin_ = [build("bo", "old.cpp", "", True), build("bn", "new.cpp", "", True)]
    assert kind not in report(bin_[0], bin_[1])


@pytest.mark.integration
def test_template_instance_removal_and_overload_reroute_are_binary_observable(
    tmp_path: Path,
) -> None:
    # Real-compile oracle (GNU ELF producer): a tag rename re-mangles an exported
    # template instance. The stripped binary pair (no headers, no DWARF) emits both
    # the missing instance and the rerouted overload set, so both are observable
    # from the binary alone.
    prefix = elf_capability("gcc", "shared")
    if prefix is None:
        pytest.skip(skip_reason("gcc", "shared"))
    (tmp_path / "h_old.h").write_text(
        "#pragma once\nstruct Tag {};\ntemplate <class T> int f(T) { return 0; }\nint use_tag(Tag t);\n"
    )
    (tmp_path / "h_new.h").write_text(
        "#pragma once\nstruct Tag2 {};\ntemplate <class T> int f(T) { return 0; }\nint use_tag(Tag2 t);\n"
    )
    (tmp_path / "old.cpp").write_text(
        '#include "h_old.h"\nint use_tag(Tag t){ return f(t); }\nint keep_me(){ return f(1); }\n'
    )
    (tmp_path / "new.cpp").write_text(
        '#include "h_new.h"\nint use_tag(Tag2 t){ return f(t); }\nint keep_me(){ return f(1); }\n'
    )
    dumps = []
    for name, src in (("old", "old.cpp"), ("new", "new.cpp")):
        so = tmp_path / f"lib{name}.so"
        subprocess.run(
            [
                *prefix,
                "-O0",
                "-fPIC",
                "-shared",
                "-I",
                str(tmp_path),
                str(tmp_path / src),
                "-o",
                str(so),
            ],
            check=True,
        )
        out = tmp_path / f"{name}.json"
        subprocess.run(
            abicheck_command("dump", str(so), "-o", str(out)),
            check=True,
            capture_output=True,
        )
        dumps.append(out)
    stdout = subprocess.run(
        abicheck_command("compare", str(dumps[0]), str(dumps[1])),
        capture_output=True,
        text=True,
    ).stdout
    assert "instantiation_missing_from_binary" in stdout, stdout
    assert "overload_set_rerouted" in stdout, stdout


@pytest.mark.integration
def test_function_becoming_variable_is_observed_from_the_stripped_binary(
    tmp_path: Path,
) -> None:
    # Real-compile oracle (GNU ELF producer): a public name that is an exported
    # function on one side and an exported variable on the other is named by the
    # export table's symbol types alone, so the stripped binary pair emits it.
    prefix = elf_capability("gcc", "shared")
    if prefix is None:
        pytest.skip(skip_reason("gcc", "shared"))
    (tmp_path / "old.cpp").write_text('extern "C" int cpo(int x){ return x; }\n')
    (tmp_path / "new.cpp").write_text('extern "C" int cpo = 1;\n')
    dumps = []
    for name in ("old", "new"):
        so = tmp_path / f"lib{name}.so"
        subprocess.run(
            [
                *prefix,
                "-O0",
                "-fPIC",
                "-shared",
                str(tmp_path / f"{name}.cpp"),
                "-o",
                str(so),
            ],
            check=True,
        )
        out = tmp_path / f"{name}.json"
        subprocess.run(
            abicheck_command("dump", str(so), "-o", str(out)),
            check=True,
            capture_output=True,
        )
        dumps.append(out)
    stdout = subprocess.run(
        abicheck_command("compare", str(dumps[0]), str(dumps[1])),
        capture_output=True,
        text=True,
    ).stdout
    assert "cpo_kind_changed" in stdout, stdout


@pytest.mark.integration
@pytest.mark.parametrize("family", ["gcc", "clang"])
def test_deleted_member_is_emitted_as_dwarf_deletion_only_with_debug_info(
    tmp_path: Path, family: str
) -> None:
    # Real-compile oracle (GNU and Clang ELF producers): a member that becomes
    # `= delete` keeps a DWARF declaration carrying DW_AT_deleted, but has no
    # export symbol. With -g the pair emits func_deleted_dwarf; without -g the
    # same change is only a removed export (func_removed_elf_only), so the
    # declared {B,D} observation is the debug info, not the binary alone.
    prefix = elf_capability(family, "shared")
    if prefix is None:
        pytest.skip(skip_reason(family, "shared"))
    (tmp_path / "old.cpp").write_text(
        "struct S { int f(int x); int g(int x); };\n"
        "int S::f(int x) { return x + 1; }\n"
        "int S::g(int x) { return x * 2; }\n"
    )
    (tmp_path / "new.cpp").write_text(
        "struct S { int f(int x) = delete; int g(int x); };\n"
        "int S::g(int x) { return x * 2; }\n"
    )

    def _emitted(debug: bool) -> str:
        dumps = []
        for name in ("old", "new"):
            so = tmp_path / f"{family}-{int(debug)}-{name}.so"
            cmd = [*prefix, "-O0", "-fPIC", "-shared"]
            if debug:
                cmd.append("-g")
            cmd += [str(tmp_path / f"{name}.cpp"), "-o", str(so)]
            subprocess.run(cmd, check=True)
            out = tmp_path / f"{family}-{int(debug)}-{name}.json"
            subprocess.run(
                abicheck_command("dump", str(so), "-o", str(out)),
                check=True,
                capture_output=True,
            )
            dumps.append(out)
        return subprocess.run(
            abicheck_command("compare", str(dumps[0]), str(dumps[1])),
            capture_output=True,
            text=True,
        ).stdout

    with_debug = _emitted(debug=True)
    assert "func_deleted_dwarf" in with_debug, with_debug
    assert _observes_on("func_deleted_dwarf", "elf", ("B", "D"))
    assert not _observes_on("func_deleted_dwarf", "elf", BINARY)

    without_debug = _emitted(debug=False)
    assert "func_deleted_dwarf" not in without_debug, without_debug
    assert "func_removed_elf_only" in without_debug, without_debug


@pytest.mark.integration
def test_stdlib_implementation_change_is_observed_from_the_binary_alone(
    tmp_path: Path,
) -> None:
    # Real-compile oracle: a library built against libc++ (clang, std::__1
    # mangling) and one built against libstdc++ (g++, std::__cxx11) differ only
    # in the mangled std:: namespace the export table names. The build mode
    # resolves from that namespace, so the binary pair alone emits
    # stdlib_implementation_changed on the live compare path (no headers, no
    # debug info). Declared {B}.
    from abicheck.checker import compare
    from abicheck.dumper import dump

    clang = elf_capability("clang", "shared")
    gcc = elf_capability("gcc", "shared")
    if clang is None:
        pytest.skip(skip_reason("clang", "shared"))
    if gcc is None:
        pytest.skip(skip_reason("gcc", "shared"))
    probe = subprocess.run(
        [*clang, "-stdlib=libc++", "-fsyntax-only", "-x", "c++", "-"],
        input="#include <string>\n",
        text=True,
        capture_output=True,
    )
    if probe.returncode != 0:
        pytest.skip("libc++ headers are not installed: " + probe.stderr[:200])
    source = tmp_path / "api.cpp"
    source.write_text(
        "#include <string>\n"
        "void take(const std::string& s);\n"
        "void take(const std::string& s) { (void)s; }\n"
    )
    libs = {
        "libcxx": [*clang, "-stdlib=libc++", "-O0", "-fPIC", "-shared"],
        "libstdcxx": [*gcc, "-O0", "-fPIC", "-shared"],
    }
    snaps = {}
    for name, cmd in libs.items():
        so = tmp_path / f"{name}.so"
        subprocess.run([*cmd, str(source), "-o", str(so)], check=True)
        snaps[name] = dump(so, [])
    kinds = {c.kind.value for c in compare(snaps["libcxx"], snaps["libstdcxx"]).changes}
    assert "stdlib_implementation_changed" in kinds, kinds
    assert _observes_on("stdlib_implementation_changed", "elf", BINARY)


@pytest.mark.integration
def test_removed_isa_tier_is_observed_from_the_export_table_alone(
    tmp_path: Path,
) -> None:
    # Real-compile oracle (GNU ELF producer): four exported `*_avx2` functions
    # removed while their `*_sse2` siblings survive. The ISA-suffix clustering
    # reads only the export table, so the binary pair alone emits
    # cpu_dispatch_isa_dropped. Declared {B}.
    from abicheck.checker import compare
    from abicheck.dumper import dump

    prefix = elf_capability("gcc", "shared")
    if prefix is None:
        pytest.skip(skip_reason("gcc", "shared"))
    old_fns = "".join(
        f'extern "C" int vec_{op}_{isa}(int x){{return x{sym} {n};}}\n'
        for isa, n in (("avx2", 1), ("sse2", 3))
        for op, sym in (("add", "+"), ("mul", "*"), ("sub", "-"), ("div", "/"))
    )
    (tmp_path / "old.cpp").write_text(old_fns)
    (tmp_path / "new.cpp").write_text(
        "".join(
            f'extern "C" int vec_{op}_sse2(int x){{return x{sym} {n};}}\n'
            for op, sym, n in (
                ("add", "+", 3),
                ("mul", "*", 4),
                ("sub", "-", 2),
                ("div", "/", 3),
            )
        )
    )
    snaps = {}
    for name in ("old", "new"):
        so = tmp_path / f"{name}.so"
        subprocess.run(
            [
                *prefix,
                "-O0",
                "-fPIC",
                "-shared",
                str(tmp_path / f"{name}.cpp"),
                "-o",
                str(so),
            ],
            check=True,
        )
        snaps[name] = dump(so, [])
    kinds = {c.kind.value for c in compare(snaps["old"], snaps["new"]).changes}
    assert "cpu_dispatch_isa_dropped" in kinds, kinds
    assert _observes_on("cpu_dispatch_isa_dropped", "elf", BINARY)


@pytest.mark.integration
def test_bundle_soname_skew_is_observed_from_the_set_of_dt_sonames(
    tmp_path: Path,
) -> None:
    # Real-compile oracle (GNU ELF producer): a cohort whose libfoo_core moves
    # SONAME major 1 -> 2 while libfoo_thread stays at 1. The detector reads
    # only each library's DT_SONAME across a package-scope set, so the binary
    # facts alone emit bundle_soname_skew. Declared B:SCOPE.
    from abicheck.bundle_detector_heuristics import _soname_skew_findings
    from abicheck.diff_cpp_patterns import BundleMember, _extract_soname_major

    prefix = elf_capability("gcc", "shared")
    if prefix is None:
        pytest.skip(skip_reason("gcc", "shared"))
    (tmp_path / "core.c").write_text("int f_core(void) { return 1; }\n")
    (tmp_path / "thread.c").write_text("int f_thread(void) { return 1; }\n")

    def _build(
        version: str, core_soname: str, thread_soname: str
    ) -> list[BundleMember]:
        out = tmp_path / version
        out.mkdir()
        for lib, src, soname in (
            ("libfoo_core", "core.c", core_soname),
            ("libfoo_thread", "thread.c", thread_soname),
        ):
            subprocess.run(
                [
                    *prefix,
                    "-O0",
                    "-fPIC",
                    "-shared",
                    f"-Wl,-soname,{soname}",
                    str(tmp_path / src),
                    "-o",
                    str(out / soname),
                ],
                check=True,
            )
        members = []
        for path in sorted(out.iterdir()):
            dynamic = subprocess.run(
                ["readelf", "-d", str(path)], capture_output=True, text=True, check=True
            ).stdout
            name = dynamic.split("SONAME", 1)[1].split("[", 1)[1].split("]", 1)[0]
            members.append(
                BundleMember(
                    library=path.name,
                    soname=name,
                    soname_major=_extract_soname_major(name),
                )
            )
        return members

    old = _build("old", "libfoo_core.so.1", "libfoo_thread.so.1")
    new = _build("new", "libfoo_core.so.2", "libfoo_thread.so.1")
    kinds = {f.kind.value for f in _soname_skew_findings(old, new, ["libfoo_"])}
    assert "bundle_soname_skew" in kinds, kinds
    assert _observes_on("bundle_soname_skew", "elf", ("B",))


@pytest.mark.integration
def test_sycl_overload_family_removal_needs_the_header_not_the_binary(
    tmp_path: Path,
) -> None:
    # Real-compile oracle (GNU ELF producer): two algorithm families each lose
    # their sycl::queue& overload while a non-SYCL sibling survives. The
    # family is named by the queue parameter type, which only the public
    # header carries, so the header pair emits sycl_overload_set_removed and
    # the binary-only pair does not (func_removed instead). Declared {B,H}.
    from abicheck.checker import compare
    from abicheck.dumper import dump

    prefix = elf_capability("gcc", "shared")
    if prefix is None:
        pytest.skip(skip_reason("gcc", "shared"))
    old_h = (
        "namespace sycl { struct queue { int id; }; }\n"
        "void run(sycl::queue& q);\nvoid run(sycl::queue& q, int n);\nvoid run(int x);\n"
        "void compute(sycl::queue& q);\nvoid compute(int x);\n"
    )
    new_h = (
        "namespace sycl { struct queue { int id; }; }\n"
        "void run(int x);\nvoid compute(int x);\n"
    )
    old_cpp = (
        '#include "old.h"\n'
        "void run(sycl::queue& q){(void)q;}\n"
        "void run(sycl::queue& q, int n){(void)q;(void)n;}\n"
        "void run(int x){(void)x;}\n"
        "void compute(sycl::queue& q){(void)q;}\n"
        "void compute(int x){(void)x;}\n"
    )
    new_cpp = (
        '#include "new.h"\nvoid run(int x){(void)x;}\nvoid compute(int x){(void)x;}\n'
    )
    (tmp_path / "old.h").write_text(old_h)
    (tmp_path / "new.h").write_text(new_h)
    (tmp_path / "old.cpp").write_text(old_cpp)
    (tmp_path / "new.cpp").write_text(new_cpp)
    for name in ("old", "new"):
        so = tmp_path / f"{name}.so"
        subprocess.run(
            [
                *prefix,
                "-g",
                "-O0",
                "-fPIC",
                "-shared",
                f"-I{tmp_path}",
                str(tmp_path / f"{name}.cpp"),
                "-o",
                str(so),
            ],
            check=True,
        )
    with_header = compare(
        dump(tmp_path / "old.so", [tmp_path / "old.h"]),
        dump(tmp_path / "new.so", [tmp_path / "new.h"]),
    )
    kinds = {c.kind.value for c in with_header.changes}
    assert "sycl_overload_set_removed" in kinds, kinds
    assert _observes_on("sycl_overload_set_removed", "elf", ("B", "H"))
    binary_only = compare(dump(tmp_path / "old.so", []), dump(tmp_path / "new.so", []))
    assert "sycl_overload_set_removed" not in {
        c.kind.value for c in binary_only.changes
    }
    assert not _observes_on("sycl_overload_set_removed", "elf", BINARY)
