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
  interface that no sanitizer runtime among its DT_NEEDED libraries
  provides. The reserved ``__asan_``/``__ubsan_``/... prefixes are defined
  only by a sanitizer runtime, so no uninstrumented process provides them,
  and each runtime defines only its own families (``libubsan`` does not
  define ``__asan_*``): provision is judged per interface family, not by the
  presence of *some* sanitizer runtime. UBSan's shared runtime
  (``libubsan``) loads into any process and is not a finding by itself.
  SanitizerCoverage callbacks (``__sanitizer_cov_*``) are not reserved: the
  user (a fuzzing host) supplies them, so they are ordinary imports.
* ``IMPORTED_SYMBOL_UNPROVIDED`` — with complete ``--follow-deps`` evidence
  on both sides: a library whose old build was self-contained (no strong
  import missing from its DT_NEEDED closure) gained a strong import nothing
  in the new closure provides. A library that already relied on its host (a
  plugin) is not judged: the host may provide the new symbol as well. This
  is a separate detector behind an evidence gate, so a run without that
  evidence records the analysis as not evaluated rather than as a zero.
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
#: Any sanitizer runtime (it defines reserved interface symbols); the
#: ``gnu``/``clang`` group names which runtime it is.
_ANY_RUNTIME = re.compile(
    r"^(?:lib(?P<gnu>asan|tsan|hwasan|ubsan|msan|lsan)"
    r"|libclang_rt\.(?P<clang>asan|tsan|hwasan|ubsan|msan|lsan)[-_.][^/]*)"
    r"(?:\.so(?:\.\d+)*)?$"
)
#: Reserved interface prefix -> its family. ``__sanitizer_`` is the common
#: interface every runtime carries.
_FAMILY_PREFIXES: tuple[tuple[str, str], ...] = (
    ("__asan_", "asan"),
    ("__ubsan_", "ubsan"),
    ("__tsan_", "tsan"),
    ("__msan_", "msan"),
    ("__hwasan_", "hwasan"),
    ("__lsan_", "lsan"),
    ("__sanitizer_", "common"),
)
#: The families each runtime defines. Every runtime embeds the common
#: interface; ASan and HWASan embed LeakSanitizer; clang's ASan/TSan/MSan/
#: HWASan runtimes embed the UBSan handlers (GCC's do not -- crediting them
#: errs toward not reporting, never toward fabricating a break).
_RUNTIME_FAMILIES: dict[str, frozenset[str]] = {
    "asan": frozenset({"asan", "lsan", "ubsan", "common"}),
    "hwasan": frozenset({"hwasan", "lsan", "ubsan", "common"}),
    "tsan": frozenset({"tsan", "ubsan", "common"}),
    "msan": frozenset({"msan", "ubsan", "common"}),
    "ubsan": frozenset({"ubsan", "common"}),
    "lsan": frozenset({"lsan", "common"}),
}
#: SanitizerCoverage callbacks: implemented by the user (e.g. a fuzzer),
#: not reserved to a sanitizer runtime.
_USER_SUPPLIED_PREFIX = "__sanitizer_cov_"


def preload_runtime(soname: str) -> bool:
    """Whether *soname* is a sanitizer runtime that must load first."""
    return bool(_PRELOAD_RUNTIME.match(soname))


def sanitizer_runtime_name(soname: str) -> bool:
    """Whether *soname* is any sanitizer runtime library."""
    return bool(_ANY_RUNTIME.match(soname))


def _runtime_family(soname: str) -> str | None:
    m = _ANY_RUNTIME.match(soname)
    return (m.group("gnu") or m.group("clang")) if m else None


def _reserved_family(symbol: str) -> str | None:
    """The sanitizer family whose runtime alone defines *symbol*, or ``None``
    for an ordinary symbol (including a user-supplied coverage callback)."""
    if symbol.startswith(_USER_SUPPLIED_PREFIX):
        return None
    for prefix, family in _FAMILY_PREFIXES:
        if symbol.startswith(prefix):
            return family
    return None


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
    provided: set[str] = set()
    for lib in new_needed:
        runtime = _runtime_family(lib)
        if runtime is not None:
            provided |= _RUNTIME_FAMILIES[runtime]
    old_imports = _strong_imports(old_elf)
    gained = sorted(
        name
        for name in _strong_imports(new_elf)
        if name not in old_imports
        and (family := _reserved_family(name)) is not None
        and family not in provided
    )
    if gained:
        shown = ", ".join(gained[:3]) + (", ..." if len(gained) > 3 else "")
        changes.append(
            make_change(
                ChangeKind.SANITIZER_RUNTIME_REQUIRED,
                symbol="<sanitizer-runtime>",
                detail=(
                    f"{len(gained)} sanitizer-runtime import(s) ({shown}) and "
                    "no sanitizer runtime among DT_NEEDED that defines them"
                ),
                new_value=gained[0],
            )
        )
    return changes


def _root_missing(snapshot: AbiSnapshot) -> set[str] | None:
    """Strong symbols the snapshot's own binary imports that no dependency
    provides, or ``None`` without complete ``--follow-deps`` evidence.

    A dependency the resolver could not open contributes no exports, so its
    symbols read as missing: an incomplete closure proves nothing."""
    info = snapshot.dependency_info
    if info is None or not info.nodes or info.unresolved:
        return None
    root = str(info.nodes[0].get("soname") or "")
    root_path = str(info.nodes[0].get("path") or "")
    return {
        str(m.get("symbol"))
        for m in info.missing_symbols
        if str(m.get("consumer") or "") in {root, root_path}
    }


def _closure_support(old: AbiSnapshot, new: AbiSnapshot) -> tuple[bool, str]:
    """Evidence gate for ``IMPORTED_SYMBOL_UNPROVIDED``: complete dependency
    closures (``--follow-deps``) on both sides."""
    if old.elf is None or new.elf is None:
        return False, "missing ELF metadata"
    for side, snap in (("old", old), ("new", new)):
        info = snap.dependency_info
        if info is None or not info.nodes:
            return False, f"no --follow-deps dependency evidence on the {side} side"
        if info.unresolved:
            return False, f"incomplete dependency closure on the {side} side"
    return True, ""


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
        if _reserved_family(name) is not None:
            continue  # judged by the sanitizer-runtime analysis
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
    """Report a sanitizer runtime no existing consumer process provides."""
    if old.elf is None or new.elf is None:
        return []
    if not both_captured_elf_identity(old.elf, new.elf):
        return []
    return _sanitizer_changes(old.elf, new.elf)


@registry.detector("imported_symbols_unprovided", requires_support=_closure_support)
def _diff_imported_symbols_unprovided(
    old: AbiSnapshot, new: AbiSnapshot
) -> list[Change]:
    """Report a strong import nothing in the new dependency closure provides."""
    if old.elf is None or new.elf is None:
        return []
    if not both_captured_elf_identity(old.elf, new.elf):
        return []
    return _unprovided_changes(old, new)
