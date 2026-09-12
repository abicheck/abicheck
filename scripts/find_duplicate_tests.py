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

"""Report same-module test functions with byte-identical bodies.

A **review queue, not a deletion list**, and deliberately not wired into any
gate. Two tests can share a body and still both be wanted -- the useful
signal is which *kind* of clone a group is, and only reading them tells you:

* **Different enclosing classes, same body.** Usually intentional. Each class
  states a distinct claim and the body coincides; `test_signature_
  normalization.py` has two such pairs, each already naming its counterpart
  in a comment ("this fix must not interfere with it"). Deleting one trades a
  stated regression guard for no measurable time.
* **Same class or module scope, different names.** The valuable category, and
  the reason this script exists: a test whose *name* promises input X while
  its body tests input Y is worse than a duplicate -- it makes a gap look
  filled. Nine such groups were found and worked through in PR #1243; two
  turned out to name genuinely untested behaviour (a function-pointer
  parameter reaching `_strip_param_signature`'s whitespace-gated branch; a
  manifest that really declares a `baseline_generation`), one was mandated by
  a catalog<->test contract and had to be strengthened rather than removed
  (`test_sc_offline_snapshot`), one pointed at a wholly untested
  *workflow-wiring* claim (`performance.yml` sourcing the PR's current label
  set), and the rest were one assertion under two names.

Two cautions from that pass, both learned the hard way:

* A clone group is not evidence the behaviour is uncovered. One group looked
  like a missing one-sided-evidence case in the ELF alignment detector; a
  mutation test showed `test_declared_alignment_known_one_side_only_falls_
  back` already covered it, so the speculative new test was withdrawn.
  Mutate before claiming a gap.
* Deleting a member can break a separate contract. `tests/test_scenarios.py`
  asserts every automated catalog scenario has a `test_sc_*` function, so the
  clone there was not removable at all.

Run: ``python scripts/find_duplicate_tests.py``. Exits 0 always; it reports.
"""

from __future__ import annotations

import ast
import collections
import json
import pathlib


def _normalized_body(fn: ast.AST) -> str:
    """The function's body, dumped without line/column attributes.

    Attributes off is what makes this a *body* comparison rather than a
    position one -- two identical bodies at different line numbers must hash
    the same or the screen finds nothing.
    """
    return ast.dump(
        ast.Module(body=fn.body, type_ignores=[]),
        annotate_fields=True,
        include_attributes=False,
    )


def _signature(fn: ast.AST) -> tuple[str, ...]:
    """Parameter names, so two tests taking different fixtures never pair."""
    a = fn.args
    parts = [p.arg for p in (a.posonlyargs + a.args + a.kwonlyargs)]
    if a.vararg:
        parts.append("*" + a.vararg.arg)
    if a.kwarg:
        parts.append("**" + a.kwarg.arg)
    return tuple(parts)


def _decorators(fn: ast.AST) -> tuple[str, ...]:
    """Decorators, so two `@parametrize`d tests with different cases never
    pair -- the audit that motivated this script counted clones "after
    accounting for signatures and decorators" for exactly this reason."""
    return tuple(ast.dump(d, include_attributes=False) for d in fn.decorator_list)


def _test_functions(tree: ast.Module) -> list[tuple[str | None, ast.AST]]:
    """Every `test_*` function with its enclosing class name (or None)."""
    found: list[tuple[str | None, ast.AST]] = []

    def walk(node: ast.AST, cls: str | None = None) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                walk(child, child.name)
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if child.name.startswith("test_"):
                    found.append((cls, child))
                walk(child, cls)
            else:
                walk(child, cls)

    walk(tree)
    return found


def main() -> int:
    groups = 0
    excess = 0
    report = []
    for path in sorted(pathlib.Path("tests").rglob("test_*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        buckets: dict[tuple, list] = collections.defaultdict(list)
        for cls, fn in _test_functions(tree):
            key = (_normalized_body(fn), _signature(fn), _decorators(fn))
            buckets[key].append((cls, fn.name, fn.lineno))
        for members in buckets.values():
            if len(members) > 1:
                groups += 1
                excess += len(members) - 1
                report.append({"file": str(path), "members": members})

    print(f"clone groups: {groups}   excess definitions: {excess}")
    same_context = [
        r
        for r in report
        if len({m[0] for m in r["members"]}) == 1
        and len({m[1] for m in r["members"]}) > 1
    ]
    print(
        f"of those, same-context with different names "
        f"(the category worth reading): {len(same_context)}"
    )
    for r in report:
        marker = " <-- same context, different names" if r in same_context else ""
        print(f"\n{r['file']}{marker}")
        for cls, name, lineno in r["members"]:
            print(f"   L{lineno:5d}  {cls or '-'}::{name}")
    pathlib.Path("/tmp/clone_report.json").write_text(json.dumps(report, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
