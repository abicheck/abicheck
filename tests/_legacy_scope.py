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

"""The config spelling of the retired ``--no-scope-public-headers`` flag.

One-comparison-product Phase 9b deleted ``--scope-public-headers``/
``--no-scope-public-headers``. Header-origin scoping stays on for a run with
no ``--contract``; a test that needs the legacy *unscoped* reading without
also turning on contract evaluation (which ``--contract all`` does) states
``.abicheck.yml``'s ``scope.public: false`` instead -- the exact setting the
opt-out flag used to override. One helper, so every such test spells it the
same way.
"""

from __future__ import annotations

from pathlib import Path


def no_scope_config_args(directory: Path) -> list[str]:
    """``["--config", <path>]`` for a config whose only setting is
    ``scope.public: false``, written under *directory*."""
    cfg = directory / "no-scope.abicheck.yml"
    cfg.write_text("scope:\n  public: false\n", encoding="utf-8")
    return ["--config", str(cfg)]


#: A committed ``scope.public: false`` document, for callers that build a
#: subprocess command line with no scratch directory of their own
#: (``tests/validate_examples.py``, ``scripts/benchmark_comparison.py``).
NO_SCOPE_CONFIG = Path(__file__).parent / "fixtures" / "no_scope.abicheck.yml"


def scope_args(scope_public_headers: bool) -> list[str]:
    """The ``compare`` args selecting a case's legacy scope reading: nothing
    for scoped (the default), ``--config`` naming :data:`NO_SCOPE_CONFIG`
    for unscoped."""
    return [] if scope_public_headers else ["--config", str(NO_SCOPE_CONFIG)]


def post_manifest_config_args(
    directory: Path, manifest: Path, *, scoped: bool = True
) -> list[str]:
    """``["--config", <path>]`` for ``contract.overlays.post_manifest`` --
    the only spelling of the retired ``--post-manifest`` (one-comparison-
    product Phase 9d) -- plus ``scope.public: false`` when *scoped* is
    false, in one document since ``--config`` names exactly one."""
    cfg = directory / "post-manifest.abicheck.yml"
    text = f"contract:\n  overlays:\n    post_manifest: {str(manifest)!r}\n"
    if not scoped:
        text += "scope:\n  public: false\n"
    cfg.write_text(text, encoding="utf-8")
    return ["--config", str(cfg)]
