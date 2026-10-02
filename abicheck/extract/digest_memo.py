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

"""The content digest per-file pass memos key on.

Header scans run the same pure pass over the same file several times per
comparison (old, new and combined header sets, two language-mode
polarities, every release member). Callers key a
:class:`~abicheck.model.execution_cache.MemoryCache` on :func:`content_digest`
of the bytes -- never the bytes themselves -- so the memo retains only
results, and an edited file can never be served a stale one. The memo itself
is the central wrapper's (design-hardening plan, Phase 4); this module only
owns the digest.
"""

from __future__ import annotations

import hashlib


def content_digest(content: bytes) -> bytes:
    """A 64-byte BLAKE2b digest of *content*."""
    return hashlib.blake2b(content).digest()
