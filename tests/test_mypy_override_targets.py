"""Tests for the ``mypy-override-targets`` gate (scripts/mypy_override_targets.py).

Two layers, per AGENTS.md's "a bug fix's regression test targets the bug
*class*, not the one reported input":

1. :class:`TestResolutionRules` states the gate's contract as invariants over
   *synthetic* trees — a stale exact target, a stale wildcard, a live target
   under every spelling a ``[[tool.mypy.overrides]]`` block may use, and a
   third-party target that must never be judged from the tree. These are what
   generalize past the eleven entries that prompted the gate: the class is
   "an override naming a module that does not resolve", not those names.
2. :func:`test_live_tree_has_no_stale_override_target` keeps the real
   ``pyproject.toml`` clean, so a contributor deleting a module learns locally
   that its override outlived it.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from _canonical_lane import is_canonical_lane

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "mypy_override_targets.py"

pytestmark = pytest.mark.skipif(
    not is_canonical_lane(), reason="canonical Linux lane only — see tests/CLAUDE.md"
)


def _load():
    spec = importlib.util.spec_from_file_location("mypy_override_targets", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["mypy_override_targets"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def mot():
    return _load()


class _Findings:
    """Minimal stand-in for check_ai_readiness.Findings."""

    def __init__(self) -> None:
        self.errors: list[tuple[str, str]] = []
        self.warnings: list[tuple[str, str]] = []

    def err(self, check: str, msg: str) -> None:
        self.errors.append((check, msg))

    def warn(self, check: str, msg: str) -> None:
        self.warnings.append((check, msg))


def _tree(root: Path, modules: tuple[str, ...]) -> Path:
    """Materialize a synthetic ``pkg/`` tree holding *modules* (dotted, relative
    to the package root; a trailing ``.__init__`` makes a package)."""
    pkg = root / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    for dotted in modules:
        parts = dotted.split(".")
        parent = pkg.joinpath(*parts[:-1])
        parent.mkdir(parents=True, exist_ok=True)
        for depth in range(1, len(parts)):
            init = pkg.joinpath(*parts[:depth], "__init__.py")
            init.parent.mkdir(parents=True, exist_ok=True)
            init.touch()
        (parent / f"{parts[-1]}.py").write_text("")
    return pkg


def _pyproject(root: Path, targets: object) -> Path:
    """Write a pyproject holding one overrides block whose ``module`` is
    *targets* — a bare string or a list, both of which TOML and this repo's own
    file use."""
    if isinstance(targets, str):
        rendered = f'"{targets}"'
    else:
        rendered = "[" + ", ".join(f'"{t}"' for t in targets) + "]"
    path = root / "pyproject.toml"
    path.write_text(
        "[project]\nname = 'x'\n\n"
        "[[tool.mypy.overrides]]\n"
        f"module = {rendered}\n"
        'disable_error_code = ["misc"]\n'
    )
    return path


class TestResolutionRules:
    def test_exact_target_naming_a_live_module_resolves(self, mot, tmp_path):
        pkg = _tree(tmp_path, ("live",))
        pp = _pyproject(tmp_path, ["pkg.live"])
        assert mot.stale_override_targets(pp, pkg, "pkg") == []

    def test_exact_target_naming_a_deleted_module_is_stale(self, mot, tmp_path):
        pkg = _tree(tmp_path, ("live",))
        pp = _pyproject(tmp_path, ["pkg.gone"])
        stale = mot.stale_override_targets(pp, pkg, "pkg")
        assert [t for t, _ in stale] == ["pkg.gone"]

    @pytest.mark.parametrize(
        "target",
        ["pkg", "pkg.sub", "pkg.sub.leaf"],
        ids=["top-level", "intermediate-package", "nested-module"],
    )
    def test_every_live_depth_resolves(self, mot, tmp_path, target):
        """A target may legally name the package root, an intermediate package,
        or a module — the eleven real stale entries spanned two of these three
        depths (``abicheck.cli_scan`` and ``abicheck.workflows.scan_config``),
        so the gate must resolve all of them rather than only flat names."""
        pkg = _tree(tmp_path, ("sub.leaf",))
        pp = _pyproject(tmp_path, [target])
        assert mot.stale_override_targets(pp, pkg, "pkg") == []

    def test_wildcard_matching_nothing_is_stale(self, mot, tmp_path):
        pkg = _tree(tmp_path, ("sub.leaf",))
        pp = _pyproject(tmp_path, ["pkg.absent.*"])
        assert [t for t, _ in mot.stale_override_targets(pp, pkg, "pkg")] == [
            "pkg.absent.*"
        ]

    def test_wildcard_matching_a_live_module_resolves(self, mot, tmp_path):
        pkg = _tree(tmp_path, ("sub.leaf",))
        pp = _pyproject(tmp_path, ["pkg.sub.*"])
        assert mot.stale_override_targets(pp, pkg, "pkg") == []

    def test_wildcard_and_exact_are_reported_with_different_reasons(
        self, mot, tmp_path
    ):
        """The two failure modes are distinguishable in the message, so a
        reader is told which kind of staleness they have."""
        pkg = _tree(tmp_path, ("live",))
        pp = _pyproject(tmp_path, ["pkg.gone", "pkg.absent.*"])
        reasons = dict(mot.stale_override_targets(pp, pkg, "pkg"))
        assert "wildcard" in reasons["pkg.absent.*"]
        assert "wildcard" not in reasons["pkg.gone"]

    @pytest.mark.parametrize(
        "target", ["yaml", "elftools.elf.elffile", "click", "pytest.*"]
    )
    def test_third_party_targets_are_never_judged_from_the_tree(
        self, mot, tmp_path, target
    ):
        """An override for a dependency proves nothing about the first-party
        tree — whether it "resolves" depends on the environment mypy runs in."""
        pkg = _tree(tmp_path, ("live",))
        pp = _pyproject(tmp_path, [target])
        assert mot.stale_override_targets(pp, pkg, "pkg") == []

    def test_bare_string_module_spelling_is_checked_too(self, mot, tmp_path):
        """``module = "pkg.gone"`` (not a list) is legal TOML and appears in
        this repo's own pyproject; it must not slip past the flattener."""
        pkg = _tree(tmp_path, ("live",))
        pp = _pyproject(tmp_path, "pkg.gone")
        assert [t for t, _ in mot.stale_override_targets(pp, pkg, "pkg")] == [
            "pkg.gone"
        ]

    def test_a_tree_with_no_overrides_at_all_is_clean(self, mot, tmp_path):
        pkg = _tree(tmp_path, ("live",))
        pp = tmp_path / "pyproject.toml"
        pp.write_text("[project]\nname = 'x'\n")
        assert mot.stale_override_targets(pp, pkg, "pkg") == []

    def test_detector_is_not_vacuous(self, mot, tmp_path):
        """Vacuity guard: an implementation that returned ``[]`` for every
        input would pass most assertions above by construction, so pin that
        the gate reports *something* for a clearly-stale target and that the
        live/stale answers actually differ for the same tree."""
        pkg = _tree(tmp_path, ("live",))
        stale = mot.stale_override_targets(
            _pyproject(tmp_path / "a", ["pkg.gone"]), pkg, "pkg"
        )
        live = mot.stale_override_targets(
            _pyproject(tmp_path / "b", ["pkg.live"]), pkg, "pkg"
        )
        assert stale and not live

    @pytest.fixture(autouse=True)
    def _subdirs(self, tmp_path):
        (tmp_path / "a").mkdir(exist_ok=True)
        (tmp_path / "b").mkdir(exist_ok=True)


class TestCheckFunction:
    def test_reports_one_error_per_stale_target(self, mot, tmp_path, monkeypatch):
        pkg = _tree(tmp_path, ("live",))
        _pyproject(tmp_path, ["pkg.gone", "pkg.also_gone"])
        monkeypatch.setattr(mot, "ROOT", tmp_path)
        monkeypatch.setattr(mot, "FIRST_PARTY_TOP_LEVEL", "pkg")
        assert pkg.exists()
        f = _Findings()
        mot.check_mypy_override_targets(f)
        assert len(f.errors) == 2
        assert all(check == "mypy-override-targets" for check, _ in f.errors)

    def test_missing_pyproject_is_an_error_not_a_crash(
        self, mot, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(mot, "ROOT", tmp_path)
        f = _Findings()
        mot.check_mypy_override_targets(f)
        assert len(f.errors) == 1


def test_live_tree_has_no_stale_override_target(mot):
    """The real pyproject.toml resolves — a contributor who deletes a module
    learns here that its override (and its comment) outlived it."""
    stale = mot.stale_override_targets(ROOT / "pyproject.toml", ROOT / "abicheck")
    assert stale == [], "stale [[tool.mypy.overrides]] targets: " + ", ".join(
        f"{t} ({r})" for t, r in stale
    )
