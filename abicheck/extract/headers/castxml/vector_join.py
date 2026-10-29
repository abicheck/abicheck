# SPDX-License-Identifier: Apache-2.0
"""Join clang's vector spellings onto a castxml root, cached as a sidecar.

castxml's XML cannot say what a vector type is (see
:mod:`.vector_spellings`), so the castxml dump is joined with the clang AST of
the same header. The joined mapping is a *sidecar* stored in the AST cache
under the castxml cache key -- the same inputs that select the XML -- so a
cache hit does not run clang again.

A sidecar that is missing or malformed is a miss: the clang pass runs again and
the fresh mapping replaces the entry. It is never read as "no vector data".
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from xml.etree.ElementTree import Element

from ....errors import SnapshotError
from ....storage.ast_cache_location import ast_cache_store
from ..clang.backend import clang_header_dump
from .function_type_cc import (
    has_function_type_nodes,
    resolve_function_type_cc,
    with_function_type_cc,
)
from .vector_spellings import (
    has_vector_nodes,
    resolve_vector_spellings,
    with_vector_spellings,
)

log = logging.getLogger(__name__)

#: The AST-cache namespace of the sidecar, distinct from the castxml XML entry.
VECTOR_SIDECAR_BACKEND = "castxml-vectors"


def _string_map(payload: Any) -> dict[str, str] | None:
    if not isinstance(payload, dict):
        return None
    if not all(
        isinstance(node, str) and isinstance(text, str)
        for node, text in payload.items()
    ):
        return None
    return payload


def _valid_sidecar(payload: Any) -> tuple[dict[str, str], dict[str, str]] | None:
    """The stored (spellings, function-type conventions), or ``None`` if not valid.

    A sidecar written before the convention map existed has no ``function_cc``
    key, so it is a miss: clang runs again and the entry is replaced.
    """
    if not isinstance(payload, dict):
        return None
    spellings = _string_map(payload.get("spellings"))
    conventions = _string_map(payload.get("function_cc"))
    if spellings is None or conventions is None:
        return None
    return spellings, conventions


def join_vector_spellings(
    root: Element,
    key: str,
    *,
    headers: list[Path],
    extra_includes: list[Path],
    compiler: str,
    gcc_path: str | None,
    gcc_prefix: str | None,
    gcc_options: str | None,
    gcc_option_tokens: tuple[str, ...],
    sysroot: Path | None,
    nostdinc: bool,
    lang: str | None,
    extra_hash_dirs: tuple[Path, ...],
    exported_symbols: frozenset[str],
) -> Element:
    """Return *root* carrying the clang-derived vector spellings and function-type
    calling conventions, using the sidecar.

    A root with neither a vector node nor a function-type declaration is
    returned untouched and costs nothing. A clang failure leaves those nodes
    as castxml wrote them and is logged; nothing is published for it, so the
    next run tries again.
    """
    if not has_vector_nodes(root) and not has_function_type_nodes(root):
        return root
    store = ast_cache_store(key, VECTOR_SIDECAR_BACKEND)
    cached = _valid_sidecar(store.lookup(key))
    if cached is not None:
        spellings, conventions = cached
        return with_function_type_cc(
            with_vector_spellings(root, spellings), conventions
        )
    try:
        clang_root, _kind, _force_cpp = clang_header_dump(
            headers,
            extra_includes,
            compiler,
            gcc_path=gcc_path,
            gcc_prefix=gcc_prefix,
            gcc_options=gcc_options,
            gcc_option_tokens=gcc_option_tokens,
            sysroot=sysroot,
            nostdinc=nostdinc,
            lang=lang,
            extra_hash_dirs=extra_hash_dirs,
            exported_symbols=exported_symbols,
        )
    except (SnapshotError, OSError) as exc:
        log.warning(
            "clang could not parse the header(s) for vector spellings and "
            "function-type calling conventions (%s); those types stay as castxml "
            "wrote them",
            exc,
        )
        return root
    resolved = resolve_vector_spellings(root, clang_root)
    conventions = resolve_function_type_cc(root, clang_root)
    store.publish(key, {"spellings": resolved, "function_cc": conventions}, ())
    return with_function_type_cc(with_vector_spellings(root, resolved), conventions)
