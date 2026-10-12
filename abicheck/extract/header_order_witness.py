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

"""Dump-time witness for declared-header order (ADR-079).

The aggregate driver TU parses declared headers in declared order, so the
order is part of the extraction. This module lets a dump *prove* that a
snapshot's declarations equal the canonical (sorted) order's declarations,
without changing how the declared order is parsed.

A witness runs only when a declared set is not already canonical: the
declared extraction and the canonical extraction are both produced, and
they count as the same only when their declarations agree record for
record. Anything the comparison cannot establish is unproven.
"""

from __future__ import annotations

import dataclasses
import functools
import inspect
import json
from collections.abc import Callable, Sequence
from contextvars import ContextVar
from pathlib import Path
from typing import Any, TypeVar, cast

from ..errors import SnapshotError

_F = TypeVar("_F", bound=Callable[..., Any])

#: True while the witness is running its own inner dumps, so those dumps are
#: not themselves witnessed (no recursion, and no second proof per dump).
_PROVING: ContextVar[bool] = ContextVar("header_order_proving", default=False)


def canonical_header_order(headers: Sequence[Path]) -> list[Path]:
    """The header set sorted by resolved path: the order a proof is taken for.

    Sorting by the resolved path gives the same order as sorting by the
    root-relative identity the contract records, since every header of one
    set shares its root.
    """
    return sorted(headers, key=lambda h: str(h.resolve()))


def has_unique_resolved_paths(headers: Sequence[Path]) -> bool:
    """Whether the set has no duplicates; a duplicate makes order ill-defined."""
    resolved = [h.resolve() for h in headers]
    return len(resolved) == len(set(resolved))


def _declaration_records(snapshot: Any) -> list[str]:
    """Every extracted declaration of *snapshot* as a sorted list of canonical
    JSON records. Sorting makes the comparison independent of list order,
    which is exactly the thing being varied.
    """
    decls = snapshot.declarations
    records = [*decls.functions, *decls.variables, *decls.types, *decls.enums]
    lines = [
        json.dumps(dataclasses.asdict(record), sort_keys=True, default=str)
        for record in records
    ]
    for name, mapping in (
        ("typedefs", decls.typedefs),
        ("typedefs_qualified", decls.typedefs_qualified),
        ("constants", decls.constants),
    ):
        lines.extend(
            json.dumps([name, key, value], sort_keys=True)
            for key, value in mapping.items()
        )
    return sorted(lines)


def extractions_agree(declared: Any, canonical: Any) -> bool:
    """Whether two extractions carry the same declarations, record for record.

    A record carrying an order-dependent identifier makes this False, which
    keeps the reorder refused: the witness fails closed. A macro or pragma
    leak that changes no declaration is not visible here; that residual is
    recorded in ADR-079 rather than claimed as proven.
    """
    return _declaration_records(declared) == _declaration_records(canonical)


def _decode_sequence(value: str | None) -> list[str] | None:
    """The contract's ``header_sequence`` back into identities, or ``None``.

    Mirrors the gate's own decoding: a JSON list of distinct strings.
    """
    if value is None:
        return None
    try:
        decoded = json.loads(value)
    except ValueError:
        return None
    if not isinstance(decoded, list) or not all(isinstance(x, str) for x in decoded):
        return None
    if len(decoded) != len(set(decoded)):
        return None
    return decoded


def header_order_proof(dump_fn: _F) -> _F:
    """Decorator for :func:`abicheck.dumper.dump` (ADR-079).

    Dumps in declared order, exactly as before, and records a canonical-order
    proof on the declared snapshot's contract when the declarations provably
    equal the sorted order's. The declared snapshot is always the one returned,
    so the extraction is unchanged; only ``contract.header_canonical`` can be
    added.
    """

    @functools.wraps(dump_fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        if _PROVING.get():
            return dump_fn(*args, **kwargs)
        bound = inspect.signature(dump_fn).bind(*args, **kwargs)
        bound.apply_defaults()
        call_args = dict(bound.arguments)
        headers = list(call_args["headers"])
        if len(headers) <= 1 or call_args.get("dump_manifest") is not None:
            return dump_fn(*args, **kwargs)

        token = _PROVING.set(True)
        try:
            declared = dump_fn(**call_args)
            _attach_proof(dump_fn, declared, call_args, headers)
        finally:
            _PROVING.reset(token)
        return declared

    return cast(_F, wrapper)


def _attach_proof(
    dump_fn: Callable[..., Any],
    declared: Any,
    call_args: dict[str, Any],
    headers: list[Path],
) -> None:
    """Set ``declared.contract.header_canonical`` when the proof holds."""
    if not declared.from_headers or declared.contract is None:
        return
    if not has_unique_resolved_paths(headers):
        return
    declared_sequence = _decode_sequence(
        declared.contract.profile_fields.get("header_sequence")
    )
    if declared_sequence is None:
        return

    canonical_headers = canonical_header_order(headers)
    if [h.resolve() for h in headers] == [h.resolve() for h in canonical_headers]:
        # Already canonical: the declared extraction is the canonical one.
        declared.contract.header_canonical = declared_sequence
        return

    try:
        canonical = dump_fn(**{**call_args, "headers": canonical_headers})
    except SnapshotError:
        # A sorted order that cannot be parsed proves nothing about the declared
        # one; the caller keeps the declared snapshot.
        return
    if not canonical.from_headers or canonical.contract is None:
        return
    canonical_sequence = _decode_sequence(
        canonical.contract.profile_fields.get("header_sequence")
    )
    if canonical_sequence is None:
        return
    if extractions_agree(declared, canonical):
        declared.contract.header_canonical = canonical_sequence
