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

"""What a run cost: wall time, turns, tokens and money.

Correctness alone does not compare the two arms fairly: a skill that gets
the same answer while reading twice the input is a worse deal. These are
read from the run's own recorded Claude Code `result` event, so the numbers
are the CLI's, not an estimate.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

#: The per-run efficiency fields, in report order.
FIELDS = (
    "wall_clock_seconds",
    "turns",
    "tool_calls",
    "tokens_in_total",
    "tokens_out",
    "cost_usd",
)


def token_usage(counts: dict) -> dict[str, Any]:
    """Token counts from a `result` event's `usage` block.

    `input_tokens` alone is only the *uncached* input, typically a few dozen
    tokens: nearly all of a session's input is read from, or written to, the
    prompt cache. Recording only that field made every run look almost free
    and hid most of the input a skill adds, so the cached counts are kept
    beside it and summed into `tokens_in_total`.
    """
    fresh = counts.get("input_tokens")
    cache_read = counts.get("cache_read_input_tokens")
    cache_write = counts.get("cache_creation_input_tokens")
    parts = [n for n in (fresh, cache_read, cache_write) if isinstance(n, int)]
    return {
        "tokens_in": fresh,
        "tokens_cache_read": cache_read,
        "tokens_cache_write": cache_write,
        "tokens_in_total": sum(parts) if parts else None,
        "tokens_out": counts.get("output_tokens"),
    }


def run_efficiency(run_dir: Path) -> dict[str, Any]:
    """One recorded run's efficiency fields (`None` where not recorded).

    Tokens are re-read from `events.jsonl` when it is there, so runs recorded
    before the cache counts were kept still report their full input.
    """
    usage: dict[str, Any] = {}
    path = run_dir / "usage.json"
    if path.is_file():
        usage.update(json.loads(path.read_text(encoding="utf-8")))
    events = run_dir / "events.jsonl"
    if events.is_file():
        for line in reversed(events.read_text(encoding="utf-8").splitlines()):
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict) and event.get("type") == "result":
                usage.update(token_usage(event.get("usage") or {}))
                break
    return {field: usage.get(field) for field in FIELDS}
