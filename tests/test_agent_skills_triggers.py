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

"""Trigger tests for the published Agent Skills (ADR-058 / G36 P0.8).

ADR-058's central product bet is that these skills trigger on real user
language and do *not* false-trigger on adjacent-but-out-of-scope requests.
This file is the **deterministic** half of testing that: corpus integrity
(every prompt still matches ADR-058's own canonical wording), static
description-vs-corpus matching, and a lexical discriminability check that no
model participates in — so it can gate the ordinary `pr` profile.

Driving a real scripted agent session to confirm which skill an agent
*actually* activates is a fundamentally different kind of test — it needs a
vendor binary, credentials, network access, and tolerates nondeterministic
routing. That subset belongs in its own opt-in lane, and the rest of the
cross-agent matrix stays the manual validation log in `skills-src/CLAUDE.md`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
CORPUS_PATH = REPO / "tests" / "agent_skills" / "trigger_corpus.yaml"
SRC = REPO / "skills-src"
ADR_PATH = (
    REPO / "docs" / "contribute" / "adr" / "058-native-compatibility-agent-skills.md"
)

CORPUS = yaml.safe_load(CORPUS_PATH.read_text(encoding="utf-8"))
POSITIVE = CORPUS["positive"]
NEGATIVE = CORPUS["negative"]

#: A numbered, quoted prompt line inside ADR-058's Product positioning list.
_ADR_PROMPT_RE = re.compile(r'^\d+\.\s+"(.+)"\s*$', re.MULTILINE)

#: Words carrying no discriminating signal between one compatibility question
#: and another.
_STOPWORDS = frozenset(
    """a an and are as at be by can do does for from how i if in is it its make
    my new of old on or our so than that the them then these this to us use
    used using we what when which who will with without you your""".split()
)


def _adr_prompts() -> list[str]:
    """The seven canonical prompts, parsed from ADR-058's own section.

    Deliberately narrow: only the numbered, quoted lines of the Product
    positioning list, so the parse cannot drift into surrounding prose.
    """
    text = ADR_PATH.read_text(encoding="utf-8")
    start = text.index("## Product positioning")
    end = text.index("## Ecosystem validation", start)
    return _ADR_PROMPT_RE.findall(text[start:end])


def _description(skill: str) -> str:
    text = (SRC / skill / "SKILL.md").read_text(encoding="utf-8")
    match = re.match(r"\A---\r?\n(.*?)\r?\n---\r?\n", text, re.DOTALL)
    assert match is not None
    return str(yaml.safe_load(match.group(1))["description"])


SKILL_NAMES = sorted(p.name for p in SRC.iterdir() if (p / "SKILL.md").is_file())
DESCRIPTIONS = {name: _description(name) for name in SKILL_NAMES}


def _tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z+]+", text.lower()) if w not in _STOPWORDS}


def _score(prompt: str, description: str) -> int:
    return len(_tokens(prompt) & _tokens(description))


# ---------------------------------------------------------------------------
# Corpus integrity
# ---------------------------------------------------------------------------


def test_corpus_prompts_match_adr_058_verbatim():
    """ADR-058's Product positioning section is the one canonical source of
    these strings. An ADR wording change that is not mirrored into the corpus
    must fail here rather than leave the corpus quietly stale."""
    adr = _adr_prompts()
    assert len(adr) == 7, f"expected seven canonical prompts, parsed {len(adr)}"
    corpus = [entry["prompt"] for entry in POSITIVE]
    assert corpus == adr


def test_every_corpus_target_is_a_real_skill():
    for entry in POSITIVE:
        target = entry["expected_skill"]
        if target is None:
            assert entry.get("unclaimed_reason"), (
                f"{entry['prompt']!r}: an unclaimed prompt must say why"
            )
            continue
        assert target in DESCRIPTIONS, f"{target!r} is not a published skill"


def test_all_four_p0_skills_are_claimed_by_the_positive_set():
    """A skill nobody's prompt routes to has no validated discovery query —
    ADR-058 admission criterion 4."""
    claimed = {e["expected_skill"] for e in POSITIVE if e["expected_skill"]}
    assert claimed == set(DESCRIPTIONS)


# ---------------------------------------------------------------------------
# Positive set
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "entry",
    [e for e in POSITIVE if e["expected_skill"]],
    ids=lambda e: e["prompt"][:40],
)
def test_target_description_carries_its_claimed_phrasings(entry):
    """Static description-vs-corpus matching: the `description` field IS the
    discovery mechanism, so a prompt's discriminating phrasing must actually
    appear in the skill that claims it."""
    description = DESCRIPTIONS[entry["expected_skill"]].lower()
    missing = [
        kw for kw in entry["description_keywords"] if kw.lower() not in description
    ]
    assert missing == [], (
        f"{entry['expected_skill']}'s description does not cover "
        f"{missing} for prompt {entry['prompt']!r}"
    )


@pytest.mark.parametrize(
    "entry",
    [e for e in POSITIVE if e["expected_skill"]],
    ids=lambda e: e["prompt"][:40],
)
def test_intended_skill_is_the_strongest_lexical_match(entry):
    """No model in the loop: the intended skill must outscore every sibling on
    plain content-word overlap. This is weaker than real agent routing, but it
    catches the failure that matters — two descriptions so alike that nothing
    distinguishes them."""
    scores = {
        name: _score(entry["prompt"], desc) for name, desc in DESCRIPTIONS.items()
    }
    best = max(scores.values())
    winners = [name for name, score in scores.items() if score == best]
    assert winners == [entry["expected_skill"]], (
        f"{entry['prompt']!r} does not uniquely favour "
        f"{entry['expected_skill']}: {scores}"
    )


@pytest.mark.parametrize(
    "entry",
    [e for e in POSITIVE if not e["expected_skill"]],
    ids=lambda e: e["prompt"][:40],
)
def test_unclaimed_prompts_are_recorded_but_not_forced_onto_a_p0_skill(entry):
    """The two P1 phrasings: no skill may claim them (that would be a false
    positive), and the corpus must say why they are unclaimed rather than
    omitting them."""
    assert entry["unclaimed_reason"].strip()
    for name, description in DESCRIPTIONS.items():
        assert entry["prompt"].lower() not in description.lower(), name


# ---------------------------------------------------------------------------
# Negative set
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("entry", NEGATIVE, ids=lambda e: e["prompt"][:40])
def test_no_skill_description_claims_an_out_of_scope_domain(entry):
    """REST/OpenAPI, database migrations, Java, and generic JSON-schema
    compatibility are adjacent questions this portfolio deliberately does not
    answer. No description may contain their domain vocabulary."""
    offenders = [
        f"{name}: {term}"
        for name, description in DESCRIPTIONS.items()
        for term in entry["forbidden_terms"]
        if term in description.lower()
    ]
    assert offenders == []


#: The tokens that actually place a request in this portfolio's domain. Plain
#: content-word overlap cannot separate "is this a breaking change to our REST
#: API" from an in-scope request -- "breaking", "change", "api", and
#: "compatibility" are shared vocabulary, and an overlap-margin assertion here
#: would fail on correct content rather than on a real defect (measured: both
#: score 3). What genuinely discriminates is whether the request is about
#: compiled native code at all, so that is what this asserts. Real agent
#: routing is checked by the opt-in scripted-session lane, not here.
DOMAIN_SCOPING_TERMS = ("c/c++", "native", "compiled", "shared library")


@pytest.mark.parametrize("entry", NEGATIVE, ids=lambda e: e["prompt"][:40])
def test_out_of_scope_prompts_carry_no_domain_scoping_signal(entry):
    """An out-of-scope prompt must carry none of the terms that place a
    request in this portfolio's domain — that absence, not raw word overlap,
    is what should keep it from selecting a `native-*` skill."""
    lowered = entry["prompt"].lower()
    assert not [term for term in DOMAIN_SCOPING_TERMS if term in lowered]


def test_every_skill_description_scopes_itself_to_compiled_native_code():
    """The scoping term is what keeps a REST/Java/JSON caller from matching in
    the first place — it is load-bearing, not decoration."""
    for name, description in DESCRIPTIONS.items():
        lowered = description.lower()
        assert any(term in lowered for term in DOMAIN_SCOPING_TERMS), name


def test_in_scope_prompts_are_lexically_separable_from_out_of_scope_ones():
    """Records the measured limit of the lexical proxy rather than asserting a
    margin it cannot support: an in-scope prompt must beat every out-of-scope
    one on overlap with its *own* target, allowing ties, so a description
    rewrite that made an out-of-scope request strictly the better match would
    still fail."""
    worst_out_of_scope = max(
        _score(entry["prompt"], desc)
        for entry in NEGATIVE
        for desc in DESCRIPTIONS.values()
    )
    for entry in POSITIVE:
        if not entry["expected_skill"]:
            continue
        score = _score(entry["prompt"], DESCRIPTIONS[entry["expected_skill"]])
        assert score >= worst_out_of_scope, (
            f"{entry['prompt']!r} matches its own target ({score}) less well "
            f"than an out-of-scope request matches some skill "
            f"({worst_out_of_scope})"
        )
