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

"""A test that reads the repository's own text must state the encoding.

Bug class `tests.locale_dependent_repo_text_read`. `Path.read_text()` and
`open(path)` with no `encoding=` decode using the *host's* preferred encoding.
That is UTF-8 on the Linux and macOS lanes and `cp1252` on a default Windows
runner, so a checked-in UTF-8 file containing any non-ASCII byte — an em dash
or a `§`, which this repository's own comment prose is full of — reads fine on
two platforms and raises `UnicodeDecodeError` on the third.

The `test_workflow_*.py` guards hit exactly this: four modules each read
`.github/workflows/*.yml` at import time with a bare `read_text()`, and the
Windows unit lane failed at *collection*, not on any assertion. A fixed-input
regression test ("this module can now be imported") would foreclose only those
four files, so this module states the class instead, in two halves:

* a **structural** half — no test module may read a path rooted at the
  repository with the encoding left to the platform; and
* a **behavioural** half — the checked-in text those readers consume really
  does contain bytes a `cp1252` host cannot decode, so the structural rule is
  defending against a live failure rather than a hypothetical one. Without
  this the rule could pass vacuously the day the repository became pure ASCII,
  which is precisely the "toy-scale fixture" failure mode AGENTS.md's
  third-party-boundary lesson describes.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from _workflow_files import ENCODING, workflow_paths

TESTS_DIR = Path(__file__).resolve().parent

#: Names that resolve to a repository-rooted path in this test suite. A read
#: relative to one of these is reading checked-in content; a read relative to
#: `tmp_path` or a fixture directory the test itself wrote is not.
_REPO_ROOTED_NAMES = frozenset({"REPO_ROOT", "WORKFLOW_DIR", "ROOT", "PROJECT_ROOT"})


#: Helpers that hand back repository-rooted paths. A name bound from one of
#: these is as repository-rooted as `REPO_ROOT / x` is.
_REPO_ROOTED_CALLS = frozenset({"workflow_paths", "workflow_texts"})


def _bindings(node: ast.AST) -> tuple[list[ast.expr], ast.expr | None]:
    """The targets a statement binds and the value it binds them from."""
    if isinstance(node, ast.Assign):
        return list(node.targets), node.value
    if isinstance(node, (ast.AnnAssign, ast.AugAssign)) and node.value:
        return [node.target], node.value
    if isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
        return [node.target], node.iter
    return [], None


_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)


def _own_nodes(scope: ast.AST) -> list[ast.AST]:
    """Every node inside *scope* except those belonging to a nested function.

    Scoping matters: collected per module, a name bound from a repository
    path in one function marks every same-named local elsewhere, and this
    suite really does reuse short names like `candidate` for a repo-derived
    value in one test and a `tmp_path` child in another -- a false positive
    that fails the build.
    """
    own: list[ast.AST] = []
    stack = list(ast.iter_child_nodes(scope))
    while stack:
        node = stack.pop()
        own.append(node)
        if not isinstance(node, _SCOPES):
            stack.extend(ast.iter_child_nodes(node))
    return own


def _repo_rooted_aliases(scope: ast.AST, inherited: frozenset[str]) -> frozenset[str]:
    """Names in *scope* that hold a repository-rooted path.

    Walking only the call target missed the two spellings this suite reaches
    for first -- `path = REPO_ROOT / "f.yml"` then `path.read_text()`, and
    `for path in workflow_paths(): path.read_text()` -- so the guard read
    them as unrooted and let a bare read through. A guard that fails open on
    the *natural* spelling of the thing it forbids is worse than none, which
    is the same lesson `_gha_expressions` learned one review earlier
    (CodeRabbit review).

    Iterated to a fixed point, so a chain (`a = REPO_ROOT / x; b = a / y`)
    resolves regardless of statement order.
    """
    aliases = set(inherited)
    nodes = _own_nodes(scope)
    while True:
        grown = False
        for node in nodes:
            targets, value = _bindings(node)
            if value is None or not _is_repo_rooted(value, aliases):
                continue
            for target in targets:
                for sub in ast.walk(target):
                    if isinstance(sub, ast.Name) and sub.id not in aliases:
                        aliases.add(sub.id)
                        grown = True
        if not grown:
            return frozenset(aliases)


def _is_repo_rooted(
    node: ast.AST, aliases: frozenset[str] | set[str] = frozenset()
) -> bool:
    """True when `node` is built out of a repository-root constant, a helper
    that returns one, or a local name bound from either -- however many `/`
    joins and attribute hops deep."""
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name) and (
            sub.id in _REPO_ROOTED_NAMES or sub.id in aliases
        ):
            return True
        if (
            isinstance(sub, ast.Call)
            and isinstance(sub.func, ast.Name)
            and sub.func.id in _REPO_ROOTED_CALLS
        ):
            return True
    return False


def _opens_in_binary_mode(call: ast.Call) -> bool:
    """A binary read decodes nothing, so no encoding applies to it."""
    mode = call.args[1] if len(call.args) >= 2 else None
    for kw in call.keywords:
        if kw.arg == "mode":
            mode = kw.value
    return isinstance(mode, ast.Constant) and "b" in str(mode.value)


def _encoding_is_stated(call: ast.Call) -> bool:
    """Does this read name its encoding -- as a keyword, positionally, or
    vacuously by reading bytes?"""
    if any(kw.arg == "encoding" for kw in call.keywords):
        return True
    if _opens_in_binary_mode(call):
        return True
    # open(path, mode, buffering, encoding) positionally — rare, but legal.
    return len(call.args) >= 4


def _reads_in_scope(scope: ast.AST, aliases: frozenset[str], name: str) -> list[str]:
    """Unencoded repository-rooted reads written directly in *scope*, then
    recursively in each function nested inside it under its own aliases."""
    findings = []
    for node in _own_nodes(scope):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in ("read_text", "write_text"):
            target = func.value
        elif isinstance(func, ast.Name) and func.id == "open":
            target = node.args[0] if node.args else None
        else:
            continue
        if target is None or not _is_repo_rooted(target, aliases):
            continue
        if _encoding_is_stated(node):
            continue
        findings.append(f"{name}:{node.lineno}: {ast.unparse(node)[:90]}")
    for node in _own_nodes(scope):
        if isinstance(node, _SCOPES):
            findings += _reads_in_scope(node, _repo_rooted_aliases(node, aliases), name)
    return sorted(findings, key=lambda f: int(f.split(":")[1]))


def _unencoded_repo_reads(path: Path) -> list[str]:
    """Every repository-rooted text read in *path* that leaves the encoding
    to the host's locale, as `file:line: source` strings."""
    tree = ast.parse(path.read_text(encoding=ENCODING))
    return _reads_in_scope(tree, _repo_rooted_aliases(tree, frozenset()), path.name)


def test_no_test_reads_checked_in_text_with_a_platform_dependent_encoding() -> None:
    """The structural half of the invariant, over the whole suite."""
    findings: list[str] = []
    for module in sorted(TESTS_DIR.rglob("*.py")):
        findings += _unencoded_repo_reads(module)
    assert not findings, (
        "these reads of checked-in repository text leave the encoding to the "
        "host's locale, so they decode as cp1252 on the Windows lanes and fail "
        "on any non-ASCII byte; read through tests/_workflow_files.py's "
        "read_repo_text(), or pass encoding='utf-8':\n  " + "\n  ".join(findings)
    )


#: Every shape the scan must judge, and the verdict it owes each. Lines are
#: 1-based and must stay in step with the source below.
_SCAN_FIXTURE = """from x import REPO_ROOT, workflow_paths
a = (REPO_ROOT / 'f.yml').read_text()
b = open(REPO_ROOT / 'f.yml')
b2 = open(REPO_ROOT / 'f.bin', 'rb')
c = (REPO_ROOT / 'f.yml').read_text(encoding='utf-8')
d = (tmp_path / 'f.yml').read_text()
path = REPO_ROOT / 'g.yml'
e = path.read_text()
for looped in workflow_paths():
    f = looped.read_text()
for ok in workflow_paths():
    g = ok.read_text(encoding='utf-8')
"""

#: Bare repository-rooted reads: direct (2, 3), through an assigned alias (8),
#: and through a loop variable bound from a repo-rooted helper (10).
_SCAN_FIXTURE_EXPECTED = [2, 3, 8, 10]


def test_the_scan_can_actually_see_an_unencoded_read(tmp_path: Path) -> None:
    """Guards the AST scan itself: a matcher that recognised nothing would
    make the assertion above vacuously true.

    Driven through `_unencoded_repo_reads` -- the real function -- rather
    than a hand-rolled copy of its matching, so the two cannot drift. The
    alias cases are the ones a review found the scan blind to: walking only
    the call target missed `path = REPO_ROOT / ...` and
    `for path in workflow_paths():`, which are how this suite most naturally
    spells the very thing the guard forbids.
    """
    fixture = tmp_path / "sample.py"
    fixture.write_text(_SCAN_FIXTURE, encoding="utf-8")
    flagged = [int(f.split(":")[1]) for f in _unencoded_repo_reads(fixture)]
    assert flagged == _SCAN_FIXTURE_EXPECTED, (
        "the scan must flag the direct and aliased bare reads and nothing "
        "else -- not the binary open, the stated encodings, or the tmp_path "
        f"read; got {flagged}"
    )


@pytest.mark.parametrize("path", workflow_paths(), ids=lambda p: p.name)
def test_workflow_text_is_utf8_that_a_cp1252_host_could_not_have_decoded(
    path: Path,
) -> None:
    """Each workflow must be valid UTF-8 — and collectively they must contain
    at least one byte `cp1252` rejects, or the rule above defends nothing."""
    path.read_bytes().decode("utf-8")


def test_some_checked_in_workflow_really_defeats_the_platform_default() -> None:
    """The non-vacuity half: the rule above must be defending against
    content that actually exists, not a hypothetical byte."""
    undecodable = []
    for path in workflow_paths():
        try:
            path.read_bytes().decode("cp1252")
        except UnicodeDecodeError:
            undecodable.append(path.name)
    assert undecodable, (
        "no checked-in workflow contains a byte cp1252 rejects, so the "
        "encoding rule above would pass even if every reader regressed; "
        "re-derive the non-vacuity precondition before deleting this test"
    )


#: Test modules whose reads are of documents **abicheck itself wrote** --
#: a rendered report at a `-o FORMAT=DESTINATION` destination under
#: `tmp_path`, not checked-in text -- so the repository-rooted rule above
#: does not reach them.
#:
#: They need the same rule for the same reason: every renderer writes UTF-8
#: unconditionally, and this tool's own report prose is full of em dashes and
#: `§`, so a bare `read_text()` decodes fine on Linux/macOS and raises
#: `UnicodeDecodeError` on a default Windows runner. That is not
#: hypothetical -- it is how `test_cli_export_grammar.py` turned the
#: windows-latest unit lane red on this branch's first push, in four tests
#: whose Linux runs were all green.
#:
#: Deliberately a named set rather than "every read under `tests/`": the
#: suite has ~249 bare `read_text()` calls across ~97 modules, nearly all of
#: them reading content the test itself wrote in ASCII. Sweeping those is a
#: real but separate change; leaving *this* class unstated because the
#: superset is large is how it would come back.
_TOOL_OUTPUT_READERS = frozenset(
    {
        "test_cli_export_grammar.py",
        "test_cli_compare_release_export_uniformity.py",
        "test_presentation_analysis_separation.py",
    }
)


def _unencoded_reads_anywhere(path: Path) -> list[str]:
    """Every text read in *path* that leaves the encoding to the host."""
    tree = ast.parse(path.read_text(encoding=ENCODING))
    findings = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in ("read_text", "write_text"):
            pass
        elif isinstance(func, ast.Name) and func.id == "open":
            pass
        else:
            continue
        if _encoding_is_stated(node):
            continue
        findings.append(f"{path.name}:{node.lineno}: {ast.unparse(node)[:90]}")
    return findings


@pytest.mark.parametrize("module", sorted(_TOOL_OUTPUT_READERS))
def test_a_module_reading_a_rendered_report_states_its_encoding(module: str) -> None:
    findings = _unencoded_reads_anywhere(TESTS_DIR / module)
    assert findings == [], (
        f"{module} reads a document abicheck wrote as UTF-8 without saying so, "
        "so it decodes with the host's locale and fails on a cp1252 Windows "
        f"runner: {findings}"
    )


def test_the_tool_output_reader_set_names_real_modules() -> None:
    """A renamed or deleted module must not silently drop out of the rule."""
    missing = [m for m in _TOOL_OUTPUT_READERS if not (TESTS_DIR / m).is_file()]
    assert missing == [], missing


def test_a_rendered_report_really_defeats_the_platform_default() -> None:
    """Non-vacuity, the same shape as the workflow half above: the renderers
    must actually emit a byte `cp1252` rejects, or the rule defends nothing."""
    # U+201D, the curly closing quote a rendered report puts around a symbol
    # name. Its UTF-8 encoding ends in 0x9D, which cp1252 leaves undefined --
    # and 0x9D is the exact byte the windows-latest lane reported. An em dash
    # would NOT do: its bytes are all mapped in cp1252, so it decodes into
    # mojibake rather than raising, and a non-vacuity guard built on one would
    # pass while proving nothing.
    sample = "Removed \u201cfoo\u201d from the export table."
    with pytest.raises(UnicodeDecodeError):
        sample.encode("utf-8").decode("cp1252")
