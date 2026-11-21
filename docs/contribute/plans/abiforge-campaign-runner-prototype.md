# Campaign manifest runner prototype

The runner implements the local plan, execute, resume, and completeness-audit
portion of the `campaign roadmap`.
It uses Python 3.11+ and the standard library on POSIX. It runs serially and
operates independently of abiforge and its CellRecord inventory.

## Reproduce

From the repository root, use a fresh directory so the demo executes on the
local Python interpreter and source bytes.

```sh
runner=research/campaign-runner
work=$(mktemp -d)
python3 -B "$runner/campaign.py" plan "$runner/demo-spec.json" "$work/manifest.json"
python3 -B "$runner/campaign.py" run "$work/manifest.json" "$work/receipt.json"
python3 -B "$runner/campaign.py" run "$work/manifest.json" "$work/receipt.json"
python3 -B "$runner/campaign.py" run "$work/manifest.json" "$work/receipt.json" --retry-failed
python3 -B "$runner/campaign.py" audit "$work/manifest.json" "$work/receipt.json"
python3 -B -m unittest discover -s "$runner" -v
```

Each demo run exits 1 because the controls intentionally produce fail and
unknown outcomes. Audit exits 0 for a complete receipt, even when some
outcomes are fail or unknown. Incomplete or invalid data exits 2. A SIGINT
during a run exits 130. The `sample manifest` records
the interpreter path and hashes for the original run. Generate a fresh plan
when the interpreter or declared inputs change.

## Receipt contract

- Authors assign stable cell IDs in the `demo spec`. Planning
  preserves argv, working directory, timeout, and named inputs, and records
  the resolved executable plus the SHA-256 hashes of the executable and input
  files. It fingerprints each binding and writes the manifest before running.
- A run revalidates the manifest against local inputs and checks the receipt's
  manifest fingerprint and per-result checksums. It skips matching passes,
  retries unknowns on resume, and retries a recorded failure only with
  `--retry-failed`. Each result retains its attempt history. Commands run
  without an implicit shell. Attempts capture duration and decoded stdout
  and stderr. A zero exit records pass, a positive exit records fail, and
  timeout, signal, launch error, and interruption record unknown with
  separate reason codes.
- A locked writer checkpoints an interruption marker before starting a cell.
  It finalizes the attempt by replacing the receipt atomically after the child
  finishes and the inputs are checked again. SIGINT and timeout kill the
  child's process group. After hard termination of the runner, a pending
  attempt can still have child side effects. Use trusted, rerunnable commands.
- Before returning a summary, the runner revalidates every planned binding
  under the receipt lock. If a later command changes an earlier cell's declared
  input, the CLI exits 2 without emitting a completeness summary. The receipt
  retains the recorded attempts, and audit rejects it while the binding is stale.
- Audit requires a unique result per planned ID. It rejects missing,
  unexpected, duplicate, conflicting, or fingerprint-mismatched records.
  Pending interruptions, launch errors, and zero executed cells make the
  audit incomplete. A timeout is a recorded execution with unknown outcome.

Fingerprints cover the declared argv, working directory, timeout, resolved
executable path and bytes, and declared input bytes. The spec author must name
relevant files. Checksums are unsigned.

## Environment binding

Set a cell's `env_allowlist` to opt into a controlled subprocess environment.
The list accepts unique names such as `PATH` and `LANG`. The runner passes only
declared variables present in its own environment. An empty list passes an
empty environment. It resolves bare executable names using the same captured
`PATH` as the launch environment, with relative entries interpreted from the
cell's working directory. When `PATH` is absent, executable lookup uses
Python's POSIX default search path. The demo declares `PATH` so its build
helper can relaunch the selected Python interpreter.

The manifest stores an SHA-256 fingerprint over the declared names and their
values, using a distinct marker for absent variables. Empty strings count as
present. Run and audit reject a changed binding before reusing a recorded
pass. To adopt changed values, generate a fresh manifest and receipt. Avoid
allowlisting secrets because predictable values can be guessed from the digest
and child output is recorded in receipts.

Cells that omit `env_allowlist` retain legacy behavior and the existing
manifest and receipt format. They inherit the runner's environment, and an
environment-only change can leave a prior pass eligible for reuse when the
other binding fields still match. Add an explicit allowlist and replan to
enable environment binding for an existing cell.

Controlled mode scopes the environment supplied at launch. Programs and
runtimes can synthesize variables, modify descendant environments, and read
host state such as locale data or configuration files. Git provenance,
transitive toolchain dependencies, produced artifact hashes, shard merges,
and abiforge oracle and evidence linkage are outside the prototype.

## Observed run

On Python 3.12.14, the initial run, resume, and explicit retry each reported
five executed cells with three passes, one failure, and one unknown. The
build reproduced the reference reduction certificate byte for byte. Verify
passed, and the certificate suite passed all 11 tests.

| Cell | Outcome | Attempts after all three invocations |
| --- | --- | ---: |
| `reduction-cert.build` | Pass | 1 |
| `reduction-cert.verify` | Pass | 1 |
| `reduction-cert.unittest` | Pass | 1 |
| `control.exit-7` | Fail, exit 7 | 2 |
| `control.timeout` | Unknown, timeout after 0.5 s | 3 |

The `execution receipt` retains the command output and
attempt history. The `audit receipt` reports complete
coverage. The `runner test log` records 11 passing
tests. They cover environment-only value and presence changes, isolation from
undeclared variables, relative `PATH` lookup, validation of allowlists, and
legacy compatibility. They also exercise SIGINT recovery, input and receipt
tampering, incomplete audits, and an injected atomic-write failure.

## Cross-cell input drift regression

The baseline stress campaign found one counterexample in 28 variants. A later
cell changed an earlier cell's declared input after it passed. The baseline run
exited 0 and reported complete, and the subsequent audit rejected the earlier
fingerprint. With final manifest validation, the initial run, audit, and resume
all exit 2 with the fingerprint mismatch. All 12 runner tests and all 28 stress
variants passed after the fix. See the `before and after results`.

## campaign.py

Save under `research/campaign-runner/`.

```python
#!/usr/bin/env python3
"""Plan, execute, and audit a local campaign of trusted commands (POSIX)."""

import argparse
from contextlib import contextmanager
import fcntl
from hashlib import file_digest as hash_file, sha256
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time


VERSION = 1
OUTCOMES = {"pass", "fail", "unknown"}


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_object)


def fingerprint(value):
    data = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return sha256(data.encode()).hexdigest()


def file_digest(path):
    with path.open("rb") as stream:
        return hash_file(stream, "sha256").hexdigest()


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
            os.replace(temporary, path)
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            temporary.unlink(missing_ok=True)


@contextmanager
def locked(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_name(path.name + ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def fields(value, keys):
    if not isinstance(value, dict) or value.keys() != set(keys.split()):
        raise ValueError(f"expected object with fields: {keys}")


def index_rows(rows, kind):
    if not isinstance(rows, list):
        raise ValueError(f"{kind} entries must be a list")
    indexed = {}
    for row in rows:
        cell_id = row.get("id") if isinstance(row, dict) else None
        if not isinstance(cell_id, str) or not re.fullmatch(r"[a-zA-Z0-9_.-]+", cell_id):
            raise ValueError(f"invalid {kind} ID: {cell_id!r}")
        if cell_id in indexed:
            issue = "duplicate" if row == indexed[cell_id] else "conflicting"
            raise ValueError(f"{issue} {kind} entry: {cell_id}")
        indexed[cell_id] = row
    return indexed


def local_path(root, name):
    if not isinstance(name, str) or not name or Path(name).is_absolute() or "\0" in name:
        raise ValueError(f"expected a workspace-relative path: {name!r}")
    path = (root / name).resolve()
    if not path.is_relative_to(root):
        raise ValueError(f"path escapes workspace: {name}")
    return path


def bind(root, spec):
    spec_fields = "id argv cwd inputs timeout_seconds"
    controlled = isinstance(spec, dict) and "env_allowlist" in spec
    fields(spec, spec_fields + (" env_allowlist" if controlled else ""))
    environment = os.environ.copy()
    if controlled:
        names = spec["env_allowlist"]
        if not isinstance(names, list) or not all(
            isinstance(name, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name)
            for name in names
        ):
            raise ValueError(f"invalid env_allowlist for {spec['id']}")
        if len(set(names)) != len(names):
            raise ValueError(f"duplicate env_allowlist name for {spec['id']}")
        environment = {name: environment[name] for name in names if name in environment}
    argv, inputs, timeout = spec["argv"], spec["inputs"], spec["timeout_seconds"]
    if not isinstance(argv, list) or not argv or not all(
        isinstance(arg, str) and "\0" not in arg for arg in argv
    ) or not argv[0]:
        raise ValueError(f"invalid argv for {spec['id']}")
    if type(timeout) not in (int, float) or not 0 < timeout <= 86400:
        raise ValueError(f"invalid timeout for {spec['id']}")
    cwd = local_path(root, spec["cwd"])
    if not cwd.is_dir():
        raise ValueError(f"missing cwd: {cwd}")
    if not isinstance(inputs, list) or not all(isinstance(name, str) for name in inputs):
        raise ValueError("inputs must be a list of paths")
    paths = {name: local_path(root, name) for name in inputs}
    if len(set(paths.values())) != len(inputs):
        raise ValueError(f"duplicate input paths for {spec['id']}")
    # Resolve PATH relative to the child's cwd, including empty PATH entries.
    search = os.pathsep.join(str(cwd / item) for item in os.get_exec_path(environment))
    command = str(cwd / argv[0]) if "/" in argv[0] else argv[0]
    found = shutil.which(command, path=search)
    if found is None:
        raise ValueError(f"executable unavailable for {spec['id']}: {argv[0]}")
    executable = Path(found).absolute()
    binding = {
        "spec": spec,
        "resolved_cwd": cwd.relative_to(root).as_posix(),
        "executable": str(executable),
        "executable_sha256": file_digest(executable),
        "input_sha256": {name: file_digest(path) for name, path in paths.items()},
    }
    if controlled:
        binding["environment_sha256"] = fingerprint({
            name: environment.get(name) for name in names
        })
    return binding, environment


def plan(root, source, manifest_path):
    fields(source, "cells")
    specs = index_rows(source["cells"], "planned")
    cells = []
    for cell_id, spec in specs.items():
        binding, _ = bind(root, spec)
        cells.append({"id": cell_id, "binding": binding, "fingerprint": fingerprint(binding)})
    manifest = {"version": VERSION, "cells": cells}
    with locked(manifest_path):
        if manifest_path.exists():
            if read_json(manifest_path) != manifest:
                raise ValueError("manifest already exists with a different plan")
        else:
            atomic_json(manifest_path, manifest)
    return manifest


def validate_manifest(root, manifest):
    fields(manifest, "version cells")
    if type(manifest["version"]) is not int or manifest["version"] != VERSION:
        raise ValueError("unsupported manifest version")
    cells = index_rows(manifest["cells"], "planned")
    for cell_id, cell in cells.items():
        fields(cell, "id binding fingerprint")
        binding = cell["binding"]
        if not isinstance(binding, dict) or "spec" not in binding:
            raise ValueError(f"invalid binding for {cell_id}")
        current, _ = bind(root, binding["spec"])
        if (current["spec"]["id"] != cell_id or current != cell["binding"]
                or fingerprint(current) != cell["fingerprint"]):
            raise ValueError(f"fingerprint mismatch for {cell_id}")
    return cells


def validate_attempt(attempt, cell_id):
    fields(attempt, "status reason returncode duration_seconds stdout stderr")
    code = attempt["returncode"]
    policies = {
        "exit-zero": ("pass", type(code) is int and code == 0),
        "exit-nonzero": ("fail", type(code) is int and code > 0),
        "signal": ("unknown", type(code) is int and code < 0),
        "timeout": ("unknown", code is None),
        "interrupted": ("unknown", code is None),
        "launch-error": ("unknown", code is None),
    }
    reason = attempt["reason"]
    duration = attempt["duration_seconds"]
    if (
        not isinstance(reason, str) or reason not in policies
        or not policies[reason][1] or attempt["status"] != policies[reason][0]
        or type(duration) not in (int, float) or not 0 <= duration < math.inf
        or not isinstance(attempt["stdout"], str) or not isinstance(attempt["stderr"], str)
    ):
        raise ValueError(f"invalid attempt for {cell_id}")


def validate_receipt(receipt, manifest, cells):
    fields(receipt, "version manifest_sha256 results")
    if type(receipt["version"]) is not int or receipt["version"] != VERSION:
        raise ValueError("unsupported receipt version")
    if receipt["manifest_sha256"] != fingerprint(manifest):
        raise ValueError("receipt manifest fingerprint mismatch")
    results = index_rows(receipt["results"], "result")
    for cell_id, result in results.items():
        fields(result, "id fingerprint attempts sha256")
        if cell_id not in cells:
            raise ValueError(f"unplanned result: {cell_id}")
        if result["fingerprint"] != cells[cell_id]["fingerprint"]:
            raise ValueError(f"result fingerprint mismatch for {cell_id}")
        if fingerprint({key: value for key, value in result.items() if key != "sha256"}) != result["sha256"]:
            raise ValueError(f"result checksum mismatch for {cell_id}")
        attempts = result["attempts"]
        if not isinstance(attempts, list) or not attempts:
            raise ValueError(f"missing attempts for {cell_id}")
        for attempt in attempts:
            validate_attempt(attempt, cell_id)
        if any(attempt["status"] == "pass" for attempt in attempts[:-1]):
            raise ValueError(f"result retried after passing: {cell_id}")
    return results


def outcome(status, reason, code=None, duration=0, stdout=b"", stderr=b""):
    return {
        "status": status,
        "reason": reason,
        "returncode": code,
        "duration_seconds": round(duration, 6),
        "stdout": stdout.decode("utf-8", errors="replace"),
        "stderr": stderr.decode("utf-8", errors="replace"),
    }


def kill_group(process):
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def execute(root, binding, environment):
    spec = binding["spec"]
    start = time.monotonic()
    try:
        process = subprocess.Popen(
            spec["argv"], executable=binding["executable"], cwd=root / binding["resolved_cwd"],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=True, env=environment,
        )
    except OSError as error:
        return outcome("unknown", "launch-error", duration=time.monotonic() - start,
                       stderr=str(error).encode())
    with process:
        try:
            stdout, stderr = process.communicate(timeout=spec["timeout_seconds"])
        except subprocess.TimeoutExpired:
            kill_group(process)
            stdout, stderr = process.communicate()
            return outcome("unknown", "timeout", duration=time.monotonic() - start,
                           stdout=stdout, stderr=stderr)
        except BaseException:
            kill_group(process)
            process.wait()
            raise
    code = process.returncode
    status, reason = {
        -1: ("unknown", "signal"), 0: ("pass", "exit-zero"), 1: ("fail", "exit-nonzero")
    }[(code > 0) - (code < 0)]
    return outcome(status, reason, code, time.monotonic() - start, stdout, stderr)


def record(path, receipt, results, cell, attempts):
    result = {"id": cell["id"], "fingerprint": cell["fingerprint"], "attempts": attempts}
    result["sha256"] = fingerprint(result)
    results[cell["id"]] = result
    receipt["results"] = list(results.values())
    atomic_json(path, receipt)


def run(root, manifest, receipt_path, retry_failed=False):
    with locked(receipt_path):
        cells = validate_manifest(root, manifest)
        receipt = read_json(receipt_path) if receipt_path.exists() else {
            "version": VERSION, "manifest_sha256": fingerprint(manifest), "results": []
        }
        results = validate_receipt(receipt, manifest, cells)
        if not receipt_path.exists():
            atomic_json(receipt_path, receipt)
        for cell_id, cell in cells.items():
            attempts = results.get(cell_id, {}).get("attempts", [])
            if attempts:
                if attempts[-1]["status"] == "pass":
                    continue
                if attempts[-1]["status"] == "fail" and not retry_failed:
                    continue
            binding = cell["binding"]
            current, environment = bind(root, binding["spec"])
            if current != binding:
                raise ValueError(f"fingerprint changed before execution: {cell_id}")
            record(receipt_path, receipt, results, cell, [*attempts, outcome("unknown", "interrupted")])
            attempt = execute(root, binding, environment)
            current, _ = bind(root, binding["spec"])
            if current != binding:
                raise ValueError(f"fingerprint changed during execution: {cell_id}")
            record(receipt_path, receipt, results, cell, [*attempts, attempt])
        # A later command can change a binding for a cell already processed.
        validate_manifest(root, manifest)
        return summarize(cells, results)


def summarize(cells, results):
    counts = dict.fromkeys(sorted(OUTCOMES), 0)
    unresolved = []
    for cell_id, row in results.items():
        latest = row["attempts"][-1]
        counts[latest["status"]] += 1
        if latest["reason"] in {"interrupted", "launch-error"}:
            unresolved.append(cell_id)
    missing = sorted(cells.keys() - results.keys())
    executed = sum(counts.values()) - len(unresolved)
    complete = bool(executed and not missing and not unresolved)
    return {
        "complete": complete,
        "planned": len(cells),
        "executed": executed,
        "missing": missing,
        "unresolved": sorted(unresolved),
        **counts,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    commands = parser.add_subparsers(dest="command", required=True)
    planner = commands.add_parser("plan")
    planner.add_argument("spec", type=Path)
    planner.add_argument("manifest", type=Path)
    for command in ("run", "audit"):
        subparser = commands.add_parser(command)
        subparser.add_argument("manifest", type=Path)
        subparser.add_argument("receipt", type=Path)
        if command == "run":
            subparser.add_argument("--retry-failed", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    try:
        if args.command == "plan":
            if args.spec.resolve() == args.manifest.resolve():
                raise ValueError("spec and manifest must be distinct")
            manifest = plan(root, read_json(args.spec), args.manifest)
            print(json.dumps({"planned": len(manifest["cells"]), "sha256": fingerprint(manifest)}))
            return 0
        if args.manifest.resolve() == args.receipt.resolve():
            raise ValueError("manifest and receipt must be distinct")
        manifest = read_json(args.manifest)
        if args.command == "run":
            summary = run(root, manifest, args.receipt, args.retry_failed)
        else:
            cells = validate_manifest(root, manifest)
            receipt = read_json(args.receipt)
            summary = summarize(cells, validate_receipt(receipt, manifest, cells))
        print(json.dumps(summary, sort_keys=True))
        if not summary["complete"]:
            return 2
        return int(args.command == "run" and (summary["fail"] > 0 or summary["unknown"] > 0))
    except KeyboardInterrupt:
        print("campaign interrupted", file=sys.stderr)
        return 130
    except (ValueError, OSError, OverflowError) as error:
        print(f"campaign error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

```

## test_campaign.py

Save under `research/campaign-runner/`.

```python
import json
from pathlib import Path
import signal
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch

import campaign

SCRIPT = Path(campaign.__file__).resolve()


class CampaignTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.manifest = self.root / "plan.json"
        self.receipt = self.root / "receipt.json"

    def cell(self, name, code, inputs=(), timeout=5):
        return {
            "id": name, "argv": [sys.executable, "-B", "-c", code],
            "cwd": ".", "inputs": list(inputs), "timeout_seconds": timeout,
        }

    def plan(self, *specs):
        return campaign.plan(self.root, {"cells": list(specs)}, self.manifest)

    def audit(self):
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--root", str(self.root), "audit",
             self.manifest, self.receipt],
            capture_output=True, text=True, timeout=10,
        )

    def test_sigint_checkpoints_and_resume_skips_prior_success(self):
        manifest = self.plan(
            self.cell("done", "open('count', 'a').write('x')"),
            self.cell("interrupted", """
from pathlib import Path
import time
marker = Path('started')
if not marker.exists():
    marker.touch()
    time.sleep(60)
""", timeout=15),
            self.cell("later", "print('later')"),
        )
        with subprocess.Popen(
            [sys.executable, str(SCRIPT), "--root", str(self.root), "run",
             self.manifest, self.receipt],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        ) as worker:
            try:
                deadline = time.monotonic() + 8
                while not (self.root / "started").exists():
                    if worker.poll() is not None or time.monotonic() > deadline:
                        self.fail("worker did not reach the second cell")
                    time.sleep(0.01)
                worker.send_signal(signal.SIGINT)
                worker.communicate(timeout=5)
                self.assertEqual(worker.returncode, 130)
            finally:
                if worker.poll() is None:
                    worker.kill()
        audit = self.audit()
        self.assertEqual(audit.returncode, 2, audit.stderr)
        summary = json.loads(audit.stdout)
        self.assertEqual(summary["missing"], ["later"])
        self.assertEqual(summary["unresolved"], ["interrupted"])
        self.assertEqual(summary["executed"], 1)
        self.assertEqual(campaign.run(self.root, manifest, self.receipt)["pass"], 3)
        self.assertEqual((self.root / "count").read_text(), "x")
        rows = campaign.read_json(self.receipt)["results"]
        self.assertEqual([a["reason"] for a in rows[1]["attempts"]], ["interrupted", "exit-zero"])
        self.assertEqual(self.audit().returncode, 0)

    def test_failure_retry_is_explicit_and_timeout_remains_unknown(self):
        manifest = self.plan(
            self.cell("fail", "open('count', 'a').write('x'); raise SystemExit(7)"),
            self.cell("timeout", "import time; time.sleep(30)", timeout=0.1),
        )
        first = campaign.run(self.root, manifest, self.receipt)
        self.assertTrue(first["complete"])
        self.assertEqual((first["pass"], first["fail"], first["unknown"]), (0, 1, 1))
        campaign.run(self.root, manifest, self.receipt)
        self.assertEqual((self.root / "count").read_text(), "x")
        campaign.run(self.root, manifest, self.receipt, retry_failed=True)
        self.assertEqual((self.root / "count").read_text(), "xx")
        rows = campaign.read_json(self.receipt)["results"]
        self.assertEqual([a["returncode"] for a in rows[0]["attempts"]], [7, 7])
        self.assertEqual([a["reason"] for a in rows[1]["attempts"]], ["timeout"] * 3)

    def test_input_change_blocks_resume_before_receipt_mutation(self):
        source = self.root / "input"
        source.write_text("original")
        manifest = self.plan(self.cell("checked", "print('ok')", ["input"]))
        campaign.run(self.root, manifest, self.receipt)
        previous = self.receipt.read_bytes()
        source.write_text("changed")
        with self.assertRaisesRegex(ValueError, "fingerprint mismatch"):
            campaign.run(self.root, manifest, self.receipt)
        self.assertEqual(self.receipt.read_bytes(), previous)
        self.assertEqual(self.audit().returncode, 2)

    def test_later_cell_mutating_earlier_input_invalidates_run_and_audit(self):
        source = self.root / "input"
        source.write_text("original")
        first = dict(self.cell("first", "print(open('input').read())", ["input"]), env_allowlist=[])
        later = dict(self.cell("later", "open('input', 'w').write('changed')"), env_allowlist=[])
        self.plan(first, later)

        run = subprocess.run(
            [sys.executable, str(SCRIPT), "--root", str(self.root), "run",
             self.manifest, self.receipt],
            capture_output=True, text=True, timeout=10, env={},
        )
        self.assertEqual(source.read_text(), "changed")
        self.assertEqual(run.returncode, 2, run.stdout + run.stderr)
        self.assertEqual(run.stdout, "")
        self.assertIn("fingerprint mismatch for first", run.stderr)

        rows = campaign.read_json(self.receipt)["results"]
        self.assertEqual([(row["id"], row["attempts"][-1]["reason"]) for row in rows],
                         [("first", "exit-zero"), ("later", "exit-zero")])
        audit = self.audit()
        self.assertEqual(audit.returncode, 2, audit.stdout + audit.stderr)
        self.assertEqual(audit.stdout, "")
        self.assertIn("fingerprint mismatch for first", audit.stderr)

    def test_environment_change_blocks_resume_and_audit(self):
        name = "CAMPAIGN_TEST_VALUE"
        for index, (before, after) in enumerate([
            ("original", "changed"), (None, ""), ("", None),
        ]):
            with self.subTest(before=before, after=after):
                self.manifest = self.root / f"plan-{index}.json"
                self.receipt = self.root / f"receipt-{index}.json"
                spec = dict(self.cell("checked", "pass"), env_allowlist=[name])
                with patch.dict(campaign.os.environ):
                    if before is None:
                        campaign.os.environ.pop(name, None)
                    else:
                        campaign.os.environ[name] = before
                    manifest = self.plan(spec)
                    self.assertEqual(campaign.run(self.root, manifest, self.receipt)["pass"], 1)
                    self.assertEqual(self.audit().returncode, 0)
                    previous = self.receipt.read_bytes()
                    if after is None:
                        del campaign.os.environ[name]
                    else:
                        campaign.os.environ[name] = after
                    with self.assertRaisesRegex(ValueError, "fingerprint mismatch"):
                        campaign.run(self.root, manifest, self.receipt)
                    self.assertEqual(self.receipt.read_bytes(), previous)
                    self.assertEqual(self.audit().returncode, 2)

    def test_controlled_environment_is_independent_of_undeclared_variables(self):
        spec = self.cell("checked", """
import os
print(os.environ["CAMPAIGN_TEST_VALUE"])
assert "CAMPAIGN_TEST_NOISE" not in os.environ
assert "CAMPAIGN_TEST_MISSING" not in os.environ
""")
        spec["env_allowlist"] = ["PATH", "CAMPAIGN_TEST_VALUE", "CAMPAIGN_TEST_MISSING"]
        spec["argv"][0] = "campaign-python"
        (self.root / "bin").mkdir()
        (self.root / "bin" / "campaign-python").symlink_to(Path(sys.executable).resolve())
        env = {"PATH": "bin", "CAMPAIGN_TEST_VALUE": "declared-value", "CAMPAIGN_TEST_NOISE": "first"}
        with patch.dict(campaign.os.environ, env, clear=True):
            manifest = self.plan(spec)
            self.assertNotIn("declared-value", self.manifest.read_text())
        with patch.dict(campaign.os.environ, dict(env, CAMPAIGN_TEST_NOISE="second"), clear=True):
            self.assertEqual(self.plan(spec), manifest)
            self.assertEqual(campaign.run(self.root, manifest, self.receipt)["pass"], 1)

        attempt, = campaign.read_json(self.receipt)["results"][0]["attempts"]
        self.assertEqual(attempt["stdout"], "declared-value\n")

    def test_legacy_spec_retains_inherited_environment_and_binding_shape(self):
        spec = self.cell("legacy", """
import os
assert os.environ["CAMPAIGN_TEST_VALUE"] == "legacy-value"
""")
        with patch.dict(campaign.os.environ, {"CAMPAIGN_TEST_VALUE": "legacy-value"}):
            manifest = self.plan(spec)
            self.assertEqual(set(manifest["cells"][0]["binding"]), {
                "spec", "resolved_cwd", "executable", "executable_sha256", "input_sha256",
            })
            self.assertEqual(campaign.run(self.root, manifest, self.receipt)["pass"], 1)
        with patch.dict(campaign.os.environ, {"CAMPAIGN_TEST_VALUE": "changed"}):
            self.assertEqual(campaign.run(self.root, manifest, self.receipt)["pass"], 1)
            self.assertEqual(self.audit().returncode, 0)

    def test_rejects_invalid_environment_allowlists(self):
        for allowlist in (None, "PATH", ["PATH", "PATH"], ["BAD=NAME"], [42]):
            with self.subTest(allowlist=allowlist):
                spec = dict(self.cell("invalid", "pass"), env_allowlist=allowlist)
                with self.assertRaisesRegex(ValueError, "env_allowlist"):
                    self.plan(spec)

    def test_audit_rejects_tampered_duplicate_conflicting_and_missing_results(self):
        manifest = self.plan(self.cell("checked", "print('ok')"))
        campaign.run(self.root, manifest, self.receipt)
        original = self.receipt.read_bytes()
        for case in ("tampered", "duplicate", "conflicting", "missing"):
            with self.subTest(case=case):
                receipt = json.loads(original)
                rows = receipt["results"]
                if case in {"duplicate", "conflicting"}:
                    rows.append(json.loads(json.dumps(rows[0])))
                if case in {"tampered", "conflicting"}:
                    rows[-1]["attempts"][0]["stdout"] = "changed"
                if case == "missing":
                    rows.clear()
                campaign.atomic_json(self.receipt, receipt)
                audit = self.audit()
                self.assertEqual(audit.returncode, 2)
                if case == "missing":
                    self.assertEqual(json.loads(audit.stdout)["missing"], ["checked"])
                else:
                    diagnostic = "checksum mismatch" if case == "tampered" else case
                    self.assertIn(diagnostic, audit.stderr)

    def test_manifest_tamper_and_duplicate_planned_id(self):
        spec = self.cell("one", "pass")
        with self.assertRaisesRegex(ValueError, "duplicate planned"):
            self.plan(spec, spec)
        manifest = self.plan(spec)
        manifest["cells"][0]["binding"]["spec"]["argv"][-1] = "raise SystemExit(1)"
        with self.assertRaisesRegex(ValueError, "fingerprint mismatch"):
            campaign.run(self.root, manifest, self.receipt)
        self.assertFalse(self.receipt.exists())

    def test_empty_plan_is_incomplete(self):
        summary = campaign.run(self.root, self.plan(), self.receipt)
        self.assertEqual(summary["executed"], 0)
        self.assertFalse(summary["complete"])
        self.assertEqual(self.audit().returncode, 2)

    def test_atomic_write_preserves_prior_receipt_if_replace_fails(self):
        campaign.atomic_json(self.receipt, {"before": 1})
        with patch.object(campaign.os, "replace", side_effect=OSError("interrupted write")):
            with self.assertRaises(OSError):
                campaign.atomic_json(self.receipt, {"after": 2})
        self.assertEqual(campaign.read_json(self.receipt), {"before": 1})
        self.assertEqual(list(self.root.iterdir()), [self.receipt])


if __name__ == "__main__":
    unittest.main()

```

## demo_build.py

Save under `research/campaign-runner/`.

```python
"""Build a temporary reduction certificate and compare it to the published bytes."""

from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory


def main():
    reduction = Path(__file__).resolve().parent.parent / "reduction-cert"
    with TemporaryDirectory() as temporary:
        output = Path(temporary) / "certificate.json"
        subprocess.run(
            [sys.executable, "-B", str(reduction / "certificate.py"), "build",
             "--certificate", str(output)],
            check=True,
        )
        if output.read_bytes() != (reduction / "certificate.json").read_bytes():
            raise SystemExit("rebuilt certificate differs from reference bytes")
        print("Rebuilt certificate matches reference bytes.")


if __name__ == "__main__":
    main()

```

## demo-spec.json

Save under `research/campaign-runner/`.

```json
{
  "cells": [
    {
      "id": "reduction-cert.build",
      "argv": [
        "python3",
        "-B",
        "research/campaign-runner/demo_build.py"
      ],
      "cwd": ".",
      "env_allowlist": ["PATH"],
      "inputs": [
        "research/reduction-cert/certificate.py",
        "research/reduction-cert/certificate.json",
        "research/packing-campaign/results.json",
        "research/campaign-runner/demo_build.py"
      ],
      "timeout_seconds": 20
    },
    {
      "id": "reduction-cert.verify",
      "argv": [
        "python3",
        "-B",
        "research/reduction-cert/certificate.py",
        "verify"
      ],
      "cwd": ".",
      "env_allowlist": ["PATH"],
      "inputs": [
        "research/reduction-cert/certificate.py",
        "research/reduction-cert/certificate.json",
        "research/packing-campaign/results.json"
      ],
      "timeout_seconds": 20
    },
    {
      "id": "reduction-cert.unittest",
      "argv": [
        "python3",
        "-B",
        "-m",
        "unittest",
        "discover",
        "-s",
        "research/reduction-cert/",
        "-p",
        "test_certificate.py",
        "-v"
      ],
      "cwd": ".",
      "env_allowlist": ["PATH"],
      "inputs": [
        "research/reduction-cert/certificate.py",
        "research/reduction-cert/certificate.json",
        "research/packing-campaign/results.json",
        "research/reduction-cert/test_certificate.py"
      ],
      "timeout_seconds": 20
    },
    {
      "id": "control.exit-7",
      "argv": [
        "python3",
        "-B",
        "-c",
        "import sys; print('intentional failure', file=sys.stderr); sys.exit(7)"
      ],
      "cwd": ".",
      "env_allowlist": ["PATH"],
      "inputs": [],
      "timeout_seconds": 20
    },
    {
      "id": "control.timeout",
      "argv": [
        "python3",
        "-B",
        "-c",
        "import time; print('before timeout', flush=True); time.sleep(30)"
      ],
      "cwd": ".",
      "env_allowlist": ["PATH"],
      "inputs": [],
      "timeout_seconds": 0.5
    }
  ]
}

```
