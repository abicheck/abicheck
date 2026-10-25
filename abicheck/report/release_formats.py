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

"""The formats a directory/package (release) ``compare`` can render.

A leaf module, so the CLI's ``compare`` routing, the release command's
``--format`` choices and ``scripts/gen_action_cli_surface.py`` (which feeds the
Action's pre-install check) all read one constant without importing each other.
"""

from __future__ import annotations

#: Every ``compare`` format except ``terminal``: the release fan-out renders the
#: whole single-pair set, one analysis per library and one summary for the release.
RELEASE_FORMATS = frozenset(
    {"json", "markdown", "junit", "oneline", "html", "sarif", "review"}
)

#: The formats a stored-``BundleFacts`` ``compare`` renders. A stored OLD_INPUT
#: has no per-member fan-out and no ``--view``. Every format here is rendered
#: from one live-shaped release evaluation of the stored comparison (its receipt
#: recomputed, its unknown fields explicit ``unavailable`` markers); JSON and
#: Markdown keep the stored document's own shape and carry it as ``release``.
STORED_RELEASE_FORMATS = frozenset({"json", "markdown", "junit", "oneline", "html"})
