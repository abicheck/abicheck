"""TEMPORARY Windows diagnostic: how the fields of _TP_CALLBACK_ENVIRON_V3 come out.

Runs only on Windows, on the case the depth projection flags as unchanged
(case226). It dumps each side with headers and without, and prints every
_TP_CALLBACK_ENVIRON* field from each source (castxml record types and the PDB
debug layout). It fails on purpose so the Windows CI log carries the output.
Removed once the false type_field_type_changed is root-caused.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from abicheck.dumper import dump
from tests.test_depth_projection_matches_shallow_dump import (
    _built_case,
    _header_list,
)

CASE = "case226_header_only_template_default_unchanged"


def _read(fn):
    try:
        return fn()
    except Exception as exc:  # diagnostic: record the failure, never crash
        return f"<{type(exc).__name__}: {exc}>"


@pytest.mark.integration
@pytest.mark.skipif(sys.platform != "win32", reason="Windows diagnostic only")
def test_windows_callback_environ_fields(
    tmp_path: Path, shared_cmake_build_dir: Path | None
) -> None:
    v1_lib, v2_lib, v1_hdr, v2_hdr = _built_case(CASE, tmp_path, shared_cmake_build_dir)
    from abicheck.policy.depth_projection import project_pair_to_depth

    def attrs(obj):
        try:
            return dict(vars(obj))
        except TypeError:
            return {}

    lines: list[str] = []
    rich_old = dump(v1_lib, headers=_header_list(v1_hdr), version="old")
    rich_new = dump(v2_lib, headers=_header_list(v2_hdr), version="new")
    p_old, p_new = project_pair_to_depth(rich_old, rich_new, "binary")

    def records(snap):
        types = _read(lambda: list(snap.declarations.types)) or []
        return {
            r.name: r
            for r in types
            if "CALLBACK_ENVIRON" in str(getattr(r, "name", ""))
        }

    ro, rn = records(p_old), records(p_new)
    lines.append(f"names old={sorted(ro)} new={sorted(rn)}")
    for side, snap in (("old", p_old), ("new", p_new)):
        types = _read(lambda snap=snap: list(snap.declarations.types)) or []
        unnamed = [r for r in types if "unnamed" in str(getattr(r, "name", "")).lower()]
        lines.append(
            f"[{side}] {len(types)} records, {len(unnamed)} named like unnamed"
        )
        for r in unnamed[:8]:
            flds = [
                f"{f.name}:{f.type}@{f.offset_bits}"
                for f in (getattr(r, "fields", []) or [])
            ]
            lines.append(f"[{side}] unnamed record {r.name!r} fields={flds[:12]}"[:360])
    for name in sorted(set(ro) & set(rn)):
        a, b = attrs(ro[name]), attrs(rn[name])
        for k in sorted(set(a) | set(b)):
            if k == "fields":
                continue
            if a.get(k) != b.get(k):
                lines.append(
                    f"[record {name}] attr {k}: old={str(a.get(k))[:160]} new={str(b.get(k))[:160]}"
                )
        fa = {f.name: f for f in ro[name].fields}
        fb = {f.name: f for f in rn[name].fields}
        for fname in sorted(set(fa) | set(fb)):
            if fname not in fa or fname not in fb:
                lines.append(
                    f"[field {name}.{fname}] present only in {'old' if fname in fa else 'new'}"
                )
                continue
            x, y = attrs(fa[fname]), attrs(fb[fname])
            for k in sorted(set(x) | set(y)):
                if x.get(k) != y.get(k):
                    lines.append(
                        f"[field {name}.{fname}] {k}: old={str(x.get(k))[:200]} new={str(y.get(k))[:200]}"
                    )
    if not any(x.startswith("[record") or x.startswith("[field") for x in lines):
        lines.append("no projected attribute differs between old and new")
    raise AssertionError("WINDOWS-DIAG\n" + "\n".join(lines))
