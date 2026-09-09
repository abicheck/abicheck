# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""``model.mangled_name.strip_macho_itanium_decoration`` -- the single
canonical ``"__Z..."`` -> ``"_Z..."`` structural check three call sites
share (``extract.headers.clang.context.strip_darwin_itanium_decoration``,
``model.mangled_name._itanium_strip_prefix``, ``dumper_hybrid.
_macho_normalize_mangled``) -- plus a real-compiler-independent property
suite over the *shape* invariant it states, and a direct unit check on
``extract.headers.clang.context.is_darwin_target``'s ``sys.platform``
probe-failure fallback (bug class ``extraction.
macho_mangled_identity_normalization``, ``tests/regressions/manifest.py``).

None of this needs a real Mach-O toolchain: the whole point of the shape
check is that it is decidable from the string alone, with no compiler or
target-triple evidence required -- these tests state and check that
contract directly, generalizing the bug class's own real-compiler
regression coverage in ``tests/test_dumper_clang_extern_c_identity.py``
(the point-of-origin normalization) and
``tests/test_dumper_hybrid_macho_idempotence.py`` (the hybrid-merge
reconciliation) rather than duplicating either.
"""

from __future__ import annotations

import string
import sys

import pytest
from hypothesis import given, strategies as st

from abicheck.model.mangled_name import (
    _itanium_strip_prefix,
    itanium_scope_components,
    strip_macho_itanium_decoration,
)


class TestStripMachoItaniumDecorationExamples:
    """Fixed-example coverage for the three shapes the function must
    distinguish."""

    def test_strips_the_doubly_underscore_decorated_shape(self) -> None:
        assert strip_macho_itanium_decoration("__Z10plain_funci") == "_Z10plain_funci"
        assert strip_macho_itanium_decoration("__ZN3lib3addEii") == "_ZN3lib3addEii"

    def test_leaves_an_already_pure_itanium_name_untouched(self) -> None:
        assert strip_macho_itanium_decoration("_Z10plain_funci") == "_Z10plain_funci"
        assert strip_macho_itanium_decoration("_ZN3lib3addEii") == "_ZN3lib3addEii"

    def test_leaves_a_bare_singly_underscore_prefixed_name_untouched(self) -> None:
        """The single-underscore shape (``"_foo"``) is genuinely ambiguous
        with a real, distinct ``asm("_foo")`` label -- resolving it needs
        extra evidence (``is_extern_c``) this shape-only helper doesn't
        have, so it must never touch this shape at all. See
        ``extract.headers.clang.context.strip_darwin_itanium_decoration``
        for the narrower, evidence-gated case that DOES resolve it."""
        assert strip_macho_itanium_decoration("_foo") == "_foo"
        assert strip_macho_itanium_decoration("_c_func") == "_c_func"

    def test_leaves_a_bare_name_with_no_leading_underscore_untouched(self) -> None:
        assert strip_macho_itanium_decoration("foo") == "foo"
        assert strip_macho_itanium_decoration("") == ""

    def test_idempotent_on_its_own_output(self) -> None:
        """Applying the strip twice must equal applying it once -- the
        exact property whose absence corrupted an already-normalized
        clang mangled name into ``dumper_hybrid``'s old unconditional
        single-underscore strip (see
        ``tests/test_dumper_hybrid_macho_idempotence.py``)."""
        for raw in ("__Z10plain_funci", "_Z10plain_funci", "_foo", "foo", ""):
            once = strip_macho_itanium_decoration(raw)
            twice = strip_macho_itanium_decoration(once)
            assert once == twice


# A restricted alphabet keeps generated "identifiers" plausible without
# needing a real Itanium grammar -- this suite is about the shape
# invariant (leading-underscore count), not full mangled-name validity.
_IDENTIFIER_CHARS = string.ascii_letters + string.digits + "_"


@given(st.text(alphabet=_IDENTIFIER_CHARS, max_size=40))
def test_never_changes_a_name_not_shaped_exactly_like_macho_decorated_itanium(
    name: str,
) -> None:
    """The only shape this function may ever change is a leading
    ``"__Z"`` -- every other string, however it's generated, must come
    back unchanged. States the function's full domain as a property
    rather than only the hand-picked examples above."""
    result = strip_macho_itanium_decoration(name)
    if name.startswith("__Z"):
        assert result == name[1:]
    else:
        assert result == name


@given(st.text(alphabet=_IDENTIFIER_CHARS, min_size=1, max_size=40))
def test_stripping_a_real_itanium_name_after_doubling_its_prefix_round_trips(
    body: str,
) -> None:
    """For ANY already-pure Itanium-shaped name (``"_Z" + body``,
    ``body`` never itself starting with another ``"Z"`` that would spell a
    different real prefix), decorating it Darwin-style (one more leading
    underscore) and then stripping it back must exactly reconstruct the
    original -- the round trip the whole normalization exists to
    guarantee for a real compiled Mach-O Itanium symbol."""
    pure = "_Z" + body
    decorated = "_" + pure
    assert decorated.startswith("__Z")
    assert strip_macho_itanium_decoration(decorated) == pure


class TestItaniumStripPrefixSharesTheSameShapeCheck:
    """``_itanium_strip_prefix`` (used by ``itanium_scope_components``, in
    turn read by ``ctor_export_match.py``'s export-table rescue and
    ``virtual_dispatch_graph.py``'s vtable-owner recovery per this
    module's own docstring) now delegates its Mach-O-decoration handling
    to :func:`strip_macho_itanium_decoration` instead of an independent,
    hand-rolled copy of the identical check -- these tests pin that it
    still resolves a Mach-O-decorated name exactly like an already-pure
    one, the behavior the delegation must preserve."""

    def test_resolves_a_macho_decorated_name_identically_to_the_pure_spelling(
        self,
    ) -> None:
        pure = "_ZN3lib3addEii"
        decorated = "__ZN3lib3addEii"
        assert _itanium_strip_prefix(pure) == _itanium_strip_prefix(decorated)

    def test_itanium_scope_components_agree_for_pure_and_decorated_spellings(
        self,
    ) -> None:
        pure = "_ZN3lib3addEii"
        decorated = "__ZN3lib3addEii"
        assert itanium_scope_components(pure) == itanium_scope_components(decorated)

    def test_a_bare_asm_label_shape_still_rejected(self) -> None:
        """A single leading underscore alone must not be misread as a
        Mach-O-decorated Itanium name -- ``_itanium_strip_prefix`` still
        requires the doubled shape, or a genuine bare ``"_Z"`` prefix,
        before it recognizes anything at all."""
        assert _itanium_strip_prefix("_foo") is None


class TestIsDarwinTargetNeverGuessesFromBareNone:
    """``is_darwin_target`` never guesses Darwin from a bare/empty
    *target_triple* -- see that function's own docstring and the bug
    class's third residual in ``tests/regressions/manifest.py``. An
    earlier revision special-cased a bare ``None`` by the host's own
    ``sys.platform``, but that shape is also what a direct,
    no-pipeline-involved unit-test construction of ``_ClangAstParser``
    produces (``tests/test_dumper_clang_extern_c_identity.py``), so
    special-casing it here would flip THOSE tests' answer depending on
    which OS runs the suite. The real dump pipeline's own probe-failure
    recovery (a real ``sys.platform``-based fallback *string*, so it
    reaches this function as an ordinary non-``None`` triple) lives one
    layer up, in ``dumper._run_clang`` -- covered by
    ``tests/test_dumper_target_triple_fallback.py``."""

    @pytest.mark.parametrize("running_platform", ["darwin", "linux", "win32", ""])
    def test_bare_none_or_empty_is_always_false(
        self, monkeypatch: pytest.MonkeyPatch, running_platform: str
    ) -> None:
        import abicheck.extract.headers.clang.context as _context

        monkeypatch.setattr(sys, "platform", running_platform)
        assert _context.is_darwin_target(None) is False
        assert _context.is_darwin_target("") is False

    def test_explicit_non_darwin_triple_is_never_overridden_by_host_platform(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A triple that was actually probed and says "linux" must never
        be second-guessed by the host OS, even if this test suite
        happened to be running on a real Darwin machine."""
        import abicheck.extract.headers.clang.context as _context

        monkeypatch.setattr(sys, "platform", "darwin")
        assert _context.is_darwin_target("x86_64-unknown-linux-gnu") is False
