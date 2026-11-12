"""The native GCC header backend decides a constraint change's direction as clang does.

Bug class: a header backend records a fact in a form the shared detector
cannot classify. The GCC projection stored every template and concept
constraint as one opaque digest (``gcc-v2:<hash>``), so
``model.concept_constraint.classify_constraint_change`` could never see a
requirement added to an unchanged set and read every tightening as
``concept_constraint_changed`` (edit corpus ``concept_constraint_added`` on
``cxx_classes`` with C++20, gcc header backend). An unconstrained template
also differed from the clang backend's empty set.

Oracle: the kind the clang header backend (which records conjunctive atom
sets, ``extract/headers/clang/concept_constraints.py``) reports for the
same pair of headers; castxml does not model constraints.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from abicheck.checker import compare
from abicheck.dumper import dump
from abicheck.extract.headers.gcc.contracts import conjuncts
from abicheck.model.snapshot import AbiSnapshot
from tests.test_native_gcc_workflow import native_plugins  # noqa: F401 (fixture)

pytestmark = pytest.mark.integration

_PRELUDE = (
    "template <typename T> concept Small = sizeof(T) <= 8;\n"
    "template <typename T> concept Signed = T(-1) < T(0);\n"
)
_BODY = "T clampv(T v) { return v; }"
# (old template head, new template head, the change's direction)
_PAIRS = {
    "requires_added": (
        "template <typename T>",
        "template <typename T> requires (sizeof(T) >= 4)",
    ),
    "second_conjunct_added": (
        "template <typename T> requires Small<T>",
        "template <typename T> requires Small<T> && Signed<T>",
    ),
    "type_constraint_then_clause": (
        "template <Small T>",
        "template <Small T> requires (sizeof(T) >= 2)",
    ),
    "conjunct_removed": (
        "template <typename T> requires Small<T> && Signed<T>",
        "template <typename T> requires Small<T>",
    ),
    "conjunct_replaced": (
        "template <typename T> requires Small<T>",
        "template <typename T> requires Signed<T>",
    ),
    "conjuncts_reordered": (
        "template <typename T> requires Small<T> && Signed<T>",
        "template <typename T> requires Signed<T> && Small<T>",
    ),
}
_CONCEPT_KINDS = {"concept_tightened", "concept_constraint_changed"}


def _require() -> None:
    if not sys.platform.startswith("linux"):
        pytest.skip("ELF shared library")
    for tool in ("g++", "clang++"):
        if shutil.which(tool) is None:
            pytest.skip(f"{tool} not available")


@pytest.fixture
def gcc_env(native_plugins: tuple[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    monkeypatch.setenv("ABICHECK_GCC_PLUGIN", str(native_plugins[1]))


def _dump(root: Path, name: str, head: str, backend: str) -> AbiSnapshot:
    d = root / f"{backend}-{name}"
    d.mkdir()
    (d / "a.hpp").write_text(f"{_PRELUDE}{head} {_BODY}\nint use(int x);\n")
    (d / "a.cpp").write_text('#include "a.hpp"\nint use(int x) { return x; }\n')
    so = d / "liba.so"
    subprocess.run(
        ["g++", "-std=c++20", "-shared", "-fPIC", "-o", str(so), str(d / "a.cpp")],
        check=True,
    )
    return dump(
        so,
        [d / "a.hpp"],
        version=name,
        compiler="c++",
        lang="c++",
        header_backend=backend,
        gcc_options="-std=c++20",
    )


def _kinds(root: Path, case: str, backend: str) -> set[str]:
    old, new = _PAIRS[case]
    snaps = [
        _dump(root, f"{case}-{side}", h, backend)
        for side, h in (("old", old), ("new", new))
    ]
    return {c.kind.value for c in compare(*snaps).changes} & _CONCEPT_KINDS


@pytest.mark.parametrize("case", sorted(_PAIRS))
def test_gcc_constraint_direction_matches_clang(
    case: str, tmp_path: Path, gcc_env: None
) -> None:
    _require()
    assert _kinds(tmp_path, case, "gcc") == _kinds(tmp_path, case, "clang")


def test_unconstrained_template_is_the_empty_set_on_gcc(
    tmp_path: Path, gcc_env: None
) -> None:
    _require()
    snap = _dump(tmp_path, "plain", "template <typename T>", "gcc")
    values = [
        v for k, v in (snap.cxx_concept_constraints or {}).items() if "clampv" in k
    ]
    assert values == ["conj:"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (None, []),
        ("", []),
        ("(sizeof (T) >= 4)", ["sizeof (T) >= 4"]),
        ("Small<T> && Signed<T>", ["Small<T>", "Signed<T>"]),
        ("(Small<T>) && ((a && b))", ["Small<T>", "a && b"]),
        ("A<T> && (B<T> || C<T>)", ["A<T>", "B<T> || C<T>"]),
        ("(x) || (y)", ["(x) || (y)"]),
    ],
)
def test_conjuncts_split_only_at_top_level_and(
    text: str | None, expected: list[str]
) -> None:
    assert conjuncts(text) == expected
