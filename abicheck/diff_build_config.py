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

"""Build-configuration / matrix-aware diff detectors.

These detectors operate on :class:`MatrixSnapshot` objects produced by
:mod:`abicheck.probe_harness` — i.e. a collection of per-configuration
``AbiSnapshot``s for the same version of the library.

Reported kinds:

* ``API_DEPENDS_ON_CONSUMER_ENV`` — a public declaration is present in
  one configuration and absent in another, *within* a single version.
  This is a property of the library itself; it tells reviewers that
  the public surface depends on the consumer's compiler / language
  standard / macro set.

* ``CXX_STANDARD_FLOOR_RAISED`` — the minimum C++ standard floor (the
  smallest ``cxx_std`` across all configurations) increased between
  old and new MatrixSnapshots.

* ``BEHAVIOURAL_DEFAULT_CHANGED`` — a value in the manifest's
  ``defaults:`` section changed between old and new.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .checker_policy import ChangeKind
from .checker_types import Change
from .diff_helpers import make_change

if TYPE_CHECKING:
    from .probe_harness import MatrixSnapshot


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _public_function_names(snap) -> set[str]:  # type: ignore[no-untyped-def]
    from .model import Visibility
    out: set[str] = set()
    for f in snap.functions:
        if f.visibility != Visibility.PUBLIC:
            continue
        out.add(f.name or f.mangled)
    return out


def _public_type_names(snap) -> set[str]:  # type: ignore[no-untyped-def]
    return {t.name for t in snap.types if t.name}


# ---------------------------------------------------------------------------
# API_DEPENDS_ON_CONSUMER_ENV — intra-matrix (single version)
# ---------------------------------------------------------------------------


def _aggregate_decls_by_cfg(
    matrix: MatrixSnapshot,
) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """Return (functions_by_cfg, types_by_cfg) aggregated across probes."""
    cfg_funcs: dict[str, set[str]] = {}
    cfg_types: dict[str, set[str]] = {}
    for cfg_id, results in matrix.by_configuration().items():
        fset: set[str] = set()
        tset: set[str] = set()
        for r in results:
            if r.snapshot is None:
                continue
            fset |= _public_function_names(r.snapshot)
            tset |= _public_type_names(r.snapshot)
        cfg_funcs[cfg_id] = fset
        cfg_types[cfg_id] = tset
    return cfg_funcs, cfg_types


def _env_dependence_change(
    name: str,
    presence: dict[str, bool],
    kind_label: str,
) -> Change | None:
    """Return an API_DEPENDS_ON_CONSUMER_ENV finding for *name*, or None
    if *name* is present in every configuration (or absent in every one)."""
    present = sorted(c for c, p in presence.items() if p)
    absent = sorted(c for c, p in presence.items() if not p)
    if not present or not absent:
        return None
    return make_change(
        ChangeKind.API_DEPENDS_ON_CONSUMER_ENV,
        symbol=name,
        name=name,
        detail=kind_label,
        old=f"{present}",
        new=f"{absent}",
        old_value=",".join(present),
        new_value=",".join(absent),
    )


def detect_api_depends_on_consumer_env(
    matrix: MatrixSnapshot,
) -> list[Change]:
    """Within one MatrixSnapshot, find public declarations present in
    some configurations and absent in others.

    Returns one finding per (name, presence_pattern) so a reviewer sees
    each truly-divergent declaration once. Names present in every
    configuration (the common case) are silently ignored.
    """
    if len(matrix.by_configuration()) < 2:
        return []
    cfg_funcs, cfg_types = _aggregate_decls_by_cfg(matrix)
    all_cfgs = sorted(cfg_funcs)
    if len(all_cfgs) < 2:
        return []

    changes: list[Change] = []
    for name in sorted(set().union(*cfg_funcs.values())):
        presence = {c: (name in cfg_funcs[c]) for c in all_cfgs}
        change = _env_dependence_change(name, presence, "Function")
        if change is not None:
            changes.append(change)
    for name in sorted(set().union(*cfg_types.values())):
        presence = {c: (name in cfg_types[c]) for c in all_cfgs}
        change = _env_dependence_change(name, presence, "Type")
        if change is not None:
            changes.append(change)
    return changes


# ---------------------------------------------------------------------------
# CXX_STANDARD_FLOOR_RAISED — inter-matrix (old vs new)
# ---------------------------------------------------------------------------


def detect_cxx_standard_floor_raised(
    old: MatrixSnapshot,
    new: MatrixSnapshot,
) -> list[Change]:
    """If the minimum C++ standard floor across configurations rose,
    emit one finding."""

    def _floor(m: MatrixSnapshot) -> int | None:
        stds = [v for v in m.cxx_stds.values() if v is not None]
        return min(stds) if stds else None

    old_floor = _floor(old)
    new_floor = _floor(new)
    if old_floor is None or new_floor is None:
        return []
    if new_floor <= old_floor:
        return []
    return [make_change(
        ChangeKind.CXX_STANDARD_FLOOR_RAISED,
        symbol="__cplusplus",
        old=f"C++{old_floor}",
        new=f"C++{new_floor}",
    )]


# ---------------------------------------------------------------------------
# BEHAVIOURAL_DEFAULT_CHANGED — inter-matrix
# ---------------------------------------------------------------------------


def detect_behavioural_default_changed(
    old: MatrixSnapshot,
    new: MatrixSnapshot,
) -> list[Change]:
    """Diff the manifest ``defaults:`` section. One finding per changed key."""
    changes: list[Change] = []
    keys = sorted(set(old.defaults) | set(new.defaults))
    for k in keys:
        ov = old.defaults.get(k)
        nv = new.defaults.get(k)
        if ov == nv:
            continue
        if ov is None:
            desc = (
                f"Default value for '{k}' added in new manifest: "
                f"{nv!r}. Was previously unspecified; behaviour may "
                f"differ from old release's implicit default."
            )
        elif nv is None:
            desc = (
                f"Default value for '{k}' removed from new manifest "
                f"(was {ov!r}); the field is now unspecified."
            )
        else:
            desc = (
                f"Default value for '{k}' changed: {ov!r} → {nv!r}. "
                f"Source compiles and links unchanged; runtime "
                f"behaviour silently differs."
            )
        changes.append(make_change(
            ChangeKind.BEHAVIOURAL_DEFAULT_CHANGED,
            symbol=k,
            description=desc,
            old_value=str(ov) if ov is not None else None,
            new_value=str(nv) if nv is not None else None,
        ))
    return changes


# ---------------------------------------------------------------------------
# Combined matrix-diff entry point
# ---------------------------------------------------------------------------


def diff_matrix(
    old: MatrixSnapshot,
    new: MatrixSnapshot,
) -> list[Change]:
    """Run every matrix-aware detector and return the combined findings.

    The intra-matrix ``API_DEPENDS_ON_CONSUMER_ENV`` check is run
    against *both* old and new (it's a property of each release in
    isolation); duplicate (kind, symbol) findings are deduped.
    """
    out: list[Change] = []
    out.extend(detect_api_depends_on_consumer_env(old))
    out.extend(detect_api_depends_on_consumer_env(new))
    out.extend(detect_cxx_standard_floor_raised(old, new))
    out.extend(detect_behavioural_default_changed(old, new))

    # Dedupe (kind, symbol) preserving first occurrence (= old).
    seen: set[tuple[ChangeKind, str]] = set()
    deduped: list[Change] = []
    for c in out:
        key = (c.kind, c.symbol)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(c)
    return deduped
