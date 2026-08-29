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

"""Tests for :mod:`abicheck.model.binary_naming`, ``strip_vendor_hash``'s
real home (ADR-061 D1, split out of ``binary_utils.py`` so ``compare``-layer
detectors can use it without a forbidden ``compare -> extract`` edge).

Behavioral coverage of ``strip_vendor_hash`` itself lives in
``test_vendor_hash.py`` (imported via the ``binary_utils`` back-compat
re-export) and is not duplicated here -- this file only pins the split
itself: the new canonical import path resolves, and the back-compat
re-export is the identical function object, not a copy that could drift.
"""

from __future__ import annotations

from abicheck import binary_utils
from abicheck.model import binary_naming


def test_strip_vendor_hash_importable_from_its_new_canonical_home() -> None:
    assert binary_naming.strip_vendor_hash("libfoo-abcdef123456.so.1") == "libfoo.so.1"


def test_binary_utils_reexports_the_identical_function_object() -> None:
    """The back-compat re-export in ``binary_utils.py`` must be the same
    function object, not an independent copy -- otherwise the two could
    silently diverge (e.g. a fix applied to one and not the other)."""
    assert binary_utils.strip_vendor_hash is binary_naming.strip_vendor_hash
