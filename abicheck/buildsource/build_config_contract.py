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

"""``BuildConfig``'s ``contract:`` block: the config home of contract overlays.

One-comparison-product Phase 9c. An overlay is a concrete document that
widens or narrows the ``public`` contract domain beside the headers --
today only the POST Python export manifest (``post_manifest``, the
``contract_evidence_collect.PROVIDER_POST_MANIFEST`` provider). It is a
stable project property, so it lives in ``.abicheck.yml``::

    contract:
      overlays:
        post_manifest: python/abi/post_manifest.json

A relative path resolves against the project root
(``config_paths.project_root_for_config``), like ``compile.include_dirs``.

A sibling of ``build_config.py`` because that file sits at its ADR-061
no-growth baseline: the block's schema, parse and serialization live here,
and the parent carries only the field and one call per stage.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "CONTRACT_KEY",
    "OVERLAY_KINDS",
    "contract_block",
    "contract_findings",
    "parse_contract_post_manifest",
]

CONTRACT_KEY = "contract"
#: Overlay kinds a ``contract.overlays`` mapping may name.
OVERLAY_KINDS = frozenset({"post_manifest"})


def contract_findings(value: object) -> list[str]:
    """Structural findings for a raw ``contract:`` block (strict loading)."""
    if value is None:
        return []
    if not isinstance(value, dict):
        return [f"contract must be a mapping, got {type(value).__name__}: {value!r}"]
    findings = [
        f"unknown .abicheck.yml key contract.{k!r}" for k in value if k != "overlays"
    ]
    overlays = value.get("overlays")
    if overlays is None:
        return findings
    if not isinstance(overlays, dict):
        return [
            *findings,
            "contract.overlays must be a mapping of overlay kind -> document "
            f"path, got {type(overlays).__name__}: {overlays!r}",
        ]
    known = ", ".join(sorted(OVERLAY_KINDS))
    for kind, path in overlays.items():
        if kind not in OVERLAY_KINDS:
            findings.append(f"unknown contract overlay {kind!r} (known: {known})")
        elif not isinstance(path, str) or not path.strip():
            findings.append(f"contract.overlays.{kind} must be a non-empty path string")
    return findings


def parse_contract_post_manifest(top: dict[str, object]) -> str | None:
    """``contract.overlays.post_manifest`` as written, or ``None``."""
    block = top.get(CONTRACT_KEY)
    overlays = block.get("overlays") if isinstance(block, dict) else None
    value = overlays.get("post_manifest") if isinstance(overlays, dict) else None
    return value if isinstance(value, str) and value.strip() else None


def contract_block(cfg: Any) -> dict[str, Any]:
    """Non-default ``contract:`` keys of *cfg* (a ``BuildConfig``)."""
    if cfg.contract_post_manifest is None:
        return {}
    return {"overlays": {"post_manifest": cfg.contract_post_manifest}}
