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

"""Positional parameter order of long-lived entry points is append-only
(bug class ``api.positional_slot_rebinding``).

Adding a parameter or dataclass field *in the middle* of a signature whose
trailing parameters all have defaults is silently breaking: every existing
positional caller keeps working, keeps type-checking, and starts binding its
arguments one slot to the left. Nothing raises. Two independent instances
landed in a single change (PR #1231) and both were caught in review rather
than by any test, which is what makes this a class rather than two slips:

* ``PolicyFile`` -- two new fields declared before the evidence-policy slots
  moved ``source_only_findings`` from positional index 7 to 9, so an existing
  8th positional argument bound to ``experimental_namespaces``: the namespace
  findings changed *and* the intended evidence policy was silently dropped.
* ``PostProcessingPipeline.run`` -- a parameter slotted next to
  ``internal_namespaces`` (where it reads better) displaced
  ``disposition_ledger``, so a positional caller's ledger became the
  namespace collection: auditing off, and a non-iterable ``DispositionLedger``
  handed to ``DetectNamespacePatterns``.

Both signatures already carried comments warning about exactly this, which is
the point: prose in the file did not prevent it twice. This states it
executably instead.

The assertions pin the *whole* order rather than one symbol's index, so they
fail for any future mid-signature insertion, not only for the two that
prompted them. Appending is intentionally allowed (it cannot rebind anything);
keyword-only is better still, and preferred for new parameters.
"""

from __future__ import annotations

import dataclasses
import inspect

import pytest

from abicheck.policy_file import PolicyFile
from abicheck.post_processing import PostProcessingPipeline

#: Positional parameters of ``PostProcessingPipeline.run``, in order.
#: Append here when a parameter is added at the END; never reorder or insert.
EXPECTED_PIPELINE_RUN_POSITIONAL = (
    "self",
    "changes",
    "old",
    "new",
    "suppression",
    "frozen_namespaces",
    "scope_to_public_surface",
    "force_public_symbols",
    "collapse_versioned_symbols",
    "public_surface_allowlist",
    "internal_namespaces",
    "disposition_ledger",
    "experimental_namespaces",
)

#: Positional fields of ``PolicyFile``, in order. Same rule; a new field
#: should normally be ``kw_only=True`` instead of appended here.
EXPECTED_POLICY_FILE_POSITIONAL = (
    "base_policy",
    "overrides",
    "source_path",
    "source_sha256",
    "frozen_namespaces",
    "internal_namespaces",
    "internal_namespaces_stated",
    "source_only_findings",
    "build_context_drift",
    "graph_risk_findings",
    "require_evidence",
)


def _positional_parameters(func: object) -> tuple[str, ...]:
    return tuple(
        name
        for name, p in inspect.signature(func).parameters.items()
        if p.kind
        in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    )


def _positional_fields(cls: type) -> tuple[str, ...]:
    return tuple(f.name for f in dataclasses.fields(cls) if not f.kw_only)


class TestPositionalOrderIsAppendOnly:
    def test_pipeline_run_signature(self) -> None:
        assert (
            _positional_parameters(PostProcessingPipeline.run)
            == EXPECTED_PIPELINE_RUN_POSITIONAL
        )

    def test_policy_file_fields(self) -> None:
        assert _positional_fields(PolicyFile) == EXPECTED_POLICY_FILE_POSITIONAL

    @pytest.mark.parametrize(
        ("actual", "expected"),
        [
            ("pipeline", EXPECTED_PIPELINE_RUN_POSITIONAL),
            ("policy_file", EXPECTED_POLICY_FILE_POSITIONAL),
        ],
    )
    def test_expected_orders_have_no_duplicates(
        self, actual: str, expected: tuple[str, ...]
    ) -> None:
        """Guards the guard: a copy-paste slip in the pinned tuples above
        would otherwise make the real assertions vacuously satisfiable."""
        assert len(set(expected)) == len(expected), actual


class TestTheTwoConcreteRebindings:
    """The specific bindings that broke, exercised through real construction
    rather than through the signature introspection above -- a pinned tuple
    proves the order, not that the order means what we think it means."""

    def test_eighth_policy_file_positional_is_the_evidence_policy(self) -> None:
        pf = PolicyFile("strict_abi", {}, None, "", [], ["detail"], True, "error")
        assert pf.source_only_findings == "error"
        assert pf.experimental_namespaces == []

    def test_twelfth_pipeline_positional_is_the_ledger(self) -> None:
        from abicheck.model import AbiSnapshot
        from abicheck.policy.disposition_ledger import DispositionLedger

        snap = AbiSnapshot(library="libtest.so.1", version="1.0")
        ledger = DispositionLedger()
        ctx = PostProcessingPipeline([]).run(
            [], snap, snap, None, None, False, None, False, None, None, ledger
        )
        assert ctx.disposition_ledger is ledger
        assert ctx.experimental_namespaces is None
