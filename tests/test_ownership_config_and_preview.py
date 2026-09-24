"""``scope:``'s ownership keys in ``.abicheck.yml`` and ``dump --dry-run``'s
ownership preview (Phase 1 of ``docs/contribute/plans/
target-ownership-and-extraction-scope.md``).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.buildsource.build_config import BuildConfig
from abicheck.model.ownership_rules import DependencyRoots, OwnershipRules

_SCOPE = {
    "public_header_dirs": ["include"],
    "dependencies": [
        {"name": "fmt", "header_roots": ["include/third-party/fmt"]},
        {"name": "toml", "header_roots": "vendor/toml"},
    ],
    "private_headers": "include/*/detail/**",
    "private_namespaces": ["lib::detail"],
}


def test_the_keys_parse_into_ownership_rules() -> None:
    cfg = BuildConfig.from_dict({"scope": _SCOPE})
    assert cfg.ownership == OwnershipRules(
        dependencies=(
            DependencyRoots("fmt", ("include/third-party/fmt",)),
            DependencyRoots("toml", ("vendor/toml",)),
        ),
        private_headers=("include/*/detail/**",),
        private_namespaces=("lib::detail",),
    )
    assert cfg.ownership.is_configured()


def test_a_config_round_trips() -> None:
    cfg = BuildConfig.from_dict({"scope": _SCOPE})
    assert BuildConfig.from_dict(cfg.to_dict()) == cfg


def test_no_ownership_key_is_unconfigured() -> None:
    cfg = BuildConfig.from_dict({"scope": {"public_header_dirs": ["include"]}})
    assert not cfg.ownership.is_configured()
    assert "dependencies" not in cfg.to_dict().get("scope", {})


@pytest.mark.parametrize(
    ("dependencies", "message"),
    [
        ("fmt", "must be a list"),
        ([["fmt"]], "must be a mapping"),
        ([{"header_roots": ["x"]}], "name must be a non-empty string"),
        ([{"name": "", "header_roots": ["x"]}], "name must be a non-empty string"),
        ([{"name": "a"}], "header_roots must be a non-empty list"),
        ([{"name": "a", "header_roots": []}], "header_roots must be a non-empty list"),
        ([{"name": "a", "header_roots": [1]}], "only non-empty strings"),
        ([{"name": "a", "header_roots": ["x"], "roots": ["y"]}], "unknown key"),
        (
            [
                {"name": "a", "header_roots": ["x"]},
                {"name": "a", "header_roots": ["y"]},
            ],
            "declared twice",
        ),
    ],
)
def test_malformed_dependencies_are_rejected(
    dependencies: object, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        BuildConfig.from_dict({"scope": {"dependencies": dependencies}})


@pytest.mark.parametrize("key", ["private_headers", "private_namespaces"])
def test_private_keys_take_only_strings(key: str) -> None:
    with pytest.raises(ValueError, match=f"scope.{key}"):
        BuildConfig.from_dict({"scope": {key: [1]}})


# ── the preview, through the real CLI ────────────────────────────────────────


def _project(tmp_path: Path, config: str) -> tuple[Path, Path, Path]:
    (tmp_path / "include" / "lib" / "detail").mkdir(parents=True)
    (tmp_path / "include" / "third-party" / "fmt").mkdir(parents=True)
    public = tmp_path / "include" / "lib" / "api.h"
    private = tmp_path / "include" / "lib" / "detail" / "impl.h"
    vendored = tmp_path / "include" / "third-party" / "fmt" / "format.h"
    for header in (public, private, vendored):
        header.write_text("struct S { int a; };\n", encoding="utf-8")
    (tmp_path / ".abicheck.yml").write_text(config, encoding="utf-8")
    so_path = tmp_path / "lib.so"
    so_path.write_bytes(b"\x7fELF" + b"\x00" * 200)
    return public, private, vendored


_CONFIG = """\
scope:
  public_header_dirs: [include]
  dependencies:
    - name: fmt
      header_roots: [include/third-party/fmt]
  private_headers: [include/*/detail/**]
"""


def _dry_run(tmp_path: Path, *headers: Path) -> str:
    from abicheck.cli import main

    args = ["dump", str(tmp_path / "lib.so"), "--dry-run"]
    args += ["--config", str(tmp_path / ".abicheck.yml")]
    for header in headers:
        args += ["-H", str(header)]
    result = CliRunner().invoke(main, args)
    assert result.exit_code == 0, result.output
    return result.output


def test_the_preview_classifies_each_named_header(tmp_path: Path) -> None:
    public, private, vendored = _project(tmp_path, _CONFIG)
    out = _dry_run(tmp_path, public, private, vendored)
    assert "Ownership:" in out
    assert f"{public}: owner=target contract=public" in out
    assert f"{private}: owner=target contract=private" in out
    assert f"{vendored}: owner=dependency:fmt contract=external" in out
    # A -H header that is not public target API is called out.
    assert f"-H {private} is owner=target contract=private" in out
    assert f"-H {vendored} is owner=dependency:fmt" in out
    assert f"-H {public} is" not in out


def test_the_preview_is_silent_without_an_ownership_key(tmp_path: Path) -> None:
    public, _, _ = _project(tmp_path, "scope:\n  public_header_dirs: [include]\n")
    assert "Ownership:" not in _dry_run(tmp_path, public)


def test_contradictory_roots_block_the_dry_run(tmp_path: Path) -> None:
    from abicheck.cli import main

    public, _, _ = _project(
        tmp_path,
        "scope:\n  public_header_dirs: [include]\n  dependencies:\n"
        "    - name: d\n      header_roots: [include]\n",
    )
    args = ["dump", str(tmp_path / "lib.so"), "--dry-run", "-H", str(public)]
    args += ["--config", str(tmp_path / ".abicheck.yml")]
    result = CliRunner().invoke(main, args)
    assert result.exit_code != 0
    assert "claimed by both" in result.output
