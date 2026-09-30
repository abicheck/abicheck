# SPDX-License-Identifier: Apache-2.0
"""Compare regression corpus -- born as the scan-vs-compare parity harness.

Phase 0 of ``docs/contribute/plans/one-comparison-product.md`` built this
package to run ``scan`` and ``compare`` over the same inputs and diff their
*finding sets* (kind, resolved identity, severity, evidence refs), so a
capability ``compare`` could not reach failed a test by name. That migration
is finished: every scan-only capability reached ``compare`` (Phases 2a-2e),
the ``gaps.py`` registry emptied, and ADR-068 Phase 6 deleted ``scan``.

What remains pins ``compare``'s own behavior for those capabilities --
the eleven cross-source checks, the pattern and preprocessor scans,
changed-path localization, ``--abi3``, source-depth scoping and
``--no-baseline`` audits -- plus the call-site pin that each engine
primitive is reached from exactly one ``compare``-side workflow. Diffs stay
on structured finding sets, never rendered report text.
"""

from __future__ import annotations
