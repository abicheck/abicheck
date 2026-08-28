# SPDX-License-Identifier: Apache-2.0
# Copyright The abicheck Authors
"""ADR-061's ``compare`` responsibility package.

Owns matching old/new entities and identifying a raw change. Most of that
behavior still lives in the flat ``diff_*`` modules `architecture/
modules.yaml` lists as this layer's ``legacy_paths``; new code belongs here.
"""

from __future__ import annotations
