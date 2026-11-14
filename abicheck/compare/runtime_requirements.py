# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
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

"""Load-time requirements a new ELF build places on every consumer process.

Two findings, each read from evidence that proves the load fails:

* ``SANITIZER_RUNTIME_REQUIRED`` — the library gained a DT_NEEDED on a
  sanitizer runtime that must be the first library in the process (ASan,
  TSan, HWASan), or gained strong imports of a sanitizer runtime's own
  interface with no sanitizer runtime among its DT_NEEDED libraries. The
  reserved ``__asan_``/``__ubsan_``/... prefixes are defined only by a
  sanitizer runtime, so no uninstrumented process provides them. UBSan's
  shared runtime (``libubsan``) loads into any process and is not a finding.
* ``IMPORTED_SYMBOL_UNPROVIDED`` — with ``--follow-deps`` evidence on both
  sides: a library whose old build was self-contained (no strong import
  missing from its DT_NEEDED closure) gained a strong import nothing in the
  new closure provides. A library that already relied on its host (a plugin)
  is not judged: the host may provide the new symbol as well.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from ..detector_registry import registry
from ..diff_helpers import make_change
from ..model.change_catalog.kinds import ChangeKind
from ..model.elf_facts import SymbolBinding
from .elf_capture_identity import both_captured_elf_identity

if TYPE_CHECKING:
    from ..model import AbiSnapshot
    from ..model.change import Change

__all__ = ["preload_runtime", "sanitizer_runtime_name"]

#: Sanitizer runtimes that must be the first library in the process: loading
#: one later (dlopen, or as a dependency of a library an uninstrumented
#: executable links) aborts with "runtime does not come first".
_PRELOAD_RUNTIME = re.compile(
    r"^(?:lib(?:asan|tsan|hwasan)|libclang_rt\.(?:asan|tsan|hwasan)[-_.][^/]*)"
    r"(?:\.so(?:\.\d+)*)?$"
)
#: Any sanitizer runtime (it defines the reserved interface symbols).
_ANY_RUNTIME = re.compile(
    r"^(?:lib(?:asan|tsan|hwasan|ubsan|msan|lsan)"
    r"|libclang_rt\.(?:asan|tsan|hwasan|ubsan|msan|lsan)[-_.][^/]*)"
    r"(?:\.so(?:\.\d+)*)?$"
)
#: The sanitizer runtimes' reserved interface prefixes.
_RUNTIME_SYMBOL_PREFIXES: tuple[str, ...] = (
    "__asan_",
    "__ubsan_",
    "__tsan_",
    "__msan_",
    "__hwasan_",
    "__lsan_",
    "__sanitizer_",
)


def preload_runtime(soname: str) -> bool:
    """Whether *soname* is a sanitizer runtime that must load first."""
    return bool(_PRELOAD_RUNTIME.match(soname))


def sanitizer_runtime_name(soname: str) -> bool:
    """Whether *soname* is any sanitizer runtime library."""
    return bool(_ANY_RUNTIME.match(soname))


def _strong_imports(elf: Any) -> dict[str, Any]:
    return {
        i.name: i
        for i in (getattr(elf, "imports", None) or [])
        if i.binding != SymbolBinding.WEAK
    }


def _sanitizer_changes(old_elf: Any, new_elf: Any) -> list[Change]:
    changes: list[Change] = []
    old_needed = set(old_elf.needed or ())
    new_needed = list(new_elf.needed or ())
    for lib in new_needed:
        if lib not in old_needed and preload_runtime(lib):
            changes.append(
                make_change(
                    ChangeKind.SANITIZER_RUNTIME_REQUIRED,
                    symbol="DT_NEEDED",
                    detail=f"{lib} must be the first library loaded",
                    new_value=lib,
                )
            )
    if any(sanitizer_runtime_name(lib) for lib in new_needed):
        return changes
    old_imports = _strong_imports(old_elf)
    gained = sorted(
        name
        for name in _strong_imports(new_elf)
        if name.startswith(_RUNTIME_SYMBOL_PREFIXES) and name not in old_imports
    )
    if gained:
        shown = ", ".join(gained[:3]) + (", ..." if len(gained) > 3 else "")
        changes.append(
            make_change(
                ChangeKind.SANITIZER_RUNTIME_REQUIRED,
                symbol="<sanitizer-runtime>",
                detail=(
                    f"{len(gained)} sanitizer-runtime import(s) ({shown}) and "
                    "no sanitizer runtime among DT_NEEDED"
                ),
                new_value=gained[0],
            )
        )
    return changes


def _root_missing(snapshot: AbiSnapshot) -> set[str] | None:
    """Strong symbols the snapshot's own binary imports that no dependency
    provides, or ``None`` without ``--follow-deps`` evidence."""
    info = snapshot.dependency_info
    if info is None or not info.nodes:
        return None
    root = str(info.nodes[0].get("soname") or "")
    root_path = str(info.nodes[0].get("path") or "")
    return {
        str(m.get("symbol"))
        for m in info.missing_symbols
        if str(m.get("consumer") or "") in {root, root_path}
    }


def _unprovided_changes(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    old_missing = _root_missing(old)
    new_missing = _root_missing(new)
    if old_missing is None or new_missing is None or old_missing:
        return []
    assert new.elf is not None and old.elf is not None
    old_imports = _strong_imports(old.elf)
    new_imports = _strong_imports(new.elf)
    changes: list[Change] = []
    for name in sorted(new_missing):
        imp = new_imports.get(name)
        if imp is None or name in old_imports:
            continue
        if name.startswith(_RUNTIME_SYMBOL_PREFIXES):
            continue  # reported as SANITIZER_RUNTIME_REQUIRED
        version = getattr(imp, "version", "")
        detail = f"@{version}" if version else ""
        changes.append(
            make_change(
                ChangeKind.IMPORTED_SYMBOL_UNPROVIDED,
                symbol=name,
                name=name,
                detail=detail,
                new_value=f"{name}{detail}",
            )
        )
    return changes


@registry.detector("runtime_requirements")
def _diff_runtime_requirements(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Report a load-time requirement no existing consumer process meets."""
    if old.elf is None or new.elf is None:
        return []
    if not both_captured_elf_identity(old.elf, new.elf):
        return []
    return [*_sanitizer_changes(old.elf, new.elf), *_unprovided_changes(old, new)]
