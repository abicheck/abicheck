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

"""P2 review finding, split out of ``test_analysis_assurance.py`` (``_extra``
-style sibling, matching e.g. ``test_analysis_assurance_basic_channel_gap.
py``) purely to stay under the AI-readiness file-size no-growth debt
baseline -- the parent file is already at its adoption baseline.

Finding: ``AnalysisAssurance.debug_evidence`` was inserted before
``l3_context_status`` (and several other pre-existing fields) in the
dataclass body. Every field carries a default, so a caller using the
positional constructor would have every argument from ``l3_context_status``
onward silently shifted by one -- accepted without an exception, but bound
to the wrong field. Fixed by appending ``debug_evidence`` after every
pre-existing field and marking it keyword-only (mirrors
``AdvancedDwarfMetadata``'s own provenance fields, added for the identical
reason).
"""

from __future__ import annotations

import dataclasses
import inspect

from abicheck.analysis_assurance import AnalysisAssurance


class TestDebugEvidenceIsKeywordOnly:
    def test_signature_marks_it_keyword_only(self) -> None:
        sig = inspect.signature(AnalysisAssurance.__init__)
        debug_evidence_param = sig.parameters["debug_evidence"]
        assert debug_evidence_param.kind == inspect.Parameter.KEYWORD_ONLY

    def test_positional_construction_never_shifts_debug_evidence(self) -> None:
        """Bug class (P2 review): a caller passing fields positionally (the
        exact shape that would have silently mis-bound ``l3_context_status``
        et al. onto ``debug_evidence`` before this fix) must be rejected
        outright rather than accepted with shifted values. Exercises several
        independently-chosen positional call shapes, not just the exact
        reported one, per this repo's bug-class regression-testing
        contract.
        """
        positional_fields = [
            f.name
            for f in dataclasses.fields(AnalysisAssurance)
            if f.name != "debug_evidence"
        ]
        assert len(positional_fields) >= 8, (
            "expected several positional fields ahead of debug_evidence to "
            "make this a meaningful shift-detection test"
        )
        for n_args in (1, 3, len(positional_fields)):
            args = ["x"] * n_args
            try:
                aa = AnalysisAssurance(*args)
            except TypeError:
                continue  # a real type mismatch is also an acceptable reject
            else:
                # If construction succeeded, debug_evidence must still be
                # its untouched default -- never one of the positional args.
                assert aa.debug_evidence == {}
