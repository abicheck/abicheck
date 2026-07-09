# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""POST Python export-manifest adapter (ABI-commitment checking).

`POST Python <https://post-py.org/>`_ is a statically-typed, ahead-of-time
compilable subset of Python.  ``post-py build`` emits C99, compiles each module
as a translation unit, and links a shared library whose **stable C ABI** is the
set of ``pp_<name>`` wrapper symbols.  Alongside the library, ``--emit-manifest``
writes a machine-readable JSON export manifest (spec §9.1.1) that names, for each
export, its Python name, ``pp_*`` C symbol, private kernel symbol, defining
module, kind, parameter/return dtypes, and — for vectorized functions — the ufunc
loop symbol and layout signature.  The manifest carries a ``"post_abi"`` integer
that POST commits to bump on ABI-breaking revisions.

This module turns that manifest into something abicheck can *enforce*:

* :func:`public_c_symbols` — the committed ABI surface (the ``pp_*`` set), for
  scoping a binary comparison to the contract and ignoring ``__pp_*`` kernel
  churn (which POST explicitly documents as internal).
* :func:`validate_manifest_against_binary` — three-way consistency: every
  ``c_symbol`` (and ufunc ``loop_symbol``) the manifest promises is actually
  exported by the built library, and every exported ``pp_*`` symbol is declared.
* :func:`diff_manifests` — a compiler-independent ABI diff of two manifests
  (removed exports, changed signatures, changed ufunc loops). The manifest
  carries dtypes the stripped binary does not, so this catches signature breaks
  a symbol-only diff misses.
* :func:`check_version_gate` — enforces POST's own promise: an ABI-breaking
  manifest diff **requires** a ``post_abi`` bump. This is the CI check that makes
  the version number mean something.

Parsing is tolerant of unknown/extra fields so it survives the v0.x draft spec.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .elf_metadata import ElfMetadata

# Symbol types that constitute an exported ABI surface in an ELF library.
# POST manifest entries describe *callable* wrapper/loop symbols (they carry
# `params`/`return_dtype`), so only callable exports can satisfy a promise. A
# data OBJECT that happens to share the name does not satisfy clients compiled to
# call `pp_foo(...)`, so OBJECT symbols are deliberately excluded. NOTYPE is
# included because the rest of the ELF path treats STT_NOTYPE as function-like
# (see dumper.py) — an asm/linker-defined wrapper exported as NOTYPE is still a
# callable entry point unversioned clients can link to.
_CALLABLE_SYM_TYPE_NAMES = frozenset({"FUNC", "IFUNC", "NOTYPE"})


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------

@dataclass
class PostUfunc:
    """The ufunc facet of a vectorized export (``@vectorize``/``@guvectorize``)."""

    loop_symbol: str = ""
    signature: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PostUfunc:
        # The loop symbol is the committed ABI surface of the facet — the later
        # public-surface and validation paths only add/check *truthy* loop
        # symbols. A ufunc object without one would silently shrink the surface
        # (validation PASSes without ever checking the promised loop), so a facet
        # missing its `loop_symbol` is malformed and must fail parsing.
        # Must be a string: a truthy non-string (123, ['pp_loop']) would str()-
        # coerce to a bogus committed loop name, so two schema-drifted manifests
        # sharing the placeholder could compare unchanged while the real loop was
        # renamed/dropped. Validate before the non-empty check (like `signature`).
        raw_loop = data.get("loop_symbol", "")
        if not isinstance(raw_loop, str):
            raise ValueError(f"ufunc 'loop_symbol' must be a string, got {raw_loop!r}")
        loop_symbol = raw_loop
        if not loop_symbol:
            raise ValueError(f"ufunc facet missing required 'loop_symbol': {data!r}")
        # The ufunc signature is the committed loop layout, and this module treats
        # a layout change as breaking. A missing/null/non-string `signature` would
        # coerce to "" and, if both manifests omit it, hide a real layout change
        # from diff_manifests()/the gate. Require a present string ("" allowed as
        # an explicit layout), like `return_dtype`.
        if "signature" not in data:
            raise ValueError(f"ufunc facet missing required 'signature': {data!r}")
        raw_sig = data["signature"]
        if not isinstance(raw_sig, str):
            raise ValueError(f"ufunc 'signature' must be a string, got {raw_sig!r}")
        return cls(loop_symbol=loop_symbol, signature=raw_sig)


# Parameter-level fields that change the POST C ABI shape of an argument.
# Everything else on a param object (name, doc, units, draft-schema keys) is
# metadata and must not distinguish the ABI descriptor.
_ABI_SHAPE_FLAGS: tuple[str, ...] = ("is_array", "is_core_dim")


def _param_descriptor(p: Any) -> str:
    """Canonicalize one manifest parameter into an ABI-comparable descriptor.

    A parameter may be a bare dtype string (``"Float64"``) or an object
    (``{"name": "x", "dtype": "Float64", "is_array": true}``). The *name* is not
    part of the ABI, but a field that actually changes the ABI shape is — a
    scalar ``Float64`` and an array/core-dim ``Float64`` have different POST C
    ABI shapes (value vs ``__pp_array`` view). Only the *known ABI-shape flags*
    (``_ABI_SHAPE_FLAGS``) contribute, and only when truthy: explicit scalar
    defaults (``is_array: false``, ``is_core_dim: false``) carry no ABI meaning,
    so ``{"dtype": "Float64", "is_array": false}`` collapses to the same
    descriptor as the bare ``"Float64"`` form. Unknown/non-ABI metadata (``doc``,
    ``units``, draft-schema fields) is ignored — the module promises tolerance of
    unknown keys, so such fields must not read as a breaking signature change.
    """
    # A parameter is a dtype *string* or an object with a string `dtype`. A
    # non-string scalar (null/false/0) or a non-string object dtype would coerce
    # via ``str(...)`` to a bogus-but-nonempty descriptor ("None"/"0"), so two
    # broken manifests could compare equal and hide a real dtype change — reject
    # like ``return_dtype`` does.
    if not isinstance(p, dict):
        if not isinstance(p, str):
            raise ValueError(f"parameter dtype must be a string, got {p!r}")
        if not p:
            # An empty bare dtype normalizes to the same "" descriptor as another
            # empty dtype, so a real change could hide. A no-arg export is
            # `params: []`, not `params: [""]`, so reject the empty dtype (parity
            # with the object-`dtype` check below).
            raise ValueError("parameter dtype must be a non-empty string")
        return p
    raw_dtype = p.get("dtype", "")
    if not isinstance(raw_dtype, str):
        raise ValueError(f"parameter 'dtype' must be a string, got {raw_dtype!r}")
    dtype = raw_dtype
    if not dtype:
        # A parameter object without a real `dtype` (missing, empty, or spelled
        # e.g. "type") would normalize to an empty descriptor, so diff_manifests
        # could compare two different real dtypes as equal and hide the exact
        # ABI break this adapter gates. Fail parsing instead.
        raise ValueError(f"parameter object missing required 'dtype': {p!r}")
    extras = {k: p[k] for k in _ABI_SHAPE_FLAGS if p.get(k)}
    if not extras:
        return dtype
    extra_str = ",".join(f"{k}={extras[k]}" for k in sorted(extras))
    return f"{dtype}[{extra_str}]" if dtype else f"[{extra_str}]"


@dataclass
class PostExport:
    """One entry from the manifest ``"exports"`` array (spec §9.1.1)."""

    name: str  # Python-level export name
    c_symbol: str  # the ``pp_<name>`` contract symbol
    kernel_symbol: str = ""  # private ``__pp_*`` implementation symbol
    module: str = ""
    kind: str = ""  # e.g. "function", "alias"
    alias_of: str | None = None
    params: list[str] = field(default_factory=list)  # parameter dtypes, in order
    return_dtype: str = ""
    ufunc: PostUfunc | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PostExport:
        name = str(data.get("name", "") or "")
        c_symbol = str(data.get("c_symbol", "") or "")
        if not name and not c_symbol:
            raise ValueError("export entry has neither 'name' nor 'c_symbol'")
        # An export's signature *is* its ABI, so params/return_dtype are required
        # for *every* export — including aliases. Nothing here resolves
        # ``alias_of`` when building ``signature_tuple()``, so an alias that omits
        # its signature would compare as an empty descriptor on both sides and a
        # retargeted alias (Float64 target -> Int64 target) would slip past
        # diff_manifests()/the version gate. The manifest must therefore
        # *materialize* every export's signature. A no-arg / void export spells
        # ``"params": []`` / ``"return_dtype": ""``; a *missing* field fails.
        if "params" not in data:
            raise ValueError(
                f"export {name or c_symbol!r}: missing required 'params' array"
            )
        # A falsey non-list value ("", 0, False, None) is malformed and must
        # reach the type check, not silently become a no-arg export.
        raw_params = data["params"]
        if not isinstance(raw_params, list):
            raise ValueError(
                f"export {name or c_symbol!r}: 'params' must be an array"
            )
        params = [_param_descriptor(p) for p in raw_params]
        if "return_dtype" not in data:
            raise ValueError(
                f"export {name or c_symbol!r}: missing required 'return_dtype'"
            )
        # Must be a string: "" is the documented void spelling, but a present
        # ``null``/``false``/``0`` would coerce to the *same* empty descriptor and
        # hide a real return-dtype change (Float64 -> Int64) from the diff/gate.
        raw_return = data["return_dtype"]
        if not isinstance(raw_return, str):
            raise ValueError(
                f"export {name or c_symbol!r}: 'return_dtype' must be a string"
            )
        return_dtype = raw_return
        # A present-but-malformed `ufunc` (e.g. a list from a schema-drifted
        # generator) must not be silently dropped to `None`: that would remove
        # its loop symbol from the committed surface (`public_c_symbols`) and from
        # binary validation, letting a bad manifest shrink the ABI surface and
        # pass without checking the promised loop. Absent/`null` means no facet;
        # anything else non-object is an error.
        ufunc_raw = data.get("ufunc")
        if ufunc_raw is None:
            ufunc = None
        elif isinstance(ufunc_raw, dict):
            ufunc = PostUfunc.from_dict(ufunc_raw)
        else:
            raise ValueError(
                f"export {name or c_symbol!r}: 'ufunc' must be an object"
            )
        alias_of = data.get("alias_of")
        return cls(
            name=name,
            c_symbol=c_symbol or f"pp_{name}",
            kernel_symbol=str(data.get("kernel_symbol", "") or ""),
            module=str(data.get("module", "") or ""),
            kind=str(data.get("kind", "") or ""),
            alias_of=str(alias_of) if alias_of else None,
            params=params,
            return_dtype=return_dtype,
            ufunc=ufunc,
        )

    def signature_tuple(self) -> tuple[str, ...]:
        """A comparable signature: (return_dtype, *params). Kind/module excluded."""
        return (self.return_dtype, *self.params)


@dataclass
class PostManifest:
    """A parsed POST export manifest."""

    post_abi: int
    exports: list[PostExport] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    def export_by_c_symbol(self) -> dict[str, PostExport]:
        return {e.c_symbol: e for e in self.exports if e.c_symbol}


def parse_manifest(data: dict[str, Any]) -> PostManifest:
    """Parse a manifest ``dict`` into a :class:`PostManifest`.

    Tolerant of extra/unknown keys (the spec is a v0.x draft). Raises
    :class:`ValueError` only when the shape is fundamentally wrong.
    """
    if not isinstance(data, dict):
        raise ValueError(f"manifest must be a JSON object, got {type(data).__name__}")
    if "post_abi" not in data:
        raise ValueError("manifest missing required 'post_abi' version field")
    # Require a genuine JSON integer. `int()` would silently coerce a malformed
    # contract (`true -> 1`, `1.9 -> 1`, `"5" -> 5`), letting the version gate
    # normalize an invalid version field and report a breaking change as covered
    # by a bump. `bool` is an `int` subclass, so exclude it explicitly.
    raw_abi = data["post_abi"]
    if isinstance(raw_abi, bool) or not isinstance(raw_abi, int):
        raise ValueError(f"'post_abi' must be an integer, got {raw_abi!r}")
    post_abi = raw_abi

    # `exports` is required, like `post_abi`: a genuinely empty ABI surface must
    # spell `"exports": []`. Accepting a *missing* key would let a truncated /
    # schema-drifted manifest read as "no promised symbols", so validation checks
    # nothing and `compare --post-manifest` scopes every wrapper removal out of
    # the verdict.
    if "exports" not in data:
        raise ValueError("manifest missing required 'exports' array")
    raw_exports = data["exports"]
    if not isinstance(raw_exports, list):
        raise ValueError("'exports' must be an array")
    # A non-object entry (e.g. a bare string) must fail parsing, not be dropped —
    # silently shrinking the ABI surface would let validation and the version
    # gate pass without ever checking the promised symbol.
    exports = []
    seen_c_symbols: set[str] = set()
    for i, e in enumerate(raw_exports):
        if not isinstance(e, dict):
            raise ValueError(
                f"exports[{i}] must be an object, got {type(e).__name__}"
            )
        exp = PostExport.from_dict(e)
        # A c_symbol is the committed contract name; duplicates are internally
        # invalid. `export_by_c_symbol()` keeps only the last, so a stale
        # duplicate carrying the *old* signature after a changed one would let
        # diff/version-gate see no break — reject rather than let ordering hide
        # an ABI change.
        if exp.c_symbol and exp.c_symbol in seen_c_symbols:
            raise ValueError(f"duplicate c_symbol in manifest: {exp.c_symbol!r}")
        if exp.c_symbol:
            seen_c_symbols.add(exp.c_symbol)
        exports.append(exp)
    return PostManifest(post_abi=post_abi, exports=exports, raw=data)


def load_manifest(path: Path) -> PostManifest:
    """Load and parse a manifest JSON file."""
    text = Path(path).read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path}: invalid JSON: {exc}") from exc
    return parse_manifest(data)


def public_c_symbols(manifest: PostManifest) -> set[str]:
    """The committed ABI surface — the set of ``pp_*`` symbols (and ufunc loops).

    Feed this to a scoped comparison so ``__pp_*`` kernel churn is ignored.
    """
    syms: set[str] = set()
    for exp in manifest.exports:
        if exp.c_symbol:
            syms.add(exp.c_symbol)
        if exp.ufunc and exp.ufunc.loop_symbol:
            syms.add(exp.ufunc.loop_symbol)
    return syms


def _snapshot_contract_symbols(snap: Any) -> set[str]:
    """Committed-namespace *callable* export names (``pp_*``, excluding ``__pp_*``).

    A ``pp_``-prefixed exported symbol is a POST contract wrapper by convention;
    ``__pp_*`` kernels start with ``__`` and are excluded. POST commitments are
    *callable* (wrappers / ufunc loops), so data exports are excluded: only DWARF
    functions and callable symbol-table entries count — ELF FUNC/IFUNC/NOTYPE,
    Mach-O non-``__DATA``. (The PE export directory carries no data/function type
    info, so its entries are kept, matching the validation path's limitation.)
    Walking the export tables as well as ``functions`` covers a symbol-only
    (no-debug) snapshot.
    """
    out: set[str] = set()

    def _add(name: str) -> None:
        if name.startswith("pp_"):  # `__pp_*` starts with "__", so not matched
            out.add(name)

    # Only *linkable* exports count as "present". A wrapper that was made hidden
    # in the new build still has a Function/symbol entry but clients can no
    # longer link to it, so it must not read as present (else `old − new` would
    # fail to recover the now-unlinkable wrapper and demote its visibility break).
    for f in getattr(snap, "functions", None) or ():
        vis = getattr(getattr(f, "visibility", None), "value", "")
        if vis and vis not in ("public", "elf_only"):
            continue  # hidden/internal/protected: not a public export
        for attr in ("mangled", "name"):
            _add(getattr(f, attr, "") or "")
    for s in getattr(getattr(snap, "elf", None), "symbols", None) or ():
        st = getattr(s, "sym_type", None)
        if st is None or st.name not in _CALLABLE_SYM_TYPE_NAMES:
            continue
        if getattr(s, "visibility", "default") in ("hidden", "internal"):
            continue  # STV_HIDDEN/INTERNAL: not dynamically linkable
        if not getattr(s, "is_default", True):
            continue  # non-default version alias (pp_old@POST_1): does not
            # satisfy an unversioned client link, so it is not "present" — matches
            # _exported_symbol_names, so a default->non-default demotion recovers.
        _add(getattr(s, "name", "") or "")
    for s in getattr(getattr(snap, "macho", None), "exports", None) or ():
        if not getattr(s, "is_data", False):
            _add(getattr(s, "name", "") or "")
    for s in getattr(getattr(snap, "pe", None), "exports", None) or ():
        _add(getattr(s, "name", "") or "")
    return out


def contract_scope_allowlist(
    manifest: PostManifest,
    old_snapshot: Any = None,
    new_snapshot: Any = None,
) -> set[str]:
    """The committed surface to scope ``compare --post-manifest`` against.

    The manifest's ``pp_*``/loop symbols, plus committed-namespace (``pp_*``,
    excluding ``__pp_*``) symbols exported by the old snapshot.

    The old-symbol union keeps any previously committed wrapper in-surface even
    when the supplied manifest is the **new** one and omits it. That covers both
    dropped wrappers and still-exported-but-undeclared wrappers: their removals,
    visibility changes, or signature churn must not be silently demoted by a
    manifest-authoritative allowlist. ``__pp_*`` kernel churn is excluded
    throughout.

    BOUNDARY: snapshot recovery keys on the ``pp_*`` namespace, since a single
    (new) manifest cannot name what was committed before. A committed ufunc
    ``loop_symbol`` that is *not* ``pp_``-prefixed and is dropped/renamed across
    versions therefore may be demoted under binary scoping. The
    manifest↔manifest :func:`diff_manifests` / :func:`check_version_gate` —
    which see *both* versions — are the authoritative check for loop-symbol
    renames and removals; use them in release gating, with binary
    ``compare --post-manifest`` as the best-effort surface filter.
    """
    old_syms = (
        _snapshot_contract_symbols(old_snapshot)
        if old_snapshot is not None
        else removed_contract_symbols(old_snapshot, new_snapshot)
    )
    return public_c_symbols(manifest) | old_syms


def removed_contract_symbols(old_snapshot: Any = None, new_snapshot: Any = None) -> set[str]:
    """Committed-namespace (``pp_*``) callable exports present in *old* but not *new*.

    The removed-wrapper recovery set: unioned into a manifest scope allowlist so a
    dropped/hidden committed wrapper stays in-surface even when the manifest is the
    new one that no longer lists it. Exposed so the Tier-2 service path can apply
    the *same* recovery the CLI does when a caller supplies a raw
    ``public_surface_allowlist`` built from the new manifest.
    """
    old_syms = _snapshot_contract_symbols(old_snapshot) if old_snapshot is not None else set()
    new_syms = _snapshot_contract_symbols(new_snapshot) if new_snapshot is not None else set()
    return old_syms - new_syms


# ---------------------------------------------------------------------------
# Manifest ↔ binary consistency
# ---------------------------------------------------------------------------

@dataclass
class ManifestValidationResult:
    """Outcome of validating a manifest against a built library."""

    library: str = ""
    missing: list[str] = field(default_factory=list)  # promised but not exported
    missing_ufunc_loops: list[str] = field(default_factory=list)
    undeclared: list[str] = field(default_factory=list)  # exported pp_* not in manifest

    @property
    def passed(self) -> bool:
        # Undeclared symbols are a warning, not a failure: the contract the
        # manifest promises must hold; extra exports are informational.
        return not self.missing and not self.missing_ufunc_loops


def _exported_symbol_names(elf_meta: ElfMetadata) -> set[str]:
    """Names of exported *callable* (FUNC/IFUNC) symbols in an ELF library.

    Two filters keep this to symbols that actually satisfy a POST promise:

    * Only callable types (``_CALLABLE_SYM_TYPE_NAMES``). A data ``OBJECT``
      named ``pp_foo`` does not satisfy a client compiled to call ``pp_foo(...)``.
    * Only default/unversioned definitions. Non-default version aliases
      (``pp_foo@POST_1`` with ``is_default == False``) do not bind an unversioned
      consumer link to ``pp_foo`` (only ``pp_foo`` / ``pp_foo@@POST_1`` do).

    Counting either would let ``validate_manifest_against_binary`` pass while real
    clients fail to link/call. This mirrors how the rest of the repo builds its
    exported surface (see ``cli_buildsource_merge`` / ``diff_platform``).
    """
    names: set[str] = set()
    for sym in elf_meta.symbols:
        if sym.sym_type.name in _CALLABLE_SYM_TYPE_NAMES and sym.is_default:
            names.add(sym.name)
    return names


def validate_manifest_against_symbols(
    manifest: PostManifest,
    exported: set[str],
    library: str = "UNKNOWN",
) -> ManifestValidationResult:
    """Core check: promised ``c_symbol``/ufunc loops vs a set of exported names.

    Format-agnostic — the caller supplies the exported-symbol set (ELF/PE/Mach-O).
    Also reports exported ``pp_*`` symbols absent from the manifest (undeclared
    public surface). Kernel ``__pp_*`` symbols are ignored — they are, by POST's
    own spec, private implementation detail.
    """
    result = ManifestValidationResult(library=library or "UNKNOWN")

    declared_symbols: set[str] = set()
    for exp in manifest.exports:
        if exp.c_symbol:
            declared_symbols.add(exp.c_symbol)
            if exp.c_symbol not in exported:
                result.missing.append(exp.c_symbol)
        if exp.ufunc and exp.ufunc.loop_symbol:
            declared_symbols.add(exp.ufunc.loop_symbol)
            if exp.ufunc.loop_symbol not in exported:
                result.missing_ufunc_loops.append(exp.ufunc.loop_symbol)

    # Undeclared: exported `pp_*` contract-looking symbols not named by the
    # manifest. `__pp_*` (kernel) symbols are private and excluded.
    for name in sorted(exported):
        if name.startswith("pp_") and name not in declared_symbols:
            result.undeclared.append(name)

    result.missing.sort()
    result.missing_ufunc_loops.sort()
    return result


def validate_manifest_against_binary(
    manifest: PostManifest,
    elf_meta: ElfMetadata,
) -> ManifestValidationResult:
    """Validate a manifest against parsed ELF metadata (see the symbols-set core)."""
    return validate_manifest_against_symbols(
        manifest, _exported_symbol_names(elf_meta), elf_meta.soname or "UNKNOWN"
    )


def _exported_names_for_binary(so_path: Path) -> tuple[set[str], str]:
    """Return (exported symbol names, library label) for an ELF/PE/Mach-O file."""
    from .binary_utils import detect_binary_format, normalize_binary_input

    normalized_path, binary_fmt = normalize_binary_input(so_path)
    if binary_fmt is None:
        binary_fmt = detect_binary_format(normalized_path)

    if binary_fmt == "elf":
        from .elf_metadata import parse_elf_metadata

        elf_meta = parse_elf_metadata(normalized_path)
        return _exported_symbol_names(elf_meta), elf_meta.soname or "UNKNOWN"
    if binary_fmt == "pe":
        from .pe_metadata import parse_pe_metadata

        pe_meta = parse_pe_metadata(normalized_path)
        # NOTE: unlike ELF (STT_OBJECT) and Mach-O (__DATA / is_data), the PE
        # export directory carries no data-vs-function type information — it is a
        # flat list of name/ordinal → RVA with no symbol type. So there is no
        # equivalent data-export filter to apply here; every named export is kept.
        # Distinguishing a data export would require an RVA-vs-code-section
        # heuristic that the PE parser does not model.
        names = {e.name for e in pe_meta.exports if e.name}
        return names, Path(so_path).name
    if binary_fmt in ("macho", "mach-o"):
        from .macho_metadata import parse_macho_metadata

        macho_meta = parse_macho_metadata(normalized_path)
        # Exclude __DATA globals (is_data) for parity with the ELF OBJECT filter:
        # a data symbol named `pp_foo` does not satisfy a client compiled to call
        # the callable POST wrapper `pp_foo(...)`. LIMITATION: MachoExport.is_data
        # only tracks the plain __DATA segment, so a const-data export in
        # __DATA_CONST or __TEXT,__const is not caught here — a complete callable
        # check would need per-section segment coverage the Mach-O parser does not
        # yet model. In practice POST wrappers live in __TEXT, so this covers the
        # common case; the residual gap needs a macho_metadata parser enhancement.
        names = {e.name for e in macho_meta.exports if e.name and not e.is_data}
        return names, macho_meta.install_name or Path(so_path).name

    raise ValueError(
        f"manifest→binary validation supports ELF/PE/Mach-O, got {binary_fmt!r}"
    )


def validate_from_binary(manifest_path: Path, so_path: Path) -> ManifestValidationResult:
    """Convenience wrapper: load manifest + binary metadata, then validate.

    Supports ELF, PE/COFF, and Mach-O shared libraries. Raises :class:`ValueError`
    for unrecognized formats.
    """
    exported, library = _exported_names_for_binary(so_path)
    manifest = load_manifest(manifest_path)
    return validate_manifest_against_symbols(manifest, exported, library)


def format_validation_report(result: ManifestValidationResult) -> str:
    """Human-readable manifest↔binary validation report."""
    lines = [f"POST manifest validation for {result.library}:"]

    lines.append("  MISSING from binary (promised in manifest, not exported):")
    if result.missing:
        lines.extend(f"    {s}" for s in result.missing)
    else:
        lines.append("    (none)")

    if result.missing_ufunc_loops:
        lines.append("  MISSING ufunc loop symbols:")
        lines.extend(f"    {s}" for s in result.missing_ufunc_loops)

    lines.append("  UNDECLARED in manifest (exported pp_* not documented):")
    if result.undeclared:
        lines.extend(f"    {s}" for s in result.undeclared)
    else:
        lines.append("    (none)")

    if result.passed:
        n = len(result.undeclared)
        suffix = f" ({n} undeclared)" if n else ""
        lines.append(f"  Result: PASS{suffix}")
    else:
        n = len(result.missing) + len(result.missing_ufunc_loops)
        lines.append(f"  Result: FAIL ({n} missing symbol{'s' if n != 1 else ''})")

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Manifest ↔ manifest ABI diff (compiler-independent)
# ---------------------------------------------------------------------------

@dataclass
class ExportChange:
    """A per-export change between two manifests."""

    name: str
    c_symbol: str
    kind: str  # "removed" | "added" | "signature" | "ufunc_added" | "ufunc_signature" | "ufunc_loop_symbol"
    detail: str = ""

    @property
    def is_breaking(self) -> bool:
        return self.kind in ("removed", "signature", "ufunc_signature", "ufunc_loop_symbol")


@dataclass
class ManifestDiff:
    """Result of :func:`diff_manifests`."""

    old_abi: int
    new_abi: int
    changes: list[ExportChange] = field(default_factory=list)

    @property
    def breaking_changes(self) -> list[ExportChange]:
        return [c for c in self.changes if c.is_breaking]

    @property
    def is_breaking(self) -> bool:
        return bool(self.breaking_changes)

    @property
    def abi_bumped(self) -> bool:
        return self.new_abi > self.old_abi


def diff_manifests(old: PostManifest, new: PostManifest) -> ManifestDiff:
    """Compiler-independent ABI diff of two manifests, keyed by ``c_symbol``.

    Breaking: a removed export, a changed signature (return/param dtypes), or a
    changed ufunc loop signature. Adding an export is compatible.
    """
    old_map = old.export_by_c_symbol()
    new_map = new.export_by_c_symbol()
    diff = ManifestDiff(old_abi=old.post_abi, new_abi=new.post_abi)

    for c_symbol in sorted(old_map.keys() - new_map.keys()):
        exp = old_map[c_symbol]
        diff.changes.append(ExportChange(exp.name, c_symbol, "removed",
                                         "export dropped from ABI surface"))

    for c_symbol in sorted(new_map.keys() - old_map.keys()):
        exp = new_map[c_symbol]
        diff.changes.append(ExportChange(exp.name, c_symbol, "added",
                                         "new export"))

    for c_symbol in sorted(old_map.keys() & new_map.keys()):
        oe, ne = old_map[c_symbol], new_map[c_symbol]
        if oe.signature_tuple() != ne.signature_tuple():
            diff.changes.append(ExportChange(
                ne.name, c_symbol, "signature",
                f"{_fmt_sig(oe)}  ==>  {_fmt_sig(ne)}",
            ))
        # ufunc changes are only breaking when the export *already had* a ufunc
        # facet. Adding one to a previously-scalar export is a compatible
        # addition — old clients could not have linked to a loop symbol that did
        # not exist (analogous to an added export). Removing or altering an
        # existing facet is breaking.
        if oe.ufunc is None and ne.ufunc is not None:
            diff.changes.append(ExportChange(
                ne.name, c_symbol, "ufunc_added",
                f"ufunc facet added with loop symbol "
                f"{ne.ufunc.loop_symbol or '(none)'}",
            ))
        if oe.ufunc is not None:
            old_sig = oe.ufunc.signature
            new_sig = ne.ufunc.signature if ne.ufunc else ""
            if old_sig != new_sig:
                diff.changes.append(ExportChange(
                    ne.name, c_symbol, "ufunc_signature",
                    f"ufunc layout {old_sig!r} -> {new_sig!r}",
                ))
            # A renamed or dropped loop symbol breaks clients linked to the old
            # `pp_*_loop` export even when the layout signature is unchanged —
            # the loop symbol is part of the committed surface.
            old_loop_sym = oe.ufunc.loop_symbol
            new_loop_sym = ne.ufunc.loop_symbol if ne.ufunc else ""
            if old_loop_sym != new_loop_sym:
                diff.changes.append(ExportChange(
                    ne.name, c_symbol, "ufunc_loop_symbol",
                    f"ufunc loop symbol {old_loop_sym or '(none)'} -> "
                    f"{new_loop_sym or '(none)'}",
                ))
    return diff


def _fmt_sig(exp: PostExport) -> str:
    return f"({', '.join(exp.params)}) -> {exp.return_dtype or 'void'}"


def format_diff_report(diff: ManifestDiff, old_label: str, new_label: str) -> str:
    """Human-readable manifest diff report."""
    lines = [f"POST manifest diff: {old_label} (post_abi={diff.old_abi}) "
             f"-> {new_label} (post_abi={diff.new_abi})"]
    if not diff.changes:
        lines.append("  (no export changes)")
        return "\n".join(lines) + "\n"
    for change in diff.changes:
        marker = "BREAK" if change.is_breaking else "ok   "
        lines.append(f"  [{marker}] {change.kind:<16} {change.c_symbol}: {change.detail}")
    n_break = len(diff.breaking_changes)
    lines.append(f"  {n_break} breaking change{'s' if n_break != 1 else ''}, "
                 f"{len(diff.changes) - n_break} compatible")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Version-bump gate — enforces POST's own stability promise
# ---------------------------------------------------------------------------

@dataclass
class VersionGateResult:
    """Outcome of :func:`check_version_gate`."""

    diff: ManifestDiff

    @property
    def violated(self) -> bool:
        """True when there is a breaking change but ``post_abi`` did not bump."""
        return self.diff.is_breaking and not self.diff.abi_bumped

    @property
    def passed(self) -> bool:
        return not self.violated


def check_version_gate(old: PostManifest, new: PostManifest) -> VersionGateResult:
    """Require a ``post_abi`` bump whenever the manifest diff is ABI-breaking.

    This is the CI enforcement behind POST's documented commitment: the
    ``"post_abi"`` integer must increase on any ABI-breaking revision.
    """
    return VersionGateResult(diff=diff_manifests(old, new))


def format_gate_report(result: VersionGateResult, old_label: str, new_label: str) -> str:
    """Human-readable version-gate report."""
    diff = result.diff
    lines = [format_diff_report(diff, old_label, new_label).rstrip("\n")]
    if result.violated:
        lines.append(
            f"  VIOLATION: {len(diff.breaking_changes)} breaking change(s) require a "
            f"post_abi bump, but post_abi stayed at {diff.old_abi}."
        )
    elif diff.is_breaking:
        lines.append(
            f"  OK: breaking changes are covered by post_abi bump "
            f"{diff.old_abi} -> {diff.new_abi}."
        )
    else:
        lines.append("  OK: no breaking changes.")
    return "\n".join(lines) + "\n"
