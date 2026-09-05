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

"""Pure path and compiler-flag helpers for header (``-H``) inputs.

A leaf module (stdlib-only) so every caller can share the include-root
derivation without an import cycle — originally ``service._dump_elf`` and the
since-retired ``cli_dump_helpers.perform_elf_dump``, across the ``cli`` →
``cli_dump_helpers`` → ``service`` → … → ``cli`` cycle (ADR-063 Track 1).

Being that leaf is also why this module, rather than ``buildsource``, owns the
compiler-dialect and include-flag vocabulary the whole codebase reads
(:func:`is_msvc_driver_stem`, :data:`_INCLUDE_FLAG_PREFIXES`, and the
forced-include recognizer below): every
consumer — the L2 header parse (``buildsource.header_compile_context``), the L4
replay command builder (``buildsource.source_extractors._argv``), the L2 seed
and the AST cache keys — already sits above it, so one shared implementation
costs no new import edge in any direction. Keep it stdlib-only: an upward
import from here would invert that.
"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence
from pathlib import Path

from ._compiler_options import split_gcc_options

#: Conventional include-root directory names. A ``-H`` umbrella that lives
#: *under* such a directory (e.g. ``include/oneapi/tbb.h``) writes its own
#: includes relative to that root (``#include "oneapi/tbb/..."``), so the root —
#: not the file's immediate parent — is what must be on the search path.
_INCLUDE_ROOT_NAMES = frozenset({"include", "inc"})

#: Recognised C/C++ header file suffixes for directory ``-H`` *expansion*
#: (``service_scan``). Deliberately conservative — these become standalone
#: translation units, so it must not sweep in files meant only to be ``#include``d
#: (``.inl``/``.tcc`` template bodies). Lives in this leaf module so the consumers
#: can share it without an import cycle.
HEADER_SUFFIXES = frozenset(
    {".h", ".hh", ".hpp", ".hxx", ".h++", ".ipp", ".tpp", ".inc"}
)

#: Header-like suffixes whose edits must invalidate the AST cache
#: (``dumper._cache_key``'s include-dir mtime walk). A **superset** of
#: :data:`HEADER_SUFFIXES`: cache invalidation has the opposite bias from
#: expansion — it must be *generous* (any file that can change the parsed AST),
#: so it also covers the ``.inl``/``.tcc`` template-implementation includes that
#: headers commonly pull in but that are never standalone TUs. Decoupling the two
#: lets the walk catch a ``.inl``/``.tcc`` edit without making ``-H <dir>`` try to
#: parse those files (#454).
CACHE_HEADER_SUFFIXES = HEADER_SUFFIXES | frozenset({".inl", ".tcc"})

#: Compiler flags that contribute an include *search directory*. Their presence
#: in the pass-through compile context means a real build supplied its own
#: include tree, which an inferred ``-H`` root must defer to. Both GNU/clang
#: (``-I``/``-isystem``/…) and MSVC/clang-cl (``/I``/``/external:I``/``/imsvc``)
#: spellings are recognised so an MSVC build context (``--compiler cl.exe`` with
#: ``/I`` options) is not mistaken for "no build context" (Codex review).
#: Distinct, case-sensitive prefixes (``-I`` ≠ ``-isystem``/``-iquote``);
#: ``startswith`` covers both spaced (``-I dir``) and attached (``-Idir``) forms.
#:
#: Known gap (CodeRabbit review, PR #782): every match against this tuple
#: (here and in every consumer below -- :func:`_has_include_build_context`,
#: :func:`_build_context_include_dirs`, :func:`_flag_tokens`,
#: :func:`_msvc_deferred_flag`, :func:`include_operand_dirs`, and
#: ``buildsource.l2_seed._split_include_tokens``) is exactly case-sensitive.
#: That is correct for the GNU/clang spellings (a real compiler only ever
#: accepts the documented lowercase ``-I``/``-isystem``/…), but wrong for the
#: two clang-cl-only entries: unlike native ``cl.exe`` (case-sensitive, and
#: does not recognise ``/imsvc`` at all -- that spelling is clang-cl's own),
#: clang-cl's own driver option parsing is documented case-insensitive, so
#: ``/IMsvc``/``/EXTERNAL:I`` are legal, real spellings this tuple's exact-case
#: ``startswith`` silently fails to recognise as include-search flags at all.
#: Concretely: a real clang-cl build record using ``/IMsvc`` would have that
#: directory (a) not suppress the L2 include-dir seed
#: (:func:`_has_include_build_context`), (b) not be resolved by
#: :func:`_build_context_include_dirs`'s existing-dir dedup, and (c) --
#: this PR's own new consumers -- not be hashed into the AST cache key's
#: ``extra_hash_dirs`` (:func:`include_operand_dirs`) and not be carved out
#: of the leading last-flag-wins token group by ``_split_include_tokens``,
#: so an explicit ``/IMsvc`` override could still lose to a derived ``-I``
#: for a colliding header. Not fixed here: a correct fix needs case-
#: insensitive matching applied *consistently* to every consumer above --
#: several of which (all but :func:`include_operand_dirs`) predate this PR
#: entirely (present at commit ``dc09aec``, this PR's own base) -- plus a
#: longest-prefix-first tie-break, since case-folding introduces a genuine
#: new ambiguity a case-sensitive scan never had: ``/imsvc`` case-
#: insensitively also matches the shorter ``/I`` prefix, and picking the
#: wrong one changes which include bucket the directory is treated as
#: (see :func:`_msvc_deferred_flag`'s own bucket-priority docstring). Fixing
#: only the two consumers this PR added (:func:`include_operand_dirs` /
#: ``_split_include_tokens``) while leaving the pre-existing consumers
#: case-sensitive would be strictly worse than today's uniform gap -- it
#: would make the seed-suppression and cache-hashing/ordering logic
#: silently *disagree* about whether a given ``/IMsvc`` token is an
#: include-search flag at all. Left as a known gap (see ``AGENTS.md``).
_INCLUDE_FLAG_PREFIXES = (
    "-I",
    # Listed ahead of the plain "-isystem" it textually extends: every match
    # here is a first-match-wins scan (`next(p for p in _INCLUDE_FLAG_
    # PREFIXES if t.startswith(p))`), and "-isystem-after" is Clang's own,
    # genuinely distinct flag (`-isystem-after <dir>` -- confirmed against
    # real `clang --help-hidden`, always spaced, never an attached form) --
    # not a longer spelling of "-isystem" itself. Ordered after "-I" a real
    # match would still find first-match-wins if it were left later: a real
    # "-isystem-after" token also `startswith("-isystem")`, so scanning
    # "-isystem" first would misparse it as an *attached* "-isystem" flag
    # whose "directory" is the bogus suffix "-after", stranding the real
    # directory operand as a bare, unconsumed token (Codex review, PR #802 --
    # confirmed with the reviewer's own repro: `gcc_options='-isystem-after
    # /a'` alongside `gcc_option_tokens=('-isystem-after', '/b')` left a bare
    # `/b` in the composed command). Pre-existing in every consumer that
    # already did `t.startswith(p)` against this tuple before this PR
    # (`_has_include_build_context`, `include_operand_dirs`, ...), not
    # introduced by this PR's own two new functions -- fixed once here,
    # for the whole shared tuple, rather than only for the two consumers a
    # reviewer happened to be reading.
    "-isystem-after",
    "-isystem",
    "-iquote",
    "-idirafter",
    "-cxx-isystem",  # GNU / clang
    "/I",
    "/external:I",
    "/imsvc",  # MSVC / clang-cl
)


def iter_directory_headers(
    directory: Path, pruned_segments: frozenset[str] = frozenset()
) -> list[Path]:
    """Recognised header files under *directory*, never descending pruned dirs.

    Shared by both ``-H <dir>`` expanders — the ``scan``/service path
    (:func:`abicheck.service_scan.expand_header_inputs`) and the ``dump``/``compare``
    CLI path (``abicheck.cli_resolve._expand_header_inputs``) — so the two
    front-ends can never disagree on what counts as a header (they previously kept
    divergent suffix literals; one was missing ``.h++``). Filters by
    :data:`HEADER_SUFFIXES` (the conservative standalone-TU set).

    *pruned_segments* directory names are dropped from the walk **in place**, so
    the walk never descends into them (VCS metadata, abicheck's own in-tree cmake
    build dir) rather than stat-ing every entry only to discard it afterwards.
    Sorted for a deterministic surface.
    """
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(directory):
        dirnames[:] = [d for d in dirnames if d not in pruned_segments]
        base = Path(dirpath)
        for name in filenames:
            f = base / name
            if f.suffix.lower() in HEADER_SUFFIXES and f.is_file():
                found.append(f)
    return sorted(found)


def split_public_header_inputs(
    paths: Sequence[Path],
) -> tuple[list[Path], list[Path]]:
    """Partition ``-H``/``--header`` *paths* into (files, directories) for
    public-header provenance tagging.

    A ``-H``/``--header`` input may name an individual header file or a
    whole directory (documented as "Public header file or directory"), and
    both ``compare``'s single-pair path (``cli_resolve._resolve_compare_
    snapshots``) and ``compare-release``'s package path
    (``service.run_compare_request``) reuse that same list, unsplit, as the
    public-header provenance set (``resolve_input``'s ``public_headers``
    argument). ``comparability.compute_extraction_contract`` fingerprints
    every ``public_headers`` entry as an individual *file* identity — a
    directory entry's own parent sits one level above the intended
    normalization root, pulling ``scope_fingerprint``'s common-root
    computation up with it and leaking that directory's name (often
    per-run-random, e.g. a tempfile-extracted devel package) into every
    sibling header's identity. Two extractions of byte-identical headers
    then spuriously fingerprint as a scope mismatch (``ScopeMismatchError``)
    even though nothing about the declared surface differs.

    Routing directory entries through ``public_header_dirs`` instead avoids
    this: that field already collapses a lone directory to a constant token
    before hashing (comparability.py's single-entry collapse), exactly the
    "declare everything under this directory public" semantics a ``-H
    <dir>`` umbrella has.
    """
    files: list[Path] = []
    dirs: list[Path] = []
    for p in paths:
        (dirs if p.is_dir() else files).append(p)
    return files, dirs


def _implicit_header_includes(headers: list[Path]) -> list[Path]:
    """Include directories implied by the ``-H`` inputs themselves.

    A ``-H`` *directory* is its own include root; a ``-H`` *file* contributes
    its parent directory **plus** any ancestor conventionally named ``include``/
    ``inc``. Adding these to the compiler search path lets quote/angle includes
    written relative to the public-header root resolve without a separate ``-I``
    (the abicheck P3 finding) — both the umbrella-at-root case (oneDNN's
    ``include/dnnl.hpp``) and the nested-umbrella case (oneTBB's
    ``include/oneapi/tbb.h`` doing ``#include "oneapi/tbb/blocked_range.h"``).
    Returns existing directories, de-duplicated in discovery order; the user's
    ``-I``/``--include`` entries still take precedence (they are listed first).
    """
    dirs: list[Path] = []
    seen: set[str] = set()

    def _add(d: Path) -> None:
        if not d.is_dir():
            return
        key = str(d.resolve())
        if key not in seen:
            seen.add(key)
            dirs.append(d)

    for h in headers:
        # A directory is its own root; a file contributes its parent. Either way
        # also walk up to any conventional include root — a `-H include/oneapi`
        # (dir) or `-H include/oneapi/tbb.h` (file) still writes includes
        # relative to `include/`, so that root must be on the path too.
        _add(h if h.is_dir() else h.parent)
        for ancestor in h.parents:
            if ancestor.name.lower() in _INCLUDE_ROOT_NAMES:
                _add(ancestor)
    return dirs


def _context_tokens(
    gcc_options: str | None, gcc_option_tokens: Sequence[str]
) -> list[str]:
    """The pass-through compile flags as a flat token list (string + tokens)."""
    toks: list[str] = list(gcc_option_tokens)
    if gcc_options:
        try:
            toks += split_gcc_options(gcc_options)
        except ValueError:
            toks += gcc_options.split()
    return toks


def _has_include_build_context(toks: list[str]) -> bool:
    """True when the compile-flag *tokens* supply their own include search dirs.

    Detects any include-search flag — GNU/clang
    ``-I``/``-isystem``/``-iquote``/``-idirafter``/``-cxx-isystem`` or MSVC/clang-cl
    ``/I``/``/external:I``/``/imsvc`` (attached or spaced). When present, a real
    build context is in play and an inferred ``-H`` root must defer to it; when
    absent, the inferred root can take ``-I`` priority. Compile-DB include dirs
    are folded into the user ``-I`` list upstream, so they need no detection here
    — an inferred ``-I`` appended after them is already lower priority. (*toks* is
    the pre-split flag list from :func:`_context_tokens`.)
    """
    return any(t.startswith(p) for t in toks for p in _INCLUDE_FLAG_PREFIXES)


def build_context_include_dirs_ordered(
    toks: list[str],
    *,
    base_dir: str | None = None,
    expand_user: bool = False,
    prefixes: tuple[str, ...] | None = None,
) -> list[str]:
    """:func:`_build_context_include_dirs`, in **argv order** and de-duplicated.

    The set-returning function below is this one, collapsed — it is the older
    of the two and every pre-existing caller only ever asks "does the build
    context already cover this directory", for which order is irrelevant.

    Order is not irrelevant to a caller *resolving a file* through the chain,
    which is why this variant exists (Codex review, PR D, sixth round): a
    compiler searches a bucket's directories in the order they appear on the
    command line and takes the first match, so ``-iquote z -iquote a`` finds
    ``z/config.h``. Sorting the set — what
    ``header_compile_context._forced_include_search_dirs`` originally did to
    get a deterministic chain — is deterministic *and wrong*: it would pin the
    derived parse to ``a/config.h``, a different file with potentially
    different macros. First occurrence wins on duplicates, matching the same
    first-match rule.

    Parses every include-search flag (spaced ``-I dir`` / ``-isystem dir`` and
    attached ``-Idir`` forms, GNU and MSVC) out of *toks* and returns their
    resolved absolute paths. Used to skip an inferred ``-H`` root the build
    context already covers: re-adding such a root as ``-isystem`` would trip GCC's
    rule that a directory given with *both* ``-I`` and ``-isystem`` has its ``-I``
    ignored — demoting the build's own ``-I`` to the system position and changing
    search order (Codex review).

    ``base_dir`` is the directory a *relative* operand is resolved against — pass a
    compile unit's ``directory`` so a compile-DB flag like ``-iquote ../deps`` (run
    with ``cwd=directory``) resolves the way the build does, not against the
    abicheck process cwd (Codex review). ``expand_user`` un-redacts a leading ``~``
    (compile-DB paths are stored home-relative via ``DEFAULT_REDACTION``) before
    resolving. ``prefixes`` restricts which include-flag prefixes are parsed (default
    all of :data:`_INCLUDE_FLAG_PREFIXES`) — a caller that will re-emit the dirs as
    plain ``-I`` should pass only the *normal-priority* buckets (``-I``/``-iquote``/
    ``/I``) so it does not promote an *after-system* dir (``-idirafter``) or a
    system dir (``-isystem``/``-imsvc``) to ``-I`` and shadow a system header (Codex
    review). All keyword-only and defaulted so existing callers are unchanged.
    """
    flag_prefixes = prefixes if prefixes is not None else _INCLUDE_FLAG_PREFIXES
    base = os.path.expanduser(base_dir) if (base_dir and expand_user) else base_dir

    def _resolve(operand: str) -> str:
        p = os.path.expanduser(operand) if expand_user else operand
        pp = Path(p)
        if base and not pp.is_absolute():
            pp = Path(base) / pp
        return str(pp.resolve())

    dirs: list[str] = []
    seen: set[str] = set()

    def _add(operand: str) -> None:
        resolved = _resolve(operand)
        if resolved not in seen:
            seen.add(resolved)
            dirs.append(resolved)

    i = 0
    while i < len(toks):
        t = toks[i]
        prefix = next((p for p in flag_prefixes if t.startswith(p)), None)
        if prefix is None:
            i += 1
            continue
        if t == prefix:  # spaced form: the directory is the next token
            if i + 1 < len(toks):
                _add(toks[i + 1])
            i += 2
            continue
        # Attached form ("-Idir" / "/Idir"): t is strictly longer than the prefix
        # here (the exact-match spaced form was handled above), so the operand is
        # always non-empty.
        _add(t[len(prefix) :])
        i += 1
    return dirs


def _build_context_include_dirs(
    toks: list[str],
    *,
    base_dir: str | None = None,
    expand_user: bool = False,
    prefixes: tuple[str, ...] | None = None,
) -> set[str]:
    """Resolved include directories the compile-flag *tokens* already search.

    Order-free view of :func:`build_context_include_dirs_ordered` above, which
    carries the full contract; every caller of this one is asking only whether
    a directory is covered at all.
    """
    return set(
        build_context_include_dirs_ordered(
            toks, base_dir=base_dir, expand_user=expand_user, prefixes=prefixes
        )
    )


def resolve_inferred_header_roots(
    headers: list[Path],
    user_includes: list[Path],
    *,
    gcc_options: str | None = None,
    gcc_option_tokens: Sequence[str] = (),
) -> tuple[list[Path], list[str]]:
    """Split the inferred ``-H`` include roots by how they should be searched.

    Returns ``(extra_includes, deferred_tokens)`` — exactly one is non-empty.
    The inferred roots (de-duplicated against the user's ``-I``) are emitted as:

    * plain ``-I`` (returned as extra-include :class:`Path`\\ s) when there is
      **no** build context to defer to — so they outrank the standard system
      dirs and an umbrella that includes a system-colliding name (``<endian.h>``)
      still resolves the package header rather than the system one;
    * a *deferred* token otherwise — emitted **after** the build context's flags
      and in the bucket that keeps it below every build-context include dir (see
      :func:`_deferred_include_flag`): ``-isystem`` below an above-system build
      context (``-I``/``-isystem``/…, still above the standard system dirs so the
      ``<endian.h>`` case resolves the package header), ``-idirafter`` below an
      ``-idirafter``-only build context, or — for an MSVC/clang-cl context — the
      build's own lowest include bucket (``/external:I`` / ``/imsvc`` / ``/I``)
      so the root never shadows the build's system dirs (#454).

    Shared by the ELF parse (``service_dump_native._dump_elf``, which ``dump``
    reaches directly now ``perform_elf_dump`` retired) and every other path.
    """
    # Tokenize the pass-through flags once, then reuse for every check below.
    ctx = _context_tokens(gcc_options, gcc_option_tokens)
    # Skip roots the user's -I *or* the build context already searches: re-adding
    # one the build supplies as -I would, when emitted as -isystem, void that -I
    # (GCC ignores -I for a dir also given via -isystem) and reorder the search.
    skip = {str(i.resolve()) for i in user_includes}
    skip |= _build_context_include_dirs(ctx)
    inferred = [
        d for d in _implicit_header_includes(headers) if str(d.resolve()) not in skip
    ]
    if not inferred:
        return [], []
    if _has_include_build_context(ctx):
        out: list[str] = []
        flag = _deferred_include_flag(ctx)
        for d in inferred:
            out += [flag, str(d)]
        return [], out
    return inferred, []


def _msvc_style_context(toks: list[str]) -> bool:
    """True when the compile-flag *tokens* use MSVC/clang-cl include spellings.

    Distinguishes ``/I``/``/external:I``/``/imsvc`` from the GNU forms so the
    deferred inferred root is emitted in the same dialect — a GNU ``-isystem``
    is silently ignored by ``cl.exe``/``clang-cl`` (Codex review). Operands of
    spaced include flags are stripped first (:func:`_flag_tokens`) so a GNU
    context whose directory merely *starts with* a slash spelling
    (``-I /imsvc-sdk``) is not misread as MSVC and routed to the wrong dialect
    (CodeRabbit review).
    """
    msvc = ("/I", "/external:I", "/imsvc")
    return any(t.startswith(p) for t in _flag_tokens(toks) for p in msvc)


#: GNU/clang include classes searched *before* the standard system dirs. An
#: inferred root deferred below these stays above the system dirs (so a
#: system-colliding basename still resolves the package header). ``-idirafter``
#: is deliberately absent — it is searched *after* the system dirs.
_ABOVE_SYSTEM_GNU_PREFIXES = ("-I", "-iquote", "-isystem", "-cxx-isystem")

#: MSVC/clang-cl *system*-include buckets, searched after the plain ``/I``
#: directories but still above the standard ``INCLUDE`` dirs. Listed
#: **lowest-search-priority first** — ``clang-cl`` searches ``/imsvc`` dirs (added
#: "as if in ``%INCLUDE%``") *after* ``/external:I`` dirs, so ``/imsvc`` is the
#: lower bucket. :func:`_msvc_deferred_flag` returns the first present here so a
#: deferred root lands in the build's lowest bucket and can never shadow it.
_MSVC_SYSTEM_BUCKETS = ("/imsvc", "/external:I")


def _flag_tokens(toks: list[str]) -> list[str]:
    """*toks* with the operands of spaced include flags dropped.

    A spaced include flag (``-I dir`` / ``/imsvc dir`` / ``/external:I dir`` …,
    where the token equals the bare prefix) consumes the *next* token as its
    directory operand. That operand is a path, not a flag, so it must not be
    matched against flag spellings — otherwise a dir that merely *starts with* a
    bucket name (``/I /imsvc-sdk``) is misread as an ``/imsvc`` flag (CodeRabbit
    review). Returns only the genuine flag tokens. Attached forms (``-Idir`` /
    ``/external:Idir``) carry their own operand and stay; the bare-prefix
    (spaced) form is the only one whose successor is a separate operand.
    """
    out: list[str] = []
    i = 0
    n = len(toks)
    while i < n:
        t = toks[i]
        out.append(t)
        if t in _INCLUDE_FLAG_PREFIXES and i + 1 < n:
            i += 2  # skip the directory operand of a spaced include flag
        else:
            i += 1
    return out


#: GCC's plain (non-quote-form) ``-I-`` divider: a bare, operand-less flag,
#: never an attached-form ``-I<dir>`` whose directory happens to be ``"-"``.
#: It splits the ``-I`` search list in two with *different* semantics on
#: each side (GCC docs, "Directory Options") — a directory given before it
#: is searched only for a quoted (``#include "..."``) include and never for
#: an angle-bracket one, while a directory given after it is searched for
#: both, the same as an ordinary ``-I`` with no divider present at all.
#: Treating the two sides as one interchangeable ``-I`` class (as an
#: earlier version of :func:`_include_class_path_pairs` effectively did, by
#: never recognizing this token at all and misparsing it as an attached
#: ``-I`` flag naming the bogus directory ``"-"``) can drop a *different*
#: post-divider ``-I <dir>`` as a "duplicate" of an identical-looking
#: pre-divider one — turning a resolvable ``#include <x.h>`` into
#: "file not found" (Codex review, PR #802, confirmed against real GCC 13).
_DASH_I_DIVIDER = "-I-"


def _include_class_path_pairs(toks: Sequence[str]) -> set[tuple[str, str]]:
    """Every ``(flag-class, resolved-directory)`` pair present in *toks*.

    The flag *class* (the matched prefix itself, e.g. ``"-I"`` vs.
    ``"-isystem"``) is part of the key, not just the directory — GCC/Clang
    consult ``-iquote``/``-I``/``-isystem``/``-idirafter`` as **distinct
    search buckets in a fixed order regardless of argv position** (see
    :func:`_split_include_tokens`'s own documented gap on this exact point),
    so a directory present via one class is not interchangeable with the
    same directory present via another — dropping an ``-isystem <dir>``
    merely because an unrelated ``-I <dir>`` exists elsewhere would change
    which bucket that directory is searched from for a colliding basename
    (Codex review, PR #802).

    A plain ``-I`` entry is further split into a ``"-I:pre"``/``"-I:post"``
    sub-class depending on whether it appears before or after a
    :data:`_DASH_I_DIVIDER` token *within this same sequence* — see that
    constant's own docstring for why the two sides are not interchangeable
    either, even though both are nominally "the ``-I`` class". No divider
    present at all means every ``-I`` entry is ``"-I:pre"``, preserving the
    pre-existing behaviour for the (overwhelmingly common) undivided case.
    """
    pairs: set[tuple[str, str]] = set()
    i = 0
    n = len(toks)
    after_divider = False
    while i < n:
        t = toks[i]
        if t == _DASH_I_DIVIDER:
            after_divider = True
            i += 1
            continue
        matched_prefix = next(
            (p for p in _INCLUDE_FLAG_PREFIXES if t.startswith(p)), None
        )
        if matched_prefix is None:
            i += 1
            continue
        if t == matched_prefix and i + 1 < n:
            operand = toks[i + 1]
            consumed = 2
        else:
            operand = t[len(matched_prefix) :]
            consumed = 1
        try:
            key = str(Path(operand).resolve())
        except OSError:
            key = operand
        class_key = matched_prefix
        if matched_prefix == "-I":
            class_key = "-I:post" if after_divider else "-I:pre"
        pairs.add((class_key, key))
        i += consumed
    return pairs


def drop_include_tokens_duplicating_paths(
    toks: Sequence[str], already_covered: Sequence[str]
) -> list[str]:
    """*toks* with an include-search flag+operand pair dropped when the
    identical ``(flag-class, directory)`` pair already appears in
    *already_covered* (a raw token list, in the same shape as *toks*).

    ``dump``'s ELF and PE/Mach-O paths (:func:`abicheck.service_dump_native.
    _dump_elf`/:func:`abicheck.service_header_scoped._try_header_
    scoped_dump`) both render the L3->L2 fold's merged compile context into
    ``gcc_option_tokens`` via ``header_compile_context._context_flags`` —
    which independently renders the *same* matched compile unit's
    ``include_paths``/``system_include_paths`` as their own ``-I``/
    ``-isystem`` tokens that the L2 include-dir seed (``eff_includes``,
    rendered separately as ``extra_includes``) already covers. Both a
    command builder's ``extra_includes`` loop *and* its verbatim
    ``gcc_option_tokens`` append then emit an ``-I`` for the identical
    directory — and since ``dumper_contract._attach_extraction_contract``
    builds ``declared_includes`` **exclusively** from ``extra_includes``
    (never ``gcc_option_tokens``), the duplicate never doubles a
    ``declared_includes``/``include_sequence`` slot by itself, but a
    real compiler still processes the same ``-I`` twice, and (more load-
    bearing) a `scan --against` candidate resolution — whose single-pass
    fold (:func:`abicheck.buildsource.l2_seed.
    seed_includes_and_fold_compile_context`) never separately renders a
    matched unit's include dirs into both places — never emits this
    second copy for the identical project, so the two sides' full argv
    (and therefore, on backends that fold gcc_option_tokens into their own
    header-parse identity) can disagree for reasons no diagnostic names.
    Confirmed via a minimal, castxml-free repro reproducing this exact
    shape (AGENTS.md's L3->L2-fold "nineteenth finding").

    Applied to ``gcc_option_tokens`` only, against the already-emitted
    ``extra_includes`` — never the reverse, since ``extra_includes`` is
    :func:`declared_includes`'s one canonical source and must always keep
    every entry it's given. Dedup key is ``(flag-class, resolved absolute
    path)`` — see :func:`_include_class_path_pairs`'s own docstring for why
    the class must be part of the key, not just the directory: a real
    compiler treats ``-iquote``/``-I``/``-isystem``/``-idirafter`` as
    distinct search buckets regardless of argv order, so a same-directory
    entry in a *different* class is never dropped, only an exact class+path
    duplicate. Search-priority is unaffected for the entries that *are*
    dropped: it is always the later, redundant duplicate of an identical
    class+directory pair that goes, never the earlier one.

    A :data:`_DASH_I_DIVIDER` (``"-I-"``) token in *toks* is always kept
    verbatim — it is never itself a class+path pair to drop — and, per
    :func:`_include_class_path_pairs`'s own ``"-I:pre"``/``"-I:post"``
    split, flips which sub-class a subsequent plain ``-I`` in *toks*
    belongs to, so an ``-I <dir>`` after the divider is never dropped as a
    "duplicate" of a same-looking ``-I <dir>`` in *already_covered* (which,
    lacking its own divider, is entirely ``"-I:pre"``) — doing so would
    silently change which includes that directory is searched for.

    The divider state also carries **across** the *already_covered* ->
    *toks* boundary, not just within *toks* itself (Codex review, PR #802):
    every caller renders *already_covered* into the command *before* *toks*
    (``cmd += drop_include_tokens_duplicating_paths(gcc_option_tokens,
    cmd)``), so a ``"-I-"`` anywhere in *already_covered* means every plain
    ``-I`` in *toks* is *already* past the divider by the time the real
    compiler sees it — even one with no ``"-I-"`` of its own. Scanning
    *toks* from a fresh ``"-I:pre"`` state regardless of what
    *already_covered* ended in would misclassify such an entry as
    pre-divider, letting it be dropped as a false "duplicate" of an
    unrelated pre-divider ``-I <dir>`` in *already_covered* that is not
    actually the same search class once the real, composed argv is
    considered.
    """
    covered = _include_class_path_pairs(already_covered)
    out: list[str] = []
    i = 0
    n = len(toks)
    # A "-I-" anywhere in *already_covered* means *toks* -- rendered after
    # it in the real command -- starts life already past the divider.
    after_divider = _DASH_I_DIVIDER in already_covered
    while i < n:
        t = toks[i]
        if t == _DASH_I_DIVIDER:
            after_divider = True
            out.append(t)
            i += 1
            continue
        matched_prefix = next(
            (p for p in _INCLUDE_FLAG_PREFIXES if t.startswith(p)), None
        )
        if matched_prefix is None:
            out.append(t)
            i += 1
            continue
        if t == matched_prefix and i + 1 < n:
            operand = toks[i + 1]
            consumed = 2
        else:
            operand = t[len(matched_prefix) :]
            consumed = 1
        try:
            key = str(Path(operand).resolve())
        except OSError:
            key = operand
        class_key = matched_prefix
        if matched_prefix == "-I":
            class_key = "-I:post" if after_divider else "-I:pre"
        if (class_key, key) in covered:
            i += consumed
            continue
        out.extend(toks[i : i + consumed])
        i += consumed
    return out


def _msvc_deferred_flag(toks: list[str]) -> str:
    """The MSVC/clang-cl bucket to defer an inferred root below *toks* (#454).

    Mirrors the build context's own *lowest* include bucket so the deferred
    root can never shadow it: if the context uses a system bucket
    (``/external:I``/``/imsvc``), emit in the *lowest-searched* one present — a
    plain ``/I`` root is searched *before* those system dirs and could shadow
    them, and even an ``/external:I`` root is searched before the build's
    ``/imsvc`` (``%INCLUDE%``-style) dirs (Codex review). Mirroring a bucket the
    context actually used is frontend-agnostic: the frontend that will run must
    already understand that spelling (it consumed the same flag on input) — in
    particular a context that uses ``/imsvc`` is necessarily ``clang-cl``, since
    ``cl.exe`` rejects ``/imsvc`` — which sidesteps threading the
    ``cl.exe``-vs-``clang-cl`` identity into this pure helper. Falls back to
    ``/I`` for a plain ``/I``-only context — there is no system bucket to shadow,
    so command-line order (after the build's own ``/I`` dirs) suffices.
    """
    flags = _flag_tokens(toks)
    for bucket in _MSVC_SYSTEM_BUCKETS:
        if any(t.startswith(bucket) for t in flags):
            return bucket
    return "/I"


def _deferred_include_flag(toks: list[str]) -> str:
    """The flag to defer an inferred ``-H`` root below the build-context *toks*.

    The root must search *after* every build-context include dir; the bucket
    that achieves that depends on the build context's own flags:

    * MSVC/clang-cl (``/I``/``/external:I``/``/imsvc``) → the build's lowest
      bucket via :func:`_msvc_deferred_flag` (a system-bucket context keeps the
      root from shadowing ``/external:I``/``/imsvc`` dirs; a GNU ``-isystem`` is
      silently ignored by ``cl.exe``/``clang-cl`` either way);
    * any *above-system* GNU class (``-I``/``-iquote``/``-isystem``/
      ``-cxx-isystem``) → ``-isystem`` (searched after those, still above the
      standard system dirs so a system-colliding basename resolves the package
      header);
    * otherwise the build context is ``-idirafter``-only (a *below-system*
      class) → ``-idirafter`` (after the build's own ``-idirafter`` dirs, in the
      same class, so the build's fallback keeps priority — Codex review).

    Known limitation (#454 item 2): a *mixed* GNU context (``-I build/primary
    -idirafter build/generated``) is unsatisfiable with a single flag — the
    root would need to sit both above the standard system dirs (to keep
    winning a system-colliding basename against the ``-I`` context) and below
    ``-idirafter`` (which is searched after the system dirs). ``-isystem`` is
    the deliberate default: it favors the common collision case, and compile
    DBs essentially never emit ``-idirafter``.
    """
    if _msvc_style_context(toks):
        return _msvc_deferred_flag(toks)
    if any(t.startswith(p) for t in toks for p in _ABOVE_SYSTEM_GNU_PREFIXES):
        return "-isystem"
    return "-idirafter"


def dedup_paths_preserve_order(paths: Sequence[Path]) -> list[Path]:
    """*paths* with exact duplicates dropped, first-occurrence order kept.

    ``dump``'s ELF and PE/Mach-O paths both compose their final
    ``extra_includes``/``declared_includes`` list as an L2-seeded include
    list *plus* :func:`resolve_inferred_header_roots`'s own inferred-root
    additions (``eff_includes + inc_extra``) — two independently derived
    lists that can resolve to the *same* directory (e.g. a matched compile
    unit's own ``-I`` dir, once from the L3->L2 fold's seed and again from
    the inferred-root resolver's own, separate lookup over the *pre-fold*
    tokens). Left undeduped, that directory gets two ``declared_includes``
    slots for one real include root, and
    ``comparability_fields._include_slot_tokens`` tokenizes one slot per
    entry — so the resulting ``include_sequence`` carries an extra token a
    `scan --against` candidate resolution (which folds the seed and the L3
    context in one pass, with no separate ``inc_extra`` add — see
    ``scan_engine._build_new_snapshot``) never produces for the identical
    project, and the two sides spuriously fail ``profile_fingerprint``
    comparability on ``include_sequence`` alone (AGENTS.md's "nineteenth
    finding" on the L3->L2-fold known gap, candidate mechanism (b) —
    confirmed via a minimal, castxml-free repro: two independent
    ``dump`` calls of the same project agree bit-for-bit, but a `dump`
    baseline's own duplicated ``-I`` disagrees with `scan`'s deduped
    candidate).

    Dedup key is the resolved absolute path (mirroring
    :func:`_implicit_header_includes`'s own ``_add`` helper above) so a
    relative and an absolute spelling of the same directory collapse too,
    not just two textually-identical entries. Order is preserved rather
    than sorted: an include-search directory is first-match-wins, so
    reordering could change which same-named header a real compile picks
    up (the same reasoning ``build_context_include_dirs_ordered`` already
    documents for its own dedup).
    """
    seen: set[str] = set()
    out: list[Path] = []
    for p in paths:
        try:
            key = str(p.resolve())
        except OSError:
            key = str(p)
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def deferred_token_dirs(deferred_tokens: Sequence[str]) -> list[Path]:
    """The directories carried by the ``<flag> <dir>`` deferred token pairs.

    The deferred inferred roots ride in ``gcc_option_tokens`` (not
    ``extra_includes``), so the header-AST cache key — which mtime-scans only
    ``extra_includes`` dirs — would miss edits to their transitively-included
    headers and reuse a stale AST (Codex review). Callers pass these dirs to the
    dumper as hash-only inputs. Pairs the flat ``[flag, dir, …]`` list (the flag
    is ``-isystem``/``-idirafter`` for GNU contexts, ``/I``/``/external:I``/
    ``/imsvc`` for MSVC ones — every pair is two tokens regardless).
    """
    return [Path(d) for _flag, d in zip(deferred_tokens[::2], deferred_tokens[1::2])]


def include_operand_dirs(tokens: Sequence[str]) -> tuple[Path, ...]:
    """The directory operand of every include-search token in *tokens*.

    Unlike :func:`deferred_token_dirs` above (a flat ``[flag, dir, …]`` list
    the caller already knows is all include-search pairs), this scans an
    arbitrary ``gcc_option_tokens`` sequence for *any* include-search entry
    -- spaced (``-I dir``) or attached (``-Idir``) -- and returns real
    directory :class:`Path` objects for each, for a caller's AST cache key's
    ``extra_hash_dirs`` channel to stat (Codex review): an include-search
    directory that reaches the header parse only as an opaque
    ``gcc_option_tokens`` string is otherwise invisible to the cache key's
    own directory-mtime hashing (which only inspects ``extra_includes``/
    ``extra_hash_dirs``), so editing a header under it would reuse a stale
    cached AST. Originally ``buildsource.l2_seed._include_operand_dirs``
    (for the P0.3 L3->L2 fold's own derived dirs specifically); moved here,
    a leaf module both ``l2_seed.py`` and ``service.py`` already import
    from, once ``service._attach_header_graph``'s own independent second
    ``_clang_header_dump`` pass needed the identical extraction for the
    *same* ``gcc_option_tokens`` the fold merges into (Codex review) --
    this closes the gap generically for any include-search token the merged
    context carries, not just an L3-derived one.
    """
    dirs: list[Path] = []
    i = 0
    n = len(tokens)
    while i < n:
        t = tokens[i]
        if t in _INCLUDE_FLAG_PREFIXES and i + 1 < n:
            dirs.append(Path(tokens[i + 1]))
            i += 2
            continue
        matched_prefix = next(
            (p for p in _INCLUDE_FLAG_PREFIXES if t.startswith(p) and t != p), None
        )
        if matched_prefix is not None:
            dirs.append(Path(t[len(matched_prefix) :]))
        i += 1
    return tuple(dirs)


#: Compiler basenames that mean a command line is in MSVC/clang-cl (``/opt``)
#: mode. ``dpcpp-cl``/``dpcpp-cl.exe`` is Intel's oneAPI CL-compatible driver —
#: the same convention as ``clang-cl``, just Intel-branded.
MSVC_DRIVER_BINARIES: frozenset[str] = frozenset(
    {"cl", "cl.exe", "clang-cl", "clang-cl.exe", "dpcpp-cl", "dpcpp-cl.exe"}
)
#: The same names with any ``.exe`` suffix stripped, for matching after a
#: version suffix has also been removed.
_MSVC_DRIVER_STEMS: frozenset[str] = frozenset(
    name.removesuffix(".exe") for name in MSVC_DRIVER_BINARIES
)
#: Matches a trailing numeric version suffix LLVM/Debian packaging commonly
#: appends to an unversioned driver name (``clang-cl-20``, ``clang-20.1``).
#: Without stripping it, a real CL-mode driver is missed just because it
#: carries its LLVM major-version suffix.
_DRIVER_VERSION_SUFFIX_RE = re.compile(r"-\d+(?:\.\d+)*$")


def is_msvc_driver_stem(stem: str) -> bool:
    """True when *stem* — an already-lowercased driver **basename** — is CL-mode.

    The single vocabulary behind both dialect decisions this codebase makes,
    which had drifted apart: ``source_extractors._argv.is_msvc_mode`` (the L4
    replay path) recognized ``dpcpp-cl`` and version-suffixed drivers, while
    ``buildsource.adapters.base._is_msvc_command`` (which decides the dialect
    for source detection, forced-language detection, structured-field masking
    and — since PR D — the L2 forced-include render) listed only ``cl`` and
    unversioned ``clang-cl``. A ``dpcpp-cl``/``clang-cl-20`` command spelled
    with GNU ``-c`` rather than ``/c`` therefore read as GNU dialect on the
    build-evidence side, silently dropping its ``/FI`` forced include from the
    derived L2 context while L4 replayed it correctly (Codex review).

    Takes the basename rather than computing one, because the two callers
    disagree — deliberately — about how to derive it: the adapter is
    backslash-aware (a Windows driver path recorded in a compile database read
    on POSIX), ``_argv`` uses ``os.path.basename``. Sharing only the name test
    fixes the vocabulary drift without changing either caller's path handling.
    """
    if stem in MSVC_DRIVER_BINARIES:
        return True
    return _DRIVER_VERSION_SUFFIX_RE.sub("", stem.removesuffix(".exe")) in (
        _MSVC_DRIVER_STEMS
    )


#: GNU/clang forced-include options in their **separate**-operand spelling
#: (``-include foo.h``): the following argv token is the operand.
#: ``-include-pch`` is here but deliberately absent from
#: :data:`GNU_JOINED_FORCED_INCLUDE_OPTS` below — clang accepts no joined
#: ``-include-pchfile`` spelling.
GNU_SEPARATE_FORCED_INCLUDE_OPTS: frozenset[str] = frozenset(
    {"-include", "-imacros", "-include-pch"}
)
#: The subset that additionally accepts a **joined** operand
#: (``-includefoo.h``/``-imacrosfoo.h``).
GNU_JOINED_FORCED_INCLUDE_OPTS: frozenset[str] = frozenset({"-include", "-imacros"})
#: MSVC/clang-cl forced-include options in their separate-operand spelling
#: (``/FI file`` or ``-FI file``); the joined ``/FIfile``/``-FIfile`` form is
#: matched by prefix instead. ``/FU`` (forced ``#using`` of a managed
#: assembly, C++/CLI) is **not** here: it names no C/C++ header, so it has no
#: meaning for either an L4 replay or an L2 header parse.
MSVC_SEPARATE_FORCED_INCLUDE_OPTS: frozenset[str] = frozenset({"/FI", "-FI"})

#: Longest-first, so a joined-spelling scan never truncates one option at a
#: shorter sibling's boundary.
_GNU_JOINED_FORCED_INCLUDE_ORDER: tuple[str, ...] = tuple(
    sorted(GNU_JOINED_FORCED_INCLUDE_OPTS, key=len, reverse=True)
)


def match_gnu_forced_include(
    tok: str, argv: list[str], i: int, out: list[str]
) -> tuple[int, str, str]:
    """Try to match a GNU forced-include token (``-include``/``-imacros``/``-include-pch``).

    Appends the matched token(s) to *out* **verbatim** (a joined spelling stays
    joined — an L4 replay hands them straight back to the real compiler) and
    returns ``(new_i, option, operand)``: the index past what was consumed plus
    the *normalized* option spelling and its operand, for a caller that needs
    to re-render rather than replay. Returns ``(i, "", "")`` when *tok* matches
    neither the separate- nor the joined-operand form.
    """
    if tok in GNU_SEPARATE_FORCED_INCLUDE_OPTS and i + 1 < len(argv):
        out += [tok, argv[i + 1]]  # -include / -imacros / -include-pch <file>
        return i + 2, tok, argv[i + 1]
    if tok not in GNU_SEPARATE_FORCED_INCLUDE_OPTS:
        for opt in _GNU_JOINED_FORCED_INCLUDE_ORDER:
            if tok.startswith(opt) and len(tok) > len(opt):
                out.append(tok)  # -includefile / -imacrosfile (joined)
                return i + 1, opt, tok[len(opt) :]
    return i, "", ""


def match_msvc_forced_include(
    tok: str, argv: list[str], i: int, out: list[str]
) -> tuple[int, str, str]:
    """Try to match an MSVC ``/FI`` forced-include token (MSVC mode only).

    Same contract as :func:`match_gnu_forced_include`; both the separate
    (``/FI file``) and joined (``/FIfile``, ``-FIfile``) spellings are handled,
    and the returned option is normalized to ``/FI`` for either.
    """
    if tok in MSVC_SEPARATE_FORCED_INCLUDE_OPTS and i + 1 < len(argv):
        out += [tok, argv[i + 1]]  # /FI file (separate operand)
        return i + 2, "/FI", argv[i + 1]
    if len(tok) > 3 and (tok.startswith("/FI") or tok.startswith("-FI")):
        out.append(tok)  # /FIfile (joined)
        return i + 1, "/FI", tok[3:]
    return i, "", ""


def forced_include_operands(
    argv: Sequence[str], *, msvc: bool
) -> list[tuple[str, str]]:
    """Every forced pre-include in *argv* as ``(option, operand)``, in order.

    The normalized read view of :func:`match_gnu_forced_include`/
    :func:`match_msvc_forced_include` — same recognizer, same option
    vocabulary, same separate/joined spellings, but reporting *what was asked
    for* rather than the verbatim tokens an L4 replay hands back to the real
    compiler. ``option`` is one of ``-include``/``-imacros``/``-include-pch``/
    ``/FI``; ``operand`` is the header the build forces in, exactly as the
    build spelled it (still relative to the compile unit's own ``directory``
    when relative, and still subject to the include search when bare).

    *msvc* enables the ``/FI``/``-FI`` family, gated the same way
    ``source_extractors._argv._scan_argv_for_extra_flags`` gates it (on the
    resolved compiler dialect) so a GNU ``-F``-family flag is never read as one.

    Lives here, in the leaf that already owns this codebase's include-flag
    vocabulary (:data:`_INCLUDE_FLAG_PREFIXES` and friends), rather than in
    ``buildsource``: both consumers are above it — the L4 replay path reaches
    the matchers through ``source_extractors._argv``, and the L2 header parse
    reaches this view through ``buildsource.header_compile_context.
    _context_flags`` — so one shared implementation costs no new import edge in
    either direction. See ``adapters.base.ABI_RELEVANT_FLAG_PREFIXES``'s own
    comment for why these deliberately never travel via
    ``CompileUnit.abi_relevant_flags``.
    """
    tokens = list(argv)
    scratch: list[str] = []
    out: list[tuple[str, str]] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        new_i, option, operand = match_gnu_forced_include(tok, tokens, i, scratch)
        if new_i == i and msvc:
            new_i, option, operand = match_msvc_forced_include(tok, tokens, i, scratch)
        if new_i == i:
            i += 1
            continue
        # A joined spelling whose "operand" is itself option-shaped is not a
        # real file operand (``-include-pchfoo`` reaches the joined ``-include``
        # branch with ``-pchfoo``); replaying it verbatim is harmless, but
        # re-rendering it as a forced include would not be.
        # Unlike the verbatim L4 replay view, this normalized view feeds a new
        # compiler invocation.  Never forward response-file syntax across
        # that boundary: clang expands an ``@file`` operand before parsing
        # ``-include``, allowing the file to inject unrelated compiler flags.
        if operand and not operand.startswith(("-", "@")):
            out.append((option, operand))
        i = new_i
    return out


def forced_include_operand_paths(tokens: Sequence[str]) -> tuple[Path, ...]:
    """Every forced pre-include in *tokens*, as the file **and** its directory.

    Sibling of :func:`include_operand_dirs`, for the same AST-cache-key
    channel and the same reason — a forced-include header
    (``-include config.h``) reaches the parse only as an opaque
    ``gcc_option_tokens`` string, so editing that header would otherwise reuse
    a stale cached AST even though it is exactly the macro-controlling input
    the parse depends on most.

    **The file itself, not only its parent directory (Codex review, PR D).**
    An earlier revision returned the parent alone, on the assumption that the
    cache key's directory walk would then cover it. It does not in general:
    that walk (:func:`iter_cache_header_files`, via
    ``dumper_ast_config._cache_key``) is suffix-filtered by
    :data:`CACHE_HEADER_SUFFIXES` — correct for "catch transitive includes
    under a search root", wrong for a file named explicitly because it is
    *itself* part of the parse. A forced include routinely carries a suffix
    that list does not name (``-imacros settings.def``) or none at all
    (``-include generated/config``), so hashing only the parent left an edit
    to it invisible while the unchanged option token kept the key identical.
    ``_cache_key`` hashes a non-directory entry's own mtime directly, the same
    way it already hashes ``headers``.

    The parent is returned alongside it, so a *neighbouring* header the forced
    one pulls in relatively is covered too — that directory need not be on the
    include search path, so nothing else would cover it.

    **A relative operand also contributes one candidate per include-search
    directory in the same token list (Codex review, PR D, third round).** A
    forced include is resolved through the ``-I`` chain when it is not found
    relative to the working directory, so ``-I /build/gen -include config``
    names ``/build/gen/config`` — and *nothing else* hashes that file: the
    operand alone stats relative to abicheck's own working directory (the
    build's, it is not), and ``/build/gen`` is walked only through the
    suffix-filtered :func:`iter_cache_header_files`, which skips an
    extensionless or ``.def`` name outright. The candidates are emitted
    whether or not they exist, which is both harmless and deliberate:
    ``_cache_key`` contributes a non-existent path's string and moves on, and
    a candidate that *starts* existing is a real change to what the compiler
    would resolve. Since the search order is first-match-wins, a candidate
    under a directory the compiler would never reach can only ever over-
    invalidate — a spurious cache miss, never a stale hit, which is the
    correct direction for this channel to err in.

    Recognition is :func:`forced_include_operands` above, run over both
    dialects — the same tokens ``header_compile_context._context_flags``
    renders — so the set hashed here cannot drift from the set actually passed
    to the compiler.
    """
    toks = list(tokens)
    search_dirs = include_operand_dirs(toks)
    paths: list[Path] = []
    for _option, operand in {
        *forced_include_operands(toks, msvc=False),
        *forced_include_operands(toks, msvc=True),
    }:
        operand_path = Path(operand)
        paths.append(operand_path)
        parent = operand_path.parent
        if str(parent) not in (".", ""):
            paths.append(parent)
        if not operand_path.is_absolute():
            paths.extend(d / operand_path for d in search_dirs)
    return tuple(sorted(set(paths), key=str))


def cache_relevant_operand_paths(tokens: Sequence[str]) -> tuple[Path, ...]:
    """Every path in *tokens* an AST cache key must cover for staleness.

    The union of :func:`include_operand_dirs` (include-*search* dirs) and
    :func:`forced_include_operand_paths` (each forced pre-included header and
    its directory). One function so the four header-parse cache keys that
    consume it — the primary ``service._dump_elf`` parse, ``service.
    _attach_header_graph``'s independent second parse, the PE/Mach-O
    ``service_header_scoped._try_header_scoped_dump`` parse, and the L2 seed's
    own ``derived_include_dirs`` return — cannot disagree about what staleness
    means for the same ``gcc_option_tokens``; three of those four have already
    each needed their own individual fix for exactly that divergence
    (``AGENTS.md``'s tenth and seventeenth L3->L2-fold findings, plus PR D's
    own PE/Mach-O round).

    Mixed files and directories: the consuming ``extra_hash_dirs`` channel
    accepts both (see ``dumper_ast_config._cache_key``), since a forced
    pre-include is a file whose content is part of the parse rather than a
    directory to search.
    """
    return include_operand_dirs(tokens) + forced_include_operand_paths(tokens)


def iter_cache_header_files(directory: Path) -> list[Path]:
    """Header-like files under *directory* whose edits should bust the AST cache.

    Recurses *directory* and returns the files whose suffix is in
    :data:`CACHE_HEADER_SUFFIXES` (the generous superset — includes ``.inl``/
    ``.tcc`` template bodies), sorted for a deterministic cache key. Used by
    ``dumper._cache_key``'s include-dir mtime walk.
    """
    return sorted(
        p for p in directory.rglob("*") if p.suffix.lower() in CACHE_HEADER_SUFFIXES
    )
