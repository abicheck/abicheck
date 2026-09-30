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

"""The code-region mask round-trip of `scripts/gen_agent_skills.py`.

Bug class: a masked region that is never restored. The generator masks
indented code blocks, then fenced blocks, then inline spans, so link rewriting
cannot touch code; regions nest (an indented-looking YAML run inside a fence
is masked first, then swallowed by the fence's own mask). Restoring in
creation order left the inner placeholder in the published file — the
`set-up-abi-compatibility-ci` skill shipped with three workflow steps of its
templates replaced by a bare ``\\x00mN\\x00`` marker, and every existing test
passed, because none asserted the round trip itself.

The invariant stated here is the round trip: for every document,
``unmask(mask(text)) == text``. The oracle is the input text, not the
implementation. It is exercised over an exhaustive enumeration of small
documents built from every block shape the masker distinguishes, in every
order and nesting, plus the real published skill trees.
"""

from __future__ import annotations

import importlib.util
import itertools
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
_spec = importlib.util.spec_from_file_location(
    "gen_agent_skills_masking", REPO / "scripts" / "gen_agent_skills.py"
)
assert _spec is not None and _spec.loader is not None
gen = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("gen_agent_skills_masking", gen)
_spec.loader.exec_module(gen)

MARKER = re.compile(r"\x00m[0-9]+\x00")

#: Every block shape the masker treats differently, each carrying a link-like
#: construct so a region that leaks out of its mask would also be rewritten.
FRAGMENTS = {
    "prose": "Some prose with a [link](../shared/x.md) in it.",
    "inline": "Run `abicheck --version` and read [it](a.md).",
    "fenced": "```yaml\nkey: value  # [not a link](b.md)\n```",
    "fenced_with_indented_run": (
        "```yaml\njobs:\n  a:\n\n      - uses: x@v1\n        with:\n          k: v  # [c](c.md)\n```"
    ),
    "tilde_fence": "~~~\n    indented inside tilde [d](d.md)\n~~~",
    "indented": "    four-space code [e](e.md)\n    second line",
    "long_fence": "````\n```\ninner fence [f](f.md)\n```\n````",
    "list_item": "- item\n\n      indented continuation [g](g.md)",
}


def _documents():
    names = sorted(FRAGMENTS)
    for size in (1, 2, 3):
        for combo in itertools.product(names, repeat=size):
            yield combo, "\n\n".join(FRAGMENTS[n] for n in combo) + "\n"


@pytest.mark.parametrize(
    ("combo", "text"),
    list(_documents()),
    ids=lambda v: "+".join(v) if isinstance(v, tuple) else "",
)
def test_mask_then_unmask_is_identity(combo, text):
    masked, replacements = gen.mask_code_regions(text)
    assert gen.unmask_code_regions(masked, replacements) == text, combo


def test_the_enumeration_exercises_nesting():
    """Vacuity guard: at least one document must actually produce a nested
    mask (an inner placeholder inside an outer region's original text), or
    the round-trip test above would pass without reaching the bug class."""
    nested = 0
    for _, text in _documents():
        _, replacements = gen.mask_code_regions(text)
        originals = list(replacements.values())
        if any(MARKER.search(o) for o in originals):
            nested += 1
    assert nested > 0


def test_an_unrestorable_placeholder_fails_generation():
    """The guard behind the fix: a placeholder surviving the restore is a
    generation error, never a silently corrupted file."""
    with pytest.raises(gen.SkillGenerationError):
        gen.unmask_code_regions("before \x00m7\x00 after", {})


def test_restoring_in_creation_order_would_corrupt():
    """Negative control: the old creation-order restore really loses the
    inner region on a nested document, so the identity test above is capable
    of failing."""
    text = FRAGMENTS["fenced_with_indented_run"] + "\n"
    masked, replacements = gen.mask_code_regions(text)
    restored = masked
    for placeholder, original in replacements.items():
        restored = restored.replace(placeholder, original)
    assert MARKER.search(restored), "fixture no longer nests; pick another"


def test_published_skill_trees_contain_no_placeholder():
    """The public surface: every file the generator publishes is free of mask
    markers, whatever the source looks like."""
    rendered = gen.render_all(REPO / "skills-src")
    assert rendered, "nothing rendered"
    assert [name for name, content in rendered.items() if MARKER.search(content)] == []
