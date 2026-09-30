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

"""The committed `skills/` publication tree (`scripts/gen_agent_skills.py`).

Split out of `test_gen_agent_skills.py`, which is at the test-file size cap.
`npx skills add abicheck/abicheck` installs from this tree, so it must match a
fresh render of `skills-src/` exactly and resolve every link inside itself.
"""

from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "gen_agent_skills", REPO / "scripts" / "gen_agent_skills.py"
)
assert _spec is not None and _spec.loader is not None
gen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen)


class TestCommittedPublishedTree:
    """`skills/` is the one committed tree (`npx skills add abicheck/abicheck`
    installs from it), so `--check` must fail whenever it drifts from a fresh
    render: an edit, a missing file, or a stale leftover file."""

    def _copy(self, tmp_path, monkeypatch):
        published = tmp_path / "skills"
        shutil.copytree(gen.PUBLISHED_ROOT, published)
        monkeypatch.setattr(gen, "PUBLISHED_ROOT", published)
        return published / "check-abi-compatibility"

    def test_the_committed_tree_matches_a_fresh_render(self):
        assert gen.main(["--check"]) == 0

    def test_a_hand_edit_is_drift(self, tmp_path, monkeypatch):
        skill = self._copy(tmp_path, monkeypatch)
        md = skill / "SKILL.md"
        md.write_text(md.read_text(encoding="utf-8") + "\nedited\n", encoding="utf-8")
        assert gen.main(["--check"]) == 1

    def test_a_missing_file_is_drift(self, tmp_path, monkeypatch):
        skill = self._copy(tmp_path, monkeypatch)
        (skill / "references" / "shared" / "safety-invariants.md").unlink()
        assert gen.main(["--check"]) == 1

    def test_a_stale_file_is_drift(self, tmp_path, monkeypatch):
        skill = self._copy(tmp_path, monkeypatch)
        (skill / "references" / "old.md").write_text("x", encoding="utf-8")
        assert gen.main(["--check"]) == 1

    def test_every_installed_link_resolves_inside_the_skill(self):
        skill = gen.PUBLISHED_ROOT / "check-abi-compatibility"
        for md in skill.rglob("*.md"):
            for m in gen._MD_LINK_RE.finditer(md.read_text(encoding="utf-8")):
                target = m.group(2).split("#", 1)[0]
                if not target or "://" in target or target.startswith("mailto:"):
                    continue
                resolved = (md.parent / target).resolve()
                assert resolved.is_file(), (md, target)
                resolved.relative_to(skill.resolve())
