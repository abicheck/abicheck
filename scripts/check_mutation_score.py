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

"""Mutation-score gate — the direct measure of whether tests *verify* or merely
*execute* the detector core.

Coverage measures reach; a surviving mutant is a line that runs but is not
checked by any assertion. ``mutmut`` mutates the modules listed under
``[tool.mutmut]`` in pyproject.toml and re-runs the suite.

Three gates, in increasing order of how much they need a baseline:

``--diff-scoped``
    **Absolute, needs no baseline.** Any survivor in a function this branch
    touched fails. This is the gate that makes mutation testing useful on a
    PR: a global count can stay flat while a newly-weakened function quietly
    accumulates survivors and an unrelated module's improvement pays for it.

``--baseline-file`` (per-module)
    Survivors are attributed to their source module, and *each module* is
    compared to its own recorded number. A module going 3 -> 5 fails even if
    the repository total went down.

``--baseline`` / :data:`SURVIVOR_BASELINE` (global total)
    The original whole-repository drift check, kept for continuity.

Regardless of gate, an *unresolved* run (timeout / suspicious / no-tests /
segfault / interrupted) is a failed measurement, never a clean zero.

Usage::

    # CI (scheduled): full run, write/refresh the per-module baseline
    python scripts/check_mutation_score.py --run --write-baseline

    # CI (PR touching the detector core): only gate what this branch changed
    python scripts/check_mutation_score.py --run --diff-scoped --base-ref origin/main

    # Check an existing run's output without re-running mutmut
    python scripts/check_mutation_score.py --results-file mutmut-results.txt

Why the parsing is careful (a real defect this gate had): ``mutmut run``
re-renders its progress summary continuously, starting at ``🙁 0``, and those
renders survive into piped CI output. Reading the *first* ``🙁 <n>`` — which a
plain ``re.search`` over run-then-results text does — reported **0 survivors
for a run that genuinely had 4**, reproduced against mutmut 3.7.0. Had
``SURVIVOR_BASELINE`` been set to ``0`` as intended, the gate would have passed
green forever. Survivor counting now comes from the per-mutant ``mutmut
results`` listing, with ``mutants/mutmut-cicd-stats.json`` as the completeness
witness; see ``scripts/mutation_results.py``.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mutation_results import (  # noqa: E402
    MODULE_SCOPE,
    MutantRecord,
    count_unresolved,
    functions_covering_lines,
    load_cicd_stats,
    parse_mutant_records,
    parse_survivors,
    summary_run_is_complete,
    survivors_by_module,
)

__all__ = [
    "SURVIVOR_BASELINE",
    "count_unresolved",
    "load_baseline",
    "main",
    "parse_survivors",
    "render_baseline",
]

REPO_ROOT = Path(__file__).resolve().parent.parent

#: Global-total baseline, kept for continuity with the original gate. ``None``
#: means "not established" — report-only. The per-module baseline file
#: (``--baseline-file``) is the preferred gate; see the module docstring.
SURVIVOR_BASELINE: int | None = None

#: Default per-module baseline, committed alongside the code.
DEFAULT_BASELINE_FILE = REPO_ROOT / "mutation-baseline.json"

#: ``@@ -old,cnt +new,cnt @@`` — we only need the new-side range.
_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def _run_mutmut(cmd: list[str]) -> tuple[str, int]:
    """Run a mutmut subcommand, returning ``(output, returncode)``.

    The return code matters for ``mutmut run`` specifically, and only for
    telling "this run aborted" from "this run completed": verified against
    mutmut 3.7.0, a completed run exits **0 even when mutants survived**, and a
    configuration/collection abort exits nonzero. So a nonzero exit here is
    never "there are survivors" — it is "no measurement happened".

    That distinction is load-bearing once ``mutants/`` is a restored CI cache
    (see mutation.yml): a run that aborts leaves the *previous* commit's result
    database in place, which would otherwise satisfy the completeness witness
    and let the gate pass on stale results (Codex review).
    """
    proc = subprocess.run(  # noqa: S603 — fixed argv, no shell
        cmd, capture_output=True, text=True, timeout=7200
    )
    return proc.stdout + proc.stderr, proc.returncode


# ---------------------------------------------------------------------------
# Baseline file
# ---------------------------------------------------------------------------


def render_baseline(records: list[MutantRecord]) -> dict[str, object]:
    """Build the committed baseline document from a measurement.

    Surviving mutant *keys* are recorded for diagnostics only and are
    deliberately **not** gated on. mutmut numbers mutants per function
    (``…__mutmut_3``), so editing a function renumbers its keys — gating on key
    identity would fire on every ordinary refactor while proving nothing. The
    per-module *count* is the stable signal; the keys are there so a reviewer
    can see which mutants were accepted.
    """
    by_module = survivors_by_module(records)
    return {
        "_comment": (
            "Per-module surviving-mutant baseline. Regenerate with "
            "`python scripts/check_mutation_score.py --run --write-baseline`. "
            "'keys' is diagnostic only — the gate compares counts, because "
            "mutmut renumbers a function's mutants whenever it is edited."
        ),
        "total_survivors": sum(len(v) for v in by_module.values()),
        "modules": {
            module: {"survivors": len(keys), "keys": keys}
            for module, keys in by_module.items()
        },
    }


def load_baseline(path: Path) -> dict[str, int] | None:
    """Read ``{module_path: survivor_count}``, or ``None`` if absent/invalid."""
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    modules = doc.get("modules") if isinstance(doc, dict) else None
    if not isinstance(modules, dict):
        return None
    out: dict[str, int] = {}
    for module, entry in modules.items():
        if isinstance(entry, dict) and isinstance(entry.get("survivors"), int):
            out[module] = entry["survivors"]
        elif isinstance(entry, int):
            out[module] = entry
    return out


def check_per_module(
    records: list[MutantRecord], baseline: dict[str, int]
) -> list[str]:
    """Modules whose survivor count rose above their own recorded number."""
    current = {m: len(k) for m, k in survivors_by_module(records).items()}
    failures = []
    for module in sorted(set(current) | set(baseline)):
        now = current.get(module, 0)
        was = baseline.get(module, 0)
        if now > was:
            failures.append(f"  {module}: {was} -> {now} (+{now - was})")
    return failures


# ---------------------------------------------------------------------------
# Diff-scoped gate
# ---------------------------------------------------------------------------


def parse_changed_lines(diff_text: str) -> dict[str, set[int]]:
    """Map ``path -> changed line numbers`` from ``git diff --unified=0``."""
    changed: dict[str, set[int]] = {}
    current: str | None = None
    for line in diff_text.splitlines():
        if line.startswith("+++ "):
            target = line[4:].strip()
            # A deleted file's header is `+++ /dev/null`, which does not match
            # `+++ b/` — so `current` kept pointing at the *previous* file and
            # the deletion's `@@ -1,5 +0,0 @@` hunk was recorded against it
            # through the count == 0 branch below. The diff-scoped gate then
            # failed on a function the branch never touched (CodeRabbit
            # review). The old `/dev/null` guard could never fire: it ran
            # after the `b/` prefix had already been stripped.
            current = target[2:] if target.startswith("b/") else None
            continue
        if current is None:
            continue
        m = _HUNK.match(line)
        if m:
            start = int(m.group(1))
            count = int(m.group(2)) if m.group(2) is not None else 1
            if count:
                changed.setdefault(current, set()).update(range(start, start + count))
            else:
                # A pure deletion (`@@ -3,1 +3,0 @@`) contributes no new-side
                # line, but deleting a guard is one of the most direct ways to
                # weaken a detector — and skipping it made the diff-scoped gate
                # ignore every survivor in the function that lost the check
                # (Codex review). Git reports the deletion as having happened
                # *after* new-side line `start`, so attribute both `start` and
                # its successor, which is the surrounding function either way.
                anchor = max(1, start)
                changed.setdefault(current, set()).update({anchor, anchor + 1})
    return changed


def changed_functions(
    changed: dict[str, set[int]], repo_root: Path
) -> dict[str, set[str]]:
    """Map ``path -> qualnames of functions this diff touched``."""
    out: dict[str, set[str]] = {}
    for path, lines in changed.items():
        if not path.endswith(".py"):
            continue
        try:
            source = (repo_root / path).read_text(encoding="utf-8")
        except OSError:
            continue
        funcs = functions_covering_lines(source, lines)
        if funcs:
            out[path] = funcs
    return out


def check_diff_scoped(
    records: list[MutantRecord], touched: dict[str, set[str]]
) -> list[str]:
    """Survivors living in a function this branch changed.

    Absolute: there is no baseline to be under. If you edited a function and a
    mutation of it still passes the suite, the edit is not verified.
    """
    failures = []
    for r in sorted(records, key=lambda r: r.key):
        if not r.is_survivor:
            continue
        module_touched = touched.get(r.module_path, set())
        if MODULE_SCOPE in module_touched:
            failures.append(
                f"  {r.module_path}::{r.function}  [{r.key}]  (module-scope change)"
            )
        elif r.function in module_touched:
            failures.append(f"  {r.module_path}::{r.function}  [{r.key}]")
    return failures


# ---------------------------------------------------------------------------
# Measurement
# ---------------------------------------------------------------------------


def _gather(args: argparse.Namespace) -> tuple[str | None, dict[str, int] | None]:
    """Return ``(results_text, cicd_stats)``.

    ``mutmut run``'s own stdout is deliberately **not** folded into the text
    used for counting — see the module docstring. It is still executed (and its
    output shown) under ``--run``; only the *measurement* comes from ``mutmut
    results`` plus the exported stats.
    """
    if args.results_file:
        if args.results_file == "-":
            return sys.stdin.read(), load_cicd_stats(Path(args.mutants_dir))
        try:
            with open(args.results_file, encoding="utf-8") as fh:
                return fh.read(), load_cicd_stats(Path(args.mutants_dir))
        except OSError as e:
            print(f"ERROR: cannot read --results-file: {e}")
            return None, None

    if shutil.which("mutmut") is None:
        print("mutation-score: mutmut not installed, skipping")
        return None, None

    if args.run:
        print("mutation-score: running `mutmut run` (this is slow)…")
        run_out, run_rc = _run_mutmut(["mutmut", "run"])
        tail = "\n".join(run_out.splitlines()[-5:])
        print(f"mutation-score: mutmut run tail:\n{tail}")
        if run_rc != 0:
            # Fail here rather than reading results: with a restored cache the
            # results on disk may be a previous commit's, and they would look
            # perfectly complete.
            print(
                f"ERROR: `mutmut run` exited {run_rc} — the run aborted, so any "
                "results on disk are from an earlier run, not this one. Not "
                "reading them."
            )
            return None, None
        _run_mutmut(["mutmut", "export-cicd-stats"])
    results_out, results_rc = _run_mutmut(["mutmut", "results"])
    if results_rc != 0:
        # Its stderr would otherwise be parsed as "no per-mutant lines", and if
        # exported stats happen to exist with total > 0 the completeness check
        # passes and the unparseable output becomes zero survivors — passing
        # even an explicit zero baseline while the stats report survivors
        # (Codex review).
        print(
            f"ERROR: `mutmut results` exited {results_rc} — cannot read the "
            "per-mutant statuses, so no survivor count is trustworthy."
        )
        return None, None
    return results_out, load_cicd_stats(Path(args.mutants_dir))


def _run_reached_its_end(text: str, stats: dict[str, int] | None) -> bool:
    """Is there positive evidence the run *finished*, not merely that it ran?

    Two witnesses, both independent of the results text's own length: the
    exported stats (`total > 0` with a survivor count), and a completed
    progress render (`N/N`). A per-mutant listing is deliberately not one — it
    carries no counter, so a truncated capture reads exactly like a whole one.
    """
    if (
        stats is not None
        and stats.get("total", 0) > 0
        and isinstance(stats.get("survived"), int)
    ):
        return True
    return summary_run_is_complete(text)


def _measurement_is_complete(
    text: str, stats: dict[str, int] | None
) -> tuple[bool, str]:
    """Did we actually measure anything? ``(ok, reason_if_not)``.

    A perfect run prints no per-mutant lines at all, so "empty results" is only
    trustworthy when the exported stats prove mutants ran (``total > 0``).
    Without that witness, empty is unmeasurable — never zero.

    The stats witness needs ``survived`` as well as ``total``. The
    parsed-vs-exported cross-check below is conditional on that key, so a
    receipt carrying only ``total`` — a corrupt file, or a schema change in a
    permitted future ``mutmut>=3.7,<4`` — silently skipped the cross-check
    while still counting as proof the run finished; with an unrecognised
    results format alongside it, the survivor count then defaulted to zero and
    passed a zero baseline unvalidated (Codex review).
    """
    if (
        stats is not None
        and stats.get("total", 0) > 0
        and isinstance(stats.get("survived"), int)
    ):
        return True, ""
    if parse_mutant_records(text):
        return True, ""
    if parse_survivors(text) is not None and summary_run_is_complete(text):
        return True, ""
    return False, (
        "no per-mutant results, no mutants/mutmut-cicd-stats.json carrying "
        "total > 0 and a survived count, and no completed progress render "
        "(N/N) — cannot tell 'all mutants killed' from 'mutmut never ran' or "
        "was interrupted"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="Run `mutmut run` first.")
    parser.add_argument(
        "--results-file", help="Read results from a file ('-' for stdin)."
    )
    parser.add_argument(
        "--mutants-dir",
        default="mutants",
        help="Directory holding mutmut's artifacts (default: mutants).",
    )
    parser.add_argument(
        "--baseline", type=int, default=None, help="Global-total baseline override."
    )
    parser.add_argument(
        "--baseline-file",
        default=str(DEFAULT_BASELINE_FILE),
        help="Per-module baseline JSON (default: mutation-baseline.json).",
    )
    parser.add_argument(
        "--write-baseline",
        action="store_true",
        help="Write the measurement to --baseline-file instead of gating on it.",
    )
    parser.add_argument(
        "--diff-scoped",
        action="store_true",
        help="Fail on any survivor in a function changed vs --base-ref.",
    )
    parser.add_argument(
        "--base-ref", default="origin/main", help="Base ref for --diff-scoped."
    )
    parser.add_argument("--diff-file", help="Read the diff from a file instead of git.")
    parser.add_argument(
        "--require-baseline",
        action="store_true",
        help=(
            "Fail if no baseline is available. The scheduled drift lane uses "
            "this so it cannot silently degrade to report-only."
        ),
    )
    parser.add_argument("--json", help="Write a machine-readable receipt here.")
    args = parser.parse_args(argv)

    text, stats = _gather(args)
    if text is None:
        if args.run:
            print(
                "ERROR: --run requested but mutmut produced no output (not "
                "installed / could not start). Failing so the mutation gate is "
                "not a silent no-op."
            )
            return 1
        if args.results_file:
            # An explicitly named input that could not be read is a failed
            # invocation, not an optional one. Sharing the "no tool, nothing
            # to do" exit meant a scripted offline check reported success
            # having processed no results at all (Codex review) — the same
            # can't-fail shape as the --run branch above, reached from the
            # other direction.
            print(
                f"ERROR: --results-file {args.results_file} could not be read, "
                "so there are no mutation results to gate on."
            )
            return 1
        return 0

    complete, why = _measurement_is_complete(text, stats)
    if not complete:
        print(f"mutation-score: unmeasurable — {why}")
        # An explicitly named input counts the same as --run: the caller
        # supplied results and asked for a verdict on them, so "cannot tell"
        # is a failure, not a skip. Only a bare invocation — no run, no input
        # — has genuinely nothing to measure (Codex review).
        return 1 if (args.run or args.results_file) else 0

    records = parse_mutant_records(text)
    # Per-mutant listings are the strong source; a summary-only text (an older
    # capture, or a clean run whose only signal is the final render) still has
    # to yield a count, so fall back to the summary readers -- which take the
    # LAST render, never the first. `by_module` is necessarily empty in that
    # case: a summary carries no attribution, and inventing one would be worse
    # than reporting none.
    if records:
        survivors = sum(1 for r in records if r.is_survivor)
    else:
        survivors = parse_survivors(text) or 0
    unresolved = count_unresolved(text)
    by_module = survivors_by_module(records)

    # Two independent sources must agree. `mutmut results`' per-mutant listing
    # and `mutmut export-cicd-stats`' counters are produced by the same run, so
    # a disagreement means this parser no longer understands the output — which
    # a permitted `mutmut>=3.7,<4` update can cause. Without this check the
    # unparsed output degrades to zero survivors while the stats report real
    # ones, and `_measurement_is_complete` accepts the stats as proof the run
    # happened, so every gate passes (Codex review).
    # A summary-only text ("🙁 3") yields a survivor *count* with no
    # attribution: `by_module` is empty, so the per-module gate compares
    # nothing and reports success, and `--write-baseline` would record
    # `total_survivors: 0` — silently discarding every survivor and then
    # gating future runs against that fiction (Codex review). Counting is fine
    # for the global-total check; anything that needs to know *where* the
    # survivors are must refuse this input.
    if survivors and not records:
        needs_attribution = (
            args.write_baseline
            or args.diff_scoped
            or (load_baseline(Path(args.baseline_file)) is not None)
        )
        if needs_attribution:
            print(
                f"ERROR: {survivors} surviving mutant(s) reported, but the "
                "results carry no per-mutant listing to attribute them to a "
                "module or function. Per-module gating, --diff-scoped and "
                "--write-baseline all need that attribution; re-run with "
                "`mutmut results` output rather than a summary."
            )
            return 1

    if stats is not None and "survived" in stats and stats["survived"] != survivors:
        print(
            f"ERROR: parsed {survivors} surviving mutant(s) but "
            f"mutants/mutmut-cicd-stats.json reports {stats['survived']}. The "
            "two disagree, so this build's parser no longer matches mutmut's "
            "output — refusing to gate on either number. Check whether mutmut "
            "changed its `results` format."
        )
        return 1

    msg = f"mutation-score: {survivors} surviving mutant(s)"
    if stats:
        msg += f" of {stats.get('total', '?')} total"
    if unresolved:
        msg += f", {unresolved} unresolved (timeout/suspicious/no-tests/segfault)"
    print(msg)
    for module, keys in by_module.items():
        print(f"  {module}: {len(keys)}")

    exit_code = 0
    #: Did any check in this run actually have something to compare against?
    #: A run that gated nothing must never be reported as a pass.
    gated = True
    baseline_modules = load_baseline(Path(args.baseline_file))
    total_baseline = args.baseline if args.baseline is not None else SURVIVOR_BASELINE
    #: Is there a *drift* reference — the only thing that can answer "did the
    #: survivor set grow", for any function, changed or not?
    baseline_available = baseline_modules is not None or total_baseline is not None
    # "Report-only" means there is genuinely nothing to be measured against.
    # Once any gate is active, an unresolved run is a failed measurement.
    gating_active = args.diff_scoped or baseline_available

    if args.require_baseline and not baseline_available:
        # Deliberately keyed on the baseline, not on `gating_active`, even
        # though --diff-scoped is a real gate. It answers a different
        # question: only whether the functions *this branch changed* have
        # surviving mutants. A diff that changes one detector function and
        # weakens a test covering another leaves the second one's new
        # survivors entirely outside its scope, and counting --diff-scoped as
        # satisfying --require-baseline let exactly that exit 0 (Codex
        # review). Without this, a "baseline drift" lane with no baseline
        # returns 0 no matter how many mutants survive — the can't-fail shape
        # this whole gate exists to remove.
        print(
            "ERROR: --require-baseline was passed but no baseline is available "
            f"({args.baseline_file} is missing/invalid and SURVIVOR_BASELINE is "
            "unset), so nothing in this run can answer whether the survivor set "
            "grew. --diff-scoped does not substitute: it only looks at the "
            "functions this branch changed. Establish the baseline once with "
            "the workflow_dispatch lane (write_baseline: true), then re-enable "
            "this lane."
        )
        return 1

    if unresolved and (gating_active or args.write_baseline):
        print(
            f"ERROR: {unresolved} mutant(s) did not resolve (timeout/suspicious/"
            "no-tests/segfault) — the measurement is incomplete; fix or silence "
            "them so the survivor count is trustworthy."
        )
        exit_code = 1
        if args.write_baseline:
            # Never bake an under-resolved run into the committed baseline: the
            # recorded number would understate the real survivor set and every
            # later run would gate against a fiction.
            print(
                "ERROR: refusing to write a baseline from an unresolved run "
                f"({args.baseline_file} left untouched)."
            )
            return 1

    if args.write_baseline and not _run_reached_its_end(text, stats):
        # `mutmut results` prints a plain listing with no progress counter, so
        # a capture truncated after a few lines is indistinguishable from a
        # complete one by its own content. That is tolerable for gating (a
        # short capture can only under-report, and the run that produced it is
        # the caller's own), but not for *recording* — a partial survivor set
        # written as the baseline becomes the thing every later run is scored
        # against, and the loss is silent and permanent (Codex review).
        print(
            "ERROR: refusing to write a baseline from a capture that cannot "
            "prove it is complete. Record it from a real run (which exports "
            "mutants/mutmut-cicd-stats.json), not from a saved `mutmut "
            "results` listing."
        )
        return 1

    if args.write_baseline:
        doc = render_baseline(records)
        Path(args.baseline_file).write_text(
            json.dumps(doc, indent=2, sort_keys=False) + "\n", encoding="utf-8"
        )
        print(f"mutation-score: wrote baseline to {args.baseline_file}")
        return exit_code

    if args.diff_scoped:
        if args.diff_file:
            diff_text = Path(args.diff_file).read_text(encoding="utf-8")
        else:
            proc = subprocess.run(  # noqa: S603 — fixed argv, no shell
                ["git", "diff", "--unified=0", f"{args.base_ref}...HEAD"],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                timeout=300,
            )
            if proc.returncode != 0:
                # Without this the fatal stderr is parsed as a diff, yielding
                # zero changed functions — so the diff-scoped gate reports OK
                # with survivors present. A typo in --base-ref silently
                # disables the gate (reproduced with `--base-ref
                # does-not-exist`, Codex review).
                print(
                    f"ERROR: `git diff {args.base_ref}...HEAD` failed "
                    f"(exit {proc.returncode}): {proc.stderr.strip()}\n"
                    "Cannot determine which functions this branch changed, so "
                    "the diff-scoped gate would pass vacuously."
                )
                return 1
            diff_text = proc.stdout
        touched = changed_functions(parse_changed_lines(diff_text), REPO_ROOT)
        n_funcs = sum(len(v) for v in touched.values())
        print(f"mutation-score: diff-scoped over {n_funcs} changed function(s)")
        failures = check_diff_scoped(records, touched)
        if failures:
            print(
                "ERROR: surviving mutants in functions this branch changed — a "
                "mutation of code you just edited still passes the suite, so "
                "the edit is executed but not verified:"
            )
            print("\n".join(failures))
            exit_code = 1
        elif n_funcs == 0 and baseline_modules is None:
            # The test-only diff. This gate is attribution-based, so a branch
            # that weakens assertions without touching a production function
            # gives it nothing to scope to — and "OK" for a run that examined
            # zero functions reads as a pass it never made (Codex review). The
            # survivors such a branch creates are only visible as *drift*,
            # which needs the baseline file.
            gated = False
            print(
                "mutation-score: diff-scoped examined 0 changed functions and "
                f"there is no per-module baseline at {args.baseline_file}, so "
                "this run GATED NOTHING. A test-only change can only be "
                "checked as drift; record the baseline once with the "
                "workflow_dispatch lane (write_baseline: true)."
            )
        else:
            print("mutation-score: diff-scoped OK (no survivors in changed functions)")

    if baseline_modules is not None:
        failures = check_per_module(records, baseline_modules)
        if failures:
            print(
                "ERROR: per-module survivor count rose above baseline — a test "
                "was weakened or new under-verified code landed:"
            )
            print("\n".join(failures))
            exit_code = 1
        else:
            print("mutation-score: per-module baseline OK")
    else:
        print(
            f"mutation-score: no per-module baseline at {args.baseline_file} — "
            "run with --write-baseline to establish it."
        )

    if total_baseline is None:
        print(
            "mutation-score: global-total baseline not set — report-only. The "
            "per-module baseline file is the preferred gate."
        )
    elif survivors > total_baseline:
        print(f"ERROR: surviving mutants {survivors} exceed baseline {total_baseline}.")
        exit_code = 1
    elif survivors < total_baseline:
        print(
            f"mutation-score: {survivors} < baseline {total_baseline} — please "
            "lower SURVIVOR_BASELINE to lock in the improvement."
        )
    else:
        print(f"mutation-score: OK ({survivors} == baseline {total_baseline})")

    if args.require_baseline and not gated:
        print(
            "ERROR: --require-baseline was passed but this run had nothing to "
            "gate against (no changed production function, no baseline)."
        )
        exit_code = 1

    if args.json:
        Path(args.json).write_text(
            json.dumps(
                {
                    "survivors": survivors,
                    "unresolved": unresolved,
                    "stats": stats,
                    "by_module": by_module,
                    "gated": gated,
                    "exit_code": exit_code,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
