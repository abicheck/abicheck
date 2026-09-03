# Copyright 2026 Nikolay Petrov
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

"""``abicheck.cli``'s historical import surface stays resolvable.

ADR-061 Phase 4 reduced ``cli.py`` to a registration facade by moving every
command body and shared helper to an owner. ``abicheck.cli`` is still the
documented import path for a long list of those names -- sibling ``cli_*``
modules and much of the test suite reach for them there -- so a lazy
``__getattr__`` keeps each one working.

A lazy shim is exactly the kind of compatibility layer that rots silently: a
stale entry raises only when someone imports that one name, which may be a
platform-specific path or a rarely-exercised branch. These tests resolve
*every* entry, so a rename that orphans one fails here immediately rather than
in whatever consumer happens to hit it first.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from abicheck import cli
from abicheck.frontends.cli.moved import MOVED


@pytest.mark.parametrize("name", sorted(MOVED))
def test_every_moved_name_resolves(name: str) -> None:
    """Each mapped name really exists on the owner the map names."""
    assert getattr(cli, name) is not None or True  # resolution is the assertion
    getattr(cli, name)


def test_unknown_name_still_raises_attribute_error() -> None:
    """The shim must not turn a typo into a silent ``None``."""
    with pytest.raises(AttributeError, match="no attribute"):
        cli.definitely_not_a_real_name


def test_every_name_the_repo_imports_from_cli_is_covered() -> None:
    """The map covers what the tree actually imports from ``abicheck.cli``.

    This is the half a per-entry resolution test cannot give: it catches a
    name that a caller imports but the map never listed, which today works
    only if it happens to still be defined in ``cli.py`` itself.
    """
    root = pathlib.Path(__file__).resolve().parent.parent
    defined = set(MOVED) | set(getattr(cli, "__all__", [])) | {"main"}
    missing: dict[str, set[str]] = {}
    for sub in ("abicheck", "tests", "scripts"):
        for path in (root / sub).rglob("*.py"):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.ImportFrom):
                    continue
                target = node.module or ""
                is_cli = target == "abicheck.cli" or (
                    node.level == 1 and target == "cli"
                )
                if not is_cli:
                    continue
                for alias in node.names:
                    if alias.name in defined or hasattr(cli, alias.name):
                        continue
                    missing.setdefault(alias.name, set()).add(
                        str(path.relative_to(root))
                    )
    assert not missing, f"imported from abicheck.cli but unresolvable: {missing}"


def test_facade_declares_all_and_stays_small() -> None:
    """ADR-061 Phase 4's acceptance for this facade, as an executable check.

    Deliberately pinned here rather than by adding ``abicheck.cli`` to
    ``architecture/modules.yaml``'s ``facades`` list. That gate's ``facade``
    rule means something narrower than the ADR's prose does: it permits only
    imports, inert assignments and a ``TYPE_CHECKING`` block, so *any* module
    defining a Click root group fails it -- ``main`` is a ``FunctionDef``, and
    ``configure_rich_help()`` is an executable expression. Both are
    registration, not product logic, which is what the acceptance criterion
    actually asks about. Widening that gate to admit this file would weaken it
    for the pure re-export modules it was written for, so the budget is
    enforced here instead.
    """
    source = pathlib.Path(cli.__file__).read_text(encoding="utf-8")
    assert cli.__all__ == ["main"]
    assert len(source.splitlines()) < 150


class TestFacadeRejectsMovedNameAssignment:
    """The lazy facade must fail loudly when a moved name is *assigned*.

    ``abicheck.cli.__getattr__`` only fires while the name is absent from the
    module's globals, so any assignment -- a ``monkeypatch.setattr`` against
    the facade included, since undo re-assigns the value it read -- freezes a
    stale reference for the rest of the process and silently defeats every
    later patch of the true owner. This shipped once as an order-dependent CI
    failure in a test file two removes from the one that caused it.
    """

    def test_assigning_a_moved_name_raises_and_names_the_owner(self) -> None:
        import abicheck.cli as cli_mod

        with pytest.raises(AttributeError) as excinfo:
            cli_mod._dispatch_release_compare = lambda *a, **k: None
        message = str(excinfo.value)
        assert "abicheck.frontends.cli.commands.compare" in message
        assert "Patch " in message

    def test_a_name_the_facade_owns_is_still_assignable(self) -> None:
        """The guard is scoped to moved names -- it is not a frozen module."""
        import abicheck.cli as cli_mod

        try:
            cli_mod._probe_not_a_moved_name = 1
            assert cli_mod._probe_not_a_moved_name == 1
        finally:
            del cli_mod._probe_not_a_moved_name

    def test_no_test_module_patches_a_moved_name_on_the_facade(self) -> None:
        """The static half: nothing in the suite targets the facade by name."""
        import re

        offenders: list[str] = []
        for path in sorted(pathlib.Path(__file__).parent.glob("test_*.py")):
            text = path.read_text(encoding="utf-8")
            aliases = set(re.findall(r"import abicheck\.cli as (\w+)", text))
            aliases |= set(re.findall(r"from abicheck import cli as (\w+)", text))
            if "from abicheck import cli\n" in text:
                aliases.add("cli")
            for alias in aliases:
                for name in re.findall(
                    rf"setattr\(\s*{alias}\s*,\s*[\"'](\w+)[\"']", text
                ):
                    if name in MOVED:
                        offenders.append(f"{path.name}: {alias}.{name}")
                for name in re.findall(
                    rf"patch\.object\(\s*{alias}\s*,\s*[\"'](\w+)[\"']", text
                ):
                    if name in MOVED:
                        offenders.append(f"{path.name}: {alias}.{name}")
            # The string-target form, which no alias scan can see.
            for name in re.findall(r"[\"']abicheck\.cli\.(\w+)[\"']", text):
                if name in MOVED:
                    offenders.append(f"{path.name}: abicheck.cli.{name}")
        assert not offenders, (
            "patch the owner named in MOVED, not the abicheck.cli facade: "
            + ", ".join(offenders)
        )
