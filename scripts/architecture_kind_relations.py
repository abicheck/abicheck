"""ADR-093 D5 gate: hand-written kind-to-kind relation tables are rejected.

A ChangeKind-to-ChangeKind dict literal is a kind relation. The catalog and
``policy/kind_relations.py`` own relations, so such a literal anywhere else is
a violation. This is a leaf module of ``check_architecture`` (see
``architecture_graph``): it returns plain messages, and the caller wraps them.
"""

from __future__ import annotations

import ast
from pathlib import Path

#: ADR-093 D5: a kind-to-kind relation is declared on the catalog and resolved
#: by ``policy/kind_relations.py``, never hand-written as a dict literal
#: elsewhere. ``file:name`` entries here are the existing violations, recorded
#: as a no-growth baseline: this list may only shrink (a removed table must be
#: deleted from it too), and a new one fails the gate.
KIND_RELATION_TABLE_ALLOWED_PREFIXES = (
    "abicheck/model/change_catalog/",
    "abicheck/policy/kind_relations.py",
)
KIND_RELATION_TABLE_BASELINE: frozenset[str] = frozenset(
    {"abicheck/compare/finding_dedup.py:_EXPORT_LOSS_ELF_ONLY_PARTNER"}
)


def _is_change_kind_member(node: ast.expr) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "ChangeKind"
    )


def _is_relation_literal(node: ast.Dict) -> bool:
    keys = [k for k in node.keys if k is not None]
    if not any(_is_change_kind_member(k) for k in keys):
        return False
    return any(_is_change_kind_member(x) for val in node.values for x in ast.walk(val))


def kind_relation_table_messages(root: Path) -> list[str]:
    """Return one message per hand-written relation literal, or stale baseline entry.

    Every ``ast.Dict`` in a module is inspected, wherever it sits: a bare
    assignment, a nested or conditional assignment, an attribute target, or a
    dict returned from a function. Only the module-level table named in the
    baseline is exempt. A nested table that happens to share that name is a
    new relation, reported by its line.
    """
    messages: list[str] = []
    seen: set[str] = set()
    for path in sorted((root / "abicheck").rglob("*.py")):
        relative = path.relative_to(root).as_posix()
        if relative.startswith(KIND_RELATION_TABLE_ALLOWED_PREFIXES):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        # The only dict literal that may be exempt is the value of a named
        # module-level assignment; record those first.
        named: dict[int, str] = {}
        for node in tree.body:
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            if not isinstance(node.value, ast.Dict):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    named[id(node.value)] = target.id
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict) or not _is_relation_literal(node):
                continue
            name = named.get(id(node))
            if name is not None:
                key = f"{relative}:{name}"
                seen.add(key)
                if key in KIND_RELATION_TABLE_BASELINE:
                    continue
                where = key
            else:
                where = f"{relative}:{node.lineno}"
            messages.append(
                f"{where}: a hand-written ChangeKind-to-ChangeKind table "
                "is a relation; declare it on the registry "
                "(ADR-093 D4) and resolve it in policy/kind_relations.py"
            )
    for stale in sorted(KIND_RELATION_TABLE_BASELINE - seen):
        # An entry whose file is absent from this tree is not stale; only a
        # file that is present but no longer holds the table is.
        if not (root / stale.split(":", 1)[0]).is_file():
            continue
        messages.append(
            f"{stale}: listed in the baseline but no longer present; remove it "
            "from KIND_RELATION_TABLE_BASELINE"
        )
    return messages
