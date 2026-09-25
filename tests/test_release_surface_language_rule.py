"""The release surface parses its headers in the language the members do.

Bug class: two paths that acquire the *same* header tree for one release
decide its language differently. The member rule is ``service_dump_native``'s
``lang if (lang_explicit or lang == "c") else None``. The release surface once
treated any non-empty ``lang`` -- ``compare``'s default ``"c++"`` included --
as explicit, so a C tree became a C++ contract no member exports; its first
fix then read explicitness off the spelling (only ``"c"``), which dropped a
stated ``compile.lang: c++`` and let an ambiguous header's C export satisfy a
C++ contract. The oracle below is the member rule, restated independently,
over every spelling and both explicitness states.
"""

from __future__ import annotations

import pytest

from abicheck.workflows.release_public_surface import build_side_identity


def _member_forced_language(lang: str, explicit: bool = False) -> str | None:
    """What an ELF member dump forces for *lang* and its explicitness."""
    if explicit and lang:
        return lang
    return lang if lang == "c" else None


@pytest.mark.parametrize("explicit", [False, True])
@pytest.mark.parametrize("lang", ["c", "c++", "", "cpp", "C", "c++20", "objc"])
def test_release_surface_forces_exactly_what_a_member_forces(
    lang: str, explicit: bool
) -> None:
    identity = build_side_identity(
        [],
        [],
        lang=lang,
        exclude_headers=(),
        public_header_dirs=None,
        compile_context=None,
        depth=None,
        include_dependencies=False,
        lang_explicit=explicit,
    )
    forced = identity.lang if identity.lang_explicit else None
    assert forced == _member_forced_language(lang, explicit)


def test_the_oracle_distinguishes_the_two_outcomes() -> None:
    """Vacuity guard: the sweep covers both a forced and an auto-detected
    language, so a constant answer cannot pass it."""
    assert _member_forced_language("c") == "c"
    assert _member_forced_language("c++") is None
    assert _member_forced_language("c++", explicit=True) == "c++"
