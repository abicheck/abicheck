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

"""Metamorphic invariants of the public ``abicheck`` CLI over real catalog builds.

Each test states one relation that must hold between two CLI runs on the same
catalog case. None of them compares against the case's ground truth; that is
``test_example_pipeline``'s job.

* **operand form** -- the live ``.so`` pair, the dumped snapshot pair and a
  mixed pair (live old library, dumped new snapshot) give the same exit code,
  verdict and set of change kinds;
* **dump determinism** -- two cold dumps of one side are equal once the
  :data:`VOLATILE_KEYS` are removed;
* **format parity** -- every ``-o FORMAT=`` export of one comparison states
  the same verdict, and JSON, SARIF and oneline count the same findings;
* **reversal** -- a removed-kind finding in ``compare(v1, v2)`` has its
  registry twin (:data:`REMOVED_TO_ADDED`) in ``compare(v2, v1)``;
* **path perturbation** -- relocating and renaming both libraries leaves the
  exit code, verdict and kinds unchanged.

Every comparison and dump runs as a subprocess of ``python -m abicheck.cli``,
so the public CLI and its output writers are what is under test. Dumps that
must be independent of one another use a fresh, empty cache directory, so a
snapshot-cache hit cannot make two dumps agree trivially. The catalog builds
come from the same helpers as the example pipeline and are cached for the
whole session.

Integration-marked: the builds need gcc/g++ and castxml, and the suite runs
only on Linux, where gcc emits ELF.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import example_catalog
import pytest
from defusedxml.ElementTree import parse as defused_parse

from abicheck.model.change_catalog.assembled import REGISTRY
from abicheck.model.change_catalog.dimensions import ChangeOperation
from tests.test_example_autodiscovery import (
    _build_libs_for_case,
    _find_sources,
    _header_list,
)
from tests._abicheck_subprocess import abicheck_command

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        sys.platform != "linux",
        reason="catalog builds are ELF on Linux only (gcc emits Mach-O/PE elsewhere)",
    ),
]

#: Catalog cases exercised by every relation: a function removal and an enum
#: member removal (the two removal kinds the reversal relation checks), and a
#: C++ vtable change, which carries a standing hygiene finding the oneline
#: total must count. Each case
#: costs eight CLI invocations of about 2.4 CPU-seconds each, almost all of it
#: interpreter start-up and analysis, and the CI sandbox does not run them in
#: parallel, so three cases is what fits the module into about a minute.
#: Cases with no change, no public header, or only risk-level changes are not
#: in the subset.
CASES: tuple[str, ...] = (
    "case01_symbol_removal",
    "case09_cpp_vtable",
    "case19_enum_member_removed",
)

#: The side each case's dump-determinism check dumps twice. The subset
#: alternates sides so both are covered, and each case costs one extra dump.
DETERMINISM_SIDE: dict[str, str] = {
    "case01_symbol_removal": "new",
    "case09_cpp_vtable": "old",
    "case19_enum_member_removed": "new",
}

#: Concurrent CLI runs. The CLI is CPU-bound per process, so more than the
#: available cores buys nothing.
_PARALLEL = max(1, min(4, os.cpu_count() or 1))

#: Exit codes a completed ``compare`` may return (0 compatible, 1 contract
#: domain not closed, 2 source break, 4 ABI break, 8 proven removal). 64 is a
#: usage error and fails the run.
COMPARE_EXIT_CODES = frozenset({0, 1, 2, 4, 8})

#: Keys whose value is the wall-clock time of a dump and so differs between
#: two dumps of one library by design. Nothing else may differ.
VOLATILE_KEYS = frozenset({"created_at"})

#: Report formats written by one ``compare`` run, as ``-o FORMAT=PATH`` files.
#: Terminal output goes to stdout instead (``-o terminal=-``).
FORMAT_FILES: dict[str, str] = {
    "json": "json",
    "sarif": "sarif",
    "markdown": "md",
    "html": "html",
    "junit": "xml",
    "review": "md",
    "oneline": "txt",
}


def _registry_twins() -> dict[str, str]:
    """Map each removed kind to its added twin, read from the ChangeKind registry.

    A removed kind's twin is the registered kind named by swapping its one
    ``removed`` token for ``added``, provided it has the same entity. The
    registry's ``operation`` is not the filter: trait-gained kinds such as
    ``type_field_added`` are declared ``MODIFIED`` because the entity persists.
    """
    entries = {str(getattr(k, "value", k)): e for k, e in REGISTRY.entries.items()}
    twins: dict[str, str] = {}
    for name, entry in entries.items():
        if entry.operation != ChangeOperation.REMOVED:
            continue
        twin = re.sub(
            r"(^|_)removed(?=_|$)",
            lambda m: m.group(1) + "added",
            name,
            count=1,
        )
        other = entries.get(twin)
        if other is not None and other.entity == entry.entity:
            twins[name] = twin
    return twins


REMOVED_TO_ADDED: dict[str, str] = _registry_twins()


# ---------------------------------------------------------------------------
# CLI plumbing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CaseBuild:
    v1: Path
    v2: Path
    h1: tuple[Path, ...]
    h2: tuple[Path, ...]


@dataclass(frozen=True)
class Run:
    """One ``compare`` invocation: its exit code, terminal text and report files."""

    rc: int
    stdout: str
    files: dict[str, Path]

    def json(self) -> dict[str, Any]:
        return json.loads(self.files["json"].read_text(encoding="utf-8"))

    def outcome(self) -> tuple[int, str, frozenset[str]]:
        report = self.json()
        return (
            self.rc,
            report["verdict"],
            frozenset(c["kind"] for c in report["changes"]),
        )


def _headers(side: str, headers: tuple[Path, ...]) -> list[str]:
    return [x for h in headers for x in ("-H", f"{side}={h}")]


def _cli(args: list[str], cache: Path) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ, XDG_CACHE_HOME=str(cache))
    return subprocess.run(
        abicheck_command(*args),
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
        env=env,
    )


def _describe(outcomes: dict[str, Any]) -> str:
    return "\n".join(f"  {name}: {value}" for name, value in outcomes.items())


class _Lab:
    """Per-session builds and CLI runs, memoised per catalog case."""

    def __init__(self, root: Path, cmake_dir: Path | None) -> None:
        self.root = root
        self._cmake_dir = cmake_dir
        self._session_cache = root / "session-cache"
        self._builds: dict[str, CaseBuild] = {}
        self._memo: dict[tuple[str, str], Any] = {}

    def _once(self, key: tuple[str, str], make: Any) -> Any:
        if key not in self._memo:
            self._memo[key] = make()
        return self._memo[key]

    def workdir(self, case: str) -> Path:
        path = self.root / case
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _fresh_cache(self, case: str) -> Path:
        return Path(tempfile.mkdtemp(prefix="cold-cache-", dir=self.workdir(case)))

    def build(self, case: str) -> CaseBuild:
        if case not in self._builds:
            case_dir = example_catalog.case_dir(case)
            v1_src, v2_src, v1_hdr, v2_hdr = _find_sources(case_dir)
            out = self.workdir(case) / "build"
            out.mkdir(exist_ok=True)
            v1, v2 = _build_libs_for_case(
                case, case_dir, out, self._cmake_dir, v1_src, v2_src
            )
            self._builds[case] = CaseBuild(
                v1=v1,
                v2=v2,
                h1=tuple(_header_list(v1_hdr)),
                h2=tuple(_header_list(v2_hdr)),
            )
        return self._builds[case]

    def _compare(
        self,
        case: str,
        label: str,
        args: list[str],
        *,
        formats: tuple[str, ...] = ("json",),
        cold: bool = False,
    ) -> Run:
        """One ``compare`` run writing *formats* into ``<case>/<label>/``.

        ``terminal`` is captured from stdout rather than written to a file.
        """

        def make() -> Run:
            out = self.workdir(case) / label
            out.mkdir(exist_ok=True)
            files = {
                fmt: out / f"{fmt}.{FORMAT_FILES[fmt]}"
                for fmt in formats
                if fmt != "terminal"
            }
            cli_args = ["compare", *args]
            for fmt, path in files.items():
                cli_args += ["-o", f"{fmt}={path}"]
            if "terminal" in formats:
                cli_args += ["-o", "terminal=-"]
            cache = self._fresh_cache(case) if cold else self._session_cache
            proc = _cli(cli_args, cache)
            assert proc.returncode in COMPARE_EXIT_CODES, (
                f"{case}: abicheck {' '.join(cli_args)} exited "
                f"{proc.returncode}:\n{proc.stderr[-2000:]}"
            )
            return Run(rc=proc.returncode, stdout=proc.stdout, files=files)

        return self._once((case, label), make)

    def dump(self, case: str, side: str, label: str) -> Path:
        """A snapshot of one side, dumped into a cold cache, keyed by *label*."""

        def make() -> Path:
            b = self.build(case)
            lib, headers = (b.v1, b.h1) if side == "old" else (b.v2, b.h2)
            out = self.workdir(case) / f"{side}.{label}.json"
            proc = _cli(
                [
                    "dump",
                    str(lib),
                    *[x for h in headers for x in ("-H", str(h))],
                    "--version",
                    side,
                    "-o",
                    str(out),
                ],
                self._fresh_cache(case),
            )
            assert proc.returncode == 0, (
                f"{case}: dump of {side} failed:\n{proc.stderr[-2000:]}"
            )
            assert "cache hit" not in proc.stderr, (
                f"{case}: a cold dump of {side} was served from a cache"
            )
            return out

        return self._once((case, f"dump-{side}-{label}"), make)

    def live(self, case: str) -> Run:
        """The libraries compared directly, with every report format written."""
        b = self.build(case)
        return self._compare(
            case,
            "live",
            [str(b.v1), str(b.v2), *_headers("old", b.h1), *_headers("new", b.h2)],
            formats=(*FORMAT_FILES, "terminal"),
        )

    def reverse(self, case: str) -> Run:
        b = self.build(case)
        return self._compare(
            case,
            "reverse",
            [str(b.v2), str(b.v1), *_headers("old", b.h2), *_headers("new", b.h1)],
        )

    def stored(self, case: str) -> Run:
        return self._compare(
            case,
            "stored",
            [
                str(self.dump(case, "old", "a")),
                str(self.dump(case, "new", "a")),
            ],
        )

    def prefetch(self, cases: tuple[str, ...]) -> None:
        """Run every CLI invocation of *cases* before the tests need them.

        Builds compile sequentially through the shared CMake tree. The runs
        that depend on nothing else (dumps, live, reverse and relocated
        compares) then go through a thread pool, and the two compares that
        read a dumped snapshot follow. Each memo key is produced by exactly one
        job, so the pool never races on a key.
        """
        for case in cases:
            self.build(case)
        independent: list[Any] = []
        for case in cases:
            for side in ("old", "new"):
                independent.append(lambda c=case, s=side: self.dump(c, s, "a"))
            independent.append(lambda c=case: self.dump(c, DETERMINISM_SIDE[c], "b"))
            independent.append(lambda c=case: self.live(c))
            independent.append(lambda c=case: self.reverse(c))
            independent.append(lambda c=case: self.moved(c))
        dependent: list[Any] = []
        for case in cases:
            dependent.append(lambda c=case: self.stored(c))
            dependent.append(lambda c=case: self.mixed(c))
        for jobs in (independent, dependent):
            with ThreadPoolExecutor(max_workers=_PARALLEL) as pool:
                for future in [pool.submit(job) for job in jobs]:
                    future.result()

    def mixed(self, case: str) -> Run:
        """Live old library against the dumped new snapshot."""
        b = self.build(case)
        args = [str(b.v1), str(self.dump(case, "new", "a")), *_headers("old", b.h1)]
        return self._compare(case, "mixed", args)

    def moved(self, case: str) -> Run:
        """Both libraries copied into other directories under other names."""
        b = self.build(case)
        one = self.workdir(case) / "moved" / "a" / "renamed_one.so"
        two = self.workdir(case) / "moved" / "b" / "renamed_two.so"
        one.parent.mkdir(parents=True, exist_ok=True)
        two.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(b.v1, one)
        shutil.copy2(b.v2, two)
        return self._compare(
            case,
            "moved",
            [str(one), str(two), *_headers("old", b.h1), *_headers("new", b.h2)],
            cold=True,
        )


@pytest.fixture(scope="session")
def lab(tmp_path_factory: pytest.TempPathFactory, shared_cmake_build_dir: Path | None):
    if shutil.which("castxml") is None:
        pytest.skip("castxml not on PATH")
    session_lab = _Lab(tmp_path_factory.mktemp("metamorphic"), shared_cmake_build_dir)
    session_lab.prefetch(CASES)
    return session_lab


# ---------------------------------------------------------------------------
# Operand form (I2)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", CASES)
def test_operand_form_does_not_change_the_outcome(case: str, lab: _Lab) -> None:
    """Live libraries, stored snapshots and mixed pairs give one exit code,
    verdict and set of change kinds."""
    outcomes = {
        "live .so pair": lab.live(case).outcome(),
        "dumped snapshot pair": lab.stored(case).outcome(),
        "live old, snapshot new": lab.mixed(case).outcome(),
    }
    assert len(set(outcomes.values())) == 1, (
        f"{case}: operand form changed the outcome:\n{_describe(outcomes)}"
    )


# ---------------------------------------------------------------------------
# Dump determinism (I3)
# ---------------------------------------------------------------------------


def _drop_volatile(node: Any) -> Any:
    if isinstance(node, dict):
        return {k: _drop_volatile(v) for k, v in node.items() if k not in VOLATILE_KEYS}
    if isinstance(node, list):
        return [_drop_volatile(v) for v in node]
    return node


def _first_difference(a: Any, b: Any, path: str = "$") -> str | None:
    if type(a) is not type(b):
        return f"{path}: {type(a).__name__} vs {type(b).__name__}"
    if isinstance(a, dict):
        for key in sorted(set(a) | set(b)):
            if key not in a or key not in b:
                return f"{path}.{key}: present in only one dump"
            found = _first_difference(a[key], b[key], f"{path}.{key}")
            if found:
                return found
        return None
    if isinstance(a, list):
        if len(a) != len(b):
            return f"{path}: length {len(a)} vs {len(b)}"
        for i, (x, y) in enumerate(zip(a, b, strict=True)):
            found = _first_difference(x, y, f"{path}[{i}]")
            if found:
                return found
        return None
    return None if a == b else f"{path}: {a!r} vs {b!r}"


@pytest.mark.parametrize("case", CASES)
def test_two_cold_dumps_agree_modulo_volatile_keys(case: str, lab: _Lab) -> None:
    """Two dumps of one side, each into its own empty cache, differ only in
    :data:`VOLATILE_KEYS`; the volatile keys must actually be present."""
    side = DETERMINISM_SIDE[case]
    first_text = lab.dump(case, side, "a").read_text(encoding="utf-8")
    second_text = lab.dump(case, side, "b").read_text(encoding="utf-8")
    assert "created_at" in first_text, (
        f"{case}/{side}: no created_at in the dump, so VOLATILE_KEYS is stale"
    )
    difference = _first_difference(
        _drop_volatile(json.loads(first_text)),
        _drop_volatile(json.loads(second_text)),
    )
    assert difference is None, f"{case}/{side}: dumps differ at {difference}"


# ---------------------------------------------------------------------------
# Format parity (I4)
# ---------------------------------------------------------------------------


def _first(pattern: str, text: str, fmt: str) -> str:
    match = re.search(pattern, text, re.MULTILINE)
    assert match is not None, f"{fmt}: no verdict found in the export"
    return match.group(1)


def _stated_verdict(fmt: str, run: Run) -> str:
    if fmt == "terminal":
        return _first(r"^Compatibility: ([A-Z_]+)", run.stdout, fmt)
    if fmt == "oneline":
        return _first(r"^([A-Z_]+):", run.files["oneline"].read_text("utf-8"), fmt)
    if fmt == "sarif":
        report = json.loads(run.files["sarif"].read_text(encoding="utf-8"))
        return report["runs"][0]["properties"]["abiVerdict"]
    if fmt == "markdown":
        return _first(
            r"\*\*Verdict\*\* \|[^`\n]*`([A-Z_]+)`",
            run.files["markdown"].read_text(encoding="utf-8"),
            fmt,
        )
    if fmt == "review":
        return _first(
            r"\*\*Verdict:\*\*[^`\n]*`([A-Z_]+)`",
            run.files["review"].read_text(encoding="utf-8"),
            fmt,
        )
    if fmt == "html":
        return _first(
            r'class="verdict-box"[^>]*>\s*<h2>[^:<]*: ([A-Z_]+)</h2>',
            run.files["html"].read_text(encoding="utf-8"),
            fmt,
        )
    raise AssertionError(f"no verdict extractor for {fmt}")


VERDICT_FORMATS = ("terminal", "oneline", "sarif", "markdown", "review", "html")


@pytest.mark.parametrize("case", CASES)
def test_every_export_states_the_same_verdict(case: str, lab: _Lab) -> None:
    run = lab.live(case)
    expected = run.json()["verdict"]
    stated = {"json": expected}
    stated.update({fmt: _stated_verdict(fmt, run) for fmt in VERDICT_FORMATS})
    assert set(stated.values()) == {expected}, (
        f"{case}: exports disagree on the verdict: {stated}"
    )


@pytest.mark.parametrize("case", CASES)
def test_json_and_sarif_count_the_same_findings(case: str, lab: _Lab) -> None:
    run = lab.live(case)
    json_total = len(run.json()["changes"])
    sarif = json.loads(run.files["sarif"].read_text(encoding="utf-8"))
    sarif_total = sum(len(r.get("results", [])) for r in sarif["runs"])
    assert sarif_total == json_total, (
        f"{case}: JSON lists {json_total} changes, SARIF has {sarif_total} results"
    )


@pytest.mark.parametrize("case", CASES)
def test_oneline_total_counts_the_same_findings(case: str, lab: _Lab) -> None:
    """The oneline total is the JSON ``total_changes`` population, hygiene included."""
    run = lab.live(case)
    json_total = len(run.json()["changes"])
    text = run.files["oneline"].read_text(encoding="utf-8")
    oneline_total = int(_first(r"\((\d+) total\)", text, "oneline"))
    assert oneline_total == json_total, (
        f"{case}: oneline reports {oneline_total} total, JSON lists {json_total}: {text.strip()}"
    )


@pytest.mark.parametrize("case", CASES)
def test_junit_gate_matches_gating_findings(case: str, lab: _Lab) -> None:
    """JUnit counts one testcase per checked symbol, so its totals are not a
    finding count. What it must carry is the gate: its failures fire exactly
    when the JSON report has gating findings."""
    run = lab.live(case)
    gating = run.json()["result_counts"]["gating"]
    root = defused_parse(run.files["junit"]).getroot()
    props: dict[str, str] = {}
    for prop in root.iter("property"):
        props.setdefault(prop.get("name", ""), prop.get("value", ""))
    failures = sum(1 for tc in root.iter("testcase") if tc.find("failure") is not None)
    assert int(props["abicheck.disposition.gating"]) == gating, (
        f"{case}: junit gating property {props['abicheck.disposition.gating']} "
        f"!= JSON gating {gating}"
    )
    assert (failures > 0) == (gating > 0), (
        f"{case}: junit has {failures} failing testcases with {gating} gating findings"
    )


# ---------------------------------------------------------------------------
# Reversal (I5)
# ---------------------------------------------------------------------------


def test_registry_twins_are_registered_kinds_of_one_entity() -> None:
    """The twin map is derived from the registry, and pins two known pairs."""
    assert REMOVED_TO_ADDED["func_removed"] == "func_added"
    assert REMOVED_TO_ADDED["enum_member_removed"] == "enum_member_added"
    entries = {str(getattr(k, "value", k)): e for k, e in REGISTRY.entries.items()}
    for removed, added in REMOVED_TO_ADDED.items():
        assert entries[removed].entity == entries[added].entity, (removed, added)


@pytest.mark.parametrize("case", CASES)
def test_removed_finding_has_its_registry_twin_in_the_reverse_run(
    case: str, lab: _Lab
) -> None:
    forward = {c["kind"] for c in lab.live(case).json()["changes"]}
    reverse = {c["kind"] for c in lab.reverse(case).json()["changes"]}
    missing = {
        k: REMOVED_TO_ADDED[k]
        for k in forward
        if k in REMOVED_TO_ADDED and REMOVED_TO_ADDED[k] not in reverse
    }
    assert not missing, (
        f"{case}: removed kinds without their added twin in the reverse run: "
        f"{missing}; reverse kinds: {sorted(reverse)}"
    )


def test_reversal_relation_is_exercised_by_the_subset(lab: _Lab) -> None:
    """Guards against a vacuous pass: the subset must contain the removal
    kinds whose reversal is checked above."""
    exercised: set[str] = set()
    for case in CASES:
        exercised |= {
            c["kind"]
            for c in lab.live(case).json()["changes"]
            if c["kind"] in REMOVED_TO_ADDED
        }
    assert {"func_removed", "enum_member_removed"} <= exercised, sorted(exercised)


# ---------------------------------------------------------------------------
# Path perturbation (I6)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", CASES)
def test_relocated_and_renamed_copies_keep_the_outcome(case: str, lab: _Lab) -> None:
    """Copies in other directories under other names give the same exit code,
    verdict and kinds, extracted from scratch with a cold cache."""
    base = lab.live(case).outcome()
    moved = lab.moved(case).outcome()
    assert moved == base, (
        f"{case}: relocating and renaming changed the outcome:\n"
        + _describe({"original paths": base, "renamed copies": moved})
    )
