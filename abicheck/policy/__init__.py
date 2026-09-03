# SPDX-License-Identifier: Apache-2.0
# Copyright The abicheck Authors
"""ADR-061's ``policy`` responsibility package.

Owns deciding relevance, suppression, classification, severity, and exit-code
(gate) effect for an already-identified change. Most of that behavior still
lives in flat root modules `architecture/modules.yaml` lists as this layer's
``legacy_paths`` (``analysis_assurance.py``, plus the several genuinely
cross-layer leaf modules ``compare`` and ``policy`` code both depend on --
``checker_policy.py``, ``contract_gating.py``, ``reclassify.py`` -- that stay
unclassified by design; see ``AGENTS.md``); new code belongs here.
"""

from __future__ import annotations
