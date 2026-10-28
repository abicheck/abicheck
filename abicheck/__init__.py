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

"""abicheck — ABI compatibility checker."""

from importlib.metadata import PackageNotFoundError, version as _pkg_version
from pathlib import Path

try:
    __version__: str = _pkg_version("abicheck")
except PackageNotFoundError:
    __version__ = "0.0.0.dev0"  # running from source without install


def build_version() -> str:
    """``__version__``, plus ``+g<sha>`` when running from an abicheck checkout.

    ``pyproject.toml`` carries a hand-bumped release number, so every commit
    after a release reports that release (e.g. ``0.7.0`` on main, months
    later). A source checkout appends its commit so a field report names the
    code it ran. Only the checkout this package was imported from counts --
    its parent holds both ``.git`` and abicheck's ``pyproject.toml`` -- so a
    wheel installed inside some other repository never borrows that
    repository's commit. Read from ``.git`` directly (a few small file reads,
    no ``git`` subprocess), so it is cheap enough to call uncached.
    """
    sha = _checkout_commit(Path(__file__).resolve().parent.parent)
    return f"{__version__}+g{sha[:9]}" if sha else __version__


def _checkout_commit(root: Path) -> str | None:
    """The commit *root* (an abicheck source checkout) has checked out."""
    try:
        if 'name = "abicheck"' not in (root / "pyproject.toml").read_text("utf-8"):
            return None
        git = root / ".git"
        if git.is_file():  # a worktree: "gitdir: <path>"
            git = (root / git.read_text("utf-8").split(":", 1)[1].strip()).resolve()
        head = (git / "HEAD").read_text("utf-8").strip()
        if not head.startswith("ref: "):
            return head or None  # detached HEAD
        ref = head[5:]
        common = git
        if (git / "commondir").is_file():  # worktree refs live in the main repo
            common = (git / (git / "commondir").read_text("utf-8").strip()).resolve()
        for base in (git, common):
            if (base / ref).is_file():
                return (base / ref).read_text("utf-8").strip() or None
        for line in (common / "packed-refs").read_text("utf-8").splitlines():
            if line.endswith(" " + ref):
                return line.split(" ", 1)[0]
    except (OSError, IndexError):
        return None
    return None


# Initialize the ``model`` package before any other abicheck module can run.
# ``model`` (its ``__init__`` and several submodules) imports the root-level,
# model-layer ``name_classification`` at module scope, while
# ``name_classification`` -- like every cached helper -- imports
# ``model.execution_cache``, which executes ``model/__init__`` first. Entered
# from ``model`` that is harmless; entered from ``name_classification`` (or any
# module that reaches it before ``model``) the package init found a partially
# initialized ``name_classification`` and raised ``ImportError``. Loading
# ``model`` here makes the import order -- and so whether import succeeds --
# independent of which abicheck module a process imports first
# (tests/test_first_import_order.py).
from . import model as _model  # noqa: E402,F401
