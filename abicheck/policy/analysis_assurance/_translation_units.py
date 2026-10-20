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
"""Translation-unit accounting axis."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._result import TranslationUnitAccounting

if TYPE_CHECKING:
    from ...buildsource.pack import BuildSourcePack


def _translation_units(
    old_pack: BuildSourcePack | None, new_pack: BuildSourcePack | None
) -> tuple[TranslationUnitAccounting, bool]:
    """Roll up TU accounting, plus a second signal: whether a side carries an
    L4 ``source_abi`` object at all (i.e. L4 replay was actually attempted)
    while producing *no* TU accounting whatsoever (Finding, P1 review).

    ``inline._run_inline_source_abi()`` returns a bare ``SourceAbiSurface()``
    -- present, but with an empty ``coverage`` dict -- when its extractor
    (clang/castxml) isn't available; ``compile_units_selected``/
    ``compile_units_parsed`` are then both absent, not zero, so the old
    ``failed = selected - parsed`` computation below silently stayed
    ``None`` (nothing to subtract) instead of reading as a failure. The
    returned bool lets the caller treat that shape -- a present-but-empty L4
    surface -- as its own incompleteness signal, independent of whatever
    ``selected``/``parsed`` end up being when they ARE populated.
    """
    selected_total: int | None = None
    parsed_total: int | None = None
    l4_present_without_accounting = False
    for pack in (old_pack, new_pack):
        sa = getattr(pack, "source_abi", None)
        if sa is None:
            continue
        cov = sa.coverage or {}
        selected = cov.get("compile_units_selected")
        parsed = cov.get("compile_units_parsed")
        if selected is None and parsed is None:
            l4_present_without_accounting = True
        if selected is not None:
            selected_total = (selected_total or 0) + int(selected)
        if parsed is not None:
            parsed_total = (parsed_total or 0) + int(parsed)
    failed = None
    if selected_total is not None and parsed_total is not None:
        failed = max(0, selected_total - parsed_total)
    return (
        TranslationUnitAccounting(
            selected=selected_total, parsed=parsed_total, failed=failed, skipped=None
        ),
        l4_present_without_accounting,
    )
