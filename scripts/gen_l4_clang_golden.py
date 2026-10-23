#!/usr/bin/env python3
"""Generate the L4 clang payload golden for plan ``l4-l2-extraction-convergence`` Phase 4.

ADR-063's amendment "L4 clang payload comparability" (test plan item 1) needs
the pre-change (``producer_version`` ``"0.13"``) side of the projection
equivalence test as a golden, generated once from the pre-change code and
checked in before the producer switch. This script writes that golden.

For every catalog C++ translation unit (``catalog/cases/*/*.cpp``, the same
glob the measurement spike uses) it runs the clang AST dump once, builds the
current L4 surface with ``ClangSourceExtractor.source_abi_from_dump`` and
keeps the reduced view the measurement compares: each entity's kind and the
candidate fields of that kind, in producer order. Nothing else is stored, so
the golden is exactly the input ``compare_views`` consumes on the current side.

Output: one JSON object per line. Line 1 is a header (producer version, clang
version, corpus size, the candidate-field table); each following line is one
TU with its compile unit and its entities, or its failure. Lines are written
with sorted keys and no timestamps, so regeneration on the same toolchain
produces an identical file (``--check`` verifies that).

Usage::

    python scripts/gen_l4_clang_golden.py            # write the golden
    python scripts/gen_l4_clang_golden.py --check    # exit 1 if it would change
"""

from __future__ import annotations

import argparse
import difflib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent

from measure_l4_clang_projection import (  # noqa: E402
    CANDIDATE_FIELDS,
    corpus_cases,
    view_of,
)

GOLDEN = REPO / "tests" / "golden" / "l4_clang" / "surface_0_13.jsonl"
TARGET_ID = "target://golden"


def compile_unit_for(cpp: Path) -> Any:
    """The compile unit the measurement dumps; recorded in the golden too."""
    from abicheck.buildsource.build_evidence import CompileUnit

    case_dir = cpp.parent
    return CompileUnit(
        id=f"cu://{case_dir.name}/{cpp.name}",
        source=str(cpp),
        directory=str(case_dir),
        language="CXX",
        standard="c++17",
        argv=["c++", "-std=c++17", "-fPIC", "-fvisibility=hidden", "-c", str(cpp)],
    )


def live_clang_version(clang_bin: str = "clang") -> str:
    """The first line of ``clang --version``, as the header records it."""
    out = subprocess.run(
        [clang_bin, "--version"], capture_output=True, text=True, check=True
    ).stdout
    return out.splitlines()[0].strip()


def _repo_relative(path: str) -> str:
    """A checkout-independent spelling of a path inside the repository."""
    return Path(path).resolve().relative_to(REPO).as_posix()


def portable_compile_unit(cu: Any) -> dict[str, Any]:
    """The compile unit as the golden stores it: repository-relative paths only.

    The dump itself runs on absolute paths; only the recorded form is portable,
    so the golden is byte-identical in any checkout.
    """
    argv = [_repo_relative(a) if Path(a).is_absolute() else a for a in cu.argv]
    return {
        "source": _repo_relative(cu.source),
        "directory": _repo_relative(cu.directory),
        "language": cu.language,
        "standard": cu.standard,
        "argv": argv,
    }


def golden_tu(cpp: Path, extractor: Any) -> dict[str, Any]:
    """One TU's reduced current-side surface, or the failure that prevented it."""
    cu = compile_unit_for(cpp)
    case = f"{cpp.parent.name}/{cpp.name}"
    try:
        dump = extractor.dump_for(cu)
    except Exception as exc:  # recorded, never hidden
        return {"tu": case, "error": f"{type(exc).__name__}: {exc}"}
    try:
        if dump.ast is None:
            return {
                "tu": case,
                "error": f"clang AST dump failed: stage={dump.stage} {dump.error[:200]}",
            }
        current = extractor.source_abi_from_dump(
            cu,
            dump,
            public_header_roots=[str(cpp.parent)],
            target_id=TARGET_ID,
        )
    except Exception as exc:
        return {"tu": case, "error": f"{type(exc).__name__}: {exc}"}
    finally:
        dump.release()
    entities = []
    for e in current.all_entities():
        if e.kind not in CANDIDATE_FIELDS:
            continue
        view = view_of(e)
        entities.append(
            {
                "kind": view.kind,
                "qualified_name": view.qualified_name,
                "fields": view.fields,
            }
        )
    return {
        "tu": case,
        "compile_unit": portable_compile_unit(cu),
        "entities": entities,
    }


#: The golden is the pre-change side of the projection test. It stays at the
#: 0.13 producer stamp after ``CLANG_EXTRACTOR_VERSION`` moved on (plan b2), so
#: regenerating it cannot silently turn the old side into the new one.
PRE_CHANGE_PRODUCER_VERSION = "0.13"


def build_golden_text(cases: list[Path], clang_bin: str = "clang") -> str:
    from abicheck.buildsource.source_extractors.clang import ClangSourceExtractor

    extractor = ClangSourceExtractor(clang_bin=clang_bin)
    if not extractor.available():
        raise SystemExit(f"{clang_bin} is not on PATH; the golden needs clang")
    header = {
        "record": "header",
        "producer_version": PRE_CHANGE_PRODUCER_VERSION,
        "clang_version": live_clang_version(clang_bin),
        "corpus": "catalog/cases/*/*.cpp",
        "tu_count": len(cases),
        "candidate_fields": {k: list(v) for k, v in CANDIDATE_FIELDS.items()},
    }
    lines = [json.dumps(header, sort_keys=True)]
    for cpp in cases:
        row = golden_tu(cpp, extractor)
        row["record"] = "tu"
        lines.append(json.dumps(row, sort_keys=True))
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--cases", type=Path, default=REPO / "catalog" / "cases")
    parser.add_argument("--out", type=Path, default=GOLDEN)
    parser.add_argument(
        "--check",
        action="store_true",
        help="regenerate in memory and exit 1 if the checked-in golden differs",
    )
    args = parser.parse_args(argv)

    text = build_golden_text(corpus_cases(args.cases))
    if args.check:
        current = args.out.read_text(encoding="utf-8") if args.out.exists() else ""
        if current == text:
            print(f"{args.out} is up to date")
            return 0
        diff = difflib.unified_diff(
            current.splitlines(),
            text.splitlines(),
            "checked-in",
            "regenerated",
            lineterm="",
        )
        sys.stdout.writelines(line + "\n" for line in list(diff)[:200])
        print("golden is stale: run scripts/gen_l4_clang_golden.py", file=sys.stderr)
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8")
    print(f"wrote {args.out} ({len(text.encode('utf-8'))} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
