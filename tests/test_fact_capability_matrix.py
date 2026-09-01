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

"""The generated fact/capability registry doc (ADR-063 D7/Phase 5) stays
in sync with `abicheck/model/fact_registry.py`."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent.parent


def _load_gen():
    path = REPO_DIR / "scripts" / "gen_fact_capability_matrix.py"
    spec = importlib.util.spec_from_file_location("gen_fact_capability_matrix", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(REPO_DIR / "scripts"))
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.path.remove(str(REPO_DIR / "scripts"))
    return mod


def test_generated_file_in_sync():
    gen = _load_gen()
    assert gen.main(["--check"]) == 0, (
        "fact-registry.md is stale — run: python scripts/gen_fact_capability_matrix.py"
    )


def test_every_registry_entry_is_rendered():
    gen = _load_gen()
    content = gen.render()
    for entry in gen.FACT_REGISTRY.entries.values():
        assert f"`{entry.id}`" in content


def test_every_unconverted_entry_is_rendered():
    gen = _load_gen()
    content = gen.render()
    for owner, field in gen.KNOWN_UNCONVERTED_ELIGIBLE_FACTS:
        assert f"`{owner}.{field}`" in content
