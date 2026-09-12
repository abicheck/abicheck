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

"""The ``experimental_namespaces:`` policy key (the opt-in that replaced the
unconditional ``v0`` default in ``DEFAULT_EXPERIMENTAL_NAMESPACES``).

A version segment is not a stability promise, so ``v0`` is no longer assumed
to mean "experimental"; a project that does mean it that way states it here.
These tests cover the whole route -- document -> ``PolicyFile`` ->
``PipelineContext`` -> ``DetectNamespacePatterns`` -- because the key's entire
purpose is to reach that last step, and a parsed-but-unthreaded field would
be a silently inert setting.
"""

from __future__ import annotations

import re

import pytest

from abicheck.checker import _experimental_namespaces
from abicheck.diff_namespaces import (
    DEFAULT_EXPERIMENTAL_NAMESPACES,
    _strip_experimental,
)
from abicheck.policy.policy_file_top_level import KNOWN_POLICY_FILE_KEYS
from abicheck.policy_file import PolicyError, PolicyFile
from abicheck.post_processing import DetectNamespacePatterns
from abicheck.post_processing_context import PipelineContext


def _load(tmp_path, text: str) -> PolicyFile:
    doc = tmp_path / "policy.yml"
    doc.write_text(text)
    return PolicyFile.load(doc)


class TestParsing:
    def test_key_is_recognized(self) -> None:
        assert "experimental_namespaces" in KNOWN_POLICY_FILE_KEYS

    def test_absent_key_leaves_default(self, tmp_path) -> None:
        pf = _load(tmp_path, "overrides: {}\n")
        assert pf.experimental_namespaces == []
        assert pf.experimental_namespaces_stated is False

    def test_stated_values_parse(self, tmp_path) -> None:
        pf = _load(tmp_path, "experimental_namespaces: [experimental, v0]\n")
        assert pf.experimental_namespaces == ["experimental", "v0"]
        assert pf.experimental_namespaces_stated is True

    def test_explicit_empty_list_is_a_statement(self, tmp_path) -> None:
        """An empty list is "this project has none", distinct from absence --
        the same distinction ``internal_namespaces_stated`` draws."""
        pf = _load(tmp_path, "experimental_namespaces: []\n")
        assert pf.experimental_namespaces == []
        assert pf.experimental_namespaces_stated is True

    @pytest.mark.parametrize(
        "body",
        [
            "experimental_namespaces: v0\n",
            "experimental_namespaces: {a: b}\n",
            "experimental_namespaces: [1]\n",
            "experimental_namespaces: [experimental, null]\n",
        ],
    )
    def test_malformed_values_are_hard_errors(self, tmp_path, body: str) -> None:
        with pytest.raises(PolicyError, match="experimental_namespaces"):
            _load(tmp_path, body)


class TestThreading:
    """The field must actually reach the detection step."""

    def test_checker_derivation(self) -> None:
        assert _experimental_namespaces(None) == ()
        assert _experimental_namespaces(PolicyFile()) == ()
        assert _experimental_namespaces(
            PolicyFile(experimental_namespaces=["v0", "preview"])
        ) == ("v0", "preview")

    def test_step_prefers_context_over_default(self) -> None:
        step = DetectNamespacePatterns()
        recorded: dict[str, tuple[str, ...]] = {}

        def _capture(_old, _new, *, experimental_namespaces):
            recorded["ns"] = experimental_namespaces
            return []

        import abicheck.diff_namespaces as dn

        original = dn.detect_namespace_patterns
        dn.detect_namespace_patterns = _capture
        try:
            ctx = _ctx(experimental_namespaces=("v0",))
            step.run([], ctx)
            assert recorded["ns"] == ("v0",)

            step.run([], _ctx())
            assert recorded["ns"] == DEFAULT_EXPERIMENTAL_NAMESPACES
        finally:
            dn.detect_namespace_patterns = original

    def test_explicit_constructor_argument_still_wins(self) -> None:
        """A directly-constructed step keeps precedence over the context,
        mirroring the internal-namespace steps' own ordering."""
        step = DetectNamespacePatterns(experimental_namespaces=("preview",))
        recorded: dict[str, tuple[str, ...]] = {}

        def _capture(_old, _new, *, experimental_namespaces):
            recorded["ns"] = experimental_namespaces
            return []

        import abicheck.diff_namespaces as dn

        original = dn.detect_namespace_patterns
        dn.detect_namespace_patterns = _capture
        try:
            step.run([], _ctx(experimental_namespaces=("v0",)))
        finally:
            dn.detect_namespace_patterns = original
        assert recorded["ns"] == ("preview",)


def _ctx(**kw) -> PipelineContext:
    from abicheck.model import AbiSnapshot

    snap = AbiSnapshot(library="libtest.so.1", version="1.0")
    return PipelineContext(old=snap, new=snap, **kw)


class TestVersionSegmentIsNotAStabilityPromise:
    def test_version_segment_is_not_experimental_by_default(self) -> None:
        """A version segment is not a stability promise.

        ``v0`` was an unconditional default, which reclassified removals from
        an inline-versioned *current public* API (``namespace v0``) as
        experimental-API removals. Projects that do mean it that way now state
        it via a policy document's ``experimental_namespaces:`` key.
        """
        assert "v0" not in DEFAULT_EXPERIMENTAL_NAMESPACES
        for seg in DEFAULT_EXPERIMENTAL_NAMESPACES:
            assert not re.fullmatch(r"v\d+", seg), seg

    def test_v0_still_strippable_when_explicitly_configured(self) -> None:
        """The opt-in path must still work exactly as the default used to."""
        configured = (*DEFAULT_EXPERIMENTAL_NAMESPACES, "v0")
        assert _strip_experimental("svs::runtime::v0::Index", configured) == (
            "svs::runtime::Index",
            "v0",
        )
        # ...and is inert without it.
        assert _strip_experimental("svs::runtime::v0::Index") == (
            "svs::runtime::v0::Index",
            None,
        )


class TestEffectiveConfigDigest:
    """ADR-069: two runs differing only in ``experimental_namespaces`` must not
    report the same effective configuration.

    The key changes which namespace-pattern findings are emitted, so a digest
    that ignored it would let a JSON report claim identical configuration
    across comparisons that produced different findings (Codex review, P1).
    """

    def test_field_key_is_registered(self) -> None:
        from abicheck.effective_config_digest import EFFECTIVE_CONFIG_FIELD_KEYS

        assert "surface.experimental_namespaces" in EFFECTIVE_CONFIG_FIELD_KEYS

    def test_digest_distinguishes_differing_namespace_sets(self) -> None:
        from abicheck.effective_config_digest import (
            EFFECTIVE_CONFIG_FIELD_KEYS,
            effective_config_digest,
        )

        base = dict.fromkeys(EFFECTIVE_CONFIG_FIELD_KEYS, "")
        without = effective_config_digest(base)
        with_v0 = effective_config_digest(
            {**base, "surface.experimental_namespaces": "experimental;preview;v0"}
        )
        other = effective_config_digest(
            {**base, "surface.experimental_namespaces": "experimental;preview"}
        )
        assert without != with_v0
        assert with_v0 != other

    def test_both_tiers_project_the_policy_file_value(self) -> None:
        """Not just the key list -- both real projections must populate it, or
        the digest silently reads empty for every run."""
        from abicheck.effective_config_digest import (
            effective_config_fields_from_diff_result,
            effective_config_fields_from_full_config,
        )
        from abicheck.policy.effective_gate import EffectiveGate

        pf = PolicyFile(experimental_namespaces=["experimental", "v0"])

        class _Result:
            policy_file = pf

        gate = EffectiveGate.from_severity(None)
        baseline = effective_config_fields_from_diff_result(_Result(), gate=gate)
        assert "v0" in baseline["surface.experimental_namespaces"]

        rich = effective_config_fields_from_full_config(
            None, result=_Result(), policy_file=pf, gate=gate
        )
        assert "v0" in rich["surface.experimental_namespaces"]


class TestPositionalConstructionIsPreserved:
    """A new ``PolicyFile`` field must not silently rebind positional args.

    The two fields this change adds sit *before* the evidence-policy slots, so
    as ordinary dataclass fields they shifted every later positional parameter
    -- an existing 8th positional argument meant for ``source_only_findings``
    would have bound to ``experimental_namespaces`` instead, quietly changing
    both the namespace findings and the evidence policy (Codex review, P1).
    ``kw_only=True`` is the fix, matching ``reclassify``/``versioning``.

    Stated as the whole positional signature rather than one field's index, so
    this fails for *any* future field inserted mid-class, not only for the two
    added here.
    """

    #: The positional parameter order of ``PolicyFile.__init__``. Appending is
    #: fine; inserting is not -- a new field belongs at the end or, better,
    #: ``kw_only=True``.
    EXPECTED_POSITIONAL_ORDER = (
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

    def _positional_fields(self) -> tuple[str, ...]:
        import dataclasses

        return tuple(f.name for f in dataclasses.fields(PolicyFile) if not f.kw_only)

    def test_positional_order_is_unchanged(self) -> None:
        assert self._positional_fields() == self.EXPECTED_POSITIONAL_ORDER

    def test_new_namespace_fields_are_keyword_only(self) -> None:
        positional = self._positional_fields()
        assert "experimental_namespaces" not in positional
        assert "experimental_namespaces_stated" not in positional

    def test_positional_construction_still_binds_evidence_policy(self) -> None:
        """The concrete break: the 8th positional argument must still be
        ``source_only_findings``, not the newly-inserted field."""
        pf = PolicyFile(
            "strict_abi", {}, None, "", [], ["detail"], True, "error"
        )
        assert pf.source_only_findings == "error"
        assert pf.experimental_namespaces == []


class TestExperimentalFindingsAreAnOverlay:
    """`EXPERIMENTAL_*` is appended to the ordinary break, never a relabelling.

    ADR-069's first draft claimed configuring `v0` *relabelled* a removal
    rather than adding a second finding, and that wrong claim was repeated in
    the ADR, the policy docs, the changelog and a review reply before Codex
    caught it. Prose was the only thing asserting it; this makes the real
    semantics executable so the next reader cannot restate it wrongly.

    The load-bearing consequence is that dropping `v0` from the defaults cannot
    hide a break: the plain removal is emitted either way and already carries
    the stronger verdict.
    """

    @staticmethod
    def _removal_pair():
        from abicheck.model import AbiSnapshot, Function, Visibility

        fn = Function(
            name="foo",
            mangled="_ZN2ns2v03fooEv",
            return_type="void",
            visibility=Visibility.PUBLIC,
        )
        old = AbiSnapshot(library="libt.so.1", version="1.0", functions=[fn])
        new = AbiSnapshot(library="libt.so.1", version="1.0", functions=[])
        return old, new

    def _kinds(self, policy_file: PolicyFile | None) -> set[str]:
        from abicheck.checker import compare

        old, new = self._removal_pair()
        return {c.kind.value for c in compare(old, new, policy_file=policy_file).changes}

    def test_plain_removal_is_reported_either_way(self) -> None:
        configured = PolicyFile(experimental_namespaces=["v0"])
        assert "func_removed" in self._kinds(None)
        assert "func_removed" in self._kinds(configured)

    def test_configuring_v0_adds_a_finding_rather_than_replacing_one(self) -> None:
        default = self._kinds(None)
        configured = self._kinds(PolicyFile(experimental_namespaces=["v0"]))
        assert configured > default, "expected a strict superset (an overlay)"
        assert configured - default == {"experimental_removed_without_replacement"}

    def test_the_default_change_cannot_hide_the_break(self) -> None:
        """The verdict is identical with and without the overlay."""
        from abicheck.checker import compare

        old, new = self._removal_pair()
        without = compare(old, new)
        with_overlay = compare(
            old, new, policy_file=PolicyFile(experimental_namespaces=["v0"])
        )
        assert without.verdict == with_overlay.verdict
        assert without.verdict.value == "BREAKING"
