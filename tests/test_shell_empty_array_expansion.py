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

"""An array that can be empty is never expanded bare under ``set -u``.

**Bug class:** ``shell.empty_array_expansion_under_nounset``. macOS ships
bash **3.2** (GPLv2-frozen). Under ``set -u`` that shell treats expanding an
*empty* array as ``"${arr[@]}"`` as an unbound-variable reference and aborts;
bash 4.4+ special-cased it away. So a step that works on every Linux runner
dies on macOS — and dies on whichever path leaves the array empty, which is
routinely the *common* one.

This is not hypothetical and not new: ``actions/aggregate/run.sh`` and
``action/run.sh`` already carry the guard and a comment explaining it. It
shipped again anyway, in ``publish-baseline.yml``'s tag-resolution step,
where the empty case is a *lightweight* tag — the ordinary kind. The Linux
unit lane could not see it; only the macOS lane could, and that is a slow and
expensive place to learn it.

So the rule is checked here, on every platform, over every first-party shell
script and every workflow/action ``run:`` block:

    an array declared empty (``name=()``) may only ever be expanded as
    ``${name[@]+"${name[@]}"}``.

Scoping it to arrays *declared empty* is what makes it precise rather than
noisy: those are exactly the ones that can reach an expansion with no
elements. An array built non-empty (``args=(foo bar)``) is outside the rule
and needs no allowlist entry, so there is no list to keep curated and no
temptation to add one.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]

#: `name=()` — an array that starts with no elements.
_DECLARED_EMPTY = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)=\(\s*\)\s*$", re.M)

#: `${name[@]}` in any form, with the offset of the match.
_EXPANSION = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\[@\]")

#: The one safe spelling: `${name[@]+"${name[@]}"}`.
_GUARDED = re.compile(r'\$\{([A-Za-z_][A-Za-z0-9_]*)\[@\]\+"\$\{\1\[@\]\}"\}')


def _shell_sources() -> list[tuple[str, str]]:
    """``(label, script)`` for every first-party shell body in the repo.

    Three shapes carry shell here and all three have hit this trap: a
    standalone ``.sh``, a workflow step's ``run:``, and a composite Action
    step's ``run:``.
    """
    sources: list[tuple[str, str]] = []

    for script in sorted(REPO_ROOT.glob("actions/*/*.sh")) + sorted(
        REPO_ROOT.glob("action/*.sh")
    ):
        sources.append((str(script.relative_to(REPO_ROOT)), script.read_text("utf-8")))

    documents = sorted((REPO_ROOT / ".github" / "workflows").glob("*.yml")) + sorted(
        REPO_ROOT.glob("actions/*/action.yml")
    )
    documents += [REPO_ROOT / "action.yml"]
    for path in documents:
        if not path.is_file():
            continue
        try:
            parsed = yaml.safe_load(path.read_text("utf-8"))
        except yaml.YAMLError:  # pragma: no cover - a parse failure is its own test
            continue
        label = str(path.relative_to(REPO_ROOT))
        for index, body in enumerate(_run_blocks(parsed)):
            sources.append((f"{label}#run[{index}]", body))
    return sources


def _run_blocks(node: object) -> list[str]:
    """Every ``run:`` string anywhere in a parsed workflow or action."""
    found: list[str] = []
    if isinstance(node, dict):
        run = node.get("run")
        if isinstance(run, str):
            found.append(run)
        for value in node.values():
            found.extend(_run_blocks(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_run_blocks(item))
    return found


def _strip_comments(script: str) -> str:
    """Blank out `#` comment bodies, keeping every byte offset intact.

    A comment is prose, not shell: this module's own guard comment quotes
    the unsafe spelling in order to explain it, and the first version of
    this scan dutifully reported that sentence as a violation. Same trap
    `AGENTS.md` records for the `performance.yml` assertion, and the same
    one `test_docs_action_examples.py` already handles. Replacing with
    spaces rather than deleting keeps the guarded-span offsets computed
    below aligned with the original text.
    """
    out: list[str] = []
    for line in script.splitlines(keepends=True):
        quote: str | None = None
        cut: int | None = None
        for index, char in enumerate(line):
            if quote is not None:
                if char == quote:
                    quote = None
            elif char in "'\"":
                quote = char
            elif char == "#" and (index == 0 or line[index - 1] in " \t"):
                cut = index
                break
        if cut is None:
            out.append(line)
        else:
            tail = line[cut:]
            out.append(
                line[:cut]
                + " " * len(tail.rstrip("\n"))
                + tail[len(tail.rstrip("\n")) :]
            )
    return "".join(out)


def _unguarded(script: str) -> list[str]:
    """Names declared empty and expanded somewhere without the guard."""
    script = _strip_comments(script)
    declared = set(_DECLARED_EMPTY.findall(script))
    if not declared:
        return []
    guarded_spans = [m.span() for m in _GUARDED.finditer(script)]
    offenders: set[str] = set()
    for match in _EXPANSION.finditer(script):
        name = match.group(1)
        if name not in declared:
            continue
        inside = any(
            start <= match.start() and match.end() <= end
            for start, end in guarded_spans
        )
        if not inside:
            offenders.add(name)
    return sorted(offenders)


SOURCES = _shell_sources()


def test_the_scan_found_shell_to_check() -> None:
    """Vacuity guard: a glob that stopped matching would pass in silence."""
    assert len(SOURCES) >= 20, len(SOURCES)
    assert any("publish-baseline" in label for label, _ in SOURCES)


@pytest.mark.parametrize("label,script", SOURCES, ids=[s[0] for s in SOURCES])
def test_an_empty_capable_array_is_never_expanded_bare(label: str, script: str) -> None:
    offenders = _unguarded(script)
    assert not offenders, (
        f"{label}: {offenders} may be empty and are expanded as "
        '"${name[@]}" — on macOS\'s bash 3.2 under `set -u` that is an '
        "unbound-variable error, not an empty expansion. Use "
        '${name[@]+"${name[@]}"}.'
    )


class TestTheRuleItself:
    """Negative and positive controls, so the scan cannot pass vacuously."""

    def test_a_bare_expansion_of_an_empty_array_is_flagged(self) -> None:
        assert _unguarded('args=()\nfoo "${args[@]}"\n') == ["args"]

    def test_the_guarded_form_is_accepted(self) -> None:
        assert _unguarded('args=()\nfoo ${args[@]+"${args[@]}"}\n') == []

    def test_an_array_never_declared_empty_is_out_of_scope(self) -> None:
        # `args=(a b)` cannot reach an expansion with no elements, so the
        # rule does not apply and no allowlist entry is needed.
        assert _unguarded('args=(a b)\nfoo "${args[@]}"\n') == []

    def test_a_guarded_and_a_bare_use_of_the_same_array_still_flags(self) -> None:
        script = 'args=()\nfoo ${args[@]+"${args[@]}"}\nbar "${args[@]}"\n'
        assert _unguarded(script) == ["args"]

    def test_a_comment_quoting_the_unsafe_form_is_not_a_violation(self) -> None:
        # This module's own fix comment does exactly this, and the first
        # version of the scan flagged the sentence.
        script = (
            'args=()\n# never write "${args[@]}" here\nfoo ${args[@]+"${args[@]}"}\n'
        )
        assert _unguarded(script) == []

    def test_a_hash_inside_a_quoted_string_does_not_start_a_comment(self) -> None:
        # Blanking from the first `#` regardless of quoting would hide a
        # real violation living after one on the same line.
        assert _unguarded('args=()\nfoo "a#b" "${args[@]}"\n') == ["args"]

    def test_stripping_preserves_byte_offsets(self) -> None:
        # The guarded-span containment check compares offsets against the
        # stripped text, so a strip that shortened lines would misjudge it.
        source = 'args=()\nfoo ${args[@]+"${args[@]}"} # trailing note\n'
        assert len(_strip_comments(source)) == len(source)

    def test_the_real_regression_would_have_been_caught(self) -> None:
        # The exact shape that shipped: an array built conditionally and
        # then expanded bare. This is the line the macOS lane failed on.
        script = (
            "args=()\n"
            'if [[ -n "$peel" ]]; then args+=(--tag-object-json "$f"); fi\n'
            'cmd "$TAG" "$REF" "${args[@]}" --out "$OUT"\n'
        )
        assert _unguarded(script) == ["args"]
