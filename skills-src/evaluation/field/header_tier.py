"""Header tier (L2: binary + public headers) for the field-evaluation runner (H4).

Non-gating, network-gated lane: for every manifest entry with a ``headers:``
block it fetches the runtime package *and* the package that ships its
``include/`` tree (the runtime package itself, or a ``-devel``/``-dev``/
``-headers``/``-static`` split — see :func:`condafetch.devel_candidates`) plus
any named dependency header packages, then runs today's CLI::

    abicheck compare OLD.so NEW.so --header old=… --header new=… \\
        -I old=… -I new=… -D NAME … -o json=-

Every failure (fetch, missing header package, header compile error) becomes a
row with ``error``; nothing here raises into the runner. The ``headers:`` block
(all keys optional except ``umbrella``)::

    headers:
      umbrella: [oneapi/tbb.h]   # public header(s), relative to the include root
      packages: [tbb-devel]      # header package(s); default: devel_candidates()
      defines: [PCRE2_CODE_UNIT_WIDTH=8]
      include: [glib-2.0]        # extra include dirs, relative to the include root
      deps:                      # dependency header packages (extra -I roots)
        - {pkg: libabseil, old: "20250127.1", new: "20250512.1"}
        - {pkg: glib, old: "2.86.4", new: "2.88.1",
           include: [lib/glib-2.0/include]}  # dirs relative to the package
                                              # root (no include/ required)
"""
from __future__ import annotations

import json
import os
import time

import condafetch as cf


def header_entries(manifest: dict, only: set[str] | None) -> list[dict]:
    """Manifest entries that carry a ``headers:`` block, filtered by ``--only``."""
    return [
        e for e in manifest["libraries"]
        if e.get("headers") and not (only and e["lib"] not in only)
    ]


def resolve_side(entry: dict, which: str) -> dict:
    """Fetch one side's header packages; return its header/include paths.

    ``{"package", "include_root", "headers": [...], "includes": [...]}`` — the
    umbrella headers resolved against the header package's ``include/``, and
    every extra ``-I`` root (the include root itself, ``include:`` subdirs,
    and each dependency package's ``include/``).
    """
    hb = entry["headers"]
    ver = str(entry[which])
    pkg, inc = cf.resolve_headers_pkg(entry["conda_pkg"], ver, hb.get("packages"))
    headers = []
    for rel in hb["umbrella"]:
        path = os.path.join(inc, rel)
        if not os.path.isfile(path):
            raise FileNotFoundError(f"umbrella {rel} not in {pkg} {ver} include/")
        headers.append(path)
    includes = [inc] + [os.path.join(inc, d) for d in hb.get("include", [])]
    for dep in hb.get("deps", []):
        dver = str(dep[which])
        if dep.get("include"):  # explicit dirs under the package root
            root = cf.fetch_extract(dep["pkg"], dver)
            includes.extend(os.path.join(root, d) for d in dep["include"])
            continue
        _, dinc = cf.resolve_headers_pkg(dep["pkg"], dver, [dep["pkg"]])
        includes.append(dinc)
    return {"package": pkg, "include_root": inc, "headers": headers, "includes": includes}


def compare_cmd(oso: str, nso: str, old: dict, new: dict, defines: list[str]) -> list[str]:
    """The ``abicheck compare`` argv for one L2 entry (pure)."""
    cmd = ["abicheck", "compare", oso, nso]
    for side, res in (("old", old), ("new", new)):
        cmd += [a for h in res["headers"] for a in ("--header", f"{side}={h}")]
        cmd += [a for i in res["includes"] for a in ("-I", f"{side}={i}")]
    for d in defines:
        cmd += ["-D", d]
    return cmd + ["-o", "json=-"]


def parse_compare(rc: int, stdout: str, stderr: str) -> dict:
    """Fold one ``compare -o json=-`` run into row fields (pure)."""
    rec: dict = {"exit": rc}
    try:
        d = json.loads(stdout) if stdout.strip() else None
    except json.JSONDecodeError:
        d = None
    if not isinstance(d, dict):
        rec["error"] = f"compare rc={rc}: " + (stderr or "").strip()[-240:]
        return rec
    rec["verdict"] = d.get("verdict")
    rec["evidence_tier"] = d.get("evidence_tier")
    aa = d.get("analysis_assurance") or {}
    rec["assurance"] = aa.get("status")
    rec["effective_depth"] = aa.get("effective_depth")
    rec["confidence"] = d.get("confidence")
    s = d.get("summary", {}) or {}
    for k in ("breaking", "source_breaks", "risk_changes", "compatible_additions", "total_changes"):
        rec[k] = s.get(k)
    return rec


def scan_header_one(entry: dict, pick_so, run) -> dict:
    """L2 scan of one entry; *pick_so*/*run* are the runner's own helpers."""
    hb = entry["headers"]
    rec: dict = {"lib": entry["lib"], "tier": "header",
                 "old": str(entry["old"]), "new": str(entry["new"]),
                 "expect": hb.get("expect")}
    try:
        t0 = time.time()
        oso = pick_so(entry["conda_pkg"], rec["old"], entry.get("so_stem"))
        nso = pick_so(entry["conda_pkg"], rec["new"], entry.get("so_stem"))
        old, new = resolve_side(entry, "old"), resolve_side(entry, "new")
        rec["fetch_s"] = round(time.time() - t0, 2)
        rec["header_pkg"] = new["package"]
        rec["umbrella"] = list(hb["umbrella"])
        tc, p = run(compare_cmd(oso, nso, old, new, list(hb.get("defines", []))))
        rec["compare_s"] = tc
        rec.update(parse_compare(p.returncode, p.stdout, p.stderr))
        if rec["expect"] is not None and "verdict" in rec:
            rec["verdict_matches_expected"] = rec["verdict"] == rec["expect"]
    except (Exception, SystemExit) as e:  # noqa: BLE001 - cf.pick raises SystemExit
        rec["error"] = f"{type(e).__name__}: {e}"
    return rec


def render_header_section(rows: list[dict]) -> list[str]:
    """REPORT.md table for the L2 header tier."""
    ok = [r for r in rows if "error" not in r]
    L = ["## Header tier (L2: binary + headers, non-gating)\n"]
    L.append(f"- entries: **{len(rows)}** | compared: **{len(ok)}**")
    L.append("")
    L.append("| lib | old→new | header pkg | umbrella | verdict | exit | assurance | evidence | break/src/risk | cmp s |")
    L.append("|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        if "error" in r:
            L.append(f"| {r['lib']} | {r['old']}→{r['new']} | | | ERROR | {r.get('exit', '')} "
                     f"| | | | {r['error'][:60]} |")
            continue
        exp = "" if r.get("verdict_matches_expected", True) else f" ✗({r.get('expect')})"
        L.append(
            f"| {r['lib']} | {r['old']}→{r['new']} | `{r.get('header_pkg')}` "
            f"| {', '.join(f'`{u}`' for u in r.get('umbrella', []))} | {r.get('verdict')}{exp} "
            f"| {r.get('exit')} | {r.get('assurance')} | {r.get('evidence_tier')} "
            f"| {r.get('breaking')}/{r.get('source_breaks')}/{r.get('risk_changes')} "
            f"| {r.get('compare_s')} |")
    L.append("")
    L.append("> `abicheck compare OLD.so NEW.so --header … -I … -D …` per entry's manifest "
             "`headers:` block. Exit is the compare exit code. Reproduce: "
             "`python skills-src/evaluation/field/runner.py --tier header`.")
    L.append("")
    return L


def merge_into(prior: dict, header_payload: dict) -> dict:
    """*prior* results with *header_payload*'s ``header_results`` folded in (pure).

    Keeps every other tier's rows and the prior run's metadata, recording the
    header run's own timestamp/version under ``header_run``.
    """
    merged = dict(prior)
    merged["header_results"] = header_payload.get("header_results", [])
    merged["header_run"] = {k: header_payload.get(k) for k in ("generated_utc", "abicheck_version")}
    return merged
