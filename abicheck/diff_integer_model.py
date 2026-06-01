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

"""Integer-model (LP64 <-> ILP64) switch detection.

Numerical libraries (for example a BLAS/LAPACK implementation such as oneMKL)
often ship two integer interfaces: an LP64 build where the integer typedef
(e.g. ``MKL_INT``) is 32-bit (``int``) and an ILP64 build where it is
64-bit (``long`` / ``int64_t``). Switching the interface flips the width of a
large fraction of public integer parameters/returns at once. This is detected
as a single high-level diagnostic, mirroring the libstdc++ dual-ABI flip
detector in ``diff_platform``.
"""
from __future__ import annotations

from .checker_policy import ChangeKind
from .checker_types import Change
from .detector_registry import registry
from .model import AbiSnapshot, Function, Visibility

# Canonical integer-width buckets. A change that moves a spelling from one
# bucket to a *different* bucket (and is not a sign-only change) is a width flip.
# NOTE: the ``long`` family is intentionally NOT here — its width is data-model
# dependent (64-bit under LP64 on Linux/macOS, 32-bit under LLP64 on Windows)
# and is resolved per-target in ``_int_width_bucket``.
_INT_WIDTH_BUCKETS: dict[str, str] = {
    "int": "32",
    "signed int": "32",
    "unsigned": "32",
    "unsigned int": "32",
    "int32_t": "32",
    "uint32_t": "32",
    "long long": "64",
    "long long int": "64",
    "signed long long": "64",
    "unsigned long long": "64",
    "int64_t": "64",
    "uint64_t": "64",
}

# The ``long`` family: 64-bit under LP64 (Linux/macOS), 32-bit under LLP64
# (Windows). Resolved against the snapshot platform in ``_int_width_bucket``.
_LONG_FAMILY = frozenset({"long", "long int", "signed long", "unsigned long"})

# Integer-typedef name hints used to corroborate an LP64<->ILP64 switch.
_INT_TYPEDEF_HINTS = ("_INT", "_int", "INT_T", "_INTEGER")


def _int_width_bucket(type_str: object, is_llp64: bool = False) -> str | None:
    """Map a type spelling to its integer-width bucket, or None.

    ``is_llp64`` must be set for Windows/LLP64 targets, where ``long`` is
    32-bit; otherwise the LP64 model is assumed (``long`` is 64-bit). Without
    this, a benign ``int``→``long`` change on Windows would be misread as an
    LP64↔ILP64 model flip (Codex review P2).

    Defensive against non-string inputs: some snapshots/tests carry type fields
    that are not plain strings, and an uncaught exception here would disable the
    whole detector for the rest of the process (the registry latches
    ``disabled`` on the first crash).
    """
    if not isinstance(type_str, str):
        return None
    t = type_str.strip()
    if t in _LONG_FAMILY:
        return "32" if is_llp64 else "64"
    return _INT_WIDTH_BUCKETS.get(t)


def _scan_function_integer_flips(
    old_map: dict[str, Function],
    new_map: dict[str, Function],
    is_llp64: bool,
) -> tuple[int, int, int, int]:
    """Return (flips, total, up, down) over matched public functions' int slots."""
    flips = total = up = down = 0
    for key in set(old_map) & set(new_map):
        of, nf = old_map[key], new_map[key]
        slots: list[tuple[object, object]] = [(of.return_type, nf.return_type)]
        for op, npm in zip(of.params, nf.params):
            slots.append((op.type, npm.type))
        for old_t, new_t in slots:
            ob = _int_width_bucket(old_t, is_llp64)
            nb = _int_width_bucket(new_t, is_llp64)
            if ob is None or nb is None:
                continue
            total += 1
            if ob != nb:
                flips += 1
                if ob == "32" and nb == "64":
                    up += 1
                elif ob == "64" and nb == "32":
                    down += 1
    return flips, total, up, down


def _scan_typedef_integer_flips(
    old: AbiSnapshot, new: AbiSnapshot, is_llp64: bool
) -> tuple[list[str], int, int]:
    """Return (descriptions, up, down) for integer-named typedefs that changed width."""
    typedef_flips: list[str] = []
    up = down = 0
    for name, old_under in old.typedefs.items():
        new_under = new.typedefs.get(name)
        if new_under is None:
            continue
        if not any(h in name for h in _INT_TYPEDEF_HINTS):
            continue
        ob = _int_width_bucket(old_under, is_llp64)
        nb = _int_width_bucket(new_under, is_llp64)
        if ob is not None and nb is not None and ob != nb:
            typedef_flips.append(f"{name} ({old_under} -> {new_under})")
            if nb == "64":
                up += 1
            else:
                down += 1
    return typedef_flips, up, down


def _integer_model_direction(up: int, down: int) -> str:
    """Return the human-readable transition direction string."""
    if up >= down:
        return "LP64 → ILP64 (32-bit → 64-bit)"
    return "ILP64 → LP64 (64-bit → 32-bit)"


def _integer_model_detail(flips: int, total: int, typedef_flips: list[str]) -> str:
    """Build the detail sentence describing what flipped width."""
    detail_bits = []
    if flips:
        detail_bits.append(
            f"{flips} of {total} public integer parameters/returns flipped width"
        )
    if typedef_flips:
        detail_bits.append(
            "integer typedef(s) resized: " + ", ".join(sorted(typedef_flips))
        )
    return "; ".join(detail_bits)


@registry.detector("integer_model")
def _diff_integer_model(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Detect an LP64<->ILP64 integer-model switch (e.g. oneMKL MKL_INT 32<->64).

    Conservative, mirrors the glibcxx dual-ABI detector: only fires when a
    meaningful number of public integer parameters/returns flip width together,
    OR a public integer-named typedef changes its underlying width. Reports ONE
    grouped diagnostic; per-symbol findings are still emitted separately by the
    symbol diff.
    """
    # Windows/LLP64: ``long`` is 32-bit, so int<->long is NOT a model flip there.
    is_llp64 = "pe" in (old.platform, new.platform)

    old_map = {f.mangled: f for f in old.functions if f.visibility == Visibility.PUBLIC}
    new_map = {f.mangled: f for f in new.functions if f.visibility == Visibility.PUBLIC}

    flips, total, up, down = _scan_function_integer_flips(old_map, new_map, is_llp64)
    typedef_flips, typedef_up, typedef_down = _scan_typedef_integer_flips(old, new, is_llp64)
    up += typedef_up
    down += typedef_down

    # Conservative thresholds (like the dual-ABI detector): require either a
    # meaningful count + ratio of flips, or a corroborating integer typedef.
    enough_func_flips = flips >= 4 and total > 0 and flips >= total * 0.5
    if not enough_func_flips and not typedef_flips:
        return []

    direction = _integer_model_direction(up, down)
    detail = _integer_model_detail(flips, total, typedef_flips)

    return [Change(
        kind=ChangeKind.INTEGER_MODEL_CHANGED,
        symbol="__integer_model",
        description=(
            f"Integer model changed ({direction}): {detail}. "
            f"This is the signature of an LP64↔ILP64 switch (e.g. oneMKL's "
            f"32-bit vs 64-bit MKL_INT interface); every caller passes/reads "
            f"integers with the wrong width."
        ),
        old_value=f"{down} narrowing / {up} widening transitions",
        new_value=direction,
    )]
