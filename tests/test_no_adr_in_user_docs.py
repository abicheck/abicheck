"""User-facing documentation must not cite internal design-record (ADR) numbers.

Sibling of ``tests/test_no_adr_in_user_text.py``, which covers help text,
messages and Action descriptions. ADR ids ("ADR-049 D8", "ADR-065 S1", ...)
are contributor vocabulary: a user reading the published guides, reference
pages or catalog cases has never heard of them, so a citation there is noise
at best and a dead end at worst. ``docs/contribute/`` (ADRs, plans,
known-gaps) and the per-directory ``AGENTS.md``/``CLAUDE.md`` agent notes are
contributor material and keep their citations.

Scope: ``README.md``, every ``docs/**/*.md`` outside ``docs/contribute/``,
``examples/**/README.md``, the catalog case READMEs the example pages are
generated from, and the Action READMEs. Generated pages are covered through
their output, so a citation in a generator's source (``scripts/gen_*.py``,
``catalog/*.yaml``, ``scripts/backend_capabilities.py``, a public docstring)
fails here too.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
ADR_RE = re.compile(r"\bADR[- ]?\d")
#: A link into the ADR tree carries a citation even when its text does not.
ADR_LINK_RE = re.compile(r"contribute/adr/\d")
AGENT_NOTES = {"AGENTS.md", "CLAUDE.md"}

#: Files allowed to cite an ADR, with why. Empty today; keep it that way
#: unless a page genuinely exists to point contributors at a record.
ALLOWLIST: dict[str, str] = {}


def _user_doc_files() -> list[Path]:
    files: set[Path] = {REPO / "README.md"}
    contribute = REPO / "docs" / "contribute"
    for path in (REPO / "docs").rglob("*.md"):
        if contribute in path.parents or path.name in AGENT_NOTES:
            continue
        files.add(path)
    for pattern in (
        "examples/**/README.md",
        "catalog/README.md",
        "catalog/cases/*/README.md",
        "actions/*/README.md",
        "action/README.md",
    ):
        files.update(REPO.glob(pattern))
    return sorted(p for p in files if p.is_file())


def _citations(text: str) -> list[tuple[int, str]]:
    return [
        (n, line.strip())
        for n, line in enumerate(text.splitlines(), 1)
        if ADR_RE.search(line) or ADR_LINK_RE.search(line)
    ]


def test_scope_is_nontrivial() -> None:
    files = _user_doc_files()
    rel = {p.relative_to(REPO).as_posix() for p in files}
    assert len(files) > 300, len(files)
    for expected in (
        "README.md",
        "docs/index.md",
        "docs/use/github-action.md",
        "docs/reference/exit-codes.md",
        "docs/reference/cli-reference.md",
        "docs/learn/build-source-data.md",
        "catalog/README.md",
    ):
        assert expected in rel
    assert not any(r.startswith("docs/contribute/") for r in rel)
    assert not any(Path(r).name in AGENT_NOTES for r in rel)


@pytest.mark.parametrize(
    ("text", "hit"),
    [
        ("see ADR-049 D8 for details", True),
        ("(ADR 23)", True),
        ("ADR068", True),
        ("[why](../contribute/adr/047-github-actions-integration-model.md)", True),
        ("[all records](contribute/adr/index.md)", False),
        ("an ADRESS field, ADR-free prose", False),
        ("CADR-12 is not a citation", False),
    ],
)
def test_detector(text: str, hit: bool) -> None:
    assert bool(_citations(text)) is hit


def test_allowlist_entries_exist_and_are_justified() -> None:
    for rel, why in ALLOWLIST.items():
        assert (REPO / rel).is_file(), rel
        assert why.strip(), rel


def test_no_adr_citation_in_user_docs() -> None:
    offenders = [
        f"{path.relative_to(REPO).as_posix()}:{n}: {line[:160]}"
        for path in _user_doc_files()
        if path.relative_to(REPO).as_posix() not in ALLOWLIST
        for n, line in _citations(path.read_text(encoding="utf-8"))
    ]
    assert not offenders, "user docs cite an internal ADR:\n" + "\n".join(offenders)
