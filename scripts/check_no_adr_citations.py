#!/usr/bin/env python3
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
"""Fail when a user-facing file cites an internal ADR id.

One pattern for every gate that checks rendered output: the Action report-policy
CI cell (``.github/workflows/test-action.yml``) and
``tests/test_no_adr_in_user_text.py`` both import it from here, so the rule
cannot drift between them. Usage: ``check_no_adr_citations.py FILE [FILE ...]``.
Exit 0 when clean, 1 on a citation, 2 when no file was given (an empty check
must not pass).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

#: An ADR id used as a citation: ``ADR-049``, ``ADR 23``, ``ADR068``. The word
#: boundary keeps ``CADR-12`` and ``ADRESS`` from matching.
ADR_CITATION_RE = re.compile(r"\bADR[- ]?\d")


def find_citations(text: str) -> list[tuple[int, str]]:
    """``(line number, line)`` for every line of *text* that cites an ADR."""
    return [
        (lineno, line)
        for lineno, line in enumerate(text.splitlines(), 1)
        if ADR_CITATION_RE.search(line)
    ]


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: check_no_adr_citations.py FILE [FILE ...]", file=sys.stderr)
        return 2
    failed = False
    for name in argv:
        for lineno, line in find_citations(Path(name).read_text(encoding="utf-8")):
            print(f"{name}:{lineno}: {line.strip()[:160]}")
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
