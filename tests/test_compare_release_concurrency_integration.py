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

"""The concurrent release fan-out end to end, on compiled multi-member input.

The shared type-spelling caches' own thread-safety is stated as invariants
in ``test_spelling_match_cache_concurrency.py``. That is necessary and not
sufficient: the claim this module owns is the *workflow* one -- that a
real multi-member ``compare`` over real ELF artifacts, dispatched through
the ``ThreadPoolExecutor`` fan-out that actually shares those caches,
covers every member, produces the same semantic findings on every run and
at every worker count, and hides no exception.

That distinction is exactly the failure mode being closed: the oneDAL
report this work started from had *passing* cache tests at the revision
whose six-member run failed three members with a match-cache ``KeyError``,
because nothing exercised the caches from more than one thread and nothing
checked the workflow's own determinism.

The sibling class below injects a member failure instead, because the
other half of that incident was diagnostic: a release must keep a failed
member's identity, must keep its successful siblings reviewable, and must
never launder a partial release into a clean pass.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "linux",
    reason="Builds ELF shared objects with GNU ld flags; the release "
    "fan-out's own analysis is ELF/Linux-only.",
)

# Four members, so the fan-out genuinely dispatches several workers against
# the shared caches. Each declares overlapping *type spellings* on purpose
# (``dense``, ``table``, ``Wrapper<Inner>``): shared spellings are what put
# several workers on the same match-cache keys, which is the contention the
# observed defect needed.
_COMMON_HEADER = """
#pragma once
namespace onemock {
struct dense { int rows; int cols; };
struct table { dense d; double scale; };
template <typename T> struct Wrapper { T value; };
}
"""

_MEMBERS: tuple[tuple[str, str, str], ...] = (
    (
        "libmock_core",
        """
#include "common.h"
namespace onemock {
int core_rows(const dense& d) { return d.rows; }
double core_scale(const table& t) { return t.scale; }
int core_wrapped(const Wrapper<dense>& w) { return w.value.rows; }
}
""",
        # NEW: one removed export -> a real, stable ABI break.
        """
#include "common.h"
namespace onemock {
int core_rows(const dense& d) { return d.rows; }
int core_wrapped(const Wrapper<dense>& w) { return w.value.rows; }
}
""",
    ),
    (
        "libmock_algo",
        """
#include "common.h"
namespace onemock {
double algo_run(const table& t, const dense& d) { return t.scale * d.cols; }
int algo_size(const Wrapper<table>& w) { return w.value.d.cols; }
}
""",
        """
#include "common.h"
namespace onemock {
double algo_run(const table& t, const dense& d) { return t.scale * d.cols; }
int algo_size(const Wrapper<table>& w) { return w.value.d.cols; }
}
""",
    ),
    (
        "libmock_io",
        """
#include "common.h"
namespace onemock {
int io_read(const dense& d) { return d.rows + d.cols; }
int io_write(const table& t) { return t.d.rows; }
}
""",
        """
#include "common.h"
namespace onemock {
int io_read(const dense& d) { return d.rows + d.cols; }
int io_write(const table& t) { return t.d.rows; }
int io_flush(const Wrapper<dense>& w) { return w.value.cols; }
}
""",
    ),
    (
        "libmock_util",
        """
#include "common.h"
namespace onemock {
int util_cols(const dense& d) { return d.cols; }
}
""",
        """
#include "common.h"
namespace onemock {
int util_cols(const dense& d) { return d.cols; }
}
""",
    ),
)


def _build_release(root: Path) -> tuple[Path, Path, Path]:
    """Compile every member's old and new side; return (old, new, headers)."""
    gxx = shutil.which("g++")
    if gxx is None:
        pytest.skip("g++ unavailable; cannot build the multi-member fixture")

    headers = root / "include"
    headers.mkdir(parents=True)
    (headers / "common.h").write_text(_COMMON_HEADER)

    sources = root / "sources"
    sources.mkdir()
    old_dir = root / "old"
    new_dir = root / "new"
    old_dir.mkdir()
    new_dir.mkdir()

    for name, old_src, new_src in _MEMBERS:
        for side, src, out_dir in (
            ("old", old_src, old_dir),
            ("new", new_src, new_dir),
        ):
            src_path = sources / f"{name}_{side}.cpp"
            src_path.write_text(src)
            out = out_dir / f"{name}.so"
            result = subprocess.run(
                [
                    gxx,
                    "-shared",
                    "-fPIC",
                    "-g",
                    "-O0",
                    f"-I{headers}",
                    str(src_path),
                    "-o",
                    str(out),
                    f"-Wl,-soname,{name}.so.1",
                ],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                pytest.fail(f"g++ failed for {name} ({side}): {result.stderr}")
    return old_dir, new_dir, headers


def _semantic_projection(report: dict) -> list[tuple]:
    """A run's findings reduced to what must not vary between runs.

    Deliberately excludes timings, paths and ordering-by-completion -- the
    claim is *semantic* stability, and a fan-out completes members in
    whatever order its workers finish.
    """
    libraries = report.get("libraries") or []
    projected = []
    for entry in libraries:
        findings = entry.get("findings") or []
        projected.append(
            (
                entry.get("library"),
                entry.get("verdict"),
                len(findings),
                sorted(
                    (str(f.get("kind")), str(f.get("symbol")), str(f.get("severity")))
                    for f in findings
                ),
            )
        )
    return sorted(projected)


@pytest.fixture(scope="module")
def release(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path, Path]:
    """Built once: four compiled members, old and new, sharing one header."""
    return _build_release(tmp_path_factory.mktemp("mock_release"))


@pytest.mark.integration
class TestConcurrentReleaseWorkflow:
    """The real entry point, on real artifacts, repeatedly."""

    def _run(self, release: tuple[Path, Path, Path]) -> tuple[int, dict, str]:
        from tests.test_compare_release import _invoke_combined  # noqa: PLC0415

        old_dir, new_dir, headers = release
        report_path = old_dir.parent / "report.json"
        code, combined = _invoke_combined(
            "compare",
            str(old_dir),
            str(new_dir),
            # The same public headers apply to both sides: the point of
            # the fixture is L2 header evidence, which is what drives the
            # spelling vocabularies the shared caches key on.
            "-H",
            str(headers / "common.h"),
            "-o",
            f"json={report_path}",
        )
        report = json.loads(report_path.read_text()) if report_path.exists() else {}
        return code, report, combined

    def test_every_member_completes_with_no_hidden_exception(
        self, release: tuple[Path, Path, Path]
    ) -> None:
        code, report, combined = self._run(release)

        libraries = report.get("libraries") or []
        names = sorted(str(entry.get("library")) for entry in libraries)
        assert names == sorted(f"{name}.so" for name, _o, _n in _MEMBERS), (
            f"member coverage is incomplete: {names}"
        )

        # No member failed operationally, and in particular none failed with
        # the internal error the shared caches used to raise.
        failed = [e for e in libraries if e.get("verdict") in {"ERROR", "unsupported"}]
        assert not failed, f"members failed: {failed}"
        assert "Error comparing" not in combined
        assert "KeyError" not in combined

        # The scope closed: a partial release must not read as a clean one.
        run_outcome = report.get("run_outcome") or {}
        assert run_outcome.get("scope") != "incomplete", run_outcome
        assert run_outcome.get("operational") in (None, "none"), run_outcome

        # The deliberate removal in libmock_core is still detected -- a run
        # that completed but found nothing would satisfy every assertion
        # above while asserting nothing about the analysis.
        assert code == 4, f"expected the seeded ABI break, got exit {code}"

    def test_findings_are_stable_across_repeated_concurrent_runs(
        self, release: tuple[Path, Path, Path]
    ) -> None:
        # The observed defect was *nondeterministic* -- one oneDAL run failed
        # three members, the next completed -- so a single green run is not
        # evidence. Repeating the whole fan-out is.
        projections = []
        for _ in range(3):
            code, report, combined = self._run(release)
            assert "Error comparing" not in combined
            projections.append((code, _semantic_projection(report)))
        first = projections[0]
        for index, other in enumerate(projections[1:], start=1):
            assert other == first, f"run {index} diverged from run 0"

    def test_findings_are_stable_across_worker_counts(
        self, release: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Sequential and parallel dispatch must agree.

        The fan-out sizes itself from the host (``jobs=0``), so the worker
        count is pinned here through the same resolver the release path
        consults -- the point being that the *only* difference between the
        runs is how many threads share the caches.
        """
        from abicheck.workflows import release_jobs  # noqa: PLC0415

        # Captured once, before any patching: re-reading the attribute
        # inside the loop would wrap the previous iteration's own wrapper.
        real = release_jobs.resolve_release_worker_count

        # Observe the dispatch itself, not just its output: a differential
        # test whose two configurations both ran the same path compares one
        # configuration with itself and passes no matter what the other
        # would have done (AGENTS.md, "A differential test must prove both
        # of its configurations actually ran").
        import abicheck.cli_compare_release_pairwise as pairwise  # noqa: PLC0415

        real_parallel = pairwise._compare_release_parallel
        parallel_calls: list[int] = []

        def spy_parallel(matched_keys, common_args, old_map, workers, admission=None):  # type: ignore[no-untyped-def]
            parallel_calls.append(workers)
            return real_parallel(matched_keys, common_args, old_map, workers, admission)

        monkeypatch.setattr(pairwise, "_compare_release_parallel", spy_parallel)

        # An operator budget turns the AST-costed admission gate off, so a
        # small runner cannot serialize the 2/4-worker runs this compares.
        monkeypatch.setenv("ABICHECK_RELEASE_JOB_MEM_GIB", "1")
        pinned_counts: list[int] = []
        results = {}
        for workers in (1, 2, 4):

            def pinned(*args: object, _w: int = workers, **kwargs: object):  # type: ignore[no-untyped-def]
                resolved = real(*args, **kwargs)  # type: ignore[arg-type]
                pinned_counts.append(_w)
                # Pinned means unclamped too: with a clamp in effect the pool
                # is the pre-clamp count and the memory gate throttles it.
                return (_w, None, *resolved[2:])

            monkeypatch.setattr(
                "abicheck.workflows.release_jobs.resolve_release_worker_count", pinned
            )
            parallel_calls.clear()
            code, report, combined = self._run(release)
            assert "Error comparing" not in combined, workers
            # `workers == 1` must take the sequential path and the others
            # the ThreadPoolExecutor one -- otherwise this loop ran one
            # configuration three times.
            if workers == 1:
                assert parallel_calls == [], "jobs=1 still dispatched in parallel"
            else:
                assert parallel_calls == [workers], (
                    f"jobs={workers} did not reach the parallel fan-out: "
                    f"{parallel_calls}"
                )
            results[workers] = (code, _semantic_projection(report))

        assert pinned_counts, "the worker-count resolver was never consulted"
        assert results[1] == results[2] == results[4], results


@pytest.mark.integration
class TestInstrumentedParallelReleaseMatchesStored:
    """Parallel live fan-out with in-worker instrumentation == stored/serial.

    The oneDAL measurement that reported members ending ``ERROR`` with
    ``tupleobject.c: bad argument to internal function`` ran the fan-out
    under a benchmark hook that took ``len(gc.get_objects())`` inside each
    worker's header-graph attach. That census broke the *other* workers'
    ``tuple(...)`` construction (``memory_trace.gc_census_is_safe``). The
    hook now goes through ``memory_trace.gc_object_count``; this states the
    workflow-level claim: with that hook installed in every worker and four
    workers pinned, the live report is the stored/serial report.
    """

    def test_live_parallel_with_census_hook_matches_stored_serial(
        self,
        release: tuple[Path, Path, Path],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import threading  # noqa: PLC0415

        import abicheck.cli_compare_release_pairwise as pairwise  # noqa: PLC0415
        from abicheck import service_dump_native as native  # noqa: PLC0415
        from abicheck.workflows import memory_trace, release_jobs  # noqa: PLC0415
        from tests.test_compare_release import _invoke_combined  # noqa: PLC0415

        old_dir, new_dir, headers = release
        header = str(headers / "common.h")

        censuses: list[tuple[bool, int | None]] = []
        real_attach = native._attach_header_graph

        def attach(snap, *args, **kwargs):  # type: ignore[no-untyped-def]
            snap = real_attach(snap, *args, **kwargs)
            censuses.append(
                (
                    threading.current_thread() is threading.main_thread(),
                    memory_trace.gc_object_count(),
                )
            )
            return snap

        monkeypatch.setattr(native, "_attach_header_graph", attach)
        monkeypatch.setenv("ABICHECK_RELEASE_JOB_MEM_GIB", "1")
        real_resolve = release_jobs.resolve_release_worker_count
        workers_now = [4]

        def pinned(*args: object, **kwargs: object):  # type: ignore[no-untyped-def]
            resolved = real_resolve(*args, **kwargs)  # type: ignore[arg-type]
            return (workers_now[0], None, *resolved[2:])

        monkeypatch.setattr(release_jobs, "resolve_release_worker_count", pinned)
        real_parallel = pairwise._compare_release_parallel
        parallel_calls: list[int] = []

        def spy_parallel(matched_keys, common_args, old_map, workers, admission=None):  # type: ignore[no-untyped-def]
            parallel_calls.append(workers)
            return real_parallel(matched_keys, common_args, old_map, workers, admission)

        monkeypatch.setattr(pairwise, "_compare_release_parallel", spy_parallel)

        live_report = tmp_path / "live.json"
        live_code, live_out = _invoke_combined(
            "compare", str(old_dir), str(new_dir), "-H", header,
            "-o", f"json={live_report}",
        )  # fmt: skip
        # Both halves of the differential actually ran: the fan-out went
        # parallel, and the census hook fired inside its workers, where it
        # must decline rather than enumerate the heap.
        assert parallel_calls == [4], parallel_calls
        worker_censuses = [n for on_main, n in censuses if not on_main]
        assert worker_censuses, f"the hook never ran in a worker: {censuses}"
        assert all(n is None for n in worker_censuses), worker_censuses

        stored = {"old": tmp_path / "old", "new": tmp_path / "new"}
        for side, lib_dir in (("old", old_dir), ("new", new_dir)):
            stored[side].mkdir()
            for lib in sorted(lib_dir.glob("*.so")):
                code, out = _invoke_combined(
                    "dump", str(lib), "-H", header,
                    "-o", str(stored[side] / f"{lib.name}.json"),
                )  # fmt: skip
                assert code == 0, out
        workers_now[0] = 1
        parallel_calls.clear()
        stored_report = tmp_path / "stored.json"
        stored_code, stored_out = _invoke_combined(
            "compare", str(stored["old"]), str(stored["new"]),
            "-o", f"json={stored_report}",
        )  # fmt: skip
        assert parallel_calls == [], "the stored oracle ran in parallel"

        live = json.loads(live_report.read_text())
        oracle = json.loads(stored_report.read_text())
        errored = [
            e for e in live.get("libraries") or [] if e.get("verdict") == "ERROR"
        ]
        assert not errored, errored
        assert "internal function" not in live_out
        # A stored member is named after its snapshot file (`libx.so.json`);
        # that suffix is the only thing the two paths may disagree on.
        for entry in oracle.get("libraries") or []:
            entry["library"] = str(entry.get("library")).removesuffix(".json")
        assert (live_code, _semantic_projection(live)) == (
            stored_code,
            _semantic_projection(oracle),
        ), (live_out, stored_out)


class TestMemberFailureIsReportedNotLaundered:
    """Failure injection at the fan-out's own exception boundary."""

    def _entry_for(self, exc: BaseException, tmp_path: Path) -> dict:
        from abicheck.frontends.cli.release_member_errors import (  # noqa: PLC0415
            member_error_entry,
        )

        return member_error_entry(
            exc,
            old_path=tmp_path / "libmock_core.so",
            old_version="1.0",
            new_version="2.0",
            output_dir=None,
        )

    def test_an_internal_failure_keeps_member_identity_and_its_class(
        self, tmp_path: Path
    ) -> None:
        # The exact shape the oneDAL run produced: a KeyError whose str() is
        # a bare tuple naming neither the subsystem nor the exception class.
        exc = KeyError((138821602538640, "const dense&", 0, 12))
        entry = self._entry_for(exc, tmp_path)

        assert entry["library"] == "libmock_core.so"
        assert entry["verdict"] == "ERROR"
        # `str(exc)` alone was the whole diagnostic before; the class name is
        # what makes it identifiable at all.
        assert entry["error_type"] == "KeyError"
        assert "const dense&" in str(entry["error"])

    def test_an_internal_failure_logs_a_traceback(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        import logging  # noqa: PLC0415

        try:
            raise KeyError((1, "const dense&", 0, 12))
        except KeyError as exc:
            raised = exc

        with caplog.at_level(logging.ERROR, logger="abicheck.release"):
            self._entry_for(raised, tmp_path)

        records = [r for r in caplog.records if r.name == "abicheck.release"]
        assert records, "no diagnostic was emitted for an internal failure"
        assert records[0].exc_info is not None, "the traceback was dropped"
        assert "libmock_core.so" in records[0].getMessage()

    def test_an_expected_contract_mismatch_is_not_reclassified(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A stated, expected outcome must stay one.

        ADR-050 D2's `not_comparable` and ADR-065 D6's `unsupported` are
        *not* internal failures, so adding diagnostics for the unexpected
        case must not drag them into the ERROR bucket or spam a traceback.
        """
        import logging  # noqa: PLC0415

        from abicheck.errors import (  # noqa: PLC0415
            ScopeMismatchError,
            UnsupportedArtifactError,
        )

        with caplog.at_level(logging.ERROR, logger="abicheck.release"):
            scope = self._entry_for(ScopeMismatchError("scopes differ"), tmp_path)
            unsupported = self._entry_for(
                UnsupportedArtifactError("no backend"), tmp_path
            )

        assert scope["verdict"] == "not_comparable"
        assert unsupported["verdict"] == "unsupported"
        assert not [r for r in caplog.records if r.name == "abicheck.release"]

    def test_the_outer_dispatch_boundary_reports_the_same_way(
        self, caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The fan-out's *second* boundary owes the same account.

        `_compare_one_library` classifies anything raised inside its own
        body; a failure reaching the executor loop escaped that -- in the
        dispatch, the context copy, or the classification itself. It is
        still an internal failure, so it must not degrade to a bare
        `str(exc)`, and it must still echo to stderr, which is what a user
        watching a long release run actually sees.
        """
        import logging  # noqa: PLC0415

        from abicheck.frontends.cli.release_member_errors import (  # noqa: PLC0415
            member_dispatch_failure_entry,
        )

        exc = KeyError((138821602538640, "const dense&", 0, 12))
        with caplog.at_level(logging.ERROR, logger="abicheck.release"):
            entry = member_dispatch_failure_entry(exc, "libmock_core.so")

        assert entry["library"] == "libmock_core.so"
        assert entry["verdict"] == "ERROR"
        assert entry["error_type"] == "KeyError"
        assert "const dense&" in str(entry["error"])

        records = [r for r in caplog.records if r.name == "abicheck.release"]
        assert records, "no diagnostic was emitted at the dispatch boundary"
        assert records[0].exc_info is not None, "the traceback was dropped"

        # The stderr line names the exception class too -- the original
        # report's `Error comparing <lib>: (<tuple>)` named neither the
        # class nor the subsystem, which is why it was undiagnosable.
        stderr = capsys.readouterr().err
        assert "libmock_core.so" in stderr
        assert "KeyError" in stderr

    def test_a_failed_member_cannot_produce_a_clean_release_exit(self) -> None:
        """The aggregation contract, stated directly.

        `OperationalStatus.EXTRACTION_ERROR` is reused deliberately -- its
        own definition is already "a library failed to dump/extract/compare",
        which covers an internal comparison failure, so no schema change is
        bought here. What matters is that it cannot fold away to success.
        """
        from abicheck.policy.outcome import (  # noqa: PLC0415
            OperationalStatus,
            PolicyGateDecision,
            fold_gate_and_operational,
            operational_status_exit_code,
        )

        assert operational_status_exit_code(OperationalStatus.EXTRACTION_ERROR) == 1
        # Even when every surviving member is perfectly compatible, the
        # release cannot report success.
        clean_gate = min(
            PolicyGateDecision, key=lambda d: d.value if isinstance(d.value, int) else 0
        )  # type: ignore[arg-type]
        assert (
            fold_gate_and_operational(clean_gate, OperationalStatus.EXTRACTION_ERROR)
            >= 1
        )


class TestFanOutExceptionBoundaryInSitu:
    """The outer boundary as the fan-out itself reaches it.

    The sibling test above calls `member_dispatch_failure_entry` directly.
    This one drives `_compare_release_parallel`, so the `except` arm in the
    executor loop -- the exact path the reported oneDAL failure took, and
    the one place a worker's exception becomes a member result -- is
    executed rather than assumed. It also states the release-level contract
    that matters most: one member's internal failure must not take its
    successful siblings with it.
    """

    def _common_args(self, tmp_path: Path, keys: tuple[str, ...]) -> tuple:
        old_map = {k: tmp_path / f"{k}.so" for k in keys}
        return (
            old_map,
            {k: tmp_path / f"{k}_new.so" for k in keys},
            None,
            None,
            lambda _o, _n: None,
            [],
            [],
            [],
            [],
            "1.0",
            "2.0",
            "c++",
            None,
            "strict_abi",
            None,
            True,
            True,
            False,
            None,
        )

    def test_one_worker_failure_does_not_lose_its_siblings(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from abicheck.cli_compare_release_pairwise import (  # noqa: PLC0415
            _compare_release_parallel,
        )

        # The reported shape: a match-cache key escaping as a bare KeyError
        # from inside one member's comparison.
        def fake_compare_one_library(key: str, *_args: object) -> dict[str, object]:
            if key == "b":
                raise KeyError((138821602538640, "const dense&", 0, 12))
            return {"library": f"{key}.so", "verdict": "NO_CHANGE"}

        monkeypatch.setattr(
            "abicheck.cli_compare_release_pairwise._compare_one_library",
            fake_compare_one_library,
        )

        keys = ("a", "b", "c")
        common_args = self._common_args(tmp_path, keys)
        results = _compare_release_parallel(list(keys), common_args, common_args[0], 3)

        by_library = {str(entry["library"]): entry for entry in results}
        # Every selected member is still accounted for -- a failure must not
        # silently drop the member from the release's coverage.
        assert set(by_library) == {"a.so", "b.so", "c.so"}
        # The successful siblings stayed reviewable.
        assert by_library["a.so"]["verdict"] == "NO_CHANGE"
        assert by_library["c.so"]["verdict"] == "NO_CHANGE"
        # And the failure kept its identity rather than becoming a bare tuple.
        failed = by_library["b.so"]
        assert failed["verdict"] == "ERROR"
        assert failed["error_type"] == "KeyError"
        assert "const dense&" in str(failed["error"])
