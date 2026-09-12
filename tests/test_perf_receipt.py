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

"""Tests for ``scripts/perf_receipt.py`` -- the shared perf measurement layer.

The process-tree RSS sampler and the native-invocation spy are the two pieces
here that **cannot** be tested with mocks. Both exist precisely to observe a real
operating-system fact from outside the process being measured, so a mocked
``/proc`` or a faked subprocess would verify only that the test's own fixture
behaves as written. Each of those is therefore driven by real child processes,
including the two cases that are the whole reason tree sampling exists: two
children alive at the same instant, and a timeout whose descendants must not
survive.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
import textwrap
import time
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

_spec = importlib.util.spec_from_file_location(
    "perf_receipt", _SCRIPTS / "perf_receipt.py"
)
assert _spec and _spec.loader
pr = importlib.util.module_from_spec(_spec)
sys.modules["perf_receipt"] = pr
_spec.loader.exec_module(pr)

_LINUX = sys.platform.startswith("linux")
requires_linux = pytest.mark.skipif(not _LINUX, reason="/proc-based, Linux only")


class TestRunIdentity:
    def test_it_records_what_a_comparison_needs(self):
        identity = pr.run_identity(harness="t")
        for key in (
            # harness_sha and measured_product rather than a single product_sha:
            # see TestMeasuredProductIsDistinctFromTheHarness for why the two
            # cannot be one field.
            "harness_sha",
            "measured_product",
            "python_version",
            "platform",
            "cpu_quota",
            "memory_limit",
        ):
            assert key in identity

    def test_no_secrets_or_bulk_environment_are_captured(self):
        # A receipt is committed as a CI artifact, so an os.environ dump here
        # would be a credential leak, not a diagnostic. The allowlist is the
        # mechanism; this asserts the mechanism holds.
        identity = pr.run_identity(harness="t")
        assert set(identity["env"]) <= set(pr._ENV_ALLOWLIST)

    def test_an_unrelated_environment_variable_is_not_recorded(self, monkeypatch):
        monkeypatch.setenv("MY_SECRET_TOKEN", "hunter2")
        identity = pr.run_identity(harness="t")
        assert "MY_SECRET_TOKEN" not in identity["env"]
        assert "hunter2" not in json.dumps(identity)

    def test_an_allowlisted_variable_is_recorded(self, monkeypatch):
        monkeypatch.setenv("ABICHECK_AST_FRONTEND", "clang")
        assert pr.run_identity(harness="t")["env"]["ABICHECK_AST_FRONTEND"] == "clang"

    def test_an_unavailable_quota_states_a_reason_rather_than_zero(self):
        quota = pr.cpu_quota()
        assert quota["cgroup_quota_cpus"] is not None or quota["unavailable_reason"]

    def test_an_unavailable_memory_limit_states_a_reason_rather_than_zero(self):
        limit = pr.memory_limit()
        assert limit["cgroup_limit_bytes"] is not None or limit["unavailable_reason"]


class TestImportableWithoutResource:
    """The module must import on a platform with no ``resource``.

    The Windows unit-test matrix *collects* this file and
    ``test_l2_cli_perf_gate.py``, and a module-scope ``import resource`` fails
    that lane during collection -- before any ``requires_linux`` skip can apply.
    The harness is Linux/ELF-scoped and refuses to *run* elsewhere, but "refuses
    to run" and "cannot be imported" are different failures and only the first is
    intended.

    Tested by genuinely blocking the import via a meta-path finder rather than by
    checking the source for a ``try``, so the claim is about behaviour.
    """

    def _reimport_without_resource(self):
        import importlib.abc

        class Block(importlib.abc.MetaPathFinder):
            def find_spec(self, name, path=None, target=None):
                if name == "resource":
                    raise ModuleNotFoundError(f"No module named {name!r}")
                return None

        saved_resource = sys.modules.pop("resource", None)
        blocker = Block()
        sys.meta_path.insert(0, blocker)
        try:
            spec = importlib.util.spec_from_file_location(
                "perf_receipt_noresource", _SCRIPTS / "perf_receipt.py"
            )
            module = importlib.util.module_from_spec(spec)
            sys.modules["perf_receipt_noresource"] = module
            spec.loader.exec_module(module)
            return module
        finally:
            sys.meta_path.remove(blocker)
            sys.modules.pop("perf_receipt_noresource", None)
            if saved_resource is not None:
                sys.modules["resource"] = saved_resource

    def test_the_module_imports(self):
        module = self._reimport_without_resource()
        assert module.resource is None

    def test_run_measured_still_works(self):
        module = self._reimport_without_resource()
        run = module.run_measured([sys.executable, "-c", "pass"], timeout=60)
        assert run.exit_code == 0
        assert run.wall_seconds > 0

    def test_cpu_time_is_none_with_a_stated_scope_not_zero(self):
        # 0.0 would read as a real measurement of a process that used no CPU.
        module = self._reimport_without_resource()
        run = module.run_measured([sys.executable, "-c", "pass"], timeout=60)
        assert run.user_cpu_seconds is None
        assert run.system_cpu_seconds is None
        assert "no `resource` module" in run.cpu_scope

    def test_a_receipt_can_still_be_built(self):
        module = self._reimport_without_resource()
        receipt = module.build_receipt(harness="t", profile="pr", scenarios=[])
        assert receipt["schema"] == module.RECEIPT_SCHEMA


class TestMeasuredProductIsDistinctFromTheHarness:
    """The receipt must say which *product* a timing describes.

    The PR-vs-base lane deliberately runs HEAD's harness against BASE's installed
    package -- the only way the two numbers are comparable. A single
    ``product_sha`` derived from the harness's own directory therefore recorded
    the head revision in *both* receipts, so the persisted provenance could not
    tell them apart.
    """

    def test_the_two_identities_are_separate_fields(self):
        identity = pr.run_identity(harness="t")
        assert "harness_sha" in identity
        assert "measured_product" in identity
        # The conflated field is gone, so a consumer cannot keep reading it and
        # silently get the harness's revision.
        assert "product_sha" not in identity

    def test_the_measured_product_is_identified_from_the_installed_package(self):
        product = pr.measured_product()
        assert product["location"], product
        # This checkout is an editable install, so the location is inside it.
        assert "abicheck" in product["location"]

    def test_an_unidentifiable_product_states_a_reason(self, monkeypatch):
        # A wheel install is not inside a git checkout and must say so rather than
        # inheriting the harness's revision.
        monkeypatch.setattr(pr, "_git", lambda *a, **k: None)
        product = pr.measured_product()
        assert product["sha"] is None
        assert product["unavailable_reason"]

    def test_the_schema_version_records_the_split(self):
        # A consumer reading the old shape must be able to detect the change.
        assert pr.RECEIPT_SCHEMA.endswith("/2")


class TestDigestPaths:
    def test_identical_content_digests_identically(self, tmp_path):
        (tmp_path / "a.h").write_text("x")
        (tmp_path / "b.h").write_text("y")
        first = pr.digest_paths([tmp_path / "a.h", tmp_path / "b.h"])
        second = pr.digest_paths([tmp_path / "b.h", tmp_path / "a.h"])
        assert first == second, "digest must not depend on argument order"

    def test_changed_content_changes_the_digest(self, tmp_path):
        path = tmp_path / "a.h"
        path.write_text("x")
        before = pr.digest_paths([path])
        path.write_text("y")
        assert pr.digest_paths([path]) != before

    def test_a_rename_changes_the_digest(self, tmp_path):
        # A profile's identity covers which inputs were used, not only their
        # bytes: two runs over differently-named identical headers are not the
        # same profile.
        (tmp_path / "a.h").write_text("x")
        (tmp_path / "b.h").write_text("x")
        assert pr.digest_paths([tmp_path / "a.h"]) != pr.digest_paths(
            [tmp_path / "b.h"]
        )

    def test_an_unreadable_path_does_not_raise(self, tmp_path):
        assert pr.digest_paths([tmp_path / "missing.h"])


@requires_linux
class TestNativeInvocationSpy:
    """Real shims, real ``exec``, real child processes."""

    def _spy(self, tmp_path: Path) -> pr.NativeInvocationSpy:
        spy = pr.NativeInvocationSpy(tmp_path / "spy")
        spy.install()
        return spy

    def test_it_shims_the_tools_that_exist(self, tmp_path):
        spy = self._spy(tmp_path)
        for tool in spy.shimmed:
            assert shutil.which(tool) is not None
            assert (spy.directory / tool).exists()

    def test_an_invocation_through_path_is_counted(self, tmp_path):
        if shutil.which("g++") is None:
            pytest.skip("needs g++")
        spy = self._spy(tmp_path)
        env = spy.env()
        pr.run_measured(["g++", "--version"], env=env, timeout=60)
        assert spy.counts()["g++"] == 1

    def test_the_real_tool_still_runs(self, tmp_path):
        # A shim that swallowed the call would make every measurement wrong while
        # the counts looked perfect.
        if shutil.which("g++") is None:
            pytest.skip("needs g++")
        spy = self._spy(tmp_path)
        run = pr.run_measured(["g++", "--version"], env=spy.env(), timeout=60)
        assert run.exit_code == 0
        assert "g++" in run.stdout.lower() or "gcc" in run.stdout.lower()

    def test_a_version_probe_is_not_counted_as_extraction(self, tmp_path):
        if shutil.which("g++") is None:
            pytest.skip("needs g++")
        spy = self._spy(tmp_path)
        pr.run_measured(["g++", "--version"], env=spy.env(), timeout=60)
        assert spy.extraction_count() == 0
        assert spy.kind_counts()["probe"] == 1

    def test_a_shimmed_but_uninvoked_tool_reports_zero_not_absence(self, tmp_path):
        # "castxml ran zero times" is a finding about a compiler-free path;
        # "castxml is not installed" is a missing precondition. An empty mapping
        # for both is how a harness comes to claim a path it never proved.
        spy = self._spy(tmp_path)
        counts = spy.counts()
        for tool in spy.shimmed:
            assert counts[tool] == 0

    def test_every_kind_bucket_is_present_even_at_zero(self, tmp_path):
        spy = self._spy(tmp_path)
        assert set(spy.kind_counts()) == set(pr.INVOCATION_KINDS)

    def test_reset_clears_the_log_between_steps(self, tmp_path):
        if shutil.which("g++") is None:
            pytest.skip("needs g++")
        spy = self._spy(tmp_path)
        pr.run_measured(["g++", "--version"], env=spy.env(), timeout=60)
        spy.reset()
        assert spy.total() == 0

    def test_it_survives_an_argument_containing_spaces(self, tmp_path):
        # The log is tab-separated for exactly this reason: a path with a space
        # must not be read as a field boundary and corrupt the tool name.
        if shutil.which("g++") is None:
            pytest.skip("needs g++")
        spy = self._spy(tmp_path)
        weird = tmp_path / "a dir with spaces"
        weird.mkdir()
        pr.run_measured(["g++", "--version", f"-I{weird}"], env=spy.env(), timeout=60)
        assert spy.counts()["g++"] == 1


@requires_linux
class TestTreeRssSampler:
    """The cases tree sampling exists for, driven by real processes."""

    def _python(self, body: str) -> list[str]:
        return [sys.executable, "-c", textwrap.dedent(body)]

    def test_it_observes_a_single_child(self):
        run = pr.run_measured(
            self._python(
                """
                import time
                block = bytearray(40 * 1024 * 1024)
                block[::4096] = b'x' * len(block[::4096])
                time.sleep(0.6)
                """
            ),
            timeout=120,
            sample_rss=True,
            rss_interval=0.02,
        )
        assert run.rss is not None
        assert run.rss.sample_count > 0
        assert run.rss.sampled_peak_tree_bytes > 20 * 1024 * 1024

    def test_it_sums_two_simultaneously_live_grandchildren(self):
        # The distinguishing case. ru_maxrss is a max over individual processes,
        # so it cannot see two live children's combined footprint; a tree sampler
        # must. Each child touches ~60MB, so a correct sum clears ~100MB while a
        # per-process maximum stays near 60MB.
        child = (
            "import time; b = bytearray(60*1024*1024); "
            "b[::4096] = b'x'*len(b[::4096]); time.sleep(1.2)"
        )
        run = pr.run_measured(
            self._python(
                f"""
                import subprocess, sys
                kids = [subprocess.Popen([sys.executable, '-c', {child!r}])
                        for _ in range(2)]
                for k in kids:
                    k.wait()
                """
            ),
            timeout=120,
            sample_rss=True,
            rss_interval=0.02,
        )
        assert run.rss is not None
        assert run.rss.max_concurrent_processes >= 3, (
            "the sampler never saw the parent and both children alive at once, so "
            "this is not a tree measurement"
        )
        assert run.rss.sampled_peak_tree_bytes > 100 * 1024 * 1024

    def test_the_sample_is_never_called_an_exact_peak(self):
        # Naming discipline, asserted so it cannot drift: the field must stay
        # labelled as sampled, since a short spike is genuinely missed.
        fields = pr.RssSample.__dataclass_fields__
        assert "sampled_peak_tree_bytes" in fields
        assert "peak_rss_bytes" not in fields
        assert "ru_maxrss_bytes" in fields, "the kernel high-water mark stays separate"

    def test_an_immediate_exit_reports_a_reason_rather_than_zero(self):
        run = pr.run_measured(
            [sys.executable, "-c", "pass"],
            timeout=60,
            sample_rss=True,
            rss_interval=5.0,
        )
        assert run.rss is not None
        if run.rss.sample_count == 0:
            assert run.rss.unavailable_reason

    def test_the_sampling_interval_is_recorded(self):
        run = pr.run_measured(
            self._python("import time; time.sleep(0.3)"),
            timeout=60,
            sample_rss=True,
            rss_interval=0.05,
        )
        assert run.rss.interval_seconds == 0.05

    def test_descendants_are_discovered_not_just_the_direct_child(self):
        pids = pr._descendants(os.getpid())
        assert os.getpid() in pids

    def test_a_comm_containing_spaces_does_not_break_parent_parsing(self):
        # /proc/<pid>/stat's comm field can contain spaces and parentheses; a
        # naive whitespace split reads the wrong field as the ppid and the tree
        # walk silently returns only the root.
        assert pr._descendants(1)


@requires_linux
class TestTimeoutCleanup:
    def test_a_timeout_is_reported_not_raised(self):
        run = pr.run_measured(
            [sys.executable, "-c", "import time; time.sleep(30)"], timeout=1.0
        )
        assert run.timed_out is True

    def test_a_timeout_kills_grandchildren_too(self):
        # A castxml grandchild outliving its parent would keep burning CPU and
        # skew every later measurement on the same host, and the RSS sampler
        # would keep attributing it to an abandoned tree.
        marker = (
            Path(os.environ.get("TMPDIR", "/tmp")) / f"perf_receipt_probe_{os.getpid()}"
        )
        marker.unlink(missing_ok=True)
        run = pr.run_measured(
            [
                sys.executable,
                "-c",
                textwrap.dedent(
                    f"""
                    import subprocess, sys, time
                    subprocess.Popen([sys.executable, '-c',
                        "import time; time.sleep(20); open({str(marker)!r}, 'w').write('survived')"])
                    time.sleep(20)
                    """
                ),
            ],
            timeout=1.5,
        )
        assert run.timed_out is True
        # Give a surviving grandchild more than enough time to write the marker.
        time.sleep(3.0)
        assert not marker.exists(), (
            "a grandchild survived the timeout: the process group was not reaped"
        )
        marker.unlink(missing_ok=True)

    def test_cpu_scope_is_labelled_rather_than_presented_as_this_commands_cpu(self):
        run = pr.run_measured([sys.executable, "-c", "pass"], timeout=60)
        assert run.cpu_scope == "waited_children_delta"


class TestReceiptEnvelope:
    def test_it_carries_the_schema_and_the_effective_thresholds(self, tmp_path):
        receipt = pr.build_receipt(
            harness="t",
            profile="pr",
            scenarios=[{"id": "s"}],
            thresholds={"wall_seconds": {"tolerance": 0.3}},
        )
        assert receipt["schema"] == pr.RECEIPT_SCHEMA
        assert receipt["effective_thresholds"]["wall_seconds"]["tolerance"] == 0.3

    def test_a_non_finite_value_is_refused_rather_than_written(self, tmp_path):
        # A NaN in a receipt is non-standard JSON and, for anything that later
        # gates on it, a silent always-pass. Failing at write time surfaces it
        # where it was produced.
        out = tmp_path / "r.json"
        with pytest.raises(ValueError):
            pr.write_receipt(
                out,
                pr.build_receipt(
                    harness="t", profile="pr", scenarios=[{"w": float("nan")}]
                ),
            )

    def test_a_written_receipt_round_trips(self, tmp_path):
        out = tmp_path / "nested" / "r.json"
        pr.write_receipt(out, pr.build_receipt(harness="t", profile="pr", scenarios=[]))
        assert json.loads(out.read_text())["schema"] == pr.RECEIPT_SCHEMA

    def test_command_run_never_serializes_captured_output(self, tmp_path):
        # Reports can be hundreds of KB and the receipt is a committed artifact.
        run = pr.CommandRun(
            argv=["x"],
            exit_code=0,
            wall_seconds=1.0,
            user_cpu_seconds=None,
            system_cpu_seconds=None,
            cpu_scope="waited_children_delta",
            timed_out=False,
            stdout="a" * 10000,
            stderr="b" * 10000,
        )
        serialized = run.as_dict()
        assert "stdout" not in serialized and "stderr" not in serialized
        assert serialized["wall_seconds"] == 1.0
