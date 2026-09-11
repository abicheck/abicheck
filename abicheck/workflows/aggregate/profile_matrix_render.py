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

"""Text rendering for one :class:`~.contracts.ProfileMatrixEntry` row.

Split out of ``fold.py`` (architecture/debt.yaml's ``no_growth`` line-count
ceiling for that file) -- a pure code move, no behavior change.
``render_profile_entry_line`` is a pure function of one already-computed
``ProfileMatrixEntry`` (no ``AggregateResult`` state), so it moves as a
plain module-level function rather than staying a method; ``fold.py``'s own
``_render_profile_entry_line`` is now a one-line delegator.
"""

from __future__ import annotations

from .contracts import ProfileMatrixEntry


def render_profile_entry_line(entry: ProfileMatrixEntry) -> str:
    """One base target's row in the profile matrix.

    Four mutually exclusive shapes -- affected, clean everywhere, partly
    clean with some profile never producing an analyzed result or
    producing no compatibility claim at all, and nothing "clean" at
    all -- then independent suffixes that qualify whichever shape was
    chosen.
    """
    unanalyzed = entry.unanalyzed_profiles
    audit_only = entry.audit_only_profiles
    # A profile is only "clean" if it produced a real, checked
    # compatibility verdict -- neither unanalyzed (nothing ran) nor
    # audit-only (a completed `compare --no-baseline` audit, ADR-068
    # D2: it makes no compatibility claim at all, clean or otherwise).
    # Folding the latter into "clean" would upgrade one-sided audit
    # completion into a compatibility statement this shape never
    # actually makes (Codex review, fresh evidence).
    not_clean = set(unanalyzed) | set(audit_only)
    if entry.affected_profiles:
        line = (
            f"  {entry.base_target}: affected on "
            f"{', '.join(entry.affected_profiles)} "
            f"(checked on {', '.join(entry.profiles)})"
        )
        if unanalyzed:
            # An affected profile and an unanalyzed one can
            # coexist on the same target -- don't let "checked
            # on" imply the unanalyzed one produced a result too
            # (Codex review).
            line += f"; no analyzed result on {', '.join(unanalyzed)}"
        if audit_only:
            line += (
                f"; audit-only (no compatibility verdict) on {', '.join(audit_only)}"
            )
    elif not not_clean:
        line = (
            f"  {entry.base_target}: clean on all checked profiles "
            f"({', '.join(entry.profiles)})"
        )
    elif len(not_clean) < len(entry.profiles):
        # Some profiles are clean, others never produced an analyzed
        # result, or produced only an audit-only completion -- never
        # call either of the latter two "clean" (Codex review).
        clean = [p for p in entry.profiles if p not in not_clean]
        line = f"  {entry.base_target}: clean on {', '.join(clean)} (checked on {', '.join(entry.profiles)})"
        if unanalyzed:
            line += f"; no analyzed result on {', '.join(unanalyzed)}"
        if audit_only:
            line += (
                f"; audit-only (no compatibility verdict) on {', '.join(audit_only)}"
            )
    elif not audit_only:
        line = (
            f"  {entry.base_target}: no analyzed result on any "
            f"checked profile ({', '.join(entry.profiles)})"
        )
    elif not unanalyzed:
        line = (
            f"  {entry.base_target}: audit-only (no compatibility "
            f"verdict) on all checked profiles ({', '.join(entry.profiles)})"
        )
    else:
        line = (
            f"  {entry.base_target}: no analyzed result on "
            f"{', '.join(unanalyzed)}; audit-only (no compatibility "
            f"verdict) on {', '.join(audit_only)} "
            f"(checked on {', '.join(entry.profiles)})"
        )
    if entry.incomplete_profiles:
        line += f" [incomplete coverage on {', '.join(entry.incomplete_profiles)}]"
    if entry.contract_incomplete_profiles:
        # Qualifies whatever precedes it, exactly as the
        # incomplete-coverage suffix above does -- including a
        # "clean" line, which stays accurate: clean is a
        # statement about compatibility, and this is the
        # orthogonal evidence axis saying the domain never
        # closed. Without it a profile that raised the exit to 1
        # on contract coverage alone read as flatly clean.
        line += (
            f" [contract evidence incomplete on "
            f"{', '.join(entry.contract_incomplete_profiles)}]"
        )
    if entry.analysis_incomplete_profiles:
        # The exact sibling suffix, for the exact sibling reason (Codex
        # review): a profile that raised the exit to 1 purely on the
        # analysis-assurance axis must not read as flatly clean either.
        line += (
            f" [analysis assurance incomplete on "
            f"{', '.join(entry.analysis_incomplete_profiles)}]"
        )
    if entry.scope_incomplete_profiles:
        line += f" [scope incomplete on {', '.join(entry.scope_incomplete_profiles)}]"
    return line
