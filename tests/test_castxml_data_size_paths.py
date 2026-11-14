"""The castxml data-size derivation's target, hierarchy and unknown paths.

Hand-built CastXML documents (``test_castxml_data_size``'s builders); every
expected size is worked out by hand from the Itanium C++ ABI's layout rules,
never by the helper under test. ``None`` is the derivation's "unknown".
"""

from __future__ import annotations

from pathlib import Path

import pytest

from abicheck.extract.headers.castxml.data_size import (
    data_size_bits,
    itanium_abi_target,
)
from abicheck.extract.headers.castxml.tail_padding_rule import (
    TailPaddingRule,
    tail_padding_rule,
)
from tests.test_castxml_data_size import (
    _ctx,
    _field,
    _implicit,
    _int_char,
    _rec,
    _struct,
)

_CTOR = '<Constructor id="_k{r}" name="S" context="{r}" access="public" file="f1" line="3"/>'


def _ctor(rid: str) -> str:
    return _CTOR.format(r=rid)


def _base(rid: str, off: int, virtual: str = "0") -> str:
    return f'<Base type="{rid}" access="public" virtual="{virtual}" offset="{off}"/>'


# --- target ABI -------------------------------------------------------------

_MSVC_FN = (
    '<Function id="_fn" name="f" returns="_int" context="_1" mangled="?f@@YAHXZ"/>'
)
_ITANIUM_FN = (
    '<Function id="_fn" name="f" returns="_int" context="_1" mangled="_Z1fv"/>'
)


@pytest.mark.parametrize(
    ("target", "itanium"),
    [
        (_ITANIUM_FN, True),
        (_MSVC_FN, False),
        # One Microsoft mangling is enough: the parse targeted MSVC.
        (_ITANIUM_FN + _MSVC_FN.replace('id="_fn"', 'id="_fn2"'), False),
        # A C++ document with nothing mangled: the target is not readable.
        ("", False),
    ],
)
def test_data_size_is_claimed_only_for_an_itanium_target(
    tmp_path: Path, target: str, itanium: bool
) -> None:
    body = _struct("_s", "S", 64, _ctor("_s") + _int_char("_s"))
    ctx = _ctx(body, tmp_path, target=target)
    assert itanium_abi_target(ctx) is itanium
    # int a; char c behind a user-provided ctor: 5 bytes under Itanium.
    assert data_size_bits(ctx, _rec(ctx, "S")) == (40 if itanium else None)


def test_c_document_without_manglings_keeps_sizeof(tmp_path: Path) -> None:
    ctx = _ctx(
        '<Struct id="_s" name="S" context="_1" file="f1" line="1" members="_sa _sc" '
        'size="64" align="32"/>' + _int_char("_s"),
        tmp_path,
        target="",
    )
    ctx.is_cxx_document = False
    assert data_size_bits(ctx, _rec(ctx, "S")) == 64


# --- nvsize vs dsize ---------------------------------------------------------


def _nv_family(tmp_path: Path):  # type: ignore[no-untyped-def]
    """``struct E{}; struct B:E{int i; char c;}; struct X:B,E{}; struct Y:X{};``
    E is a base of B at 0, so X's own E goes past B's data, at byte 5."""
    e = _struct("_e", "E", 8, _implicit("_e"))
    b = _struct("_b", "B", 64, _base("_e", 0) + _implicit("_b") + _int_char("_b"))
    x = _struct("_x", "X", 64, _base("_b", 0) + _base("_e", 5) + _implicit("_x"))
    y = _struct("_y", "Y", 64, _base("_x", 0) + _implicit("_y"))
    return _ctx(e + b + x + y, tmp_path)


@pytest.mark.parametrize(
    ("name", "bits"),
    [
        ("B", 40),  # i at 0, c at 4: five bytes
        ("X", 48),  # B's five bytes, then E at byte 5 occupies byte 6's end
        ("Y", 48),  # X allocated by its six-byte non-virtual size
    ],
)
def test_a_base_is_allocated_by_its_non_virtual_size(
    tmp_path: Path, name: str, bits: int
) -> None:
    ctx = _nv_family(tmp_path)
    assert data_size_bits(ctx, _rec(ctx, name)) == bits


# --- hierarchy -----------------------------------------------------------------


def test_dynamic_base_supplies_the_vptr(tmp_path: Path) -> None:
    """``struct V{virtual void f(); char c;}; struct W:V{void f() override;
    char d;};`` -- W's vptr is V's: W adds only d at byte 9."""
    virt = (
        '<Method id="_m{r}" name="f" context="{r}" access="public" file="f1" '
        'line="3" virtual="1"/>'
    )
    v = _struct(
        "_v",
        "V",
        128,
        _implicit("_v") + virt.format(r="_v") + _field("_vc", "_v", "_char", 64),
    )
    w = _struct(
        "_w",
        "W",
        128,
        _base("_v", 0)
        + _implicit("_w")
        + virt.format(r="_w")
        + _field("_wd", "_w", "_char", 72),
    )
    ctx = _ctx(v + w, tmp_path)
    assert data_size_bits(ctx, _rec(ctx, "V")) == 72
    assert data_size_bits(ctx, _rec(ctx, "W")) == 80


def test_non_virtual_diamond_ladder_is_computed_once_per_record(tmp_path: Path) -> None:
    """``L0 {int}``; each ``Lk`` has two ``L(k-1)`` bases side by side. The
    hierarchy queries reach ``L0`` along 2**n paths; memoized per record and
    depth they are evaluated a polynomial number of times."""
    n = 20
    body = _struct("_l0", "L0", 32, _implicit("_l0") + _field("_l0a", "_l0", "_int", 0))
    for k in range(1, n + 1):
        half = 32 * 2 ** (k - 1)
        body += _struct(
            f"_l{k}",
            f"L{k}",
            2 * half,
            _base(f"_l{k - 1}", 0)
            + _base(f"_l{k - 1}", half // 8)
            + _implicit(f"_l{k}"),
        )
    ctx = _ctx(body, tmp_path)
    assert data_size_bits(ctx, _rec(ctx, f"L{n}")) == 32 * 2**n
    # (query, record, depth): polynomial, never one entry per path (2**n).
    assert len(ctx.layout_flag_cache) <= 3 * (n + 1) ** 2


@pytest.mark.parametrize("case", ["unresolved_base", "virtual_base_deep"])
def test_unknown_base_leaves_data_size_unknown(tmp_path: Path, case: str) -> None:
    if case == "unresolved_base":
        body = _struct(
            "_s", "S", 64, _base("_missing", 0) + _ctor("_s") + _int_char("_s")
        )
    else:
        vb = _struct(
            "_vb", "VB", 32, _implicit("_vb") + _field("_vba", "_vb", "_int", 0)
        )
        mid = _struct("_m", "M", 128, _base("_vb", 0, virtual="1") + _implicit("_m"))
        body = vb + mid + _struct("_s", "S", 128, _base("_m", 0) + _ctor("_s"))
    ctx = _ctx(body, tmp_path)
    assert data_size_bits(ctx, _rec(ctx, "S")) is None


# --- members -------------------------------------------------------------------


def test_flexible_array_member_is_unknown(tmp_path: Path) -> None:
    arr = '<ArrayType id="_flex" min="0" max="" type="_char"/>'
    body = arr + _struct(
        "_s",
        "S",
        32,
        _ctor("_s") + _field("_sa", "_s", "_int", 0) + _field("_sf", "_s", "_flex", 32),
    )
    ctx = _ctx(body, tmp_path)
    assert data_size_bits(ctx, _rec(ctx, "S")) is None


def test_sized_array_member_counts_every_element(tmp_path: Path) -> None:
    arr = '<ArrayType id="_a3" min="0" max="2" type="_char"/>'
    body = arr + _struct(
        "_s",
        "S",
        64,
        _ctor("_s") + _field("_sa", "_s", "_int", 0) + _field("_sb", "_s", "_a3", 32),
    )
    ctx = _ctx(body, tmp_path)
    assert data_size_bits(ctx, _rec(ctx, "S")) == 56  # 4 + 3 bytes


def test_field_without_offset_is_unknown(tmp_path: Path) -> None:
    field = _field("_sa", "_s", "_int", 0).replace(' offset="0"', "")
    ctx = _ctx(_struct("_s", "S", 32, _ctor("_s") + field), tmp_path)
    assert data_size_bits(ctx, _rec(ctx, "S")) is None


@pytest.mark.parametrize(("pod_union", "expected"), [(True, 32), (False, None)])
def test_union(tmp_path: Path, pod_union: bool, expected: int | None) -> None:
    ctor = "" if pod_union else _ctor("_u")
    impl = _implicit("_u") if pod_union else ""
    members = (
        impl + ctor + _field("_ua", "_u", "_int", 0) + _field("_uc", "_u", "_char", 0)
    )
    ids = " ".join(p.split('"')[1] for p in members.split("<")[1:])
    body = (
        f'<Union id="_u" name="U" context="_1" file="f1" line="1" members="{ids}" '
        f'size="32" align="32"/>' + members
    )
    ctx = _ctx(body, tmp_path)
    # A POD union keeps its sizeof; a non-POD one has no derivation here.
    assert data_size_bits(ctx, _rec(ctx, "U")) == expected


# --- special members read from the header text ---------------------------------


def _special(tag: str, name: str, extra: str = "") -> str:
    return (
        f'<{tag} id="_k" name="{name}" context="_s" access="public" file="f1" '
        f'line="3" inline="1"{extra}/>'
    )


_COPY_ARG = '<Argument type="_cref"/>'


@pytest.mark.parametrize(
    ("member", "source_line", "expected"),
    [
        # ~S() = default; -- not user-provided: still POD, sizeof kept.
        (_special("Destructor", "S") + "IMPLICIT", "~S() = default;", 64),
        # ~S() {} -- user-provided: non-POD, tail padding exposed.
        (_special("Destructor", "S") + "IMPLICIT", "~S() {}", 40),
        # S() {} -- a user-provided inline constructor.
        (_special("Constructor", "S"), "S() {}", 40),
    ],
)
def test_special_member_text_decides(
    tmp_path: Path, member: str, source_line: str, expected: int
) -> None:
    member = member.replace("IMPLICIT", _implicit("_s"))
    source = "\n\n" + source_line + "\n" * 30
    ctx = _ctx(_struct("_s", "S", 64, member + _int_char("_s")), tmp_path, source)
    assert data_size_bits(ctx, _rec(ctx, "S")) == expected


def test_unreadable_header_text_is_unknown(tmp_path: Path) -> None:
    """An inline constructor whose declaration text cannot be read may be
    defaulted (compilers disagree) or user-provided: unknown."""
    member = _special("Constructor", "S").replace('file="f1"', 'file="f9"')
    ctx = _ctx(
        '<File id="f9" name="/nonexistent/h.hpp"/>'
        + _struct("_s", "S", 64, member + _int_char("_s")),
        tmp_path,
    )
    assert data_size_bits(ctx, _rec(ctx, "S")) is None


# --- [[no_unique_address]] spellings -------------------------------------------


@pytest.mark.parametrize(
    "defines",
    [
        "#if A\n#define NUA [[no_unique_address]]\n#else\n#define NUA\n#endif",
        # The empty arm last must not hide the attribute arm.
        "#if A\n#define NUA\n#else\n#define NUA [[no_unique_address]]\n#endif",
        "#define INNER no_unique_address\n#define NUA [[INNER]]",
    ],
)
def test_every_macro_arm_counts_as_a_possible_attribute(
    tmp_path: Path, defines: str
) -> None:
    lines = defines.split("\n")
    pad = 10 - len(lines)
    source = defines + "\n" * pad + "NUA E e;\n" + "\n" * 20
    empty = _struct("_e", "E", 8, _implicit("_e"))
    members = _ctor("_s") + _int_char("_s") + _field("_se", "_s", "_e", 40, line=11)
    ctx = _ctx(empty + _struct("_s", "S", 64, members), tmp_path, source)
    # With the attribute e takes no byte (5 bytes); without, byte 5 (6): unknown.
    assert data_size_bits(ctx, _rec(ctx, "S")) is None


def test_macro_that_never_spells_the_attribute_is_ignored(tmp_path: Path) -> None:
    source = "#define NUA alignas(1)\n" + "\n" * 9 + "NUA E e;\n" + "\n" * 20
    empty = _struct("_e", "E", 8, _implicit("_e"))
    members = _ctor("_s") + _int_char("_s") + _field("_se", "_s", "_e", 40, line=11)
    ctx = _ctx(empty + _struct("_s", "S", 64, members), tmp_path, source)
    assert data_size_bits(ctx, _rec(ctx, "S")) == 48  # e occupies byte 5


@pytest.mark.parametrize(("tail_char", "expected"), [(False, None), (True, 72)])
def test_possibly_nua_non_empty_member_range(
    tmp_path: Path, tail_char: bool, expected: int | None
) -> None:
    """A possibly-[[no_unique_address]] member of a non-POD type ends at its
    type's data size (5 bytes) or its sizeof (8): unknown, unless a later
    member ends past both."""
    inner = _struct(
        "_r",
        "R",
        64,
        '<Constructor id="_kr" name="R" context="_r" access="public" '
        'file="f1" line="3"/>' + _int_char("_r"),
    )
    members = _ctor("_s") + _field("_sm", "_s", "_r", 0, line=5)
    if tail_char:
        members += _field("_sx", "_s", "_char", 64, line=6)
    source = "\n\n\n\n[[no_unique_address]] R m;\nchar x;\n" + "\n" * 20
    ctx = _ctx(inner + _struct("_s", "S", 128, members), tmp_path, source)
    assert data_size_bits(ctx, _rec(ctx, "S")) == expected


# --- the target's tail-padding rule --------------------------------------------


@pytest.mark.parametrize(
    ("triple", "rule"),
    [
        ("x86_64-pc-linux-gnu", TailPaddingRule.POD03),
        ("x86_64-linux-gnu", TailPaddingRule.POD03),
        ("aarch64-unknown-linux-gnu", TailPaddingRule.POD03),
        ("x86_64-w64-mingw32", TailPaddingRule.POD03),
        ("x86_64-apple-darwin23.0.0", TailPaddingRule.POD03),
        ("arm64-apple-darwin23.4.0", TailPaddingRule.POD11),
        ("aarch64-apple-darwin", TailPaddingRule.POD11),
        ("arm64-apple-ios17.0", TailPaddingRule.POD11),
        ("arm64_32-apple-watchos", TailPaddingRule.POD11),
        ("wasm32-unknown-emscripten", TailPaddingRule.POD11),
        ("x86_64-unknown-fuchsia", TailPaddingRule.POD11),
        ("x86_64-pc-windows-msvc", None),
        ("riscv64-unknown-elf", None),  # not modeled
        (None, None),  # not recorded
        ("", None),
    ],
)
def test_tail_padding_rule_of_a_triple(
    triple: str | None, rule: TailPaddingRule | None
) -> None:
    assert tail_padding_rule(triple) is rule


def _implicit_copyable(rid: str) -> str:
    """The implicit default and copy constructors CastXML emits for a record
    without user-declared ones (the copy constructor is what proves it
    trivially copyable)."""
    return (
        _implicit(rid)
        + f'<Constructor id="{rid}_cc" name="S" context="{rid}" access="public" '
        f'file="f1" line="1" inline="1" artificial="1"><Argument type="{rid}_cr"/>'
        f'</Constructor><ReferenceType id="{rid}_cr" type="{rid}_cq" size="64" '
        f'align="64"/><CvQualifiedType id="{rid}_cq" type="{rid}" const="1"/>'
    )


def _raw_struct(rid: str, size: int, specials: str, fields: str) -> str:
    """A record whose special members carry child elements (``_struct``
    cannot nest them)."""
    ids = [rid + "_dc"] if f'id="{rid}_dc"' in specials else []
    ids += [rid + "_cc"] if f'id="{rid}_cc"' in specials else []
    ids += [p.split('"')[1] for p in fields.split("<")[1:]]
    return (
        f'<Struct id="{rid}" name="S" context="_1" file="f1" line="1" '
        f'members="{" ".join(ids)}" size="{size}" align="32"/>' + specials + fields
    )


def _private(xml: str) -> str:
    return xml.replace('access="public"', 'access="private"')


_ALL_PRIVATE = _raw_struct(
    "_s", 64, _implicit_copyable("_s"), _private(_int_char("_s"))
)
_AGGREGATE = _raw_struct("_s", 64, _implicit_copyable("_s"), _int_char("_s"))
_NSDMI = _raw_struct(
    "_s",
    64,
    _implicit_copyable("_s"),
    _field("_sa", "_s", "_int", 0, ' init="1"') + _field("_sc", "_s", "_char", 32),
)
_MIXED = _raw_struct(
    "_s",
    64,
    _implicit_copyable("_s"),
    _private(_field("_sa", "_s", "_int", 0)) + _field("_sc", "_s", "_char", 32),
)
_USER_CTOR = _struct("_s", "S", 64, _ctor("_s") + _int_char("_s"))
_NO_CTOR = _raw_struct("_s", 64, "", _int_char("_s"))


@pytest.mark.parametrize(
    ("record", "triple", "expected"),
    [
        # class { int a; char c; } all private: not C++03 POD (5 bytes of
        # data), but C++11 POD -- trivial and standard-layout (sizeof, 8).
        (_ALL_PRIVATE, "x86_64-pc-linux-gnu", 40),
        (_ALL_PRIVATE, "arm64-apple-darwin23.4.0", 64),
        # A user-provided constructor: non-trivial under both rules.
        (_USER_CTOR, "x86_64-pc-linux-gnu", 40),
        (_USER_CTOR, "arm64-apple-darwin23.4.0", 40),
        # A plain aggregate: POD under both.
        (_AGGREGATE, "x86_64-pc-linux-gnu", 64),
        (_AGGREGATE, "arm64-apple-darwin23.4.0", 64),
        # A default member initializer: the default ctor is not trivial.
        (_NSDMI, "arm64-apple-darwin23.4.0", 40),
        # Mixed access: not standard-layout, so not C++11 POD either.
        (_MIXED, "arm64-apple-darwin23.4.0", 40),
        # No constructor emitted at all: a deleted default may hide -> unknown.
        (_NO_CTOR, "arm64-apple-darwin23.4.0", None),
        # MSVC and unknown targets claim nothing.
        (_ALL_PRIVATE, "x86_64-pc-windows-msvc", None),
        (_ALL_PRIVATE, "riscv64-unknown-elf", None),
        (_ALL_PRIVATE, None, None),
    ],
)
def test_pod_follows_the_targets_rule(
    tmp_path: Path, record: str, triple: str | None, expected: int | None
) -> None:
    ctx = _ctx(record, tmp_path, triple=triple)
    assert data_size_bits(ctx, _rec(ctx, "S")) == expected


def test_pod11_base_with_user_ctor_is_not_trivial(tmp_path: Path) -> None:
    """``struct B { B(); int a; }; struct D : B { char c; };`` on Apple arm64:
    D is not trivial (B's constructor), so D's data size is B's 4 bytes plus
    c: 5 bytes, and a further derived class may reuse D's padding."""
    b = _struct("_b", "B", 32, _ctor("_b") + _field("_ba", "_b", "_int", 0))
    d = _struct(
        "_d",
        "D",
        64,
        _base("_b", 0) + _implicit("_d") + _field("_dc", "_d", "_char", 32),
    )
    ctx = _ctx(b + d, tmp_path, triple="arm64-apple-darwin23.4.0")
    assert data_size_bits(ctx, _rec(ctx, "D")) == 40


def test_probe_target_triple_prefers_an_explicit_target() -> None:
    from abicheck.extract.headers.castxml.abi_fundamental import probe_target_triple

    assert probe_target_triple("c++", None, None, ("--target=arm64-apple-darwin",)) == (
        "arm64-apple-darwin"
    )
    assert probe_target_triple(
        "c++", None, None, ("-target", "x86_64-w64-mingw32")
    ) == ("x86_64-w64-mingw32")
    assert probe_target_triple("cl", None, None, ()) is None
