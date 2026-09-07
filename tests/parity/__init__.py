# SPDX-License-Identifier: Apache-2.0
"""Phase 0 of docs/contribute/plans/one-comparison-product.md.

The scan-vs-compare parity harness: a fixture corpus plus a runner that
invokes ``scan`` and ``compare`` over the same inputs with equivalent
evidence and diffs the *finding sets* (kind, resolved identity, severity,
evidence refs) they produce — never the rendered report text.

This package exists while both ``scan`` and ``compare`` are still public
commands (ADR-068 D9). It is the only point at which the two can be
compared directly, and its job is to make a silent capability loss loud:
a check that runs under ``scan`` and produces nothing under ``compare``
must fail a test here, by name.

Do not add production-code changes alongside this package (Phase 0 is
parity-detection only, per the plan); a red entry in ``gaps.py`` records a
known gap, it does not excuse fixing it in this PR.

Distinct from ``tests/test_scan_compare_parity.py`` (ADR-049 Phase 5 §6.4):
that module asserts field-for-field agreement on the findings both tools
*already* produce (policy/suppression/scope config parity, on the ordinary
compare pipeline both share). This package asserts the opposite direction
-- which findings ``scan`` produces that ``compare`` cannot reach *at all*.
"""

from __future__ import annotations
