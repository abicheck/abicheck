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
"""`gen_agent_skills.published_source_files()` — the one definition of which
`skills-src/` Markdown can reach a published skill. Source-level gates
(`test_agent_skills_drift.py`, `test_agent_skills_structural.py`) scope
themselves to it, so it must include every shippable file and nothing else."""

from __future__ import annotations

import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "gen_agent_skills_published_sources", REPO / "scripts" / "gen_agent_skills.py"
)
assert _spec is not None and _spec.loader is not None
gen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen)


def _write(root: Path, rel: str, body: str = "x\n") -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def test_published_source_files_is_exactly_skills_plus_shared(tmp_path: Path) -> None:
    """A sibling tree without a `SKILL.md` (`evaluation/`, a draft directory)
    and `skills-src/CLAUDE.md` itself stay out, however deeply they nest."""
    src = tmp_path / "skills-src"
    for rel in (
        "CLAUDE.md",
        "alpha/SKILL.md",
        "alpha/references/deep/r.md",
        "beta/SKILL.md",
        "shared/frag.md",
        "evaluation/README.md",
        "evaluation/agents/skills/CLAUDE.md",
        "draft/notes.md",
    ):
        _write(src, rel)

    got = {p.relative_to(src).as_posix() for p in gen.published_source_files(src)}

    assert got == {
        "alpha/SKILL.md",
        "alpha/references/deep/r.md",
        "beta/SKILL.md",
        "shared/frag.md",
    }


def test_the_real_tree_publishes_no_evaluation_file() -> None:
    files = gen.published_source_files()
    assert files, "vacuity guard: the real skill source must publish something"
    assert not [p for p in files if "evaluation" in p.relative_to(REPO).parts]
