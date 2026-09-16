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

"""Does a finding's description already state its own old -> new transition?

Its own module rather than a helper inside ``pr_comment.py`` (at its
``architecture/debt.yaml`` no-growth baseline): this is a small, pure,
separately-testable text predicate with one job, and it guards something
load-bearing -- a row's *authoritative* old and new values are suppressed
only when the prose above them already says the same thing. Getting it
wrong hides the values the row exists to report, which is why it earned a
named module and its own test class rather than staying an inline
substring check.
"""

from __future__ import annotations

#: Characters that continue a value token. A delta match flanked by one of
#: these is part of a larger value, not the transition this row states.
VALUE_TOKEN_CHARS = frozenset("0123456789abcdefghijklmnopqrstuvwxyz_.-+")

#: Characters allowed to follow a delta and still leave it the complete
#: statement of the transition -- closing punctuation, nothing that could
#: itself be part of a value.
VALUE_STATEMENT_TERMINATORS = ").,;:] \t"

#: Unit words a description may place *after* a transition while still
#: stating exactly that transition. ``"Size changed: Ctx (64 -> 96 bits)"``
#: says precisely what a row's own ``64 -> 96`` would, so repeating it is the
#: duplication this module exists to suppress -- a trailing *unit* is not
#: more of the value, which is the thing the completion rule below guards
#: against. Kept to the bit/byte family because ``type_size_changed`` is the
#: kind that renders one; a unit this set does not know simply falls through
#: to the safe answer (report the values), never to a wrong suppression.
VALUE_TRAILING_UNITS = frozenset({"bit", "bits", "byte", "bytes", "b", "kb", "mb"})


def states_delta(desc: str, delta: str) -> bool:
    """Whether *desc* already states exactly the transition *delta*.

    A plain ``delta in desc`` is a substring test, and a transition is not a
    substring-safe token: ``"0 → 1"`` occurs inside ``"10 → 11"``, so a
    description stating some *other* numeric transition suppressed the row's
    own authoritative values.

    Boundaries alone are not enough either. Spaces are boundaries, so
    ``"unsigned int → long long"`` also matched inside ``"const unsigned int
    → long long volatile"``, whose values are a different pair (CodeRabbit
    review). So the match must additionally *complete* the description:
    anything but closing punctuation after it means the description states
    more of the value than this delta does.

    A decoy earlier in the string does not mask a real match later --
    ``"offset 100 → 104 and 0 → 1"`` really does state ``0 → 1``.

    A trailing *unit* is the one thing allowed to follow and still count as
    a complete statement (:data:`VALUE_TRAILING_UNITS`): ``"(64 → 96 bits)"``
    states the same transition a bare ``"64 → 96"`` does. Without that,
    every ``type_size_changed`` row rendered its delta twice -- the
    description's own ``(64 → 96 bits)`` followed by the row's ``(64 → 96)``
    (``tests/test_pr_comment_reporting.py::
    test_a_two_sided_delta_the_description_already_spells_is_not_repeated``).
    """
    start = desc.find(delta)
    while start != -1:
        end = start + len(delta)
        before = desc[start - 1].lower() if start > 0 else ""
        trailing = desc[end:].strip()
        remainder = trailing.strip(VALUE_STATEMENT_TERMINATORS)
        if before not in VALUE_TOKEN_CHARS and (
            not remainder or remainder.lower() in VALUE_TRAILING_UNITS
        ):
            return True
        start = desc.find(delta, start + 1)
    return False
