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

"""Bug classes about turning a model into its persisted or projected form.

A sibling of `manifest_evidence.py`/`manifest_guards.py`/
`manifest_report.py`/`manifest_tool_surface.py` (see `manifest.py` for what
this registry is and is not). The classes here are about the *encode*
direction specifically: a walk over a model tree that produces bytes, a dict,
or a digest.

The two share a root cause worth naming once: in both, a generic tree-walking
primitive got a *special* value wrong while handling the general case
correctly -- a `str`-subclass `Enum` silently downcast by a rewrite that
returns a new plain `str`, and a field excluded from a projection by mutating
the source rather than by skipping it. Neither is a wrong computation over
the right input; both are a general walk meeting a case its generality does
not actually cover.
"""

from __future__ import annotations

from .bug_class_schema import BugClass

SERIALIZATION_BUG_CLASSES: tuple[BugClass, ...] = (
    BugClass(
        id="serialization.pure_projection_mutates_its_own_input",
        invariant=(
            "A projection function documented as pure with respect to its "
            "argument -- a serializer, encoder, digest or any other "
            "read-only view -- must never reach its result by *writing to "
            "that argument*, however briefly, and a `try/finally` that "
            "restores the original values does not make it pure. For the "
            "window between the write and the restore the caller's own "
            "object is observably wrong to every other holder of it: a "
            "second serialization, a reader on another thread, or a "
            "callback reached from inside the projection's own walk. "
            "Fields the projection must not descend into are excluded at "
            "the point of the walk (a skip set, a filtered field "
            "iteration), never by clearing them on the source. The "
            "regression test must observe the argument *from inside* the "
            "projection -- a comparison taken before and after the call "
            "cannot fail, because the `finally` has already restored "
            "everything by the time the call returns, which is exactly why "
            "this defect survived in `snapshot_to_dict()` unnoticed."
        ),
        fixed_by=(1323,),
        seed_tests=("tests/test_snapshot_encode_single_pass.py",),
    ),
    BugClass(
        id="serialization.str_enum_downcast_via_generic_rewrite",
        invariant=(
            "A generic tree-walking string-collect/rewrite primitive "
            "(`isinstance(value, str)`-gated) must never treat a "
            "`str`-subclass `Enum` field (e.g. `ParamKind(str, Enum)`) as "
            "ordinary rewritable free text -- even though isinstance() is "
            "true for it too -- because a real rewrite function (`re.sub`) "
            "returns a genuinely new, plain `str` object even on zero "
            "substitutions, silently downcasting the field's type while "
            "leaving its string value unchanged. The walk must exclude any "
            "`str`-subclass `Enum` member regardless of that enum's own "
            "vocabulary, not special-case the one field that happened to "
            "crash a caller."
        ),
        fixed_by=(985,),
        seed_tests=(
            "tests/test_param_kind_enum_identity.py",
            "tests/test_str_enum_downcast_walk.py",
        ),
    ),
)
