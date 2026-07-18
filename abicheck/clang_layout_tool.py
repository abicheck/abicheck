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

"""G28 Phase 4: optional Clang ``ASTRecordLayout`` enrichment for the direct-
clang L2 backend (``--ast-frontend clang``).

``dumper_clang.py``'s ``-ast-dump=json`` parse is syntactic only: it never
computes a record's actual compiled layout (``RecordType.size_bits``/
``alignment_bits``/``data_size_bits``/``vptr_offset_bits``/``base_offsets``/
``TypeField.offset_bits`` all stay ``None``/empty), which is exactly why
CastXML — which runs its own bundled Clang internally and exports the layout
it already computed — remains the layout-authoritative L2 backend today. See
``docs/development/plans/g28-castxml-clang-l2-parity-hardening.md``, "Phase 4
— a Clang ASTRecordLayout plugin".

This module bridges that gap with a small, OPTIONAL, out-of-process
companion (``tools/clang-layout-tool/``, built with LibTooling) that walks
every complete, non-dependent ``CXXRecordDecl`` and serializes the REAL
layout ``clang::ASTRecordLayout`` computes internally. It is deliberately
never a hard dependency (ADR-001's "lightweight, pure-Python tool" stance):

- The binary is resolved via :func:`find_layout_tool_bin` — an explicit
  ``ABICHECK_CLANG_LAYOUT_TOOL=/path/to/binary`` env var, or a bare
  ``abicheck-clang-layout-tool`` on ``PATH``. Neither being set/found is the
  overwhelmingly common case, and is silent (returns ``None``), never an
  error — a caller simply skips this enrichment.
- Every failure mode (tool missing, a compile the tool couldn't recover from
  at all, a timeout, malformed JSON) degrades to "no enrichment happened,"
  never raises — mirroring the same "degrade gracefully, never abort" policy
  every other optional evidence layer in this codebase follows (ADR-028 D3).
- Clang's internal AST API (what the companion tool links against) has no
  cross-LLVM-release ABI stability guarantee the way CastXML's own versioned
  XML schema does — this is exactly why it lives as a standalone, optional,
  separately-built companion rather than a `abicheck` package dependency.

Only backfills a field that is CURRENTLY ``None``/empty on the snapshot's
existing ``RecordType`` — never overrides a value another parser already
populated (a no-op today for castxml/hybrid snapshots, which already carry
real layout from castxml; meaningful only for a pure ``--ast-frontend
clang`` snapshot, where every one of these fields starts out empty).
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any

from .buildsource.build_query import PRUNED_HEADER_DIR_SEGMENTS
from .dumper import (
    _build_clang_header_command,
    _resolve_clang_langmode,
)
from .dumper_clang import _resolve_clang_bin
from .errors import SnapshotError, ValidationError
from .header_utils import iter_directory_headers, resolve_inferred_header_roots
from .model import AbiSnapshot, RecordType

log = logging.getLogger(__name__)

#: Env var pointing at the compiled companion tool binary. Its mere presence
#: is the opt-in signal — unset means "skip this enrichment entirely."
LAYOUT_TOOL_ENV_VAR = "ABICHECK_CLANG_LAYOUT_TOOL"
_DEFAULT_BIN_NAME = "abicheck-clang-layout-tool"

_LAYOUT_SCALAR_FIELDS = (
    "size_bits",
    "alignment_bits",
    "data_size_bits",
    "is_standard_layout",
    "is_trivially_copyable",
    "vptr_offset_bits",
)


def find_layout_tool_bin() -> str | None:
    """Resolve the G28 Phase 4 layout tool binary, or ``None`` if unavailable."""
    override = os.environ.get(LAYOUT_TOOL_ENV_VAR)
    if override:
        return override
    return shutil.which(_DEFAULT_BIN_NAME)


def _compile_flags_from_ast_dump_command(cmd: list[str]) -> list[str]:
    """Strip ``cc_bin`` and the ``-ast-dump=json``-specific tail from a
    ``_build_clang_header_command()`` result, leaving the shared compiler
    context (includes, sysroot, ``-nostdinc``, pass-through options,
    language mode) the layout tool needs too, just with a different final
    action (no ``-Xclang -ast-dump=json``, no output-mode file argument).
    Reusing that function's own flag-building keeps this module's compile
    context bit-for-bit consistent with whatever the direct-clang L2
    backend actually used to successfully parse these headers, rather than
    risking a second, subtly different derivation drifting out of sync.
    """
    try:
        xclang_idx = cmd.index("-Xclang")
    except ValueError:
        return cmd[1:]
    return cmd[1:xclang_idx]


def run_layout_tool(
    binary: str,
    resolved_headers: list[Path],
    extra_includes: list[Path],
    *,
    compiler: str = "c++",
    gcc_path: str | None = None,
    gcc_prefix: str | None = None,
    gcc_options: str | None = None,
    gcc_option_tokens: tuple[str, ...] = (),
    sysroot: Path | None = None,
    nostdinc: bool = False,
    lang: str | None = None,
    timeout: int = 60,
) -> list[dict[str, Any]] | None:
    """Run the G28 Phase 4 layout tool over *resolved_headers*.

    *resolved_headers*/*extra_includes* must already be fully resolved (a
    directory entry expanded, inferred include roots folded in) — the same
    caller-side responsibility ``service._attach_header_graph`` already has
    for its own second clang pass, kept here rather than re-imported from
    ``service_scan`` to avoid a service.py <-> clang_layout_tool.py import
    cycle (this module is imported FROM service.py).

    Returns the tool's per-record layout facts (a list of dicts, one per
    ``CXXRecordDecl`` it saw), or ``None`` on any failure — a missing
    ``binary``, no resolvable clang driver, a subprocess timeout/error, or
    malformed JSON output. Never raises: this is a best-effort enrichment
    layered on top of an already-successful direct-clang dump, not a
    required step (ADR-028 D3).
    """
    if not resolved_headers:
        return None
    try:
        clang_bin = _resolve_clang_bin(compiler, gcc_path, gcc_prefix)
    except Exception:  # noqa: BLE001 -- best-effort enrichment, never raises
        return None
    force_cpp, force_cpp20, _explicit_c, cc_id = _resolve_clang_langmode(
        lang, resolved_headers, clang_bin, gcc_options, gcc_option_tokens
    )

    agg_ext = ".hpp" if force_cpp else ".h"
    with tempfile.NamedTemporaryFile(
        suffix=agg_ext, mode="w", delete=False
    ) as agg:
        agg_path = Path(agg.name)
        agg.write(
            "".join(f'#include "{h.resolve()}"\n' for h in resolved_headers)
        )

    try:
        full_cmd = _build_clang_header_command(
            clang_bin,
            cc_id,
            extra_includes,
            agg_path,
            sysroot=sysroot,
            nostdinc=nostdinc,
            gcc_options=gcc_options,
            gcc_option_tokens=gcc_option_tokens,
            force_cpp=force_cpp,
            force_cpp20=force_cpp20,
        )
        compile_flags = _compile_flags_from_ast_dump_command(full_cmd)
        cmd = [binary, str(agg_path), "--", *compile_flags]
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout, check=False
            )
        except (subprocess.SubprocessError, OSError) as exc:
            log.debug("clang layout tool invocation failed: %s", exc)
            return None
        try:
            payload = json.loads(result.stdout)
        except ValueError:
            log.debug("clang layout tool produced non-JSON output")
            return None
        records = payload.get("records")
        if not isinstance(records, list):
            return None
        return records
    finally:
        agg_path.unlink(missing_ok=True)


def _apply_record_facts(t: RecordType, facts: dict[str, Any]) -> RecordType:
    """Backfill *t*'s currently-empty layout fields from one tool record."""
    updates: dict[str, Any] = {}
    for attr in _LAYOUT_SCALAR_FIELDS:
        if getattr(t, attr) is None and facts.get(attr) is not None:
            updates[attr] = facts[attr]

    if not t.base_offsets:
        base_facts = facts.get("bases")
        if isinstance(base_facts, list):
            offsets = {
                b["name"]: b["offset_bits"]
                for b in base_facts
                if isinstance(b, dict) and "name" in b and "offset_bits" in b
            }
            if offsets:
                updates["base_offsets"] = offsets

    field_offsets = {
        f["name"]: f["offset_bits"]
        for f in facts.get("fields", [])
        if isinstance(f, dict) and "name" in f and "offset_bits" in f
    }
    if field_offsets:
        new_fields = []
        fields_changed = False
        for f in t.fields:
            if f.offset_bits is None and f.name in field_offsets:
                new_fields.append(replace(f, offset_bits=field_offsets[f.name]))
                fields_changed = True
            else:
                new_fields.append(f)
        if fields_changed:
            updates["fields"] = new_fields

    return replace(t, **updates) if updates else t


def apply_layout_facts(
    snapshot: AbiSnapshot, records: list[dict[str, Any]] | None
) -> AbiSnapshot:
    """Backfill missing layout fields on *snapshot*'s ``RecordType``\\ s from
    the G28 Phase 4 layout tool's per-record facts.

    Matches by qualified name (``RecordType.qualified_name`` falling back to
    the bare ``name`` for a global-scope record, where they're identical to
    the tool's own ``RD->getQualifiedNameAsString()``). Only fills a field
    that is currently ``None``/empty — never overrides an existing value.
    A no-op (returns *snapshot* unchanged) when *records* is empty/``None``
    or matches nothing.
    """
    if not records:
        return snapshot
    by_name: dict[str, dict[str, Any]] = {
        r["qualified_name"]: r
        for r in records
        if isinstance(r, dict) and isinstance(r.get("qualified_name"), str)
    }
    if not by_name:
        return snapshot

    new_types = []
    changed = False
    for t in snapshot.types:
        facts = by_name.get(t.qualified_name or t.name)
        if facts is None:
            new_types.append(t)
            continue
        updated = _apply_record_facts(t, facts)
        if updated is not t:
            changed = True
        new_types.append(updated)

    if not changed:
        return snapshot
    return replace(snapshot, types=new_types, _type_by_name=None)


def _expand_header_inputs(inputs: list[Path]) -> list[Path]:
    """Expand a header directory entry into its recognised header files.

    A third copy of the same small expander ``service_scan.expand_header_inputs``
    / ``cli_resolve._expand_header_inputs`` already provide (see
    ``header_utils.iter_directory_headers``'s own docstring) — deliberately
    NOT imported from ``service_scan`` here: that module sits in an import
    chain that eventually reaches back to ``service.py``, which imports THIS
    module, so importing it would form a real cycle (this module's whole
    reason for existing is to be importable FROM ``service.py``).
    """
    out: list[Path] = []
    for p in inputs:
        if not p.exists():
            raise ValidationError(f"Header file not found or not a file: {p}")
        if p.is_file():
            out.append(p)
            continue
        if p.is_dir():
            found = iter_directory_headers(p, PRUNED_HEADER_DIR_SEGMENTS)
            if not found:
                raise ValidationError(
                    f"Header directory contains no supported header files: {p}"
                )
            out.extend(found)
            continue
        raise ValidationError(f"Header path is neither file nor directory: {p}")

    seen: set[str] = set()
    deduped: list[Path] = []
    for h in out:
        k = str(h.resolve())
        if k not in seen:
            seen.add(k)
            deduped.append(h)
    return deduped


def attach_clang_layout(
    snap: AbiSnapshot,
    headers: list[Path],
    extra_includes: list[Path],
    *,
    lang: str | None,
    compile: Any,
) -> AbiSnapshot:
    """Optionally enrich a pure ``--ast-frontend clang`` snapshot with real
    layout facts from the G28 Phase 4 companion tool.

    A no-op unless ALL of: the snapshot's L2 backend was actually
    ``"clang"`` (never for castxml/hybrid/DWARF-only/symbols-only — those
    either already have real layout or have no header-AST at all), headers
    were supplied, and the optional companion tool binary is resolvable
    (:func:`find_layout_tool_bin` — an explicit opt-in via
    ``ABICHECK_CLANG_LAYOUT_TOOL``, never a hard dependency). Every failure
    past that point (a bad header path, no resolvable clang driver, a
    compile error, a timeout, malformed output) degrades to "no
    enrichment," never raises (ADR-028 D3).

    *compile* is typed ``Any`` rather than ``service_scan.CompileContext``
    (duck-typed: only ``.gcc_path``/``.gcc_prefix``/``.gcc_options``/
    ``.gcc_option_tokens``/``.sysroot``/``.nostdinc`` are read) purely to
    avoid importing ``service_scan`` here — the same import-cycle reason
    :func:`_expand_header_inputs` is a local copy instead of a reuse.
    """
    if snap.ast_producer != "clang" or not headers:
        return snap
    binary = find_layout_tool_bin()
    if binary is None:
        return snap
    try:
        resolved_headers = _expand_header_inputs(headers)
        if not resolved_headers:
            return snap
        inc_extra, deferred = resolve_inferred_header_roots(
            resolved_headers,
            list(extra_includes),
            gcc_options=compile.gcc_options if compile is not None else None,
            gcc_option_tokens=compile.gcc_option_tokens if compile is not None else (),
        )
        eff_includes = list(extra_includes) + inc_extra
        eff_tokens = (
            (compile.gcc_option_tokens if compile is not None else ()) + tuple(deferred)
        )
    except (SnapshotError, ValidationError):
        return snap
    records = run_layout_tool(
        binary,
        resolved_headers,
        eff_includes,
        gcc_path=compile.gcc_path if compile is not None else None,
        gcc_prefix=compile.gcc_prefix if compile is not None else None,
        gcc_options=compile.gcc_options if compile is not None else None,
        gcc_option_tokens=eff_tokens,
        sysroot=compile.sysroot if compile is not None else None,
        nostdinc=compile.nostdinc if compile is not None else False,
        lang=lang,
    )
    return apply_layout_facts(snap, records)
