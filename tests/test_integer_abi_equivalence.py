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

"""ABI-equivalent integer type changes must not be reported as binary breaks.

On LP64, ``size_t`` is ``unsigned long`` and both ``long`` / ``long long`` are
64-bit, so a name-only change between such spellings is not a binary ABI break.
A real width change (``int`` -> ``size_t``) or a signedness change
(``long`` -> ``unsigned long``) must still be reported.
"""

from __future__ import annotations

import pytest

from abicheck.checker import ChangeKind, Verdict, compare
from abicheck.diff_symbols import _abi_equivalent_scalar
from abicheck.model import AbiSnapshot, Function, Param, Visibility


@pytest.mark.parametrize("a,b", [
    # Pointer-width spellings co-vary on any non-LLP64 target (long == size).
    ("unsigned long", "size_t"),
    ("long unsigned int", "size_t"),
    ("size_t", "uintptr_t"),
    ("long", "ptrdiff_t"),
    ("ssize_t", "ptrdiff_t"),
    ("long", "long int"),
    # Fixed-width spellings, data-model independent.
    ("int", "int32_t"),
    ("unsigned int", "uint32_t"),
    ("long long", "int64_t"),
    ("unsigned long long", "uint64_t"),
])
def test_nonllp64_equivalent(a: str, b: str) -> None:
    assert _abi_equivalent_scalar(a, b, is_llp64=False)


@pytest.mark.parametrize("a,b", [
    ("int", "long"),                  # 32 vs pointer-width
    ("int", "size_t"),                # 32 vs pointer-width
    ("long", "unsigned long"),        # signedness differs
    ("size_t", "ssize_t"),            # signedness differs
    # Data-model-dependent vs fixed width: equal only on LP64, a real width
    # change on ILP32 — and the snapshot does not record bitness, so these are
    # conservatively reported rather than suppressed.
    ("long", "long long"),
    ("long", "int64_t"),
    ("unsigned long", "uint64_t"),
    ("long*", "long long*"),          # pointers are not bare scalars
    ("int", "float"),                 # float not modelled
])
def test_nonllp64_not_equivalent(a: str, b: str) -> None:
    assert not _abi_equivalent_scalar(a, b, is_llp64=False)


def test_llp64_differs() -> None:
    # On Windows LLP64 long is 32-bit while size_t/pointer types stay 64-bit.
    assert not _abi_equivalent_scalar("long", "long long", is_llp64=True)
    assert not _abi_equivalent_scalar("unsigned long", "size_t", is_llp64=True)


@pytest.mark.parametrize("is_llp64", [False, True])
def test_pointer_width_never_equated_with_fixed_width(is_llp64: bool) -> None:
    # A pointer-width typedef has an unknown absolute width on every platform
    # (32-bit on ILP32 / 32-bit Windows, 64-bit on LP64 / LLP64), so it must
    # never be treated as ABI-equal to a fixed-width spelling such as uint64_t.
    # On 32-bit Windows size_t is 32-bit, so equating it with uint64_t would be
    # a false negative.
    assert not _abi_equivalent_scalar("size_t", "uint64_t", is_llp64=is_llp64)
    assert not _abi_equivalent_scalar("ptrdiff_t", "int64_t", is_llp64=is_llp64)


def test_pointer_width_typedefs_equivalent_to_each_other() -> None:
    # size_t and uintptr_t are both pointer-width unsigned on every platform.
    assert _abi_equivalent_scalar("size_t", "uintptr_t", is_llp64=False)
    assert _abi_equivalent_scalar("size_t", "uintptr_t", is_llp64=True)


def _fn(ret: str) -> Function:
    return Function(name="f", mangled="f", return_type=ret,
                    params=[], visibility=Visibility.PUBLIC)


def _snap(ver: str, ret: str, platform: str = "elf") -> AbiSnapshot:
    return AbiSnapshot(library="lib.so", version=ver, platform=platform,
                       functions=[_fn(ret)])


def test_return_unsigned_long_to_size_t_compatible() -> None:
    # size_t IS unsigned long on LP64 (and co-varies on ILP32) → not a break.
    r = compare(_snap("1", "unsigned long"), _snap("2", "size_t"))
    assert ChangeKind.FUNC_RETURN_CHANGED not in {c.kind for c in r.changes}
    assert r.verdict in (Verdict.NO_CHANGE, Verdict.COMPATIBLE)


def test_return_int_to_size_t_still_breaking() -> None:
    # 32 -> pointer-width is a real representation change.
    r = compare(_snap("1", "int"), _snap("2", "size_t"))
    assert ChangeKind.FUNC_RETURN_CHANGED in {c.kind for c in r.changes}


def test_return_long_to_long_long_reported_unknown_bitness() -> None:
    # long vs long long is only ABI-equal on LP64; on ILP32 it is a real width
    # change. The snapshot does not record bitness, so it is conservatively
    # reported rather than silently suppressed.
    r = compare(_snap("1", "long"), _snap("2", "long long"))
    assert ChangeKind.FUNC_RETURN_CHANGED in {c.kind for c in r.changes}


def _fn_param(ptype: str) -> Function:
    return Function(name="g", mangled="g", return_type="void",
                    params=[Param(name="a", type=ptype)], visibility=Visibility.PUBLIC)


def _snap_param(ver: str, ptype: str, platform: str = "elf") -> AbiSnapshot:
    return AbiSnapshot(library="l", version=ver, platform=platform,
                       functions=[_fn_param(ptype)])


def test_param_size_t_to_unsigned_long_compatible() -> None:
    r = compare(_snap_param("1", "size_t"), _snap_param("2", "unsigned long"))
    assert ChangeKind.FUNC_PARAMS_CHANGED not in {c.kind for c in r.changes}


def test_param_int_to_long_still_breaking() -> None:
    r = compare(_snap_param("1", "int"), _snap_param("2", "long"))
    assert ChangeKind.FUNC_PARAMS_CHANGED in {c.kind for c in r.changes}


# ── End-to-end LLP64 (platform="pe") — is_llp64 derived from snapshot ─────────

def test_llp64_return_long_to_long_long_breaking() -> None:
    r = compare(_snap("1", "long", platform="pe"), _snap("2", "long long", platform="pe"))
    assert ChangeKind.FUNC_RETURN_CHANGED in {c.kind for c in r.changes}


def test_llp64_return_unsigned_long_to_size_t_breaking() -> None:
    # On LLP64 unsigned long is 32-bit but size_t is 64-bit → a real change.
    r = compare(_snap("1", "unsigned long", platform="pe"), _snap("2", "size_t", platform="pe"))
    assert ChangeKind.FUNC_RETURN_CHANGED in {c.kind for c in r.changes}


@pytest.mark.parametrize("platform", ["elf", "pe"])
def test_int_to_long_param_breaking_on_both_models(platform: str) -> None:
    # int vs long are distinct built-ins; the change is reported regardless of
    # data model (mirrors examples/case102: a frozen extern-C signature widened
    # from int to long must stay a FUNC_PARAMS_CHANGED on Windows too, where
    # both are 32-bit).
    r = compare(_snap_param("1", "int", platform), _snap_param("2", "long", platform))
    assert ChangeKind.FUNC_PARAMS_CHANGED in {c.kind for c in r.changes}, platform
