# SPDX-License-Identifier: Apache-2.0
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

"""Structural backstop: a pre-gate `non_gating` cannot forget its marker.

Split out of `test_disposition_scope_matrix.py` (which reached the 1200-line
test cap): these two tests scan every real `record(...)` call site under
`abicheck/`, so they are `repo_scan` tests, run once by CI's
`repo-scan-tests` job rather than on every unit leg.
"""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# The structural backstop: a pre-gate `non_gating` cannot forget its marker
# ---------------------------------------------------------------------------
#
# Three review rounds each found one more call site that produced a
# `non_gating` label before the gate ever ran and left `gate_excluded` unset,
# letting a severity configuration promote the finding back into a gate it was
# never scored by. Fixing them one at a time is what produced three rounds; the
# rule now lives in `DispositionLedger.record`, which derives the marker rather
# than trusting each caller, and these tests are what keep it underivable-by-
# accident: they inspect the real call sites and the real step metadata, not a
# hand-listed set of fixtures.


def _record_call_sites():
    """Every ``…record(<disposition>, …)`` call under ``abicheck/``.

    Yields ``(path, lineno, source)`` for each call to a method named
    ``record`` — the ledger's own recording entry point. An AST walk rather
    than a text scan, so a call split across lines is one site and a mention
    in a comment or docstring is none.
    """
    import ast
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent / "abicheck"
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "record"
            ):
                yield path, node


@pytest.mark.repo_scan
def test_only_a_gate_resolved_call_may_declare_from_gate() -> None:
    """`from_gate=True` is the one way to opt a `non_gating` record out of
    the marker, so it may only be used where the disposition really did come
    from the gate — i.e. where `_kept_disposition` produced it.

    This is the mechanical half. Without it, the derivation in `record` is a
    convention a future call site can defeat by passing `from_gate=True` for
    a label the gate never produced, which is the same bug in a new spelling.
    """
    import ast

    offenders = []
    for path, call in _record_call_sites():
        declares = any(
            kw.arg == "from_gate"
            and isinstance(kw.value, ast.Constant)
            and kw.value.value is True
            for kw in call.keywords
        )
        if not declares:
            continue
        rendered = ast.unparse(call)
        if "_kept_disposition" not in rendered:
            offenders.append(f"{path.name}:{call.lineno}: {rendered.splitlines()[0]}")
    assert not offenders, (
        "these call sites claim their disposition came from the gate without "
        "computing it with `_kept_disposition`:\n" + "\n".join(offenders)
    )


@pytest.mark.repo_scan
def test_no_call_site_passes_a_literal_false_gate_exclusion() -> None:
    """The other way to defeat the derivation: state the old default aloud.

    `gate_excluded=False` on an explicitly-labelled `non_gating` record is
    exactly the bug the last three rounds fixed, spelled as an assertion. The
    marker is derived; a caller that needs the record open to severity says
    `from_gate=True`, which the test above then holds to its word.
    """
    import ast

    offenders = [
        f"{path.name}:{call.lineno}"
        for path, call in _record_call_sites()
        for kw in call.keywords
        if kw.arg == "gate_excluded"
        and isinstance(kw.value, ast.Constant)
        and kw.value.value is False
    ]
    assert not offenders, (
        "pass `from_gate=True` (and mean it) rather than re-stating the "
        f"pre-derivation default at: {offenders}"
    )
