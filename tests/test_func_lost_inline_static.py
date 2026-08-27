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

"""FUNC_LOST_INLINE fires for a ``static`` function, and for a C++
unnamed-namespace function, the same way as an ordinary external one.

Split out rather than added to test_diff_symbols_deep.py, which is at its
architecture-gate no-growth debt baseline.
"""

from __future__ import annotations

from abicheck.checker import ChangeKind, compare
from abicheck.model import AbiSnapshot, Function, Visibility


def _snap(functions):
    return AbiSnapshot(
        library="libtest.so.1",
        version="1.0",
        functions=functions,
        from_headers=True,
    )


def _pub_func(name, mangled, **kwargs):
    return Function(
        name=name,
        mangled=mangled,
        return_type="void",
        params=[],
        visibility=Visibility.PUBLIC,
        **kwargs,
    )


def test_lost_inline_fires_even_for_a_static_function():
    """The detector fires the same way for a ``static`` function too.

    Codex review, PR #882, fresh evidence: ``Function.is_static`` is
    captured but ``_check_inline_transitions()`` never gates on it — a
    ``static`` function losing ``inline`` keeps internal linkage
    regardless of language, so the multiple-definition risk the
    ``func_lost_inline`` impact text describes for the non-``static`` C
    case cannot occur here. Pinned as a detector-contract test: the
    finding still fires (this detector's job is only to report the
    transition), it is the *impact text* that now scopes the risk
    conditionally, not the detector's own firing behavior.
    """
    f_v1 = _pub_func("helper", "_Z6helperv", is_inline=True, is_static=True)
    f_v2 = _pub_func("helper", "_Z6helperv", is_inline=False, is_static=True)
    r = compare(_snap([f_v1]), _snap([f_v2]))
    assert ChangeKind.FUNC_LOST_INLINE in {c.kind for c in r.changes}


def test_lost_inline_fires_even_for_an_unnamed_namespace_function():
    """The detector fires for a C++ unnamed-namespace function too.

    Codex review, PR #882, fresh evidence: ``Function.is_static`` captures
    only the ``static`` keyword, not C++ unnamed-namespace membership —
    which gives a function the identical internal linkage ``static``
    does, without setting ``is_static``. With no ELF export table (a
    header-only snapshot, as here), ``_public_functions()`` has no other
    signal to exclude it either, so this reproduces the same false
    "external linkage" premise the impact text's `static`-only caveat
    didn't cover. Pinned the same way as the ``static`` sibling test: the
    finding still fires here, only the impact text now scopes the C++
    external-linkage claim conditionally.
    """
    f_v1 = _pub_func(
        "(anonymous namespace)::helper",
        "_ZN12_GLOBAL__N_16helperEv",
        is_inline=True,
        is_static=False,
    )
    f_v2 = _pub_func(
        "(anonymous namespace)::helper",
        "_ZN12_GLOBAL__N_16helperEv",
        is_inline=False,
        is_static=False,
    )
    r = compare(_snap([f_v1]), _snap([f_v2]))
    assert ChangeKind.FUNC_LOST_INLINE in {c.kind for c in r.changes}
