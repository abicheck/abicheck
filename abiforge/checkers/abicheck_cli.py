"""abicheck's CLI as a peer of the other tools: ``abicheck compare`` on the
same library files, with the same public headers, into a JSON report.

The in-process adapter (``checkers.abicheck``) serves the corpus; this one
runs the public command a maintainer runs, so its exit status, wall time and
raw report are recorded exactly like ``abidiff``'s and ABICC's.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from abiforge.checkers.tools import ToolRun, run_tool

ABICHECK = (sys.executable, "-m", "abicheck")


def run(
    old_so: Path,
    new_so: Path,
    out: Path,
    *,
    headers: tuple[list[Path], list[Path]] | None,
    includes: tuple[Path, Path] | None = None,
    config: Path | None = None,
    timeout: float = 300.0,
    stem: str = "abicheck",
) -> ToolRun:
    tr = ToolRun(mode="abicheck")
    rep = out / f"{stem}.json"
    argv = [*ABICHECK, "compare", str(old_so), str(new_so)]
    if config is not None:
        argv += ["--config", str(config)]
    if headers is not None:
        for side, hs in (("old", headers[0]), ("new", headers[1])):
            argv += [a for h in hs for a in ("--header", f"{side}={h}")]
    if includes is not None:
        argv += ["--include", f"old={includes[0]}", "--include", f"new={includes[1]}"]
    argv += ["-o", f"json={rep}"]
    tr.steps.append(run_tool(argv, out / f"{stem}.log", timeout=timeout))
    if rep.exists():
        tr.reports["report"] = rep.name
    return tr


def parse(text: str) -> dict:  # type: ignore[type-arg]
    rep = json.loads(text)
    if not isinstance(rep.get("changes"), list) or "verdict" not in rep:
        raise ValueError("not an abicheck JSON report")
    return rep


#: abicheck exit statuses that are an answer (0 compatible, 2 source break,
#: 4 ABI break; 1 a raised assurance axis). Anything else is an error.
ANSWER_EXITS = frozenset({0, 1, 2, 4})
