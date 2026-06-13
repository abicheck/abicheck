#!/usr/bin/env python3
"""Timing harness: run a command, record wall-time + peak child RSS, append to ledger.

Usage: python runt.py <label> -- cmd arg arg ...
"""
from __future__ import annotations
import sys, time, json, resource, subprocess, os

LEDGER = "/tmp/scan/timing.jsonl"

def main():
    label = sys.argv[1]
    assert sys.argv[2] == "--", "need -- before command"
    cmd = sys.argv[3:]
    t0 = time.time()
    r0 = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    p = subprocess.run(cmd, capture_output=True, text=True)
    wall = time.time() - t0
    r1 = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    # ru_maxrss is the high-water mark for ALL children; approximate this cmd's peak
    peak_kb = max(r1, r0)
    rec = {"label": label, "cmd": " ".join(os.path.basename(c) if i == 0 else c
                                            for i, c in enumerate(cmd)),
           "wall_s": round(wall, 3), "rc": p.returncode,
           "peak_rss_mb": round(peak_kb / 1024, 1)}
    with open(LEDGER, "a") as f:
        f.write(json.dumps(rec) + "\n")
    sys.stderr.write(p.stderr)
    sys.stdout.write(p.stdout)
    print(f"[runt] {label}: {wall:.3f}s rc={p.returncode}", file=sys.stderr)
    sys.exit(p.returncode)

if __name__ == "__main__":
    main()
