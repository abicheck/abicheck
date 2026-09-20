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

"""Contracts of the header-graph perf *harness* itself, not of its gate.

`test_header_graph_perf_gate.py` owns the regression algebra -- which gate
fires for which shape of slowdown. This module owns the two contracts that
are about the harness being a program that runs in someone else's
environment, both of which shipped broken and were caught by CI rather than
by a test:

* a metric's value must live in the domain `is_gateable` accepts, and
* an `abicheck` symbol the harness reads must resolve against a *different*
  installed `abicheck` than the checkout it ships in.

Split into its own file because `test_header_graph_perf_gate.py` sits at
`architecture/debt.yaml`'s 1200-line test cap, and because these are a
different subject -- per AGENTS.md, the way to shrink such a file is to
move responsibility to a properly-owned module, never to trim it to fit.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_GATE_PATH = (
    Path(__file__).resolve().parent.parent / "scripts" / "check_header_graph_perf.py"
)
_spec = importlib.util.spec_from_file_location("check_header_graph_perf", _GATE_PATH)
assert _spec and _spec.loader
hg_gate = importlib.util.module_from_spec(_spec)
sys.modules["check_header_graph_perf"] = hg_gate
_spec.loader.exec_module(hg_gate)

is_gateable = hg_gate.is_gateable


class TestGatedMetricDomain:
    """Every gated metric must live in the domain `is_gateable` accepts.

    The bug class: `perf_measurement.is_gateable` is written for wall-clock
    durations and rejects anything `<= 0` ("neither is a plausible
    wall-clock duration"). A metric whose value can *legitimately* be zero
    or negative therefore cannot be gated by it -- and, worse, its best
    outcome is indistinguishable from a broken measurement.

    Not hypothetical. `attach_retained_mib` was first defined as a signed
    delta (RSS after the attach minus RSS before it). On the clang backend
    the AST comes from the in-process memo the primary pass wrote, so the
    attach frees a tree it never allocated and the delta is negative. CI
    failed with `measured attach_retained_mib=-28.37890625 is not a
    gateable value` -- on a *better* result than the baseline's. Both
    memory metrics are now absolute RSS, which cannot be negative.

    Checked against real arithmetic through `_memory_metrics`, including
    the exact reading pattern that produced that failure, rather than by
    asserting the metric names look absolute.
    """

    @pytest.mark.parametrize(
        ("peak", "end"),
        [
            (600.0, 580.0),  # ordinary: ends below its own peak
            (600.0, 600.0),  # ends exactly at the peak
            (2215.2, 1291.5),  # the real oneDAL shape
            (0.5, 0.25),  # a tiny process
        ],
    )
    def test_every_metric_is_gateable_for_any_real_reading(self, peak, end):
        metrics = hg_gate._memory_metrics(peak_mib=peak, end_rss_mib=end)
        assert set(metrics) == set(hg_gate.MEMORY_METRICS)
        for name, value in metrics.items():
            assert is_gateable(value), (name, value)

    def test_a_net_release_is_still_gateable(self):
        """The exact case that failed CI, as a property rather than a number.

        A clang-backend attach releases more than it allocates, so *any*
        definition that subtracts a pre-attach reading goes negative here.
        The end-of-attach RSS stays positive because it is absolute, and
        that is the whole reason the metric is defined that way.
        """
        before, after = 900.0, 871.6  # a 28.4 MiB net release, as in CI
        assert before - after > 0  # i.e. a signed delta would be negative
        assert not is_gateable(after - before)
        metrics = hg_gate._memory_metrics(peak_mib=1200.0, end_rss_mib=after)
        assert all(is_gateable(v) for v in metrics.values())

    def test_no_gated_metric_is_named_like_a_delta(self):
        """A cheap structural guard on the next metric added here.

        Names are not the contract -- the arithmetic above is -- but a
        metric reintroduced as a `_delta`/`_retained`/`_growth` figure is
        overwhelmingly likely to be signed, and this is the one place that
        would notice before CI does.
        """
        suspicious = [
            m
            for m in hg_gate.METRICS
            if any(t in m for t in ("_delta", "_retained", "_growth", "_diff"))
        ]
        assert not suspicious, (
            f"{suspicious} read as signed quantities; is_gateable rejects "
            "<= 0, so a legitimate zero-or-negative result would fail the "
            "gate as an invalid measurement"
        )


class TestCrossVersionPackageImports:
    """This harness must import from an abicheck it did not ship with.

    `performance.yml`'s PR-vs-base job deliberately runs **head's** copy of
    `check_header_graph_perf.py` against **base's** installed package -- one
    harness measuring two products is the only way the two numbers are
    comparable. That makes every `abicheck` import in this script a
    cross-version compatibility surface, not an ordinary import.

    The bug class, stated as the invariant these tests check: *an
    `abicheck` symbol this harness reads must resolve against a package
    that predates the PR moving it.* Not hypothetical -- moving
    `HEADER_CALL_GRAPH_PASS` to its new owner module took the base
    measurement down with `ModuleNotFoundError` before it recorded a single
    sample, and the job's existing degrade-to-report-only grep did not
    match it, so it failed the whole job rather than degrading.

    Checked by actually hiding the module rather than by asserting on the
    source text: a test that greps for a `try`/`except ImportError` passes
    against one that catches the wrong thing.
    """

    @staticmethod
    def _hide(monkeypatch, *module_names: str) -> None:
        """Make `module_names` unimportable, as a pre-move package would."""
        import builtins

        real_import = builtins.__import__
        hidden = set(module_names)

        def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name in hidden:
                raise ModuleNotFoundError(f"No module named {name!r}", name=name)
            return real_import(name, globals, locals, fromlist, level)

        for name in hidden:
            monkeypatch.delitem(sys.modules, name, raising=False)
        monkeypatch.setattr(builtins, "__import__", fake_import)

    def test_pass_name_resolves_against_a_pre_move_package(self, monkeypatch):
        """The real base-branch shape: no new module, constant on the old one.

        Faithful to what `performance.yml` actually installs on the base
        side, rather than to a shape no build has ever had: hiding the new
        module *without* restoring the constant where it used to live would
        simulate a package that lost the symbol entirely, which is a broken
        install, not a previous release.
        """
        import abicheck.buildsource.header_graph as old_owner

        monkeypatch.setattr(
            old_owner, "HEADER_CALL_GRAPH_PASS", "header_call_graph", raising=False
        )
        self._hide(monkeypatch, "abicheck.buildsource.header_graph_ast_projection")
        assert hg_gate._header_call_graph_pass() == "header_call_graph"

    def test_pass_name_resolves_against_this_build(self, monkeypatch):
        # The other half of the same claim: the fallback must not be what is
        # always taken, or the harness would stop noticing a real rename.
        assert hg_gate._header_call_graph_pass() == "header_call_graph"

    def test_a_package_carrying_it_in_neither_place_still_raises(self, monkeypatch):
        """Not silently defaulting is the point.

        A harness that swallowed this and returned a guess would assert the
        attach stamped a pass name the package never defines -- an
        always-green check over a measurement that may be degraded. Better
        to abort the run loudly.
        """
        self._hide(
            monkeypatch,
            "abicheck.buildsource.header_graph_ast_projection",
            "abicheck.buildsource.header_graph",
        )
        with pytest.raises(ModuleNotFoundError):
            hg_gate._header_call_graph_pass()

    def test_the_value_is_read_from_the_package_not_hard_coded(self, monkeypatch):
        """A literal copy would verify a pass name no build stamps.

        The point of checking this constant at all is that the attach really
        stamped the pass *this* abicheck names. If the harness carried its
        own string, a future rename would leave it asserting against a value
        nothing produces -- green, over a degraded attach.
        """
        import abicheck.buildsource.header_graph_ast_projection as owner

        monkeypatch.setattr(owner, "HEADER_CALL_GRAPH_PASS", "renamed_in_this_build")
        assert hg_gate._header_call_graph_pass() == "renamed_in_this_build"
