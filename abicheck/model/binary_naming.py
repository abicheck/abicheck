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

"""Vendored-library filename normalization (ADR-061 D1).

Split out of ``binary_utils.py`` (a whole-file-classified ``extract`` module)
because :func:`strip_vendor_hash` is a pure string transform with no I/O and
no other ``abicheck`` dependency — the same shape ``qualified_name_segments.py``
has, and the same reason it belongs in ``model`` rather than ``extract``:
several ``compare``-layer detectors (`diff_platform.py`,
`diff_platform_elf_dynamic.py`, `diff_versioning.py`, `diff_wheel_deployment.py`,
`diff_cpp_patterns.py`) need this normalization to pair a vendored library
across a rebuild, and ``compare`` may not import ``extract``
(`architecture/modules.yaml`). Lives physically under ``model/`` (not as a
flat-root ``legacy_paths`` entry) since it is new code with no pre-existing
root import path needing a compatibility facade — unlike
``qualified_name_segments.py``, which predates ADR-061's classification
(Codex review, PR #942). ``binary_utils.py``'s own ``_canonical_library_key``
(which *does* need file I/O, via ``_pe_is_dll_content``) imports this back —
an ``extract -> model`` edge, which is allowed.
"""

from __future__ import annotations

import re

#: `auditwheel` (Linux) and `delocate` (macOS) rewrite each vendored library to
#: ``lib<name>-<hex>.so.<ver>`` / ``lib<name>-<hex>.dylib`` and rewrite its
#: SONAME/install-name to match, so the hash changes on every rebuild even
#: though the underlying dependency didn't. Restricted to a hyphen + 6-16 hex
#: chars immediately before ``.so``/``.dylib`` (or a numeric version
#: component leading to one) so ordinary hyphenated names — e.g.
#: ``libwebpdemux``, ``libbrotlicommon``, or a real ``-cafe`` (too short) —
#: are never touched (G9, ADR: docs/contribute/plans/g9-wheel-vendored-matching.md).
#: The lookahead ``(?=[0-9a-f]*[a-f])`` requires at least one non-decimal hex
#: letter in the run: without it, a purely-decimal 6-16-digit suffix (a
#: legitimate embedded build/version number, e.g. ``libfoo-100200.so.1`` vs.
#: ``libfoo-100300.so.1``) also matched and stripped to the same key,
#: silently hiding a real SONAME/dependency change as vendor-hash noise —
#: the exact false-negative an ABI-breaking-change detector must not produce
#: (self-review finding).
#: Case-insensitive (``re.IGNORECASE``): a vendored library can carry an
#: uppercase-hex or uppercase-extension spelling (``libfoo-ABCDEF.SO.1``) --
#: matching only lowercase let two releases differing solely in that
#: generated hash's case key as unrelated libraries, reporting spurious
#: removal/addition noise (Codex). Only the matched hash/extension span is
#: affected -- re.sub() replaces just that span with "", so the rest of the
#: name (and every other consumer of the stripped result) keeps its
#: original case.
#: The numeric-version lookahead branch (``\.\d+(?:\.\d+)*\.(?:so|dylib)\b``,
#: e.g. ``libfoo-abcdef.1.2.so``, a version segment sitting between the hash
#: and the extension) must still require a real ``.so``/``.dylib`` suffix
#: somewhere after it -- a bare ``\.\d`` matched *any* numeric-looking
#: continuation regardless of what followed, so a non-library filename that
#: merely happens to contain a hyphenated hex run before a numeric segment
#: (e.g. ``plugin-abcdef.1.json``) was silently mis-stripped to
#: ``plugin.1.json`` (CodeRabbit review, PR #942, fresh evidence) -- a false
#: positive this normalization must not produce, the same class of bug the
#: at-least-one-hex-letter guard above already exists to prevent for the
#: purely-decimal case.
_VENDOR_HASH_RE = re.compile(
    r"-(?=[0-9a-f]*[a-f])[0-9a-f]{6,16}"
    r"(?=\.(?:so|dylib)\b|\.\d+(?:\.\d+)*\.(?:so|dylib)\b)",
    re.IGNORECASE,
)


def strip_vendor_hash(name: str) -> str:
    """Strip an auditwheel/delocate content-hash suffix from a library name.

    Pairing on the unhashed stem lets ``compare-release`` diff two wheels'
    vendored libraries directly instead of reporting every one as
    removed+added noise every rebuild (G9), and lets SONAME/install-name
    diffing treat a hash-only rebuild as unchanged rather than a spurious
    ``SONAME_CHANGED``. A genuinely changed vendored dependency (e.g. a
    SONAME major bump) still surfaces as a real break — this only normalizes
    the filename/SONAME spelling, never the content.
    """
    return _VENDOR_HASH_RE.sub("", name)
