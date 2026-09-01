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
import re
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


# ---------------------------------------------------------------------------
# Codex review: a raw `|` inside a table cell (a union value_type like
# "bool | None") is parsed as an extra GFM column delimiter even inside
# backticks, shifting every later column in that row.
# ---------------------------------------------------------------------------


def test_table_cell_text_escapes_pipe():
    gen = _load_gen()
    assert gen._table_cell_text("bool | None") == "bool \\| None"
    assert gen._table_cell_text("list[str]") == "list[str]"


def test_real_union_value_type_is_escaped_in_rendered_table():
    gen = _load_gen()
    content = gen.render()
    # RecordType.is_final/vptr_offset_bits both register a real union
    # value_type today -- proves the escape actually reaches the page, not
    # just the helper in isolation.
    assert "`bool \\| None`" in content
    assert "`int \\| None`" in content
    assert "`bool | None`" not in content
    assert "`int | None`" not in content


def _unescaped_pipe_count(text: str) -> int:
    """Count real GFM column-delimiter pipes -- an escaped ``\\|`` (what
    ``_table_cell_text`` produces for a union value_type) is not a
    delimiter and must not count as one, or this check would be blind to
    exactly the bug it exists to catch."""
    return len(re.findall(r"(?<!\\)\|", text))


def test_registry_table_rows_have_consistent_cell_count():
    """The actual invariant the bug broke: an unescaped `|` gives one row
    more pipe-delimited cells than its header, silently misaligning every
    later column -- checked structurally rather than by string-matching one
    known-union row, so any future value_type shape is covered too."""
    gen = _load_gen()
    lines = gen._render_registry_table().splitlines()
    header_pipes = _unescaped_pipe_count(lines[0])
    assert header_pipes > 0
    for row in lines[2:]:  # skip the header and the "---" separator row
        assert _unescaped_pipe_count(row) == header_pipes, row
