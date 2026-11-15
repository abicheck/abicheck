"""The guard branches of Mach-O object-size derivation and corroboration.

Platform-independent: the symbols and fixup addresses are literal values, and
the expected sizes are hand-computed from the Itanium layout the module
documents (``_ZTV``: every word but offset-to-top is relocated).
"""

from __future__ import annotations

from abicheck.extract.macho_object_sizes import (
    corroborate_object_sizes,
    derive_object_sizes,
)

# Section 1 spans 0x100..0x120.
SECTIONS = {1: (0x100, 0x20)}


def test_closer_past_the_section_end_is_not_a_boundary() -> None:
    # _ZTV3Foo at 0x100 is followed only by a label at 0x140, outside its
    # section. That label is not this object's closer, so no size is derived.
    defined = [(1, 0x100, "_ZTV3Foo"), (1, 0x140, "_outside")]
    assert derive_object_sizes(defined, SECTIONS) == {}


def test_closer_inside_the_section_bounds_the_object() -> None:
    # Negative control: the same object with its closer at 0x110 is derived.
    defined = [(1, 0x100, "_ZTV3Foo"), (1, 0x110, "_ZTI3Foo")]
    assert derive_object_sizes(defined, SECTIONS) == {"_ZTV3Foo": (0x10, "_ZTI3Foo")}


def test_a_size_with_no_recorded_address_keeps_no_size() -> None:
    # A derived size whose symbol has no address entry cannot be corroborated.
    sizes = {"_ZTV3Foo": (16, "_ZTI3Foo")}
    assert corroborate_object_sizes(sizes, {}, frozenset({0x108}), 8) == {}


def test_a_size_not_a_whole_number_of_pointers_keeps_no_size() -> None:
    # 12 bytes cannot hold whole 8-byte pointer words: refused before counting.
    sizes = {"_ZTV3Foo": (12, "_ZTI3Foo")}
    addresses = {"_ZTV3Foo": 0x100}
    assert corroborate_object_sizes(sizes, addresses, frozenset({0x108}), 8) == {}


def test_a_whole_pointer_size_with_matching_fixups_is_kept() -> None:
    # Negative control for the two refusals above: 16 bytes of _ZTV is one
    # relocated word after offset-to-top, and one fixup (0x108) sits inside.
    sizes = {"_ZTV3Foo": (16, "_ZTI3Foo")}
    addresses = {"_ZTV3Foo": 0x100}
    assert corroborate_object_sizes(sizes, addresses, frozenset({0x108}), 8) == {
        "_ZTV3Foo": (16, "_ZTI3Foo")
    }
