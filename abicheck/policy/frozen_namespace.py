# Copyright 2026 Nikolay Petrov
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

"""The one frozen-namespace pattern match (``PolicyFile.frozen_namespaces``).

``post_processing``'s ``EscalateFrozenNamespaceViolations`` tags a finding
with the pattern it falls in, and ``DemoteUnreachableInternalChurn`` keeps
such a finding in surface. They used to build their candidate names
differently -- demotion looked only at the root type, escalation at the
symbol, cause and qualified name -- so a finding escalation would tag could
be demoted first and never escalated. Both now gather the same candidate
forms and ask :func:`frozen_pattern_for`.
"""

from __future__ import annotations

import fnmatch
import re
from collections.abc import Iterable, Sequence

_TEMPLATE_ARGS_RE = re.compile(r"<[^<>]*>")


def _strip_template_args(name: str) -> str:
    prev = None
    while prev != name:
        prev, name = name, _TEMPLATE_ARGS_RE.sub("", name)
    return name


def frozen_pattern_for(forms: Iterable[str], patterns: Sequence[str]) -> str | None:
    """The first pattern any of *forms* -- or any enclosing scope of one --
    matches, or ``None``.

    Every ancestor scope is tried, so ``**::detail::r1`` matches both
    ``ns::detail::r1::foo`` and the deeper ``ns::detail::r1::sub::foo``.
    """
    for form in forms:
        candidate = _strip_template_args(form)
        while True:
            for pat in patterns:
                if fnmatch.fnmatchcase(candidate, pat):
                    return pat
            if "::" not in candidate:
                break
            candidate = candidate.rsplit("::", 1)[0]
    return None
