# Copyright 2026 Nikolay Petrov
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

"""Snapshot-level cache for avoiding redundant binary analysis.

Cache key = SHA-256 of (binary content hash + header mtimes + compiler params).
Cache location = ``$XDG_CACHE_HOME/abi_check/snapshots/<key>.json`` or
``~/.cache/abi_check/snapshots/<key>.json``.
"""

from __future__ import annotations

import hashlib
import logging
import os
import stat
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from .header_utils import iter_cache_header_files

if TYPE_CHECKING:
    from .model import AbiSnapshot

_logger = logging.getLogger("abicheck.cache")

#: Maximum number of cached snapshots (LRU eviction by mtime).
MAX_ENTRIES: int = 100

#: Bumped whenever a change to the dumping/provenance pipeline could alter a
#: snapshot's content without changing any of the caller-supplied cache-key
#: inputs (headers/includes/version/lang/``extra``) — folding it into every
#: key invalidates all previously-cached entries on upgrade rather than risk
#: serving a stale snapshot computed by an older, behaviorally-different
#: abicheck version.
_SNAPSHOT_CACHE_VERSION: str = "24"
# v24: AbiSnapshot.semantic_ir is now genuinely populated for a real ELF
# header-AST dump (ADR-063 Phase 6, second slice -- extract/
# semantic_normalizer.py wired through dumper_manifest.resolve_header_ast_
# result). A snapshot cached by an older abicheck would silently keep
# semantic_ir=None/empty forever, even though a fresh dump of the identical
# inputs now populates it.
# v2: castxml's CvQualifiedType type-name spelling changed for a
# volatile-qualified pointer/reference VALUE (now a suffix, "T * volatile",
# matching clang's own convention, rather than always a prefix) -- an
# unconditional change to the default/most common cacheable dump path (Codex
# review). Any G28 Phase 3/4 hybrid-provenance or clang-layout-tool fact this
# PR also introduced is additionally covered by AbiSnapshot.SCHEMA_VERSION
# (serialization.py) for the on-disk snapshot JSON format itself; this
# constant is specifically for the separate whole-snapshot disk cache
# (snapshot_cache.py), which persists across process invocations and isn't
# gated by that schema version at all.
#
# v3 (G29 Phase A): the L2 header-only semantic graph became unconditional
# (previously gated behind the now-removed --header-graph/--header-graph-includes
# flags). service_dump_cache._dump_is_cacheable() allows the same plain
# "binary + public headers" shape onto this cache that a pre-upgrade,
# no-graph dump would already have stored under v2 -- without this bump, a
# warm cache from before the upgrade would be replayed verbatim and silently
# omit the new default-on graph until manually cleared (Codex review).
#
# Also folded into v3's key computation (_cache_key below): before G31 Phase
# A, a header-graph-enabled dump was *always* uncacheable, so a transitively
# included header changing under one of the ``-I``/``includes`` directories
# (e.g. a public header pulling in ``inc/detail.h``) always forced a live
# re-dump. Now that the same plain shape is cacheable, ``_cache_key`` walks
# each include directory and folds in the (path, mtime) of every header-like
# file found there -- not just the directory's own path -- so a transitive
# header edit invalidates the cache the same way editing an explicitly
# passed header already does (Codex review).
#
# v4 (ADR-050 D1/D2): ``_cache_key`` stopped sorting the caller-given
# ``headers``/``includes`` lists before hashing them. Order is a real,
# load-bearing input -- the same "-I search-precedence order is a real
# compile difference" rule ``comparability.py``'s ``profile_fingerprint``
# already enforces for the gate -- so two dumps requesting the same
# headers/includes in a different order can legitimately resolve to a
# different snapshot; sorting collapsed them to the same key and let a warm
# cache silently serve the wrong order's result. Bumped so an old,
# order-collapsed cache entry is never replayed as if it were order-aware.
#
# v6 (dependency-scope comparability gate, PR #651 follow-up, Codex review):
# ``cached_run_dump`` (``service_dump_cache.py``) returns a cache hit
# directly, without ever calling the ``run_dump`` callable it was passed --
# so a pre-existing cache entry (written before ``service.run_dump`` started
# tagging its result ``AbiSnapshot.dependency_scope="full"``) would keep
# replaying with that field ``None`` forever, silently bypassing
# ``comparability._check_dependency_scope_comparable`` for exactly the
# scenario it exists to catch (a stale cached "live dump" compared against a
# freshly filtered ``dump`` baseline). Bumped so every such entry is
# invalidated and recomputed through the now-tagging ``run_dump``.
#
# v7 (G31 Phase C, Codex review, fresh evidence): the direct-clang backend
# started extracting ``deprecated``/``EnumType.is_scoped``/
# ``RecordType.is_standard_layout``/``is_trivially_copyable`` for the first
# time, for the same headers/includes/version/lang/``extra`` inputs a
# pre-upgrade cache entry already covers -- an upgrading user's warm clang/
# hybrid cache would keep replaying the old snapshot (missing all four
# facts, or -- for a hybrid entry -- retaining stale bare-keyed
# ``fact_provenance``) until the entry happened to expire or was manually
# cleared, silently suppressing every newly-added detector this PR wires up.
# Bumped so the upgrade forces re-extraction instead.
#
# v8 (G31 Phase C continuation): the direct-clang backend started extracting
# ``TypeField.default`` (default member initializer) too, the one remaining
# fact-completeness gap from the same phase's list that backend can close.
# Identical reasoning to v7, one fact later -- an upgrading user's warm
# clang/hybrid cache entry would keep replaying the old snapshot (every
# field's initializer missing, or a hybrid entry's field ``default``
# provenance still bare-keyed from before the qualification fix that landed
# alongside this extraction) until it happened to expire or was manually
# cleared. Bumped so the upgrade forces re-extraction instead.
#
# v9 (G31 Phase C continuation, Codex review): ``dwarf_snapshot.py``'s
# ``RecordType.vptr_offset_bits`` stopped using a ``0 if vtable else None``
# heuristic in favor of reading GCC/Clang's own artificial vptr debug-info
# member (plus a whole-binary resolution pass for a class whose vtable is
# entirely inherited). This DWARF-derived value reaches a *cacheable*
# snapshot indirectly: ``dumper_layout_backfill.backfill_dwarf_layout()``
# backfills a header-AST (castxml/clang) snapshot's ``vptr_offset_bits``
# from the real binary's DWARF whenever the header-derived value is
# ``None`` -- the normal "binary + public headers" cacheable shape, not
# only the always-uncacheable ``--dwarf-only`` path. Without this bump, an
# upgrading user's warm cache entry (headers/includes/version/lang/
# ``extra`` all unchanged) would keep replaying the old, less-accurate
# backfilled value indefinitely. Bumped so the upgrade forces
# re-extraction instead.
#
# v10 (G31 Phase C continuation): the direct-clang backend started extracting
# ``Param.is_restrict`` (``dumper_clang._clang_param_is_restrict``), until
# now populated by castxml alone. Identical reasoning to v7/v8 -- an
# upgrading user's warm clang/hybrid cache entry, keyed on the same
# headers/includes/version/lang/``extra`` inputs a pre-upgrade dump already
# covers, would keep replaying a snapshot whose every parameter reads
# ``is_restrict=False`` until the entry happened to expire or was manually
# cleared, silently preserving the cross-backend false positive this
# extraction closes. Bumped so the upgrade forces re-extraction instead.
#
# v11 (G31 Phase C continuation): the direct-clang backend started
# extracting ``Param.is_va_list`` (``dumper_clang_qualifiers.
# _clang_param_is_va_list``, x86-64 System V spelling only), until now
# populated by no backend at all. Identical reasoning to v10 -- an
# upgrading user's warm clang/hybrid cache entry, keyed on the same
# headers/includes/version/lang/``extra`` inputs a pre-upgrade dump already
# covers, would keep replaying a snapshot whose every parameter reads
# ``is_va_list=False`` until the entry happened to expire or was manually
# cleared, silently keeping ``PARAM_BECAME_VA_LIST``/``PARAM_LOST_VA_LIST``
# unreachable. Bumped so the upgrade forces re-extraction instead.
#
# v12 (G31 Phase C continuation): the castxml backend started extracting
# ``Variable.access`` (``dumper_castxml._CastxmlParser._access_level``,
# reused from the existing Function/TypeField access extraction) and
# ``Variable.value`` (``el.get("init")``, const/constexpr variables only),
# until now unconditionally ``AccessLevel.PUBLIC``/``None`` for EVERY
# variable. Identical reasoning to v10/v11 for ``access`` specifically (the
# fact with no "not collected" state) -- an upgrading user's warm castxml
# cache entry would keep replaying a snapshot whose every variable reads
# ``access=PUBLIC`` regardless of its real C++ access specifier, silently
# preserving the false ``VAR_ACCESS_WIDENED`` a fresh dump vs. a legacy
# cache entry would otherwise produce. Bumped so the upgrade forces
# re-extraction instead.
#
# v13 (G31 Phase C fact-completeness, Codex review): the castxml backend
# started extracting ``EnumType.underlying_type``
# (``dumper_castxml.parse_enums``' new ``<Enumeration type=...>`` read),
# until now unconditionally the dataclass default ``"int"`` for EVERY
# castxml-backed (and therefore hybrid) enum. Identical reasoning to v10/
# v11/v12 -- an upgrading user's warm castxml/hybrid cache entry would keep
# replaying a snapshot whose every enum reads ``underlying_type="int"``
# regardless of its real declared/compiler-chosen underlying type, silently
# preserving the exact false ODR-agreement ``tu_merge.py``'s conflict check
# this extraction closes. Bumped so the upgrade forces re-extraction
# instead.
#
# v14 (G31 Phase C fact-completeness continuation): the hybrid merge
# (``dumper_hybrid._merge_record_type``) started OR-merging
# ``RecordType.is_template_pattern``/``has_anonymous_aggregate_fields`` from
# clang onto a castxml-matched type, until now silently dropped for a type
# both backends saw (castxml's own always-``False`` for these two plain
# bools was never itself the trigger for the existing None-check backfill
# pattern). An upgrading user's warm hybrid cache entry would keep replaying
# a snapshot missing this backfill for an anonymous-aggregate-only record
# whose castxml-side ``fields`` happen to be empty (an opaque/incomplete
# castxml record) until the entry happened to expire or was manually
# cleared. Bumped so the upgrade forces re-extraction instead.
#
# v15 (G31 Phase C fact-completeness continuation): the direct-clang backend
# started emitting an opaque stub (``RecordType.is_opaque=True``, empty
# fields/bases/vtable) for a forward-declaration-only record identity
# (``struct Handle;`` with no definition anywhere in the TU), until now
# silently ABSENT from a clang snapshot entirely (``parse_types`` skipped
# every non-definition record). An upgrading user's warm clang cache entry
# would keep replaying a snapshot missing every opaque handle type until the
# entry happened to expire or was manually cleared -- not just a wrong
# value, an entity that used to not exist in the snapshot at all. Bumped so
# the upgrade forces re-extraction instead.
#
# v16 (symbol-binding model field, Codex review): ``dumper_elf_symbols.
# _populate_elf_visibility`` started also populating ``Function.elf_binding``/
# ``Variable.elf_binding`` from the same unconditional ``.dynsym`` symbol-map
# pass that already populates ``elf_visibility`` -- reached by every ELF
# dump, not gated behind any opt-in flag. Without this bump, an upgrading
# user's warm whole-snapshot cache entry (headers/includes/version/lang/
# ``extra`` all unchanged) would keep replaying the old snapshot with every
# function/variable's ``elf_binding`` still ``None``, silently making a new
# ``binding:`` suppression selector never match until the entry happened to
# expire or was manually cleared. Bumped so the upgrade forces re-extraction
# instead.
#
# v17 (G31 Phase C backend audit): the direct-clang backend started
# extracting ``Function.is_override`` (from a real ``OverrideAttr`` child
# node) and ``RecordType.is_abstract`` (from ``definitionData.isAbstract``),
# until now unconditionally ``None`` on every clang-parsed declaration (both
# facts were castxml-only). An upgrading user's warm clang/hybrid cache entry
# would keep replaying a snapshot with both facts silently unset -- and,
# correspondingly, the two now-live diff detectors
# (``func_override_specifier``/``type_became_abstract``) permanently
# declining every clang-side comparison via ``both_known_backed_fact`` --
# until the entry happened to expire or was manually cleared. Bumped so the
# upgrade forces re-extraction instead.
#
# v18 (G31 Phase C continuation, same pass): two changes bundled together,
# both purely additive so neither needs its own version bump alone, but a
# warm cache entry from before either shipped would keep replaying a
# snapshot/merge missing the new data:
#   - ``AbiSnapshot.typedefs_qualified`` (schema v25): both header backends
#     now also populate a fully-qualified-name-keyed twin of ``typedefs``,
#     closing the bare-name member-typedef collision gap documented on that
#     field. A pre-v18 cache entry has an unconditionally empty
#     ``typedefs_qualified`` even when the underlying headers would produce
#     real entries.
#   - the hybrid merge (``dumper_hybrid.py``) started stamping
#     ``is_override``/``is_abstract`` provenance for a clang-only-appended
#     method/type (previously only "deprecated" got this treatment for
#     clang-only entities) -- without it, ``both_known_backed_fact`` saw no
#     recorded provenance for a clang-only declaration's is_override/
#     is_abstract and silently declined to compare a real transition, even
#     though v17 above already made the underlying facts real. A pre-v18
#     hybrid cache entry's ``fact_provenance`` dict is missing these two
#     entries for every clang-only declaration.
# Bumped so the upgrade forces re-extraction instead of replaying either gap.
#
# v19 (G31 Phase C continuation, same pass, Codex review): v18's own
# clang-only ``is_abstract`` hybrid-merge provenance stamp used the
# namespace-qualified key, but ``diff_types.py``'s own lookup only ever
# reads the bare-name key (matching ``_merge_record_type``'s pre-existing
# bare convention for this one fact specifically) -- so the v18 stamp was
# silently inert for any namespaced clang-only type. A warm hybrid cache
# entry computed under v18 has the wrong (qualified) key recorded, which
# would keep making a real abstractness transition on a namespaced
# clang-only type undetectable even after upgrading to the fix. Bumped so
# the upgrade forces re-extraction instead of replaying the inert stamp.
#
# v20 (ADR-063 Phase 0, Codex review on #909): castxml/direct-clang started
# constructing ``vptr_offset_bits_fact``/``is_va_list_fact`` as
# ``Fact.partial(...)`` instead of ``Fact.present(...)`` for the
# already-documented heuristic/target-scoped values -- a real change to
# snapshot content with no change to any caller-supplied cache-key input.
# A warm cache from before this fix would keep serving the overclaimed
# ``PRESENT`` status this fix exists to correct. Bumped so upgrading forces
# re-extraction instead of silently replaying the stale status.
#
# v22 (ADR-063 Phase 2 (c1), Codex review on PR #949): the entity_id carrier
# (schema v28) is now persisted through this cache too, since store_key/
# lookup_key round-trip through write_snapshot/load_snapshot -- a real
# on-disk JSON serialization, not an in-memory object cache. A warm cache
# entry written before this change has entity_id=None for every declaration
# (the field genuinely didn't survive serialization then), and re-saving
# that loaded snapshot stamps it with the CURRENT SCHEMA_VERSION (28)
# regardless of what schema_version it was loaded from -- so a stale v21
# cache entry would silently masquerade as a genuine v28 extraction that
# happens to have resolved no identities, rather than one that never had
# the chance to. Bumped for the identical reason v21 above was: the same TU
# inputs now produce a different AbiSnapshot, with no change to any
# caller-supplied cache-key input.
#
# v23 (ADR-063 Phase 2 closing slice): both header backends now also
# populate ``AbiSnapshot.typedef_entity_ids``/``constant_entity_ids``
# (schema v31) -- the same TU inputs produce a different snapshot, with no
# change to any caller-supplied cache-key input, so a warm pre-v23 entry
# would keep replaying empty sidecars and identity-less typedef/constant
# findings. Bumped for the identical reason v22/v21 below were.
#
# v21 (PR C item 3, Codex review): castxml/direct-clang started stamping
# ``Function.is_compiler_generated`` (schema v27) from castxml's own
# ``artificial="1"`` attribute / clang's implicit-node-skipping guarantee --
# the same TU inputs now produce a different ``AbiSnapshot``, with no change
# to any caller-supplied cache-key input. A warm whole-snapshot cache entry
# from before this fix would keep replaying ``is_compiler_generated=None``
# on every declaration (silently masking the phantom-implicit-member L4
# link fix this same change makes) even after re-serializing under schema
# v27, since ``SCHEMA_VERSION`` gates the on-disk snapshot JSON format only,
# not this separate whole-process disk cache. Bumped so upgrading forces
# re-extraction instead of silently replaying the stale, unstamped facts.


def _get_cache_dir() -> Path:
    """Return the cache directory, deferring Path.home() to call time."""
    xdg = os.environ.get("XDG_CACHE_HOME", "")
    if xdg:
        base = Path(xdg)
    else:
        try:
            base = Path.home() / ".cache"
        except RuntimeError:
            base = Path(tempfile.gettempdir())
    return base / "abi_check" / "snapshots"


# Module-level reference (can be monkeypatched in tests).
_CACHE_DIR: Path = _get_cache_dir()


def _hash_include_dir_headers(h: hashlib._Hash, inc: Path) -> None:
    """Fold the (relative path, mtime) of every header-like file under
    ``inc`` into ``h``, so an edit to a header reached only transitively
    through an ``-I``/``--include`` directory (never itself passed as an
    explicit ``headers`` entry) still invalidates the whole-snapshot cache.

    Reuses :func:`abicheck.header_utils.iter_cache_header_files` (the same
    ``CACHE_HEADER_SUFFIXES`` set ``dumper._cache_key``'s own AST-level cache
    already walks) rather than a second, independently-maintained suffix
    list -- an earlier ad hoc set here was missing ``.tpp``/``.inc`` (Codex
    review), which the shared set already accounted for.

    Best-effort and bounded by whatever is actually on disk under ``inc`` --
    a missing/unreadable directory degrades to hashing nothing extra (same
    as before this function existed) rather than raising, matching this
    module's existing "any read problem is cache-safe, never a crash"
    stance (see ``lookup``/``store``).
    """
    from .header_utils import iter_cache_header_files

    try:
        entries = iter_cache_header_files(inc)
    except OSError:
        return
    for p in entries:
        try:
            h.update(str(p.relative_to(inc)).encode())
            h.update(str(p.stat().st_mtime_ns).encode())
        except OSError:
            h.update(b"MISSING")


def _cache_key(
    binary_path: Path,
    headers: list[Path],
    includes: list[Path],
    version: str,
    lang: str,
    *,
    extra: str = "",
) -> str:
    """Compute a deterministic cache key from all inputs that affect the snapshot.

    ``extra`` is an opaque, caller-assembled string folding in any additional
    inputs that affect the resulting snapshot but aren't one of this
    function's named parameters (e.g. the binary format, header-AST backend,
    or public-header scoping set) — kept generic here so this module doesn't
    need to know every option a caller's dump pipeline exposes.
    """
    h = hashlib.sha256()
    # Binary content hash — chunked to avoid loading huge files into memory
    try:
        with open(binary_path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
    except OSError:
        return ""  # uncacheable
    # Hash contents, not only mtimes: restored timestamps must not resurrect a
    # stale snapshot. Directory-valued ``-H`` inputs and explicit include roots
    # contribute every header-like descendant reachable by the parser.
    hash_files: set[Path] = set()
    # ADR-050 D1/D2: *not* sorted — headers/includes order is real,
    # load-bearing search-precedence/preprocessing order (the same "order is
    # a real compile difference" rule comparability.py's profile_fingerprint
    # already enforces), not cosmetic. Two dumps requesting the same
    # headers/includes in a different order can legitimately resolve to a
    # different snapshot (e.g. -I a -I b vs -I b -I a with a same-named
    # header in both, or a macro one header defines before another is
    # included) — sorting here would let a warm cache silently serve the
    # wrong order's snapshot. `hash_files` (the transitively-discovered
    # descendants of a directory input) stays a sorted set below: those are
    # an unordered content aggregate, not a caller-ordered sequence.
    for hdr in headers:
        h.update(str(hdr).encode())
        try:
            if hdr.is_dir():
                hash_files.update(iter_cache_header_files(hdr))
            else:
                hash_files.add(hdr)
        except OSError:
            h.update(b"UNREADABLE_HEADER_INPUT")
    for inc in includes:
        h.update(str(inc).encode())
        try:
            hash_files.update(iter_cache_header_files(inc))
        except OSError:
            h.update(b"UNREADABLE_INCLUDE_DIR")
    for hdr in sorted(hash_files):
        h.update(str(hdr).encode())
        try:
            fd = os.open(hdr, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
            file_stat = os.fstat(fd)
            if not stat.S_ISREG(file_stat.st_mode):
                os.close(fd)
                h.update(b"NONREGULAR")
                continue
            # Preserve the existing mtime invalidation contract as well as
            # hashing contents. The latter catches restored/coarse timestamps;
            # the former also treats a touched header as a changed input.
            h.update(str(file_stat.st_mtime_ns).encode())
            with os.fdopen(fd, "rb") as f:
                for chunk in iter(lambda: f.read(65536), b""):
                    h.update(chunk)
        except OSError:
            h.update(b"MISSING")
    # Compiler params
    h.update(version.encode())
    h.update(lang.encode())
    h.update(extra.encode())
    h.update(_SNAPSHOT_CACHE_VERSION.encode())
    return h.hexdigest()


def lookup(
    binary_path: Path,
    headers: list[Path],
    includes: list[Path],
    version: str,
    lang: str,
    *,
    extra: str = "",
) -> AbiSnapshot | None:
    """Look up a cached snapshot. Returns None on miss."""
    key = _cache_key(binary_path, headers, includes, version, lang, extra=extra)
    return lookup_key(key, binary_path)


def lookup_key(key: str, binary_path: Path) -> AbiSnapshot | None:
    """Look up an entry using a key already bound to validated inputs.

    ADR-059: new entries are written zstd-compressed (``<key>.json.zst``);
    this checks that first, then falls back to a legacy plain ``<key>.json``
    entry so a cache warmed by an older abicheck is not thrown away wholesale
    on upgrade. A corrupt/truncated compressed entry is a cache miss, same as
    any other read problem here -- never a caller-visible failure."""
    if not key:
        return None
    from .serialization import load_snapshot

    for cache_file in (_CACHE_DIR / f"{key}.json.zst", _CACHE_DIR / f"{key}.json"):
        if not cache_file.exists():
            continue
        try:
            snap = load_snapshot(cache_file)
        except Exception:
            _logger.debug("Cache read error for %s, treating as miss", key[:12])
            continue
        # Touch mtime for LRU
        try:
            cache_file.touch()
        except OSError:
            pass
        _logger.debug("Cache hit: %s → %s", binary_path.name, key[:12])
        return snap
    return None


def store(
    snap: AbiSnapshot,
    binary_path: Path,
    headers: list[Path],
    includes: list[Path],
    version: str,
    lang: str,
    *,
    extra: str = "",
) -> None:
    """Store a snapshot in the cache (atomic write via rename)."""
    key = _cache_key(binary_path, headers, includes, version, lang, extra=extra)
    store_key(snap, key, binary_path)


def store_key(snap: AbiSnapshot, key: str, binary_path: Path) -> None:
    """Store under a previously computed, post-execution-validated key.

    ADR-059: written zstd-compressed at the fast, cache-tuned level (this
    runs on nearly every ``dump``/``compare`` invocation, unlike a baseline/
    release write) via the canonical atomic writer -- a plain-JSON entry
    from before this cache was compression-aware is never written again,
    only read as a fallback by :func:`lookup_key`. If a stale legacy
    ``<key>.json`` for the same key exists, it's removed so a lookup doesn't
    keep preferring an old snapshot over a freshly stored one."""
    if not key:
        return
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file = _CACHE_DIR / f"{key}.json.zst"
        from .serialization import write_snapshot
        from .snapshot_io import ZSTD_LEVEL_CACHE, SnapshotCompression

        write_snapshot(
            snap,
            cache_file,
            compression=SnapshotCompression.ZSTD.value,
            zstd_level=ZSTD_LEVEL_CACHE,
        )
        legacy_file = _CACHE_DIR / f"{key}.json"
        if legacy_file.exists():
            try:
                legacy_file.unlink()
            except OSError:
                pass
        _logger.debug("Cache store: %s → %s", binary_path.name, key[:12])
        _evict_if_needed()
    except Exception as exc:
        # Caching is a pure optimization layered on top of a real dump that
        # already succeeded — any failure here (disk full, an unserializable
        # field, ...) must never surface as a caller-visible error. Broad
        # except is deliberate (mirrors lookup()'s "any read problem is a
        # miss" stance): a write-time TypeError from an unusual snapshot is
        # exactly as harmless to swallow as an OSError.
        _logger.debug("Cache write failed: %s", exc)


def _safe_mtime(p: Path) -> float:
    """Return file mtime, or 0.0 if stat fails (e.g. concurrent deletion)."""
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0


def _evict_if_needed() -> None:
    """Remove oldest entries if cache exceeds MAX_ENTRIES.

    Globs both the current ``*.json.zst`` suffix and the legacy plain
    ``*.json`` suffix (ADR-059) so LRU eviction accounts for a cache
    directory that still has pre-upgrade entries mixed in with new ones."""
    try:
        entries = sorted(
            (*_CACHE_DIR.glob("*.json.zst"), *_CACHE_DIR.glob("*.json")),
            key=_safe_mtime,
        )
    except OSError:
        return
    excess = len(entries) - MAX_ENTRIES
    if excess <= 0:
        return
    for p in entries[:excess]:
        try:
            p.unlink()
            _logger.debug("Cache evict: %s", p.name[:12])
        except OSError:
            pass
