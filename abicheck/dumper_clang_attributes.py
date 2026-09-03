# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Clang JSON contract-attribute normalization helpers."""

from __future__ import annotations

import re
from typing import Any


def _follows_type_operator(qualtype: str, paren_index: int) -> bool:
    """Whether a parenthesized group is a ``typeof``-like type operand."""
    end = paren_index
    while end > 0 and qualtype[end - 1].isspace():
        end -= 1
    start = end
    while start > 0 and (qualtype[start - 1].isalnum() or qualtype[start - 1] == "_"):
        start -= 1
    return qualtype[start:end] in {
        "decltype", "typeof", "__typeof", "__typeof__", "typeof_unqual",
    }


def _function_qualifiers(qualtype: str) -> str:
    """Return qualifiers after the outer function parameter list."""
    bracket = 0
    idx = 0
    while idx < len(qualtype):
        ch = qualtype[idx]
        if ch in "<[":
            bracket += 1
        elif ch in ">]":
            bracket = max(0, bracket - 1)
        elif ch == "(" and bracket == 0:
            depth = 1
            j = idx + 1
            while j < len(qualtype) and depth:
                if qualtype[j] == "(":
                    depth += 1
                elif qualtype[j] == ")":
                    depth -= 1
                j += 1
            if _follows_type_operator(qualtype, idx):
                idx = j
                continue
            tail = qualtype[j:]
            # A FunctionDecl returning a pointer to function is rendered by
            # Clang as e.g. ``void (*(void))(int) __attribute__((ms_abi))``.
            # The first parenthesized group is that declaration's parameter
            # list; a following function suffix belongs to its *return type*,
            # so an attribute after it must not be assigned to the declaration.
            if tail.lstrip().startswith("("):
                return ""
            return tail
        idx += 1
    return ""


# clang attribute node kinds → normalized contract-attribute tokens (matching
# the castxml spellings so cross-frontend snapshots stay comparable).
_CLANG_ATTR_TOKENS: dict[str, str] = {
    "NoReturnAttr": "noreturn",
    "C11NoReturnAttr": "noreturn",
    "NonNullAttr": "nonnull",
    "ReturnsNonNullAttr": "returns_nonnull",
    "RestrictAttr": "malloc",
    "FormatAttr": "format",
    "FormatArgAttr": "format_arg",
    "AllocSizeAttr": "alloc_size",
    "AllocAlignAttr": "alloc_align",
    "WarnUnusedResultAttr": "warn_unused_result",
    "SentinelAttr": "sentinel",
    "CDeclAttr": "cdecl",
    "StdCallAttr": "stdcall",
    "FastCallAttr": "fastcall",
    "ThisCallAttr": "thiscall",
    "VectorCallAttr": "vectorcall",
    "MSABIAttr": "ms_abi",
    "SysVABIAttr": "sysv_abi",
    "RegparmAttr": "regparm",
}


def _clang_attr_arg_tokens(child: dict[str, Any]) -> list[str]:
    """Return ordered ABI-significant argument scalars from an attribute node."""
    args: list[str] = []

    def _walk(nodes: Any) -> None:
        for sub in nodes or []:
            if not isinstance(sub, dict):
                continue
            value = sub.get("value")
            if isinstance(value, bool):
                _walk(sub.get("inner", []))
            elif isinstance(value, int):
                args.append(str(value))
            elif isinstance(value, str) and value:
                args.append(value.strip('"'))
            else:
                _walk(sub.get("inner", []))

    _walk(child.get("inner", []))
    return args


def _target_default_abi_attribute(target_triple: str | None) -> str | None:
    """Return the explicit default ABI token for known x86-64 targets."""
    if not target_triple:
        return None
    parts = target_triple.lower().split("-")
    if not parts or parts[0] not in {"x86_64", "amd64"}:
        return None
    if "windows" in parts:
        return "ms_abi"
    if any(os_name in parts for os_name in (
        "linux", "android", "darwin", "freebsd", "netbsd", "openbsd", "solaris",
    )):
        return "sysv_abi"
    return None


def clang_contract_attributes(
    node: dict[str, Any], *, target_triple: str | None = None
) -> list[str]:
    """Normalize Clang function contract/calling-convention attributes.

    Public (no leading underscore) since ``extract.headers.clang.functions``
    reads it across the module boundary -- ``_clang_contract_attributes``
    below is kept as a back-compat alias for every existing caller spelling
    the old private name (Codex review, PR #940).
    """
    tokens: set[str] = set()
    for child in node.get("inner", []) or []:
        if not isinstance(child, dict):
            continue
        token = _CLANG_ATTR_TOKENS.get(str(child.get("kind", "")))
        if token:
            arg_tokens = _clang_attr_arg_tokens(child)
            if arg_tokens:
                token = f"{token}({','.join(arg_tokens)})"
            tokens.add(token)

    type_obj = node.get("type")
    if isinstance(type_obj, dict):
        effective_type = type_obj.get("desugaredQualType")
        if not isinstance(effective_type, str) or not effective_type:
            effective_type = type_obj.get("qualType")
        if isinstance(effective_type, str):
            qualifiers = _function_qualifiers(effective_type)
            for spelling in ("ms_abi", "sysv_abi"):
                if re.search(
                    rf"__attribute__\s*\(\(\s*{spelling}\s*\)\)", qualifiers
                ):
                    tokens.add(spelling)

    default_abi = _target_default_abi_attribute(target_triple)
    if default_abi is not None:
        tokens.discard(default_abi)
    return sorted(tokens)


#: Back-compat alias -- see :func:`clang_contract_attributes`'s own docstring.
_clang_contract_attributes = clang_contract_attributes
