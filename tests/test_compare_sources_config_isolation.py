"""A pairwise ``compare``'s per-side ``--sources`` tree config stays on its own side.

Pinned after ADR-091 Phase 5 removed the Action's sources-root overlay merge
(#1869 left this behaviour unpinned): the CLI's own discovery now decides it.

For ``compare OLD NEW --sources SIDE=tree``:

* the tree's ``.abicheck.yml`` is that side's build config, even when the file
  is empty (an empty file is selected and read as all defaults; discovery does
  not fall through to the checkout-root config for that side);
* the tree's ``compile:`` block folds onto that side's compile context only, under
  the run's pair-wide context (which wins a repeated flag);
* the other side never sees the tree's config: no ``sources``, no build config
  from the tree, and a compile context equal to the run's pair-wide one.

The oracle for "the run's pair-wide context" is the same run with no tree
config at all, observed through the same CLI path.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from abicheck.cli import main

#: Tree ``.abicheck.yml`` contents and the compile tokens/frontend each adds.
TREE_CONFIGS: list[tuple[str, str, tuple[str, ...], str]] = [
    (
        "compile",
        "compile:\n  std: c++17\n  frontend: clang\n",
        ("-std=c++17",),
        "clang",
    ),
    ("options", "compile:\n  options: [-DTREE=1]\n", ("-DTREE=1",), "auto"),
    ("build-only", "build:\n  system: cmake\n", (), "auto"),
    ("empty", "", (), "auto"),
]

#: Checkout-root ``.abicheck.yml`` (pair-wide) or none.
ROOT_CONFIGS: list[tuple[str, str | None]] = [
    ("no-root", None),
    ("root", "compile:\n  std: c++20\nbuild:\n  system: make\n"),
]


class _Stop(Exception):
    pass


def _request(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    side: str | None,
    tree_yaml: str | None,
    root_yaml: str | None,
) -> Any:
    """The ``CompareRequest`` the real ``compare`` CLI hands the shared resolution."""
    import abicheck.workflows.compare.resolve as resolve_mod

    monkeypatch.chdir(tmp_path)
    for name in ("old.so", "new.so"):
        (tmp_path / name).write_bytes(b"\x7fELF" + b"\x00" * 100)
    (tmp_path / "foo.h").write_text("int foo(void);\n", encoding="utf-8")
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "a.c").write_text("int foo(void) { return 0; }\n", encoding="utf-8")
    if tree_yaml is not None:
        (tree / ".abicheck.yml").write_text(tree_yaml, encoding="utf-8")
    if root_yaml is not None:
        (tmp_path / ".abicheck.yml").write_text(root_yaml, encoding="utf-8")

    seen: list[Any] = []

    def _capture(request: Any, *_a: object, **_kw: object) -> Any:
        seen.append(request)
        raise _Stop

    monkeypatch.setattr(resolve_mod, "resolve_compare_request", _capture)
    args = ["compare", "old.so", "new.so", "-H", "foo.h"]
    if side is not None:
        args += ["--sources", f"{side}=tree"]
    with pytest.raises(_Stop):
        CliRunner().invoke(main, args, catch_exceptions=False)
    (request,) = seen
    return request


def _build_config(spec: Any) -> Path | None:
    execution = spec.execution
    path = getattr(execution, "build_config", None) if execution else None
    return None if path is None else Path(path).resolve()


@pytest.mark.parametrize("side", ["old", "new"])
@pytest.mark.parametrize(
    ("tree_id", "tree_yaml", "tree_tokens", "tree_frontend"),
    TREE_CONFIGS,
    ids=[c[0] for c in TREE_CONFIGS],
)
@pytest.mark.parametrize(
    ("root_id", "root_yaml"), ROOT_CONFIGS, ids=[c[0] for c in ROOT_CONFIGS]
)
def test_a_sources_tree_config_stays_on_its_own_side(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    side: str,
    tree_id: str,
    tree_yaml: str,
    tree_tokens: tuple[str, ...],
    tree_frontend: str,
    root_id: str,
    root_yaml: str | None,
) -> None:
    other = "new" if side == "old" else "old"
    baseline_dir = tmp_path / "baseline"
    baseline_dir.mkdir()
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    # The pair-wide context: the same run with no --sources tree at all.
    baseline = _request(
        monkeypatch, baseline_dir, side=None, tree_yaml=None, root_yaml=root_yaml
    )
    pair_wide = baseline.old.compile
    assert baseline.new.compile == pair_wide

    request = _request(
        monkeypatch, run_dir, side=side, tree_yaml=tree_yaml, root_yaml=root_yaml
    )
    tree_cfg = (run_dir / "tree" / ".abicheck.yml").resolve()
    mine, theirs = getattr(request, side), getattr(request, other)

    # The tree's file is this side's config, even when it is empty.
    assert mine.sources is not None
    assert _build_config(mine) == tree_cfg
    # Its compile block folds onto this side's context. The run's pair-wide
    # context is the CLI layer of that fold, so its tokens come last and win a
    # repeated flag (`merge_compile_config`: config first, CLI after).
    assert mine.compile.gcc_option_tokens == tree_tokens + pair_wide.gcc_option_tokens
    assert mine.compile.frontend == tree_frontend

    # The other side never sees the tree's config.
    assert theirs.sources is None
    assert _build_config(theirs) != tree_cfg
    assert theirs.compile == pair_wide
