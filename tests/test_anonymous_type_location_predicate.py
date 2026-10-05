"""``has_anonymous_type_location`` gates the load-time strip walk, so it must
never answer ``False`` for a name the strip would change."""

from __future__ import annotations

import itertools

from abicheck.name_classification import (
    has_anonymous_type_location,
    strip_anonymous_type_location,
)

_PIECES = [
    "",
    "ns::",
    "guard<",
    ">",
    "(lambda at /a/b/foo.h:4:37)",
    "(unnamed struct at /x/y.hpp:56:5)",
    "(anonymous union at C:/w/z.h:1:2)",
    "(lambda:foo.h:4:37)",
    '"(lambda at /q/r.h:1:2)"',
    " at ",
    "(lambda\tat /t/tab.h:3:4)",
    "(unnamed struct  at  /two/spaces.h:5:6)",
    "(lambda at nowhere)",
    "Tag<",
]


def test_predicate_false_implies_strip_is_identity() -> None:
    checked = 0
    for n in range(1, 4):
        for parts in itertools.product(_PIECES, repeat=n):
            name = "".join(parts)
            changed = strip_anonymous_type_location(name) != name
            if changed:
                assert has_anonymous_type_location(name), name
            checked += 1
    assert checked > 1000


def test_predicate_true_for_raw_markers_false_for_stripped() -> None:
    assert has_anonymous_type_location("g<(lambda at /a/foo.h:4:37)>")
    assert not has_anonymous_type_location("g<(lambda:foo.h:4:37)>")
    assert not has_anonymous_type_location("plain::name")
    # Any whitespace the strip accepts, not only a single space.
    assert has_anonymous_type_location("g<(lambda\tat /a/foo.h:4:37)>")
