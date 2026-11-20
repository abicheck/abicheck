"""Witness entities: public functions an edit provably leaves alone.

An entity claim "the report must not attribute a break to ``g``" is only
ground truth when ``g`` really is untouched. Two conditions decide it, and
neither reads the checker's answer:

1. **From the edit's meaning** (``candidates``): ``g``'s role is not among
   the roles the edit rewrites, no rewritten role declares a function of
   the same name (a C++ overload of an edited function is never a witness),
   the edit is no rebuild, link or asymmetric-evidence edit (those reach
   every symbol), and -- when a rewritten role defines or names a type, so
   a type may change under ``g`` transitively -- ``g``'s prototype names only
   builtin scalar types. Unchanged source text alone is never enough.
2. **From the built pair** (``confirm``): ``nm`` finds ``g`` defined and
   exported in both the old and the new library. A C++ witness is pinned to
   its exact mangled symbol, so overloads stay distinct.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from pathlib import Path

from abiforge.model.expect import Expectation
from abiforge.model.finding import EntityClaim, EntityRef
from abiforge.model.opkit import EditOp
from abiforge.model.seeds import BUILD_ROLES, Seed
from abiforge.run.binfacts import exported_functions

_BUILTIN = frozenset(
    {
        "void",
        "char",
        "short",
        "int",
        "long",
        "signed",
        "unsigned",
        "float",
        "double",
        "bool",
        "_Bool",
        "const",
        "volatile",
        "size_t",
    }
)
_KEYWORDS = frozenset({"if", "while", "for", "return", "sizeof", "switch"})
_TYPE_TEXT = re.compile(r"\b(struct|union|enum|class|typedef|using|template)\b")
_PROTO = re.compile(
    r"^\s*([^;{}()#]*?)\b([A-Za-z_]\w*)\s*\(([^()]*)\)\s*(?:const\s*)?;"
)


@dataclass(frozen=True)
class Candidate:
    role: str
    name: str
    prototype: str


def _prototypes(header: str) -> list[tuple[str, str, str]]:
    """(return-type text, name, parameter text) per function declaration line."""
    out = []
    for line in header.splitlines():
        if (m := _PROTO.match(line)) and m.group(2) not in _KEYWORDS:
            out.append((m.group(1), m.group(2), m.group(3)))
    return out


def _builtin_only(ret: str, params: str) -> bool:
    if not ret.strip():
        return False
    if any(t not in _BUILTIN for t in re.findall(r"[A-Za-z_]\w*", ret)):
        return False
    for p in params.split(","):
        toks = re.findall(r"[A-Za-z_]\w*", p)
        if len(toks) > 1 and toks[-1] not in _BUILTIN:
            toks = toks[:-1]  # the parameter's name
        if any(t not in _BUILTIN for t in toks):
            return False
    return True


def changed_roles(op: EditOp, seed: Seed) -> frozenset[str]:
    """The roles whose text the edit actually rewrites."""
    new = op.edit(seed.roles)
    return frozenset(r for r, text in new.items() if seed.roles.get(r) != text)


def candidates(op: EditOp, seed: Seed) -> tuple[Candidate, ...]:
    """Functions of *seed* that *op* provably leaves alone, by the edit's meaning."""
    if op.rebuild is not None or op.new_evidence is not None:
        return ()
    changed = changed_roles(op, seed)
    if changed & set(BUILD_ROLES):
        return ()
    edited_names: set[str] = set()
    typed = False
    new = op.edit(seed.roles)
    for role in changed:
        for text in (*seed.roles.get(role, ("", "")), *new[role]):
            # Every call-shaped name, members and macros included: a
            # witness sharing any of them could be an edited overload.
            edited_names |= set(re.findall(r"(\w+)\s*\(", text))
            typed = typed or bool(_TYPE_TEXT.search(text))
    out: list[Candidate] = []
    for role in sorted(seed.roles):
        if role in changed or role in BUILD_ROLES:
            continue
        header = seed.roles[role][0]
        for ret, name, params in _prototypes(header):
            if name in edited_names or "inline" in ret or "static" in ret:
                continue
            if typed and not _builtin_only(ret, params):
                continue
            out.append(Candidate(role, name, f"{ret}{name}({params})".strip()))
    return tuple(out)


def _base_name(demangled: str) -> str:
    return demangled.split("(", 1)[0].rsplit("::", 1)[-1]


def confirm(
    cands: tuple[Candidate, ...], old_so: Path, new_so: Path, limit: int
) -> tuple[tuple[EntityRef, ...], tuple[str, ...]]:
    """(confirmed witnesses, why each unconfirmed candidate was dropped).

    At most *limit* witnesses, in role order. A candidate is confirmed only
    when ``nm`` reads it defined and exported in both libraries.
    """
    old = exported_functions(old_so)
    new = exported_functions(new_so)
    if old is None or new is None:
        return (), ("nm could not read the pair's exports",)
    refs: list[EntityRef] = []
    dropped: list[str] = []
    for c in cands:
        if len(refs) >= limit:
            break
        syms = sorted(
            s
            for s, d in old.items()
            if _base_name(d) == c.name and s in new and new[s] == d
        )
        if not syms:
            dropped.append(f"{c.name}: not exported by both libraries")
            continue
        for s in syms:
            refs.append(EntityRef(c.name, symbol=None if s == c.name else s))
    return tuple(refs[:limit]), tuple(dropped)


def witness_claims(
    op: EditOp, seed: Seed, old_so: Path, new_so: Path
) -> tuple[tuple[EntityClaim, ...], tuple[str, ...]]:
    """(forbidden claims for *op*'s witnesses, why a witness could not be pinned).

    A non-empty reason with no claim means the witness claim is undecided:
    ``with_witnesses`` reports such a cell as ``evidence-limited``."""
    if op.witnesses <= 0:
        return (), ()
    refs, dropped = confirm(candidates(op, seed), old_so, new_so, op.witnesses)
    if not refs:
        # The operator asks for a witness, none could be pinned: the claim
        # about an untouched function is unknown here, never passed.
        dropped = dropped or ("no candidate function",)
    claims = tuple(
        EntityClaim(
            ref,
            required=False,
            why="witness: untouched by the edit, exported by both libraries (nm)",
        )
        for ref in refs
    )
    return claims, dropped


def with_witnesses(
    exp: Expectation, op: EditOp, seed: Seed, old_so: Path, new_so: Path
) -> Expectation:
    """*exp* plus *op*'s witness claims; undecided when none could be pinned."""
    claims, dropped = witness_claims(op, seed, old_so, new_so)
    if claims:
        exp = replace(exp, claims=exp.claims + claims)
    if dropped:
        why = f"witness dropped: {'; '.join(dropped)}"
        if claims:
            exp = replace(exp, note=f"{exp.note} [{why}]".strip())
        else:
            exp = replace(exp, undecided="; ".join(filter(None, (exp.undecided, why))))
    return exp
