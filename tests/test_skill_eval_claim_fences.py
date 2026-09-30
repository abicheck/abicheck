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

"""Claim extraction must find the envelope whatever other code blocks surround it.

Regression for a grader bug that paired one block's closing fence with the
next block's opening fence: any fenced block (a `bash` command, a `c`
snippet) ahead of the JSON envelope made a well-formed answer grade as
`absent`, which hit the skill arm hardest because the skill teaches the agent
to show its commands.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from hypothesis import given, settings, strategies as st

EVAL_DIR = (
    Path(__file__).resolve().parents[1]
    / "skills-src"
    / "evaluation"
    / "agents"
    / "skills"
)
sys.path.insert(0, str(EVAL_DIR))

from graders import claim as claim_mod  # noqa: E402

ENVELOPE = {"verdict": "BREAKING", "evidence": [0], "confident": True}

_prose = st.sampled_from(
    [
        "The loader picked the stale copy.",
        "Run this:",
        "Note the `verdict` key in the report.",
        "Inline `code` and a ``double`` span.",
        "",
    ]
)
_other_block = st.tuples(
    st.sampled_from(["```", "~~~", "````"]),
    st.sampled_from(["bash", "sh", "c", "text", "console", "diff"]),
    st.sampled_from(
        [
            "./env/run.sh",
            "abicheck compare a.so b.so -o json=r.json",
            "int widget_area(const struct widget *w);",
            "line one\nline two",
            "{not json}",
        ]
    ),
).map(lambda t: f"{t[0]}{t[1]}\n{t[2]}\n{t[0]}")
# A quoted report is verdict-bearing but not an envelope; it must not count.
_report_block = st.sampled_from(["```json", "```", "~~~json"]).map(
    lambda open_: (
        f"{open_}\n{json.dumps({'verdict': 'BREAKING', 'changes': []}, indent=2)}\n{open_[:3]}"
    )
)
_filler = st.one_of(_prose, _other_block, _report_block)


def _envelope_block(indent: bool, pretty: bool) -> str:
    body = json.dumps(ENVELOPE, indent=2 if pretty else None)
    return f"{'  ' if indent else ''}```json\n{body}\n{'  ' if indent else ''}```"


@settings(max_examples=300, deadline=None)
@given(
    before=st.lists(_filler, max_size=5),
    after=st.lists(_filler, max_size=5),
    indent=st.booleans(),
    pretty=st.booleans(),
)
def test_the_one_envelope_is_found_among_any_other_blocks(
    before, after, indent, pretty
):
    text = "\n\n".join([*before, _envelope_block(indent, pretty), *after])
    assert claim_mod.extract(text) == (ENVELOPE, "ok")


@settings(max_examples=150, deadline=None)
@given(filler=st.lists(_filler, max_size=6))
def test_no_envelope_is_absent_or_invalid_never_ok(filler):
    _, status = claim_mod.extract("\n\n".join(filler))
    assert status != "ok"


@settings(max_examples=150, deadline=None)
@given(before=st.lists(_filler, max_size=3), between=st.lists(_filler, max_size=3))
def test_two_envelopes_are_ambiguous(before, between):
    text = "\n\n".join(
        [*before, _envelope_block(False, False), *between, _envelope_block(False, True)]
    )
    assert claim_mod.extract(text)[1] == "ambiguous"


def test_the_reported_regression():
    text = (
        "Run:\n```bash\n./env/run.sh\n```\n\nDone.\n\n"
        '```json\n{"verdict": "BREAKING", "evidence": [0], "confident": true}\n```\n'
    )
    assert claim_mod.extract(text) == (ENVELOPE, "ok")


def test_an_unclosed_final_block_still_counts():
    # CommonMark: an unclosed fence runs to the end of the document.
    text = "```bash\nls\n```\n\n```json\n" + json.dumps(ENVELOPE)
    assert claim_mod.extract(text) == (ENVELOPE, "ok")


def test_blocks_keep_their_info_word_and_body():
    text = "```bash\na\n```\ntext\n~~~~json\n{}\n~~~~\n```\nplain\n```"
    assert claim_mod.fenced_blocks(text) == [
        ("bash", "a"),
        ("json", "{}"),
        ("", "plain"),
    ]
