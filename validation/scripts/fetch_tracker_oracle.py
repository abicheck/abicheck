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

"""Harvest ABI-tracker verdicts from abi-laboratory.pro as a labelled oracle.

abi-laboratory.pro/tracker (run by Andrey Ponomarenko, the author of
abi-compliance-checker) publishes, for ~800 open-source C/C++ libraries, a
per-version-pair backward-compatibility verdict computed by ABICC. That is a
large, pre-labelled corpus of *real-world* ABI outcomes from the canonical tool
abicheck replaces — an independent ground-truth oracle.

This harness turns a library's published timeline into an abicheck-consumable
oracle:

  1. fetch the timeline page (``?view=timeline&l=<lib>``),
  2. parse each release row (version, date, soname, backward-compat %, added /
     removed symbol counts),
  3. derive an *expected* abicheck verdict (COMPATIBLE / BREAKING) for each
     consecutive version pair,
  4. write ``validation/data/tracker_oracle/<lib>.json``.

The derived pairs slot straight into the existing real-world validation flow
(``run_matrix.py``): fetch binaries for those versions, run abicheck, and check
agreement against this oracle. No ABI dumps are downloaded or redistributed —
only the published verdicts are read — which keeps the licensing surface to
"reading a public web page".

Parsing is split into pure functions (``parse_timeline`` / ``derive_verdict`` /
``build_oracle``) so they can be unit-tested offline against a saved HTML
fixture; only ``main`` touches the network.

Usage:
    python validation/scripts/fetch_tracker_oracle.py zstd libxml2 openssl
    python validation/scripts/fetch_tracker_oracle.py --from-file zstd.html zstd
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse
import urllib.request
from html import unescape
from pathlib import Path

TRACKER_BASE = "https://abi-laboratory.pro/index.php"
USER_AGENT = "abicheck-tracker-oracle/1.0 (+https://github.com/napetrov/abicheck)"


def timeline_url(library: str) -> str:
    """Build the timeline URL, URL-encoding the library so a name containing
    ``&``/``=``/``#`` can't break or inject into the query string."""
    return f"{TRACKER_BASE}?view=timeline&l={urllib.parse.quote(library, safe='')}"


VALID_DIR = Path(__file__).resolve().parent.parent  # validation/
ORACLE_DIR = VALID_DIR / "data" / "tracker_oracle"


def _strip_tags(html: str) -> str:
    """Collapse a cell's inner HTML to its visible text."""
    return unescape(re.sub(r"<[^>]+>", "", html)).strip()


def _parse_count(cell_text: str) -> int:
    """Extract the leading integer from a symbol-count cell (``4 new`` -> 4)."""
    m = re.search(r"\d+", cell_text.replace(",", ""))
    return int(m.group()) if m else 0


def _parse_percent(cell_text: str) -> float | None:
    """Parse a backward-compat cell (``96.9%``) to a float, or None if absent."""
    m = re.search(r"(\d+(?:\.\d+)?)\s*%", cell_text)
    return float(m.group(1)) if m else None


def _header_columns(html: str) -> dict[str, int] | None:
    """Map BackwardCompat./Added/Removed (+ Version/Date/Soname) to column indices.

    Locating cells by header label keeps parsing correct when the tracker emits
    optional columns — ChangeLog, a separate *Source* compatibility cell, or a
    trailing *Total Changes* count — that shift positions. The binary
    backward-compat figure is matched on ``backward`` specifically (never the
    source-compat cell), and the count columns by their ``Added``/``Removed``
    prefixes, so extra columns are ignored rather than mis-read.

    Returns ``None`` if no header row carrying all three metric columns is found
    (the caller then falls back to a fixed layout).
    """
    for m in re.finditer(r"<tr[^>]*>(.*?)</tr>", html, re.S):
        headers = re.findall(r"<th[^>]*>(.*?)</th>", m.group(1), re.S)
        if not headers:
            continue
        col: dict[str, int] = {}
        for i, raw in enumerate(headers):
            label = re.sub(r"[^a-z0-9]", "", _strip_tags(raw).lower())
            if label == "version":
                col["version"] = i
            elif label == "date":
                col["date"] = i
            elif label == "soname":
                col["soname"] = i
            elif "backward" in label:  # binary BackwardCompat., not SourceCompat.
                col["backward_compat"] = i
            elif label.startswith("added"):
                col["added"] = i
            elif label.startswith("removed"):
                col["removed"] = i
        if {"backward_compat", "added", "removed"} <= col.keys():
            return col
    return None


def parse_timeline(html: str) -> list[dict[str, object]]:
    """Parse an abi-laboratory timeline page into per-release rows.

    Each returned row carries the raw cells for one release:
    ``version``, ``date``, ``soname``, ``backward_compat`` (percent or None),
    ``added``, ``removed``. The backward-compat figure compares the *previous*
    tracked release to this one (that is how the tracker lays the table out).
    Rows are returned newest-first, exactly as published.
    """
    rows: list[dict[str, object]] = []
    # Data rows are anchored by an id like <tr id='v1.5.4'>. Prefer locating the
    # metric cells by header label (``_header_columns``) so optional columns —
    # ChangeLog, a separate Source-Compatibility cell, or a trailing
    # Total-Changes count — don't shift the binary BackwardCompat./Added/Removed
    # values we score. Fall back to the fixed layout (Version|Date|Soname|...|
    # BackwardCompat.|Added|Removed, metrics last) only when no header is found.
    col = _header_columns(html)
    for m in re.finditer(r"<tr[^>]*\bid='v([^']+)'[^>]*>(.*?)</tr>", html, re.S):
        version = m.group(1)
        text = [
            _strip_tags(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", m.group(2), re.S)
        ]
        if col is not None and len(text) > max(col.values()):
            date = (
                text[col["date"]]
                if "date" in col
                else (text[1] if len(text) > 1 else "")
            )
            soname = (
                text[col["soname"]]
                if "soname" in col
                else (text[2] if len(text) > 2 else "")
            )
            bc, added, removed = (
                text[col["backward_compat"]],
                text[col["added"]],
                text[col["removed"]],
            )
        elif len(text) >= 6:
            date, soname = text[1], text[2]
            bc, added, removed = text[-3], text[-2], text[-1]
        else:
            continue
        rows.append(
            {
                "version": version,
                "date": date,
                "soname": soname,
                "backward_compat": _parse_percent(bc),
                "added": _parse_count(added),
                "removed": _parse_count(removed),
            }
        )
    return rows


def derive_verdict(backward_compat: float | None, removed: int) -> str:
    """Map a tracker row to an expected abicheck verdict.

    - any removed symbol, or backward-compat below 100% -> ``BREAKING``
      (ABICC found a binary-incompatible change),
    - otherwise (100% backward-compatible, no removals) -> ``COMPATIBLE``.

    Added-only changes stay COMPATIBLE: new symbols do not break existing
    binaries. ``None`` backward-compat (tracker did not compute a figure, e.g.
    first release or a skipped pair) yields ``UNKNOWN`` and should be excluded
    from agreement scoring.

    A SONAME change is **not** treated as breaking here: abicheck's policy
    classifies a SONAME-only bump as ``COMPATIBLE_WITH_RISK`` (it is the correct
    mitigation, not a break — see ``validation/realworld-scan-coverage-2026-06.md``),
    which normalizes to COMPATIBLE. Forcing such pairs to BREAKING would flag
    every correctly-handled major bump as a false ``ABICHECK_WEAKER``. The SONAME
    change is still recorded in the pair metadata for context.
    """
    if backward_compat is None:
        return "UNKNOWN"
    if removed > 0 or backward_compat < 100.0:
        return "BREAKING"
    return "COMPATIBLE"


def build_oracle(library: str, html: str) -> dict[str, object]:
    """Turn timeline HTML into an oracle document of consecutive version pairs.

    Rows are published newest-first; we walk them oldest-first so each pair is
    ``(older -> newer)`` with the newer row's backward-compat figure (which the
    tracker computes against its immediate predecessor).
    """
    rows = parse_timeline(html)
    chron = list(reversed(rows))  # oldest -> newest
    pairs: list[dict[str, object]] = []
    for prev, cur in zip(chron, chron[1:]):
        soname_changed = prev["soname"] != cur["soname"]
        verdict = derive_verdict(cur["backward_compat"], int(cur["removed"]))
        pairs.append(
            {
                "pair": f"{library}_{prev['version']}_to_{cur['version']}",
                "library": library,
                "old_ver": prev["version"],
                "new_ver": cur["version"],
                "soname_old": prev["soname"],
                "soname_new": cur["soname"],
                "soname_changed": soname_changed,
                "backward_compat_pct": cur["backward_compat"],
                "added_symbols": cur["added"],
                "removed_symbols": cur["removed"],
                "expected_verdict": verdict,
                "source": "abi-laboratory.pro",
            }
        )
    return {
        "library": library,
        "tracker_url": timeline_url(library),
        "release_count": len(rows),
        "pair_count": len(pairs),
        "pairs": pairs,
    }


# abicheck / ABICC verdicts collapse to a binary backward-compat axis so they
# can be scored against the tracker's backward-compat figure.
_BREAKING_VERDICTS = {"BREAKING", "API_BREAK"}
_COMPATIBLE_VERDICTS = {"COMPATIBLE", "COMPATIBLE_WITH_RISK", "NO_CHANGE"}


def _normalize_verdict(verdict: str) -> str:
    """Collapse an abicheck verdict to the oracle's COMPATIBLE/BREAKING axis."""
    v = (verdict or "").strip().upper()
    if v in _BREAKING_VERDICTS:
        return "BREAKING"
    if v in _COMPATIBLE_VERDICTS:
        return "COMPATIBLE"
    return "UNKNOWN"


def compare_to_results(
    oracle: dict[str, object], results: dict[str, str]
) -> dict[str, object]:
    """Score abicheck verdicts against the tracker oracle.

    ``results`` maps a pair id (``<lib>_<v1>_to_<v2>``) to an abicheck verdict
    string. Each scored pair gets a status:

    - ``MATCH`` — abicheck and the tracker agree,
    - ``ABICHECK_STRICTER`` — abicheck flags BREAKING where the tracker says
      COMPATIBLE (often legitimate: ABICC has documented blind spots),
    - ``ABICHECK_WEAKER`` — abicheck says COMPATIBLE where the tracker found a
      break (a real divergence worth investigating — a likely false negative),
    - ``UNCOMPARABLE`` — either side has no clear verdict, or abicheck has no
      result for this pair (excluded from the agreement rate).

    Returns the per-pair rows plus a summary. The agreement rate is computed
    over comparable pairs only.
    """
    rows: list[dict[str, object]] = []
    counts = {
        "MATCH": 0,
        "ABICHECK_STRICTER": 0,
        "ABICHECK_WEAKER": 0,
        "UNCOMPARABLE": 0,
    }
    for pair in oracle.get("pairs", []):  # type: ignore[union-attr]
        pid = str(pair["pair"])
        expected = str(pair["expected_verdict"])
        actual = (
            _normalize_verdict(results.get(pid, "")) if pid in results else "MISSING"
        )
        if expected == "UNKNOWN" or actual in ("UNKNOWN", "MISSING"):
            status = "UNCOMPARABLE"
        elif expected == actual:
            status = "MATCH"
        elif actual == "BREAKING" and expected == "COMPATIBLE":
            status = "ABICHECK_STRICTER"
        else:
            status = "ABICHECK_WEAKER"
        counts[status] += 1
        rows.append(
            {
                "pair": pid,
                "expected_verdict": expected,
                "abicheck_verdict": results.get(pid),
                "status": status,
            }
        )
    comparable = (
        counts["MATCH"] + counts["ABICHECK_STRICTER"] + counts["ABICHECK_WEAKER"]
    )
    return {
        "library": oracle.get("library"),
        "comparable_pairs": comparable,
        "agreement_rate": (counts["MATCH"] / comparable) if comparable else None,
        "counts": counts,
        "rows": rows,
    }


def fetch_timeline(library: str, timeout: float = 30.0) -> str:
    """Fetch the raw timeline HTML for a library (the only network touchpoint)."""
    url = timeline_url(library)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (fixed https host)
        return resp.read().decode("utf-8", errors="replace")


def main(argv: list[str] | None = None) -> int:
    """CLI entry: harvest oracles for the given libraries, or score with --compare."""
    ap = argparse.ArgumentParser(
        description="Harvest abi-laboratory.pro verdicts as an abicheck oracle."
    )
    ap.add_argument(
        "libraries", nargs="+", help="tracker library slugs, e.g. zstd libxml2 openssl"
    )
    ap.add_argument(
        "--from-file",
        help="parse this local HTML file instead of fetching (single library)",
    )
    ap.add_argument(
        "--out-dir",
        default=str(ORACLE_DIR),
        help="output directory for <lib>.json oracles",
    )
    ap.add_argument(
        "--compare",
        metavar="RESULTS_JSON",
        help="score abicheck results against the oracle instead of fetching. "
        "RESULTS_JSON maps pair ids to abicheck verdicts, or is a run_matrix-style "
        "results list (objects with 'pair'/'tag' and 'verdict'). Pass one library.",
    )
    args = ap.parse_args(argv)

    if args.from_file and len(args.libraries) != 1:
        # --from-file points at one HTML page; harvesting many libs from it would
        # write each <lib>.json from the same parsed timeline (silently wrong).
        print("--from-file takes exactly one library", file=sys.stderr)
        return 2

    if args.compare:
        return _run_compare(
            args.libraries, args.from_file, Path(args.out_dir), args.compare
        )

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    rc = 0
    for lib in args.libraries:
        try:
            html = (
                Path(args.from_file).read_text()
                if args.from_file
                else fetch_timeline(lib)
            )
        except OSError as exc:  # URLError/HTTPError/timeout/file-read are all OSError
            print(f"[{lib}] fetch failed: {exc}", file=sys.stderr)
            rc = 1
            continue

        oracle = build_oracle(lib, html)
        if oracle["pair_count"] == 0:
            print(
                f"[{lib}] no version pairs parsed (unknown library or page layout changed)",
                file=sys.stderr,
            )
            rc = 1
            continue

        out = out_dir / f"{lib}.json"
        out.write_text(json.dumps(oracle, indent=2) + "\n")
        breaking = sum(
            1 for p in oracle["pairs"] if p["expected_verdict"] == "BREAKING"
        )
        compat = sum(
            1 for p in oracle["pairs"] if p["expected_verdict"] == "COMPATIBLE"
        )
        print(
            f"[{lib}] {oracle['pair_count']} pairs "
            f"({compat} COMPATIBLE, {breaking} BREAKING) -> {out}"
        )
    return rc


# Conservative aggregation rank: when several records collapse to one pair id
# (e.g. a package with multiple shared objects), the most-breaking verdict wins
# so a per-.so break can't be masked by a sibling's COMPATIBLE result.
_SEVERITY_RANK = {"BREAKING": 2, "UNKNOWN": 1, "COMPATIBLE": 0}


def _more_severe(a: str, b: str) -> str:
    """Return whichever raw verdict normalizes to the more-breaking outcome."""
    return (
        a
        if _SEVERITY_RANK[_normalize_verdict(a)]
        >= _SEVERITY_RANK[_normalize_verdict(b)]
        else b
    )


def load_results_map(raw: object) -> dict[str, str]:
    """Coerce a results file into a ``pair id -> verdict`` map.

    Accepts either a plain ``{pair: verdict}`` object or a run_matrix-style list
    of records (each with ``pair``/``tag`` and ``verdict``). When multiple list
    records share a pair id (run_matrix emits one record per shared object), the
    verdicts are aggregated conservatively via :func:`_more_severe` so a BREAKING
    result is never silently overwritten by a later COMPATIBLE one.
    """
    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items() if v is not None}
    out: dict[str, str] = {}
    if isinstance(raw, list):
        for rec in raw:
            if not isinstance(rec, dict):
                continue
            pid = rec.get("pair") or rec.get("tag")
            verdict = rec.get("verdict")
            if pid and verdict:
                key, val = str(pid), str(verdict)
                out[key] = _more_severe(out[key], val) if key in out else val
    return out


def _run_compare(
    libraries: list[str], from_file: str | None, out_dir: Path, results_path: str
) -> int:
    """Load the oracle and abicheck results, score agreement, print the report."""
    if len(libraries) != 1:
        print("--compare takes exactly one library", file=sys.stderr)
        return 2
    lib = libraries[0]
    oracle_path = out_dir / f"{lib}.json"
    try:
        if from_file:
            oracle = build_oracle(lib, Path(from_file).read_text())
        elif oracle_path.is_file():
            oracle = json.loads(oracle_path.read_text())
        else:
            print(
                f"no oracle for {lib}: run the fetch step first or pass --from-file",
                file=sys.stderr,
            )
            return 1
        results = load_results_map(json.loads(Path(results_path).read_text()))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[{lib}] compare failed reading inputs: {exc}", file=sys.stderr)
        return 1

    report = compare_to_results(oracle, results)
    c = report["counts"]
    rate = report["agreement_rate"]
    print(
        f"[{lib}] comparable={report['comparable_pairs']} "
        f"agreement={'n/a' if rate is None else f'{rate:.1%}'} "
        f"match={c['MATCH']} stricter={c['ABICHECK_STRICTER']} "
        f"weaker={c['ABICHECK_WEAKER']} uncomparable={c['UNCOMPARABLE']}"
    )
    for row in report["rows"]:
        if row["status"] == "ABICHECK_WEAKER":
            print(
                f"  WEAKER (likely FN): {row['pair']} "
                f"oracle={row['expected_verdict']} abicheck={row['abicheck_verdict']}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
