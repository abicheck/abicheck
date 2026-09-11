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
