"""The release surface parses its headers in the language the members do.

Bug class: two paths that acquire the *same* header tree for one release
decide its language differently. The member dumps honour ``lang`` only when
it is ``"c"`` (``service_dump_native``: ``lang if (lang_explicit or lang ==
"c") else None``, with the release fan-out never stating explicitness), while
the release surface treated any non-empty ``lang`` -- including ``compare``'s
default ``"c++"`` -- as explicit. A C tree then became a C++ contract whose
manglings no member exports. The oracle below is the member rule itself,
restated independently, over every spelling a caller can pass.
"""

from __future__ import annotations

import pytest

from abicheck.workflows.release_public_surface import build_side_identity


def _member_forced_language(lang: str) -> str | None:
    """What an ELF member dump forces, with no explicitness stated."""
    return lang if lang == "c" else None


@pytest.mark.parametrize("lang", ["c", "c++", "", "cpp", "C", "c++20", "objc"])
def test_release_surface_forces_exactly_what_a_member_forces(lang: str) -> None:
    identity = build_side_identity(
        [],
        [],
        lang=lang,
        exclude_headers=(),
        public_header_dirs=None,
        compile_context=None,
        depth=None,
        include_dependencies=False,
    )
    forced = identity.lang if identity.lang_explicit else None
    assert forced == _member_forced_language(lang)


def test_the_oracle_distinguishes_the_two_outcomes() -> None:
    """Vacuity guard: the sweep covers both a forced and an auto-detected
    language, so a constant answer cannot pass it."""
    assert _member_forced_language("c") == "c"
    assert _member_forced_language("c++") is None
