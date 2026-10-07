# SPDX-License-Identifier: Apache-2.0
# Copyright The abicheck Authors
"""ADR-061's ``policy`` responsibility package.

Owns deciding relevance, suppression, classification, severity, and exit-code
(gate) effect for an already-identified change. Most of that behavior still
lives in flat root modules `architecture/modules.yaml` lists as this layer's
``legacy_paths`` (``analysis_assurance.py``, ...); new code belongs here.
``checker_policy.py`` stays an unclassified re-export only because the public
Python API documentation names it.
"""

from __future__ import annotations
