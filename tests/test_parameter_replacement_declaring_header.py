"""``_declaring_header`` strips only a trailing ``:line`` (PR #1812 review):
a bare Windows path keeps its drive colon."""

from __future__ import annotations

import pytest

from abicheck.compare.parameter_replacement import _declaring_header
from abicheck.model import Function


@pytest.mark.parametrize(
    ("location", "expected"),
    [
        ("/usr/include/api.h:12", "api.h"),
        ("api.h:3", "api.h"),
        ("C:\\inc\\api.h:7", "api.h"),
        ("C:\\inc\\api.h", "api.h"),
        ("C:/inc/api.h", "api.h"),
        ("/usr/include/api.h", "api.h"),
        ("", ""),
        (None, ""),
    ],
)
def test_declaring_header_is_the_basename(location: str | None, expected: str) -> None:
    fn = Function(
        name="f", mangled="_Z1fv", return_type="void", source_location=location
    )
    assert _declaring_header(fn) == expected


def test_two_drives_do_not_collide_on_the_drive_letter() -> None:
    a = Function(
        name="f", mangled="_Z1fv", return_type="void", source_location="C:\\a\\x.h"
    )
    b = Function(
        name="f", mangled="_Z1fv", return_type="void", source_location="D:\\b\\y.h"
    )
    assert (_declaring_header(a), _declaring_header(b)) == ("x.h", "y.h")
