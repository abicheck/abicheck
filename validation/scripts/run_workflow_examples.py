#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Execute every curated workflow example end to end.

Phase 5 of the examples/catalog split
(docs/contribute/plans/examples-catalog-split.md). `examples/workflows/<id>/`
is a task-oriented walkthrough -- a small project plus the real `abicheck`
invocation a user would run. Nothing executed those commands before this
runner: workflow coverage was a count of subdirectories, so an empty or
broken directory raised it, and the documented commands could rot while
every calibration-catalog gate stayed green.

Each workflow's `workflow.yaml` (see `scripts/workflow_examples.py` for the
schema) states the commands, the expected exit code, and the expected
verdict/change kinds. This runner copies the workflow to a scratch
directory -- so a build never dirties the checkout, and a workflow that
writes outside its own tree is caught -- runs each documented command
through a shell, and checks:

  * the exit code the walkthrough claims;
  * substrings the walkthrough shows in its own output;
  * the verdict and change kinds, structurally, by re-running the identical
    command with `--format json` appended (`json_variant`), rather than by
    grepping prose;
  * that the source tree is byte-identical afterwards.

`--json <path>` writes a machine-readable receipt. A workflow whose
`platforms`/`requires` the host does not satisfy is reported as a skip, and
`--require <id>` turns a specific skip into a failure so a CI lane that
just installed a toolchain cannot silently pass having run nothing (the
same silent-skip failure mode `tests/conftest.py`'s `ABICHECK_MIN_EXECUTED`
guard exists for).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parents[2]
if str(REPO_DIR / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO_DIR / "scripts"))
import workflow_examples  # noqa: E402

SCHEMA_VERSION = "workflow_examples.v1"

_PLATFORM_BY_SYS = {"linux": "linux", "darwin": "macos", "win32": "windows"}


def _host_platform() -> str:
    return _PLATFORM_BY_SYS.get(sys.platform, sys.platform)


def _missing_tools(workflow: workflow_examples.Workflow) -> list[str]:
    return [tool for tool in workflow.requires if shutil.which(tool) is None]


def _tree_signature(root: Path) -> dict[str, str]:
    """path -> content hash, for the untouched-source-tree check.

    Contents, not `st_size`: a workflow command that rewrites a checked-in
    file *in place without changing its length* leaves a size-keyed
    signature identical, so the runner would report success while the
    byte-identical-source-tree invariant it claims to enforce had been
    violated. Same-length rewrites are the ordinary shape of an in-place
    edit (a flag flipped, a version bumped, a name swapped).
    """
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def _run(
    argv: list[str] | tuple[str, ...], cwd: Path, timeout: int
) -> subprocess.CompletedProcess[str]:
    """Run one documented command with no shell involved.

    `workflow_examples` rejects any `run:` line carrying a shell
    metacharacter, so `shlex.split` reproduces exactly what a reader typing
    that line into their terminal would get -- without granting a committed
    manifest the injection surface `shell=True` would.
    """
    return subprocess.run(
        list(argv),
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        env={**os.environ, "PYTHONPATH": str(REPO_DIR)},
    )


def _check_json(payload: object, expected: dict[str, object]) -> list[str]:
    """Compare a report against the manifest's expectations.

    A list expectation is containment, not equality: a walkthrough names the
    finding it is teaching, and pinning the complete kind set would make
    every unrelated detector improvement fail an unrelated tutorial.
    """
    failures: list[str] = []
    if not isinstance(payload, dict):
        return ["report is not a JSON object"]
    for key, want in expected.items():
        got = payload.get(key)
        if isinstance(want, list):
            got_items = got if isinstance(got, list) else []
            missing = [item for item in want if item not in got_items]
            if missing:
                failures.append(f"{key}: missing {missing} (got {got_items})")
        elif got != want:
            failures.append(f"{key}: expected {want!r}, got {got!r}")
    return failures


def _change_kinds(report: dict) -> list[str]:
    """Every change kind a compare report names, whatever shape it uses."""
    kinds: list[str] = []
    for change in report.get("changes") or []:
        if isinstance(change, dict) and change.get("kind"):
            kinds.append(str(change["kind"]))
    return kinds


def check_json_variant(
    step,
    *,
    base_returncode: int,
    json_returncode: int,
    json_stdout: str,
    json_command: str,
) -> list[str]:
    """Check the machine-readable rerun of a documented command.

    The exit-code comparison is against the *plain* run rather than the
    step's declared expectation, because that is the real invariant: the
    JSON variant is the same command with only a format flag appended, so it
    must gate identically whatever the declared code is (and a step may
    declare none at all). Recording the code without checking it would let a
    regression that keeps the payload right but returns the wrong exit code
    pass -- and the machine-readable path is precisely the one a consumer's
    CI gates on.
    """
    failures: list[str] = []
    if json_returncode != base_returncode:
        failures.append(
            f"{json_command}: exit code {json_returncode}, but the same command "
            f"without {' '.join(step.json_variant)} exited {base_returncode} -- "
            "a format flag must not change gating"
        )
    try:
        payload = json.loads(json_stdout)
    except json.JSONDecodeError as exc:
        failures.append(f"{json_command}: output is not JSON ({exc})")
        return failures

    expected = dict(step.expect_json)
    want_kinds = expected.pop("change_kinds", None)
    failures.extend(_check_json(payload, expected))
    if want_kinds is not None and isinstance(payload, dict):
        got = _change_kinds(payload)
        missing_kinds = [k for k in want_kinds if k not in got]
        if missing_kinds:
            failures.append(f"change_kinds: missing {missing_kinds} (got {got})")
    return failures


def run_workflow(
    workflow: workflow_examples.Workflow, *, timeout: int
) -> dict[str, object]:
    result: dict[str, object] = {
        "id": workflow.id,
        "task": workflow.task,
        "status": "pass",
        "steps": [],
        "failures": [],
    }
    failures: list[str] = result["failures"]  # type: ignore[assignment]

    drift = workflow_examples.readme_drift(workflow)
    if drift:
        result["status"] = "fail"
        failures.extend(drift)
        return result

    host = _host_platform()
    if host not in workflow.platforms:
        result["status"] = "skip"
        result["reason"] = f"host platform {host} not in {list(workflow.platforms)}"
        return result
    missing = _missing_tools(workflow)
    if missing:
        result["status"] = "skip"
        result["reason"] = f"missing required tool(s): {missing}"
        return result

    with tempfile.TemporaryDirectory(prefix=f"abicheck-wf-{workflow.id}-") as tmp:
        scratch = Path(tmp) / workflow.id
        shutil.copytree(workflow.directory, scratch)
        before = _tree_signature(workflow.directory)

        for step in workflow.steps:
            started = time.monotonic()
            proc = _run(step.argv, scratch, timeout)
            record = {
                "name": step.name,
                "command": step.run,
                "exit_code": proc.returncode,
                "seconds": round(time.monotonic() - started, 3),
            }
            step_failures: list[str] = []
            if step.exit_code is not None and proc.returncode != step.exit_code:
                step_failures.append(
                    f"exit code {proc.returncode}, expected {step.exit_code}"
                )
            elif (
                step.exit_code is None
                and proc.returncode != 0
                and not step.allow_failure
            ):
                step_failures.append(f"exit code {proc.returncode}, expected 0")
            for needle in step.stdout_contains:
                if needle not in proc.stdout:
                    step_failures.append(f"stdout is missing {needle!r}")
            for needle in step.stdout_excludes:
                if needle in proc.stdout:
                    step_failures.append(f"stdout unexpectedly contains {needle!r}")

            if step.json_variant:
                json_argv = (*step.argv, *step.json_variant)
                json_command = " ".join(json_argv)
                json_proc = _run(json_argv, scratch, timeout)
                record["json_command"] = json_command
                record["json_exit_code"] = json_proc.returncode
                step_failures.extend(
                    check_json_variant(
                        step,
                        base_returncode=proc.returncode,
                        json_returncode=json_proc.returncode,
                        json_stdout=json_proc.stdout,
                        json_command=json_command,
                    )
                )

            if step_failures:
                record["failures"] = step_failures
                record["stdout_tail"] = proc.stdout[-2000:]
                record["stderr_tail"] = proc.stderr[-2000:]
                failures.extend(
                    f"{workflow.id}/{step.name}: {f}" for f in step_failures
                )
            result["steps"].append(record)  # type: ignore[union-attr]

        after = _tree_signature(workflow.directory)
        if before != after:
            failures.append(
                f"{workflow.id}: running the workflow modified the checked-in "
                "example tree (it must build only inside its own scratch copy)"
            )

    if failures:
        result["status"] = "fail"
    return result


def apply_required(
    results: list[dict[str, object]], required_ids: list[str]
) -> list[dict[str, object]]:
    """Promote a `--require`d workflow's skip to a real failure, in place.

    The promotion has to happen *on the result itself*, before anything is
    counted or serialized. Recording it as a detached entry instead left the
    JSON receipt reporting a plain skip while the console counted the same
    workflow as both failed and skipped -- with one workflow that printed
    "-1 passed, 1 failed, 1 skipped".
    """
    for required in required_ids:
        for result in results:
            if result["id"] != required or result["status"] != "skip":
                continue
            reason = result.get("reason", "skipped")
            result["status"] = "fail"
            result["required"] = True
            failures: list[str] = result.setdefault("failures", [])  # type: ignore[assignment]
            failures.append(f"{required}: required by --require but skipped ({reason})")
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workflows", nargs="*", help="workflow ids (default: all)")
    parser.add_argument("--json", type=Path, help="write a JSON receipt here")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument(
        "--require",
        action="append",
        default=[],
        metavar="ID",
        help=(
            "fail if this workflow is skipped -- for a CI lane that just "
            "installed the toolchain and must not pass having run nothing"
        ),
    )
    args = parser.parse_args(argv)

    try:
        workflows = workflow_examples.load_all()
    except workflow_examples.ManifestError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if args.workflows:
        wanted = set(args.workflows)
        unknown = sorted(wanted - {w.id for w in workflows})
        if unknown:
            print(f"ERROR: unknown workflow(s): {unknown}", file=sys.stderr)
            return 1
        workflows = [w for w in workflows if w.id in wanted]
    if not workflows:
        print("ERROR: no workflow examples found", file=sys.stderr)
        return 1

    # A `--require` id that names nothing in the selected set is a usage
    # error, not a no-op. Silently ignoring it defeats the flag's entire
    # purpose: a misspelling, a renamed workflow, or a positional selection
    # that excludes the required id would let a run where every workflow
    # skipped still exit 0 -- the zero-work pass `--require` exists to stop.
    unknown_required = sorted(set(args.require) - {w.id for w in workflows})
    if unknown_required:
        print(
            f"ERROR: --require names workflow(s) not in this run: {unknown_required}",
            file=sys.stderr,
        )
        return 1

    results = [run_workflow(w, timeout=args.timeout) for w in workflows]

    apply_required(results, args.require)

    failed = [r for r in results if r["status"] == "fail"]
    skipped = [r for r in results if r["status"] == "skip"]

    for result in results:
        marker = {"pass": "PASS", "fail": "FAIL", "skip": "SKIP"}[str(result["status"])]
        detail = result.get("reason") or result["task"]
        print(f"{marker}  {result['id']}: {detail}")
        for failure in result.get("failures") or []:
            print(f"        {failure}")

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(
                {
                    "schema_version": SCHEMA_VERSION,
                    "host_platform": _host_platform(),
                    "results": results,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    print(
        f"\n{len(results) - len(failed) - len(skipped)} passed, "
        f"{len(failed)} failed, {len(skipped)} skipped"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
