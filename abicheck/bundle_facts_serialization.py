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

"""Compatibility facade for the historical ``abicheck.bundle_facts_serialization``
import path (G38 Phase 2).

ADR-061 gap E: the real implementation moved to ``storage.bundle_facts_codec``
-- ``BundleFacts`` (de)serialization is a ``storage`` responsibility now that
the value type itself is classified ``model`` (see ``storage/
bundle_facts_codec.py``'s own module docstring for why the historical
``bundle_facts.py``/``bundle_facts_serialization.py`` split existed and why
it no longer needs to). This module re-exports the same four public names
unchanged, so every existing ``from abicheck.bundle_facts_serialization
import ...`` call site -- including the test suite -- is unaffected.
``abicheck.serialization`` still resolves its own ``bundle_facts_to_dict``/
``bundle_facts_from_dict``/``load_bundle_facts``/``save_bundle_facts``
wrappers dynamically, now pointed at ``storage.bundle_facts_codec`` directly
rather than through this facade."""

from __future__ import annotations

from .storage.bundle_facts_codec import (
    bundle_facts_from_dict,
    bundle_facts_to_dict,
    load_bundle_facts,
    looks_like_bundle_facts_document,
    save_bundle_facts,
)

__all__ = [
    "bundle_facts_from_dict",
    "bundle_facts_to_dict",
    "load_bundle_facts",
    "looks_like_bundle_facts_document",
    "save_bundle_facts",
]
