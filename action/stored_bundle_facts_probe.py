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
"""Pre-install answer to "is this operand a stored BundleFacts document?".

``action/validate-inputs.sh`` runs before ``pip install abicheck``, yet it
must know whether ``old-library`` is a stored ``BundleFacts`` document,
because that comparison renders a narrower format set
(``STORED_RELEASE_FORMATS``, the generated ``stored-bundle-facts`` lines of
``action/cli-surface.txt``) than any other directory/package compare.

**No second classifier.** The answer is the CLI's own
``abicheck.workflows.bundle_compare_operand.looks_like_stored_bundle_facts``
-- the predicate ``compare``'s routing calls -- imported from the Action's
own source tree (``$1``, the same tree ``install-abicheck.sh`` installs from
and ``cli-surface.txt`` was generated from). Its import closure is the
standard library only (``tests/test_action_stored_operand_probe.py`` runs it
under ``python -S``, with no site-packages), so it runs before the install.
A shell or Python re-implementation would be the second transcription
ADR-070 forbids; a path-extension heuristic was rejected because a stored
``ProjectSnapshot`` has the same on-disk shape.

**Bounded reads only.** The predicate reads a bounded, decoded prefix and
never fully decompresses or JSON-parses the operand, which is what makes it
safe on an attacker-controlled PR checkout before any config-derived decode
limit exists. The interpreter runs with ``-I`` (the caller's job), so the
untrusted working directory is never on ``sys.path``; only ``$1`` is added.

Exit codes: ``0`` stored BundleFacts, ``1`` not, ``2`` cannot decide (the
interpreter is older than the package's floor, or the tree is not
importable). The validator skips the check on ``2``; the CLI still enforces
the narrowing after install.

Kept to Python 3.8 syntax so an old runner interpreter reaches the ``2``
answer instead of a ``SyntaxError``.
"""

import sys

_STORED, _NOT_STORED, _UNDECIDED = 0, 1, 2

#: ``requires-python`` of the package this imports from (pyproject.toml).
_MIN_PYTHON = (3, 11)


def main(argv):
    if len(argv) != 3:
        sys.stderr.write("usage: stored_bundle_facts_probe.py ACTION_ROOT PATH\n")
        return _UNDECIDED
    if sys.version_info < _MIN_PYTHON:
        return _UNDECIDED
    action_root, operand = argv[1], argv[2]
    sys.path.insert(0, action_root)
    try:
        from pathlib import Path

        import abicheck
        from abicheck.workflows.bundle_compare_operand import (
            looks_like_stored_bundle_facts,
        )
    except Exception:  # noqa: BLE001 - any import failure is "cannot decide"
        return _UNDECIDED
    # Only the Action's own tree answers: an abicheck found anywhere else
    # (an older install in the runner's site-packages) is another version's
    # classifier, so it is "cannot decide", not an answer.
    root = Path(action_root).resolve()
    if root not in Path(abicheck.__file__).resolve().parents:
        return _UNDECIDED
    try:
        stored = looks_like_stored_bundle_facts(Path(operand))
    except Exception:  # noqa: BLE001 - documented never to raise; stay safe
        return _UNDECIDED
    return _STORED if stored else _NOT_STORED


if __name__ == "__main__":
    sys.exit(main(sys.argv))
