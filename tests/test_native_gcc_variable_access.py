"""The native GCC header backend's static-member access is compared like castxml's.

Bug class: a header backend records a fact the detector never reads. The
native GCC backend (ADR-092, ``ast_producer == "gcc-experimental"``) always
recorded a variable's member access (``TREE_PRIVATE``/``TREE_PROTECTED``),
but ``records_variable_access`` admitted only castxml and current clang, so
narrowing a static data member's access read NO_CHANGE under gcc (edit
corpus ``static_member_access_narrowed``/``_widened``).

Oracle: the C++ access rule for each member, and castxml's record of the
same header (``tests/test_clang_variable_access_parity._CASES``).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from abicheck.checker import compare
from abicheck.compare.declaration_facts import records_variable_access
from abicheck.model.snapshot import AbiSnapshot
from tests.test_clang_variable_access_parity import (
    _CASES,
    _build,
    _count_access,
    _dump,
    _require,
)
from tests.test_native_gcc_workflow import native_plugins  # noqa: F401 (fixture)

pytestmark = pytest.mark.integration


@pytest.fixture
def gcc_env(native_plugins: tuple[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    monkeypatch.setenv("ABICHECK_GCC_PLUGIN", str(native_plugins[1]))


@pytest.mark.parametrize("case", sorted(_CASES))
def test_gcc_records_static_member_access_like_castxml(
    case: str, tmp_path: Path, gcc_env: None
) -> None:
    _require()
    header, expected = _CASES[case]
    so, hdr = _build(tmp_path, case, header)
    assert _count_access(_dump(so, hdr, "castxml")) == expected
    assert _count_access(_dump(so, hdr, "gcc")) == expected


@pytest.mark.parametrize(
    ("old", "new", "kind"),
    [
        ("struct_default", "struct_private", "var_access_changed"),
        ("struct_default", "struct_protected", "var_access_changed"),
        ("struct_private", "struct_default", "var_access_widened"),
        ("class_default", "class_public", "var_access_widened"),
    ],
)
def test_static_member_access_change_is_reported_by_gcc(
    old: str, new: str, kind: str, tmp_path: Path, gcc_env: None
) -> None:
    _require()
    snaps = [_dump(*_build(tmp_path, c, _CASES[c][0]), "gcc") for c in (old, new)]
    assert kind in {c.kind.value for c in compare(*snaps).changes}


def test_unchanged_private_static_member_reports_nothing_on_gcc(
    tmp_path: Path, gcc_env: None
) -> None:
    """Negative control: private on both sides is no change."""
    _require()
    snaps = [
        _dump(*_build(tmp_path, f"v{i}", _CASES["class_default"][0]), "gcc")
        for i in (1, 2)
    ]
    kinds = {c.kind.value for c in compare(*snaps).changes}
    assert not kinds & {"var_access_changed", "var_access_widened"}


def test_gcc_snapshot_is_admitted_by_the_access_gate() -> None:
    snap = AbiSnapshot(library="a.so", version="1", ast_producer="gcc-experimental")
    assert records_variable_access(snap)
