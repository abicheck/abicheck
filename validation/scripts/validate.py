#!/usr/bin/env python3
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

"""Unified ABI validation harness: score abicheck against any expectation source.

One engine (``conda_harness``), many sources of *expected* verdicts:

* ``--source manifest`` — the hand-curated ``data/manifest.json`` (human-labelled
  edge cases with notes),
* ``--source tracker`` — a harvested abi-laboratory oracle
  (``data/tracker_oracle/<lib>.json``, whole version histories, automated).

Both feed the same fetch → extract → ``abicheck compare`` → score pipeline and
the same report format, so the only difference between "validate against curated
examples" and "validate against an automated oracle" is one flag. Future sources
(libabigail/abidiff cross-check, Debian ``.symbols``) plug in as another adapter.

Usage:
    python validation/scripts/validate.py --source tracker --lib libxml2
    python validation/scripts/validate.py --source manifest
    python validation/scripts/validate.py --source manifest --lib oneTBB
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
VALID_DIR = SCRIPTS_DIR.parent  # validation/
DATA = VALID_DIR / "data"
ORACLE_DIR = DATA / "tracker_oracle"
PARITY_DIR = DATA / "tracker_parity"
MANIFEST = DATA / "manifest.json"

sys.path.insert(0, str(SCRIPTS_DIR))
from conda_harness import evaluate_pair, query_conda  # noqa: E402
from fetch_tracker_oracle import _normalize_verdict, compare_to_results  # noqa: E402

# ---------------------------------------------------------------------------
# Expectation sources: each returns a list of *normalised* pair dicts with keys
#   pair, library, pkg, old_ver, new_ver, expected_verdict, subdir
# and optionally old_file/new_file (pin exact builds) and note.
# ---------------------------------------------------------------------------


def tracker_pairs(lib: str, pkg: str | None, subdir: str) -> list[dict]:
    """Pairs from a harvested abi-laboratory oracle (tracker_oracle/<lib>.json)."""
    oracle_path = ORACLE_DIR / f"{lib}.json"
    if not oracle_path.is_file():
        raise FileNotFoundError(
            f"no oracle for {lib}: run fetch_tracker_oracle.py {lib} first"
        )
    oracle = json.loads(oracle_path.read_text())
    out = []
    for p in oracle["pairs"]:
        out.append(
            {
                "pair": p["pair"],
                "library": lib,
                "pkg": pkg or lib,
                "old_ver": p["old_ver"],
                "new_ver": p["new_ver"],
                "expected_verdict": p["expected_verdict"],
                "removed_symbols": p.get("removed_symbols"),
                "backward_compat_pct": p.get("backward_compat_pct"),
                "soname_changed": p.get("soname_changed"),
                "subdir": subdir,
            }
        )
    return out


def manifest_pairs(filter_lib: str | None, subdir: str) -> list[dict]:
    """Pairs from the hand-curated manifest (data/manifest.json)."""
    if not MANIFEST.is_file():
        raise FileNotFoundError(f"manifest not found: {MANIFEST}")
    entries = json.loads(MANIFEST.read_text())
    out = []
    for m in entries:
        if filter_lib and filter_lib not in (m.get("library"), m.get("pkg")):
            continue
        out.append(
            {
                "pair": m["pair"],
                "library": m["library"],
                "pkg": m["pkg"],
                "old_ver": m["old_ver"],
                "new_ver": m["new_ver"],
                "expected_verdict": m["expectation"],
                "subdir": subdir,
                "old_file": m.get("old_file"),
                "new_file": m.get("new_file"),
                "note": m.get("note"),
            }
        )
    return out


def _is_evidence_limited(pair: dict, verdict: str, ev: dict) -> bool:
    """True when a divergence is an evidence limit, not an abicheck miss.

    The oracle break is type-level only (``removed_symbols == 0``) but the
    compared binary carries no DWARF, so abicheck can only see the symbol table
    and cannot observe the change ABICC saw in its debug build. A non-breaking
    verdict there is expected, not a false negative.
    """
    return (
        pair.get("expected_verdict") == "BREAKING"
        and pair.get("removed_symbols") == 0
        and not ev.get("has_dwarf", False)
        and _normalize_verdict(verdict) != "BREAKING"
    )


def _is_scope_divergence(pair: dict, verdict: str, ev: dict) -> bool:
    """True when abicheck is stricter than the oracle purely from scope, not error.

    A header-scoped oracle (ABICC / abi-laboratory) only counts changes to the
    *public-header* surface; a binary-only abicheck run, by deliberate policy,
    treats every exported symbol as ABI. So abicheck can correctly flag a real
    binary change (an exported-but-internal symbol removed, an internal data
    table resized, a dual-ABI ``std::string`` shift) that the oracle ignores.

    That is an *expected scope divergence*, not a false positive. We classify a
    pair as such only when all three hold:

    1. the oracle declared the pair COMPATIBLE, and independently reports no
       public symbol removed (``removed_symbols == 0``) at full backward compat
       (``backward_compat_pct`` 100 / unknown) — so it genuinely saw no public
       break;
    2. abicheck's verdict is BREAKING;
    3. *every* breaking finding abicheck emitted is symbol/toolchain-scope
       (``ev['scope_divergent']``, computed by the engine) rather than a
       type-level layout break.

    Condition 1 is the safety gate: it uses the oracle's own public-surface
    counts, so we never excuse a divergence on a pair where the oracle actually
    saw a public symbol change.
    """
    if pair.get("expected_verdict") != "COMPATIBLE":
        return False
    if _normalize_verdict(verdict) != "BREAKING":
        return False
    if not ev.get("scope_divergent", False):
        return False
    if pair.get("removed_symbols") != 0:
        return False
    bc = pair.get("backward_compat_pct")
    return bc is None or bc >= 100.0


def run_validation(pairs: list[dict], max_pairs: int, label: str) -> dict:
    """Fetch/extract/compare every pair and score abicheck against expectations.

    ``pairs`` are normalised source records (see the source adapters). Returns the
    comparison report (also written to ``tracker_parity/<label>.json``).
    """
    api_cache: dict[str, dict] = {}
    results: dict[str, str] = {}
    attempted: list[dict] = []
    evidence: dict[str, dict] = {}
    evidence_limited: list[str] = []
    scope_divergent: list[str] = []
    done = 0
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        for i, pair in enumerate(pairs):
            if max_pairs and done >= max_pairs:
                break
            attempted.append(pair)
            pkg = pair["pkg"]
            if pkg not in api_cache:
                try:
                    api_cache[pkg] = query_conda(pkg)
                except (OSError, json.JSONDecodeError) as exc:
                    print(
                        f"failed to query conda-forge for {pkg}: {exc}", file=sys.stderr
                    )
                    api_cache[pkg] = {}
            verdict = evaluate_pair(
                pair, api_cache[pkg], pair["subdir"], tmp, i, evidence
            )
            if verdict is None:
                continue
            done += 1
            ev = evidence.get(pair["pair"], {})
            if _is_evidence_limited(pair, verdict, ev):
                # Type-level-only oracle break, no DWARF in the binary: abicheck
                # can't see it, so a non-breaking verdict is not a false negative.
                # Leave it out of results -> scored UNCOMPARABLE, not ABICHECK_WEAKER.
                evidence_limited.append(pair["pair"])
                continue
            if _is_scope_divergence(pair, verdict, ev):
                # abicheck is stricter only because it scopes every exported
                # symbol as ABI while the header-scoped oracle does not. Not a
                # false positive -> exclude from scoring (UNCOMPARABLE), track
                # separately.
                scope_divergent.append(pair["pair"])
                continue
            results[pair["pair"]] = verdict

    # Score only the pairs actually attempted: with --max-pairs the loop stops
    # early, and pairs it never reached must not be reported as UNCOMPARABLE.
    # Without a limit, `attempted` is the full list, so full runs are unchanged.
    oracle_like = {
        "pairs": [
            {"pair": p["pair"], "expected_verdict": p["expected_verdict"]}
            for p in attempted
        ]
    }
    report = compare_to_results(oracle_like, results)
    report["ran_pairs"] = done
    report["evidence_limited"] = evidence_limited
    report["scope_divergent"] = scope_divergent
    PARITY_DIR.mkdir(parents=True, exist_ok=True)
    out = PARITY_DIR / f"{label}.json"
    out.write_text(json.dumps(report, indent=2) + "\n")

    c = report["counts"]
    rate = report["agreement_rate"]
    print(
        f"\n[{label}] ran {done} pairs | comparable={report['comparable_pairs']} "
        f"agreement={'n/a' if rate is None else f'{rate:.1%}'} "
        f"match={c['MATCH']} stricter={c['ABICHECK_STRICTER']} "
        f"weaker={c['ABICHECK_WEAKER']} "
        f"scope_divergent={len(scope_divergent)} "
        f"evidence_limited={len(evidence_limited)} -> {out}"
    )
    for pid in scope_divergent:
        print(f"  SCOPE-DIVERGENCE (expected, not FP): {pid}")
    for row in report["rows"]:
        if row["status"] == "ABICHECK_WEAKER":
            print(
                f"  WEAKER (likely FN): {row['pair']} "
                f"expected={row['expected_verdict']} abicheck={row['abicheck_verdict']}"
            )
        elif row["status"] == "ABICHECK_STRICTER":
            print(
                f"  STRICTER (likely FP): {row['pair']} "
                f"expected={row['expected_verdict']} abicheck={row['abicheck_verdict']}"
            )
    return report


def main(argv: list[str] | None = None) -> int:
    """Score abicheck against a curated manifest or a harvested tracker oracle."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--source", required=True, choices=("tracker", "manifest"))
    ap.add_argument("--lib", help="tracker: library slug (required); manifest: filter")
    ap.add_argument("--pkg", help="conda package name when it differs from --lib")
    ap.add_argument(
        "--subdir", default="linux-64", help="conda subdir (default: linux-64)"
    )
    ap.add_argument("--max-pairs", type=int, default=0, help="limit pairs (0 = all)")
    ap.add_argument("-o", "--label", help="report label (default: lib / 'manifest')")
    args = ap.parse_args(argv)

    try:
        if args.source == "tracker":
            if not args.lib:
                ap.error("--source tracker requires --lib")
            pairs = tracker_pairs(args.lib, args.pkg, args.subdir)
            label = args.label or args.lib
        else:
            pairs = manifest_pairs(args.lib, args.subdir)
            label = args.label or (
                "manifest" if not args.lib else f"manifest-{args.lib}"
            )
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 1

    if not pairs:
        print("no pairs to evaluate", file=sys.stderr)
        return 1

    run_validation(pairs, args.max_pairs, label)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
