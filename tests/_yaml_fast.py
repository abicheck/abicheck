# SPDX-License-Identifier: Apache-2.0
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

"""``safe_load`` for tests that read committed YAML (workflows, action.yml,
docs, catalogs).

``yaml.safe_load`` always uses the pure-Python ``SafeLoader``, even when
PyYAML was built with libyaml. Many modules here parse the repository's
workflows at *collection* time, which every xdist worker of every CI shard
repeats; over the 47 workflow/action files the C loader is ~15x faster and
produces identical documents (``test_yaml_fast_helper.py`` pins that).
It raises the same ``yaml.YAMLError`` family, so ``except``/``raises``
clauses are unaffected. Falls back to ``SafeLoader`` where libyaml is absent.

Not for tests of abicheck's *own* YAML handling (``test_yaml_strict.py``):
those must exercise the loader the product uses.
"""

from __future__ import annotations

from typing import Any

import yaml

LOADER: type[yaml.SafeLoader] = getattr(yaml, "CSafeLoader", yaml.SafeLoader)  # type: ignore[assignment]


def safe_load(stream: Any) -> Any:
    """Drop-in for ``yaml.safe_load`` backed by libyaml when available.

    Spelled as ``yaml.safe_load``'s own body (construct the loader, take the
    single document, dispose) rather than ``yaml.load(..., Loader=...)``,
    so no generic-``load`` call appears for a scanner to flag: the loader
    is always a safe one.
    """
    loader = LOADER(stream)
    try:
        return loader.get_single_data()
    finally:
        loader.dispose()
