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

"""ADR-050 D4 (G32 Phase C) — compatible merge across translation units.

Replaces Phase B's placeholder merge (``dumper_manifest.py``'s original
``merge_tu_fragments``, which raised the moment the same ``entity_key``
appeared in more than one :class:`~abicheck.tu_fragment.TuFragment`,
regardless of whether the two declarations were actually compatible) with
the real merge lattice the ADR describes: for each ``entity_key`` seen in
more than one fragment, the merge is only *trivial* — union provenance,
keep the richer declaration — when the two declarations are genuinely
compatible: a forward declaration paired with its full definition, a plain
declaration + redeclaration, or a difference confined to an added default
argument / initializer. Two full declarations that disagree on anything
else (return type, layout, calling convention, ...) raise
:class:`abicheck.errors.TuMergeError` with ``code="INCONSISTENT_DECLARATION"``
instead of silently picking one side.

**Both conflict codes are extraction-time failures, not
:class:`~abicheck.checker_policy.ChangeKind` members**, despite the
all-caps naming reading exactly like one. A :class:`~abicheck.checker_policy.ChangeKind`
is something ``checker.compare``'s diff produces when comparing two already-
complete snapshots; :class:`~abicheck.errors.TuMergeError` fires *before* a
manifest-driven dump ever produces a snapshot to diff — a fragment set with
an unresolved conflict is not a complete snapshot and can never reach D2's
comparability gate as a clean side. It is therefore correctly outside the
``ChangeKind`` registry and its four-step procedure, the
``changekind-partition``/``changekind-detector`` completeness gates, and
``RISK_KINDS``/``QUALITY_KINDS`` severity classification entirely — the
same reasoning already applied to ``IncompatibleSnapshotSchemaError`` (D1)
and ``DumpDepthNotSatisfiedError``.

``HETEROGENEOUS_ABI_CONTEXT`` fires when the *declared* compiler/target is
uniform (``dump_manifest.py``'s parse-time rule already guarantees that,
D3) but the TUs were nonetheless *extracted* by different AST producers --
``--ast-frontend auto`` falls back to a different backend independently per
TU, so one TU can land on castxml while another falls back to clang within
the same manifest (Codex review, PR #635). ``merge_fragments`` checks every
contributing fragment's ``ast_producer`` for exactly this before merging
any entities.

**Determinism**: merge is deterministic regardless of the order fragments
are passed in — a required property (shuffled TU-processing order must
produce byte-identical merged output). This module never folds fragments
in the caller-supplied order: every per-``entity_key`` candidate list is
built by iterating fragments sorted by their (unique) ``tu_name`` first, so
both which candidate "wins" a union (e.g. which side's default argument is
kept) and the final entity ordering depend only on fragment *content*
(tu_name, entity_key), never on the incidental order ``run_tu_loop``
happened to finish TUs in.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import replace
from functools import partial
from typing import TYPE_CHECKING, Protocol, TypeVar

from .dumper_castxml import (
    _mangled_name_is_local_linkage as _mangled_name_is_local_linkage,
)
from .errors import TuMergeError
from .model import (
    EnumType,
    Fact,
    Function,
    Param,
    RecordType,
    ScopeOrigin,
    Variable,
    replace_with_fact_sync,
)
from .model.cc_attributes import is_cc_attribute as _is_cc_attribute
from .provenance import build_public_set
from .tu_fragment import MergedTuFragments, TuFragment, entity_key
from .tu_merge_provenance import (
    _blank_provenance,
    _more_public_of,
    _other_is_strictly_less_public,
    _pick_deprecated,
    _with_more_public_provenance,
)

if TYPE_CHECKING:
    from .model.identity import EntityId

#: TuMergeError.code values (ADR-050 D4). Kept as plain module constants
#: (not an enum) since TuMergeError.code is a bare string field, matching
#: ManifestValidationError's/comparability's own string-code precedent.
INCONSISTENT_DECLARATION = "INCONSISTENT_DECLARATION"
HETEROGENEOUS_ABI_CONTEXT = "HETEROGENEOUS_ABI_CONTEXT"

#: Sentinel written into a merged manifest's ``ast_toolchain["resolved_lang_mode"]``
#: (never a real ``"c"``/``"c++"`` tag) when the contributing TUs genuinely
#: disagree on it -- an ordinary mixed-language manifest, not an error.
#: ``dumper_toolchain._resolve_standard_provenance`` recognizes this exact
#: value and returns ``None`` outright rather than falling back to a static
#: re-derivation that can be confidently *wrong* (not just unknown) for a TU
#: whose language mode came from something invisible to the manifest's
#: combined public headers (Codex review, fresh evidence). See
#: ``merge_fragments``'s own inline comment for the full reasoning.
_HETEROGENEOUS_LANG_MODE = "heterogeneous"


class _Provenanced(Protocol):
    """The ADR-015 schema v6 provenance fields every model type this module
    merges (:class:`Function`/:class:`Variable`/:class:`RecordType`/
    :class:`EnumType`) shares -- expressing this as a bound on ``_T`` (CodeRabbit
    review, PR #635) lets :func:`_blank_provenance`/:func:`_more_public_of`/
    :func:`_with_more_public_provenance` type-check their real contract
    directly, instead of an unbound ``_T`` plus per-call-site ``# type:
    ignore[attr-defined]`` suppressions.
    """

    source_location: str | None
    source_header: str | None
    origin: ScopeOrigin
    deprecated: str | None


_T = TypeVar("_T", bound=_Provenanced)


def _has_local_linkage_mangling(mangled: str) -> bool:
    """Whether *mangled* carries an Itanium ABI marker for genuine internal
    (TU-local) linkage -- the ``<local-name>`` production's bare ``L``
    immediately before a source-name (a file-scope ``static``
    function/variable, whether at global scope -- ``_ZL6helperi`` -- or
    nested in a named namespace -- ``static int state;`` inside
    ``namespace ns`` mangles to ``_ZN2nsL5stateE``, *not* a leading ``_ZL``
    prefix, since the ``L`` marks the innermost component, not the string's
    start (Codex review, PR #635 round 7; empirically confirmed against real
    clang) -- or a ``_GLOBAL__N_`` component (an anonymous-namespace
    entity, e.g. ``_ZN12_GLOBAL__N_1...``). A ``static`` **member**
    function/variable of an ordinarily-visible class -- externally linked,
    not TU-local at all, despite ``storageClass == "static"`` being set in
    the AST exactly the same way -- mangles to an ordinary marker-free
    symbol like ``_ZN6Widget4makeEi`` (round 6). Neither marker ever
    appears in an externally-linked Itanium symbol, so this is a precise
    linkage signal, not a heuristic guess.

    The ``L``-marker half of this check delegates to
    :func:`abicheck.dumper_castxml._mangled_name_is_local_linkage`, which
    already parses the full length-prefixed identifier chain component by
    component rather than substring-matching -- reusing it here (instead of
    a second, narrower ``_ZL``-prefix-only reimplementation) is what catches
    the nested-namespace case; a plain ``startswith("_ZL")`` check only ever
    matches when the local entity is the *first* component, i.e. at global
    scope.
    """
    return _mangled_name_is_local_linkage(mangled) or "_GLOBAL__N_" in mangled


def _function_key(tu_name: str, fn: Function) -> tuple[str, str]:
    """``entity_key`` for a :class:`Function`, scoped by TU for
    internal-linkage declarations.

    A TU-local function's mangled spelling is **not** unique across
    translation units the way an externally-linked symbol's is -- it never
    needs to be, since the symbol never leaves its own TU's object file.
    Two unrelated TUs can each declare their own private
    ``static void helper(int)`` (or their own same-named entity in an
    anonymous namespace) and get the identical mangled string, even though
    they are two distinct, TU-local entities, not redeclarations of "the
    same" function (Codex review, PR #635 rounds 5-6). Keying only on
    ``fn.mangled`` would either silently fold them into one (discarding one
    TU's declaration) or raise a false ``INCONSISTENT_DECLARATION`` the
    moment they happen to differ.

    Detecting this from ``fn.is_static`` alone is wrong in *both*
    directions: it misses an anonymous-namespace function entirely (clang
    never sets ``storageClass`` for one -- confirmed empirically, it stays
    unset even though the function is just as TU-local), and it wrongly
    flags an ordinarily-visible class's ``static`` **member** function
    (``storageClass == "static"`` is set there too, but a static member
    function has the class's own, ordinary external linkage, nothing to do
    with TU-locality). ``fn.mangled`` itself resolves both cases correctly
    via :func:`_has_local_linkage_mangling` -- except when no C++ mangling
    was applied at all (a plain-C parse, or `extern "C"`), where there is
    no member-function concept to conflate with and ``is_static`` is an
    unambiguous, purely C signal.

    Folding ``tu_name`` into the key for a genuinely TU-local function
    makes it trivially TU-scoped -- it can only ever collide with *itself*
    (the same TU's own repeat, already tolerated by :func:`_merge_group`'s
    same-TU-extras handling), never with another TU's unrelated local
    entity.

    **Known, accepted limitation** (Codex review, PR #635 round 9):
    ``entity_key`` assumes ``fn.mangled`` is a return-type-independent
    identity for "the same function," which holds for every mangling
    scheme this module has been verified against (Itanium, and this
    function's own plain-C fallback) but not for the Microsoft C++ ABI --
    MSVC's decorated name for a free function encodes its return type, so
    two genuinely conflicting cross-TU declarations differing only in
    return type (``int compute(int);`` vs. ``double compute(int);`` --
    itself only reachable as an ODR violation the two TUs' own compilers
    never catch, since each sees only its own declaration) would decorate
    to two different names, land in two different ``entity_key`` buckets,
    and both survive the merge silently instead of raising
    ``INCONSISTENT_DECLARATION``. This module already carries direct,
    observed evidence of the same underlying MSVC-mangling difference --
    ``tests/test_tu_merge.py``'s ``test_odr_conflict_fixture_raises_through_real_clang_backend``
    documents exactly this symptom from real Windows CI (clang in MSVC
    compatibility mode) and works around it by not forcing C++ mode there,
    rather than fixing entity identity at the source. A real fix needs a
    return-type-independent normalization of MSVC decorated names -- a
    scheme-specific parser this module cannot write and verify without a
    real MSVC/``clang-cl`` toolchain to check output against (unavailable
    in the environment this round's changes were verified in), so this is
    documented rather than guess-fixed, the same call already made for the
    anonymous-namespace-type and typedef-qualification gaps above.

    **Second known, accepted limitation** (Codex review, PR #635 round
    12): ``fn.mangled == fn.name`` is not actually proof of a plain-C
    declaration -- clang's header AST also has no ``mangledName`` for an
    *uninstantiated* C++ function/method template, so
    ``dumper_clang.py``'s ``mangled = ... or name`` falls back to the bare
    name there too (empirically confirmed: ``template <typename T> struct
    A { void run(T); };`` and an unrelated ``template <typename T> struct
    B { void run(T, T); };`` both parse with ``mangledName=None`` for
    their ``run`` method). Two structurally unrelated template methods
    sharing a bare name in different TUs therefore reach this branch and
    fall to ``fn.is_static`` -- ordinarily ``False`` for a non-static
    method, so ``is_local`` is wrongly ``False`` and the two land in the
    same ``entity_key`` bucket. If their signatures happen to differ (the
    common case) :func:`_merge_functions` raises a spurious
    ``INCONSISTENT_DECLARATION``; if their signatures happen to coincide,
    they silently merge into one function despite being unrelated. Fixing
    this precisely needs a way to tell "genuinely unmangled, e.g. plain
    C/`extern "C"`" apart from "C++ but clang produced no mangled name" at
    this call site -- :class:`Function` carries no such signal today
    (unlike :class:`RecordType`'s ``is_template_pattern``, there is no
    per-function equivalent, and nothing here links a ``Function`` back to
    its enclosing template even if there were) -- so, like the MSVC gap
    above, this needs a producer-side model/schema addition rather than a
    guess at this call site, and is documented rather than fixed this
    round.
    """
    if fn.mangled == fn.name:
        is_local = fn.is_static
    else:
        is_local = _has_local_linkage_mangling(fn.mangled)
    name = f"{tu_name}::{fn.mangled}" if is_local else fn.mangled
    return entity_key("function", name)


def _variable_key(tu_name: str, var: Variable) -> tuple[str, str]:
    """``entity_key`` for a :class:`Variable`, the variable analogue of
    :func:`_function_key` -- a file-scope ``static`` or anonymous-namespace
    variable is exactly as TU-local as its function counterpart, and
    verified to follow the identical Itanium marker convention (a
    ``static int state;`` mangles to ``_ZL5state``; an anonymous-namespace
    variable to ``_ZN12_GLOBAL__N_1...``; a ``static`` **member** variable
    to an ordinary marker-free symbol, e.g. ``_ZN6Widget7counterE`` --
    Codex review, PR #635 round 6).

    **Known, accepted limitation**: unlike :class:`Function`,
    :class:`Variable` carries no ``is_static`` field at all, so a plain-C
    (or `extern "C"`) file-scope ``static`` variable -- whose mangled name
    equals its bare name, carrying no Itanium marker to detect -- has no
    signal this function can read to distinguish it from an ordinary
    external variable. Closing that gap needs a new ``Variable.is_static``
    model field (a schema change, `dumper_clang.py`/`dumper_castxml.py`
    updates to populate it) -- out of proportionate scope for this fix,
    matching this PR's typedef-qualification precedent (see G32 Phase C
    PR discussion) of documenting a producer-side gap rather than papering
    over it. The C++ case (the more common source of same-named
    file-scope/anonymous-namespace variable collisions in practice) is
    fully handled.
    """
    name = (
        f"{tu_name}::{var.mangled}"
        if _has_local_linkage_mangling(var.mangled)
        else var.mangled
    )
    return entity_key("variable", name)


def merge_fragments(
    fragments: Sequence[TuFragment],
    *,
    public_header_paths: Sequence[str] = (),
    public_header_dirs: Sequence[str] = (),
) -> MergedTuFragments:
    """Merge *fragments* into one :class:`MergedTuFragments`, resolving
    cross-TU redeclarations of the same :func:`~abicheck.tu_fragment.entity_key`
    when they are compatible and raising :class:`~abicheck.errors.TuMergeError`
    when they are not (ADR-050 D4).

    *public_header_paths*/*public_header_dirs* are the same manifest-wide
    public-surface inputs :func:`abicheck.provenance.apply_provenance` later
    classifies declarations against (opt-in — omitted, every declaration's
    origin stays ``UNKNOWN`` and this has no effect, matching that module's
    own default). When supplied, they let a trivial merge prefer whichever
    side's ``source_location`` classifies as ``PUBLIC_HEADER`` as the
    winning declaration's representative provenance -- see
    :func:`_more_public_of`'s own docstring for why this matters: a merged
    entity carries exactly one ``source_location``, and picking the wrong
    side can make a genuinely public declaration read as private/unreachable
    once ``apply_provenance`` runs on the merged snapshot.
    """
    if not fragments:
        return MergedTuFragments(
            functions=(),
            variables=(),
            types=(),
            enums=(),
            typedefs={},
            typedefs_qualified={},
            constants={},
            typedef_entity_ids={},
            constant_entity_ids={},
            ast_producer="castxml",
            ast_toolchain={},
            ast_fallback_reason=None,
            ast_toolchain_supported=None,
            ast_toolchain_unsupported_reasons=(),
            frontend_context_kind=None,
        )

    # Fixed, content-derived order (tu_name, never the caller's own sequence
    # order) -- see this module's own "Determinism" docstring section.
    ordered = sorted(fragments, key=lambda f: f.tu_name)

    # HETEROGENEOUS_ABI_CONTEXT's real trigger (Codex review, PR #635):
    # dump_manifest.py's parse-time rule only rejects a manifest that
    # *declares* different compilers/target triples across its TUs -- it
    # says nothing about `--ast-frontend auto`'s per-TU fallback, which
    # picks castxml/clang independently for each TU at extraction time. A
    # manifest with two TUs, one falling back to clang while the other
    # stays on castxml, would otherwise merge fine and silently stamp the
    # whole snapshot with just one representative fragment's ast_producer
    # -- which `resolve_header_ast_result`/`dumper.py` then trusts globally
    # (`is_clang` gates DWARF layout backfill/coherence for every
    # declaration, not just the representative fragment's own). Reject
    # that mix outright rather than let a wrong-producer assumption leak
    # into layout backfill for declarations that didn't actually come from
    # the representative producer.
    producers = {f.ast_producer for f in ordered}
    if len(producers) > 1:
        raise TuMergeError(
            "translation units were extracted by different AST producers "
            f"({sorted(producers)!r}) -- likely --ast-frontend auto falling "
            "back to a different backend for only some TUs (see "
            "--allow-ast-frontend-fallback). A manifest-driven dump requires "
            "every TU to share one AST producer, the same way it already "
            "requires one compiler/target triple, since downstream layout "
            "backfill/coherence logic trusts a single producer for the "
            "whole merged snapshot.",
            code=HETEROGENEOUS_ABI_CONTEXT,
            entity_key=("manifest", "ast_producer"),
            tu_names=tuple(f.tu_name for f in ordered),
        )

    # Same reasoning, for frontend_context_kind (CodeRabbit review): every TU
    # is parsed under the manifest's one, uniform frontend_context request
    # (dump_manifest.py has no per-TU override) against one, uniform compiler
    # binary, so this should never actually diverge -- but blindly copying
    # ordered[0]'s value below would misrepresent the merged snapshot's
    # provenance if that ever changed, exactly like an unguarded ast_producer
    # copy would.
    frontend_context_kinds = {f.frontend_context_kind for f in ordered}
    if len(frontend_context_kinds) > 1:
        raise TuMergeError(
            "translation units resolved different SYCL/DPC++ frontend "
            f"contexts ({sorted(str(k) for k in frontend_context_kinds)!r}) "
            "-- every TU in a manifest is parsed under the same requested "
            "frontend_context by construction, so this should be "
            "unreachable; refusing to guess a representative value.",
            code=HETEROGENEOUS_ABI_CONTEXT,
            entity_key=("manifest", "frontend_context_kind"),
            tu_names=tuple(f.tu_name for f in ordered),
        )

    header_segs, dir_segs, have_public_set = build_public_set(
        list(public_header_paths), list(public_header_dirs)
    )

    functions = _flatten(
        _merge_group(
            ((f.tu_name, fn) for f in ordered for fn in f.functions),
            key_fn=_function_key,
            merge_fn=partial(
                _merge_functions,
                header_segs=header_segs,
                dir_segs=dir_segs,
                have_public_set=have_public_set,
            ),
        )
    )
    variables = _flatten(
        _merge_group(
            ((f.tu_name, var) for f in ordered for var in f.variables),
            key_fn=_variable_key,
            merge_fn=partial(
                _merge_variables,
                header_segs=header_segs,
                dir_segs=dir_segs,
                have_public_set=have_public_set,
            ),
        )
    )
    types = _flatten(
        _merge_group(
            ((f.tu_name, rt) for f in ordered for rt in f.types),
            # RecordType.name is deliberately bare (namespace lives in
            # qualified_name -- see RecordType's own docstring); keying on
            # the bare name alone would collide `one::X` and `two::X` into
            # one spurious INCONSISTENT_DECLARATION conflict (Codex review,
            # PR #635). Falls back to the bare name when qualified_name is
            # unset (global-scope types, or a producer that never captured
            # it), matching every other merge key's "None means unknown,
            # don't invent structure" convention.
            #
            # Known, accepted limitation (Codex review, PR #635 round 7):
            # a type declared inside an *anonymous* namespace is exactly as
            # TU-local as an anonymous-namespace function/variable (see
            # _function_key/_variable_key above), but has no analogous
            # signal to detect it from here -- unlike a Function/Variable,
            # a RecordType/EnumType carries no mangled linker symbol to
            # read a `_GLOBAL__N_` marker off of, and dumper_clang.py's AST
            # walker drops an anonymous namespace's (nameless) segment from
            # `scope` entirely rather than encoding it distinctly (see
            # `_SCOPE_NODE_KINDS` handling in `_walk`), so `qualified_name`
            # for such a type is indistinguishable from one declared
            # directly at the enclosing named scope. Two unrelated
            # same-named anonymous-namespace types in different TUs can
            # therefore still be incorrectly merged/conflicted here.
            # Closing this needs a producer-side signal (a new model field
            # marking anonymous-namespace membership, populated by both
            # dumper_clang.py and dumper_castxml.py) -- a schema change out
            # of proportionate scope for this fix, the same call already
            # made for the typedef/constant namespace-qualification gap
            # documented on `_merge_scalar_group`'s call sites below.
            key_fn=lambda _tu, rt: entity_key("type", rt.qualified_name or rt.name),
            merge_fn=partial(
                _merge_types,
                header_segs=header_segs,
                dir_segs=dir_segs,
                have_public_set=have_public_set,
            ),
        )
    )
    enums = _flatten(
        _merge_group(
            ((f.tu_name, en) for f in ordered for en in f.enums),
            # Same bare-name-collision fix as RecordType above (Codex
            # review, PR #635) -- EnumType.name is likewise bare, with the
            # namespace in qualified_name.
            key_fn=lambda _tu, en: entity_key("enum", en.qualified_name or en.name),
            merge_fn=partial(
                _merge_enums,
                header_segs=header_segs,
                dir_segs=dir_segs,
                have_public_set=have_public_set,
            ),
        )
    )
    # Known, accepted limitation: TuFragment.typedefs/.constants are bare
    # `name -> value` dicts with no namespace-qualified-name channel (unlike
    # RecordType/EnumType's `qualified_name`), so `namespace one { using X =
    # int; }` and `namespace two { using X = double; }` still collide on the
    # bare key `X` here -- a real gap, but closing it needs a producer-side
    # schema change (dumper_clang.py/dumper_castxml.py would need to start
    # emitting qualified typedef/constant names), out of proportionate scope
    # for this fix (Codex review, PR #635; see also the anonymous-namespace
    # RecordType/EnumType gap documented on the `types`/`enums` key_fn above,
    # the same category of producer-side limitation).
    typedefs = _merge_scalar_group(
        (
            (f.tu_name, name, value)
            for f in ordered
            for name, value in f.typedefs.items()
        ),
        kind="typedef",
    )
    # typedefs_qualified (schema v25, G31 Phase C): keyed by fully-qualified
    # name, so unlike `typedefs` above this does NOT inherit the bare-name
    # cross-TU collision the comment above documents -- two distinct
    # declarations always carry distinct qualified names. The same TU
    # legitimately re-parsing an identical shared header still needs
    # `_merge_scalar_group`'s existing same-key/same-value dedup, so this
    # reuses that helper rather than a plain dict union.
    typedefs_qualified = _merge_scalar_group(
        (
            (f.tu_name, name, value)
            for f in ordered
            for name, value in f.typedefs_qualified.items()
        ),
        kind="typedef",
    )
    constants = _merge_scalar_group(
        (
            (f.tu_name, name, value)
            for f in ordered
            for name, value in f.constants.items()
        ),
        kind="constant",
    )
    # `EntityId` sidecars (ADR-063 Phase 2): unioned by
    # `_merge_entity_id_sidecar`, not `_merge_scalar_group` -- the latter
    # compares wire *values* (a typedef's underlying-type string, a
    # constant's value string), which two TUs already had to agree on above
    # for `typedefs_qualified`/`constants` to merge at all. An `EntityId` is
    # a *structural* fact instead (scope + kind + leaf name), and two TUs
    # can agree on a qualified name's string spelling while disagreeing on
    # its structure -- ADR-063's own motivating collision (`ns::Alias`
    # nested in a namespace in one TU, nested in a same-named `struct ns`
    # in another) is exactly this shape, so it needs its own conflict check
    # rather than inheriting `_merge_scalar_group`'s value-only one (Codex
    # review).
    typedef_entity_ids = _merge_entity_id_sidecar(
        (
            (f.tu_name, name, eid)
            for f in ordered
            for name, eid in f.typedef_entity_ids.items()
        ),
        kind="typedef",
    )
    constant_entity_ids = _merge_entity_id_sidecar(
        (
            (f.tu_name, name, eid)
            for f in ordered
            for name, eid in f.constant_entity_ids.items()
        ),
        kind="constant",
    )

    # Any contributing fragment's AST provenance is representative: ADR-050
    # D3 rejects a manifest declaring different compilers/target triples
    # across TUs at parse time (dump_manifest.py -- compiler/target are
    # base-profile-only fields), so every fragment here was produced by the
    # same toolchain by construction. `ordered[0]` (not `fragments[0]`) so
    # the choice is itself order-independent.
    representative = ordered[0]
    # resolved_lang_mode (Codex review, fresh evidence) is NOT one of the
    # toolchain-identity facts the comment above guarantees uniform -- a
    # perfectly ordinary mixed-language manifest (some .c TUs, some .cpp
    # TUs, one shared compiler) legitimately resolves it differently per
    # TU, unlike ast_producer/frontend_context_kind above, which really
    # should never diverge and are rejected outright when they do. Blindly
    # copying one representative TU's resolved_lang_mode would silently
    # mislabel the whole merged snapshot's language_standard for every
    # other TU.
    #
    # Merely *dropping* the key when it's not unanimous (an earlier version
    # of this fix) is not safe either: dropping it makes
    # ``_resolve_standard_provenance`` fall back to its static
    # re-derivation (``_resolve_force_cpp`` over the manifest's combined,
    # *public/declared* headers alone) -- which can be confidently *wrong*,
    # not merely uninformed, for a TU whose C++-ness comes only from a
    # private forced include invisible to those public headers (Codex
    # review, fresh evidence). A merged manifest genuinely known to mix
    # language modes must not let that static re-derivation guess at all.
    # ``_HETEROGENEOUS_LANG_MODE`` is a sentinel value (never a real
    # ``"c"``/``"c++"`` tag) that ``_resolve_standard_provenance`` (see its
    # own docstring) recognizes and treats as "cannot determine this at
    # all" -- skipping *both* the forced-``gnu11``-literal path and the
    # probe-fallback path, returning ``None`` outright, the same honest
    # "unknown" this field didn't exist to improve on before this PR, not
    # a new failure mode and not a wrong guess either.
    lang_modes = {
        f.ast_toolchain["resolved_lang_mode"]
        for f in ordered
        if "resolved_lang_mode" in f.ast_toolchain
    }
    merged_ast_toolchain = representative.ast_toolchain
    if len(lang_modes) > 1:
        merged_ast_toolchain = {
            **representative.ast_toolchain,
            "resolved_lang_mode": _HETEROGENEOUS_LANG_MODE,
        }
    return MergedTuFragments(
        functions=functions,
        variables=variables,
        types=types,
        enums=enums,
        typedefs=typedefs,
        typedefs_qualified=typedefs_qualified,
        constants=constants,
        typedef_entity_ids=typedef_entity_ids,
        constant_entity_ids=constant_entity_ids,
        ast_producer=representative.ast_producer,
        ast_toolchain=merged_ast_toolchain,
        ast_fallback_reason=representative.ast_fallback_reason,
        ast_toolchain_supported=representative.ast_toolchain_supported,
        ast_toolchain_unsupported_reasons=representative.ast_toolchain_unsupported_reasons,
        frontend_context_kind=representative.frontend_context_kind,
    )


def _merge_group(
    items: Iterable[tuple[str, _T]],
    *,
    key_fn: Callable[[str, _T], tuple[str, str]],
    merge_fn: Callable[[_T, _T], _T | None],
) -> dict[tuple[str, str], tuple[_T, ...]]:
    """Group *items* (``(tu_name, entity)`` pairs) by ``key_fn(tu_name,
    entity)`` and fold each group's candidates through *merge_fn*, raising
    :class:`~abicheck.errors.TuMergeError` the moment two candidates from
    *different* TUs for the same key don't merge.

    *key_fn* receives ``tu_name`` alongside the entity so a caller can fold
    TU identity into the key itself when an entity's identity is inherently
    TU-scoped (e.g. a ``static``-linkage function's mangled name -- see the
    ``functions`` call site in :func:`merge_fragments`).

    A single TU's own parser output may legitimately repeat a key (e.g. two
    destructors both falling back to castxml's synthesized no-mangled-name
    marker within the same TU, already tolerated by the flat single-TU
    dump path) -- this is not a cross-TU merge concern, so a TU's *own*
    repeated candidates for a key are never passed to *merge_fn* against
    each other or against their own TU's representative; they ride through
    untouched as extra entries in the returned tuple. Only the *first*
    candidate contributed by each distinct TU (that TU's representative)
    participates in the cross-TU fold below.

    A representative is identified by grouping candidates *per TU* before
    folding, not by comparing against the accumulator's current identity --
    a candidate's tu_name is checked against the TU that actually produced
    it, not against whichever TU's declaration the accumulator happens to
    carry after a prior successful merge. The naive "skip when tu_name
    equals the accumulator's tu_name" check used before this fix made a
    TU's own repeat ride through unchecked only by *accident*, whenever it
    happened to immediately follow a successful cross-TU merge that left
    the accumulator attributed to that same TU -- the exact same repeat,
    processed in a different fragment order, would instead be compared
    (via the accumulator still carrying the *other* TU's identity) and
    correctly raise. For example, TU ``a`` declaring ``f(): void`` and TU
    ``b`` declaring both ``f(): void`` and ``f(): int`` under the same key
    would previously merge fine or raise ``TuMergeError`` depending purely
    on which of ``b``'s two entries happened to be listed first in ``b``'s
    own fragment -- not on the actual conflict (Codex review, PR #635).

    Once every distinct TU's representative has been folded into a single
    cross-TU accumulator, each TU's remaining repeats (from *any*
    contributing TU) are validated against that final accumulator too --
    not merged into it, just checked for compatibility -- so a repeat that
    genuinely conflicts with what every other TU agrees on still raises,
    rather than silently riding through as an unvalidated extra. When only
    one distinct TU contributes to a key at all, there is no cross-TU
    accumulator to validate against, and repeats ride through exactly as
    before (unconditionally tolerated, matching the flat single-TU
    behavior this carve-out exists to preserve).

    Known, accepted limitation: if *two different* TUs both exhibit the
    same producer-side key-collision quirk (e.g. two unrelated symbols in
    two different TUs both fall back to the same synthesized marker), the
    repeat-vs-accumulator check above can raise a spurious conflict between
    two entities that don't actually correspond to each other -- an
    inherent consequence of the producer's lossy key derivation, the same
    category of gap already documented on the ``types``/``enums`` key_fn
    and the typedef/constant ``key`` above in :func:`merge_fragments`, and
    likewise not fixable here without a producer-side schema change.

    Iteration order over *items* determines both dict insertion order (the
    returned mapping's iteration order, and therefore the caller's final
    tuple order) and the left-to-right fold order within a group; callers
    pass *items* already derived from tu_name-sorted fragments, so both are
    deterministic regardless of the original fragment sequence's order.
    """
    by_key: dict[tuple[str, str], list[tuple[str, _T]]] = {}
    for tu_name, entity in items:
        by_key.setdefault(key_fn(tu_name, entity), []).append((tu_name, entity))

    merged: dict[tuple[str, str], tuple[_T, ...]] = {}
    for key, candidates in by_key.items():
        per_tu: dict[str, list[_T]] = {}
        for tu_name, entity in candidates:
            per_tu.setdefault(tu_name, []).append(entity)

        tu_names = list(per_tu)
        acc_tu, acc_entity = tu_names[0], per_tu[tu_names[0]][0]
        for tu_name in tu_names[1:]:
            entity = per_tu[tu_name][0]
            result = merge_fn(acc_entity, entity)
            if result is None:
                kind, name = key
                raise TuMergeError(
                    f"translation units {acc_tu!r} and {tu_name!r} declare "
                    f"incompatible versions of {kind} {name!r} -- ADR-050 "
                    "Phase C's merge only reconciles a forward declaration + "
                    "definition, a plain redeclaration, or a default-"
                    "argument-only difference; this pair disagrees on "
                    "something else (return type, layout, calling "
                    "convention, ...) and cannot be silently resolved.",
                    code=INCONSISTENT_DECLARATION,
                    entity_key=key,
                    tu_names=(acc_tu, tu_name),
                )
            acc_entity = result
            acc_tu = tu_name

        extras: list[_T] = []
        for tu_name in tu_names:
            for entity in per_tu[tu_name][1:]:
                if len(tu_names) > 1 and merge_fn(acc_entity, entity) is None:
                    kind, name = key
                    raise TuMergeError(
                        f"translation unit {tu_name!r} declares a repeated "
                        f"{kind} {name!r} that conflicts with the version "
                        "every other translation unit agrees on -- ADR-050 "
                        "Phase C's merge only reconciles a forward "
                        "declaration + definition, a plain redeclaration, "
                        "or a default-argument-only difference.",
                        code=INCONSISTENT_DECLARATION,
                        entity_key=key,
                        tu_names=(acc_tu, tu_name),
                    )
                extras.append(entity)
        merged[key] = (acc_entity, *extras)
    return merged


def _flatten(grouped: dict[tuple[str, str], tuple[_T, ...]]) -> tuple[_T, ...]:
    return tuple(entity for entities in grouped.values() for entity in entities)


def _merge_scalar_group(
    items: Iterable[tuple[str, str, str]], *, kind: str
) -> dict[str, str]:
    """The typedef/constant analogue of :func:`_merge_group`: a bare
    ``name -> value`` mapping has no "richer declaration" to prefer, so a
    trivial merge only exists when every contributing TU agrees on the
    exact same value; any disagreement is an
    :class:`~abicheck.errors.TuMergeError`.
    """
    by_name: dict[str, list[tuple[str, str]]] = {}
    for tu_name, name, value in items:
        by_name.setdefault(name, []).append((tu_name, value))

    merged: dict[str, str] = {}
    for name, candidates in by_name.items():
        acc_tu, acc_value = candidates[0]
        for tu_name, value in candidates[1:]:
            if value != acc_value:
                raise TuMergeError(
                    f"translation units {acc_tu!r} and {tu_name!r} declare "
                    f"{kind} {name!r} with different values ({acc_value!r} "
                    f"vs {value!r}) -- ADR-050 Phase C cannot reconcile two "
                    "different values for the same name.",
                    code=INCONSISTENT_DECLARATION,
                    entity_key=entity_key(kind, name),
                    tu_names=(acc_tu, tu_name),
                )
        merged[name] = acc_value
    return merged


def _merge_entity_id_sidecar(
    items: Iterable[tuple[str, str, EntityId]], *, kind: str
) -> dict[str, EntityId]:
    """The `EntityId`-sidecar analogue of :func:`_merge_scalar_group`: two
    TUs contributing the same qualified key must resolve the identical
    structural identity, or the merge cannot pick one over the other any
    more safely than :func:`_merge_scalar_group` can pick one disagreeing
    value over another. Compares by value (`EntityId` is a frozen,
    structurally-equal dataclass), not by `.key` string, so this stays exact
    even before `.key`'s own cross-release stability is established
    elsewhere (`model/identity.py`).
    """
    by_name: dict[str, list[tuple[str, EntityId]]] = {}
    for tu_name, name, eid in items:
        by_name.setdefault(name, []).append((tu_name, eid))

    merged: dict[str, EntityId] = {}
    for name, candidates in by_name.items():
        acc_tu, acc_eid = candidates[0]
        for tu_name, eid in candidates[1:]:
            if eid != acc_eid:
                raise TuMergeError(
                    f"translation units {acc_tu!r} and {tu_name!r} resolve "
                    f"different entity identities for {kind} {name!r} "
                    f"({acc_eid!r} vs {eid!r}) -- two TUs agreeing on this "
                    "name's spelling while disagreeing on its structural "
                    "scope cannot be reconciled.",
                    code=INCONSISTENT_DECLARATION,
                    entity_key=entity_key(kind, name),
                    tu_names=(acc_tu, tu_name),
                )
        merged[name] = acc_eid
    return merged


#: Attribute families whose arguments are a *set* that legally accumulates
#: across separate attribute occurrences -- GCC/clang both accept
#: ``__attribute__((nonnull(1))) __attribute__((nonnull(2)))`` (or the
#: equivalent split across two compatible redeclarations in different TUs)
#: as "parameters 1 and 2 are both nonnull", not two conflicting claims.
#: :func:`abicheck.dumper_clang._clang_contract_attributes` already keeps
#: each occurrence as its own list entry rather than folding them into one
#: token (see that function's own docstring), so a same-family, different-
#: argument pair here is ordinary redeclaration, not a conflict (Codex
#: review, PR #635 round 12) -- unlike an argument-bearing family such as
#: ``format``/``regparm``, where two different argument sets truly are
#: incompatible claims about the same function.
_SET_VALUED_ATTRIBUTE_FAMILIES = frozenset({"nonnull"})


def _merge_contract_attributes(
    a: list[str] | None, b: list[str] | None
) -> tuple[bool, list[str] | None]:
    """Union two :class:`Function`'s ``contract_attributes`` lists -- an
    additive attribute one TU's redeclaration carries and another doesn't
    (e.g. clang accepts ``int f(int);`` alongside a later
    ``[[nodiscard]] int f(int);`` redeclaration, normalized here as
    ``warn_unused_result``) is exactly as routine a cross-TU difference as
    ``deprecated``, not a structural disagreement (Codex review, PR #635).

    ``None`` means "not captured" (an older snapshot, or a dumper without
    attribute support -- see :attr:`Function.contract_attributes`'s own
    docstring), not "captured, empty" (that's ``[]``) -- so a ``None`` side
    contributes no information and the other side's value (however
    incomplete) is kept as-is, the same "missing means unknown, defer to
    whichever side actually captured something" treatment
    :func:`_merge_functions`'s ``default``-argument union and
    :func:`_merge_variables`'s ``value`` union give their own optional
    facts.

    Each token already carries its own arguments where relevant (e.g.
    ``nonnull(1)``, ``format(printf,1,2)`` -- see
    :func:`abicheck.dumper_clang._clang_contract_attributes`), so two
    tokens from the same attribute family (the text before any ``(``, e.g.
    both ``nonnull(...)``) that aren't byte-identical describe genuinely
    different arguments for the same attribute and are a real conflict --
    **except** :data:`_SET_VALUED_ATTRIBUTE_FAMILIES`, whose differing
    arguments legitimately accumulate instead (Codex review, PR #635 round
    12). An unrecognized/unparsed token has no family boundary to find and
    is compared whole. A token present in only one TU's list is purely
    additive and is kept.

    Calling-convention tokens (``_CC_ATTRIBUTE_BASES`` --
    :mod:`abicheck.diff_symbols`'s own canonical set, reused here rather
    than duplicated) are mutually exclusive as a *group*, not just within
    one family: two TUs redeclaring the same Itanium-mangled function with
    ``ms_abi`` vs. ``sysv_abi`` share a linker identity but have
    incompatible call ABIs -- each is its own bare family (no ``(``), so
    the per-family check above never sees them as the same family and
    would otherwise silently union both onto one function, exactly what
    :func:`abicheck.diff_symbols._check_contract_attributes_change`
    treats as ``CALLING_CONVENTION_CHANGED`` when it later diffs two
    already-merged snapshots (Codex review, PR #635 round 12). More than
    one surviving calling-convention token is therefore rejected here,
    before that comparison ever gets the chance to run on a nonsensical
    merged function.
    """
    if a is None:
        return True, b
    if b is None:
        return True, a

    def _family(token: str) -> str:
        paren = token.find("(")
        return token if paren == -1 else token[:paren]

    a_families = {_family(token) for token in a}
    merged = set(a)
    for token in b:
        if token in merged:
            continue
        family = _family(token)
        if family in a_families and family not in _SET_VALUED_ATTRIBUTE_FAMILIES:
            return False, None
        merged.add(token)
    if sum(1 for token in merged if _is_cc_attribute(token)) > 1:
        return False, None
    return True, sorted(merged)


def _suppress_private_only_attributes(
    merged: list[str] | None,
    base_attrs: list[str] | None,
    other_attrs: list[str] | None,
) -> list[str] | None:
    """Drop tokens from *merged* (a :func:`_merge_contract_attributes`
    result) that came *only* from *other_attrs* and not *base_attrs* --
    called only when the caller has already proven *other* is the
    strictly-less-public side of a merge (Codex review, PR #635 round 18).

    A calling-convention token is never dropped: unlike a source-facing
    attribute such as ``nodiscard``/``nonnull``, it describes the actual
    compiled function's ABI regardless of which header happens to spell
    it, so it is a real fact about the public entity either way --
    :func:`_merge_contract_attributes`'s conflict validation already ran
    unconditionally on both sides before this function is ever called.

    If either *base_attrs* or *other_attrs* is ``None`` ("not captured",
    not "captured empty" -- see :func:`_merge_contract_attributes`'s own
    docstring), this returns *merged* unchanged: an uncaptured side's true
    attribute set is unknown, not provably empty, so there is nothing safe
    to subtract.
    """
    if merged is None or base_attrs is None or other_attrs is None:
        return merged
    base_set = set(base_attrs)
    other_set = set(other_attrs)
    return [
        token
        for token in merged
        if _is_cc_attribute(token) or token in base_set or token not in other_set
    ]


def _merge_identical_modulo_provenance(
    a: _T,
    b: _T,
    *,
    header_segs: list[tuple[str, ...]],
    dir_segs: list[tuple[str, ...]],
    have_public_set: bool,
) -> _T | None:
    """Merge two same-``entity_key`` declarations that are required to be
    identical except for provenance/``deprecated`` -- the "two complete
    definitions" branch shared by :func:`_merge_types`/:func:`_merge_enums`
    (a struct/enum with fields/members on both sides; an ordinary function/
    variable redeclaration goes through :func:`_merge_functions`/
    :func:`_merge_variables` instead, which have their own param-default/
    value unions alongside this same ``deprecated`` treatment).

    ``deprecated`` is blanked out of the equality check by
    :func:`_blank_provenance` -- one TU seeing ``[[deprecated]]`` on an
    otherwise-identical redeclaration is a union, not a disagreement, the
    same "ordinary redeclaration" case :func:`_with_more_public_provenance`
    already handles for the forward-declaration/definition pair (Codex
    review, PR #635 round 8) -- so it must be picked back explicitly here
    via :func:`_pick_deprecated`; two differing non-``None`` messages are
    not a conflict (round 13).
    """
    if _blank_provenance(a) != _blank_provenance(b):
        return None
    winner = _more_public_of(
        a,
        b,
        header_segs=header_segs,
        dir_segs=dir_segs,
        have_public_set=have_public_set,
    )
    other = b if winner is a else a
    other_is_private = _other_is_strictly_less_public(
        winner,
        other,
        header_segs=header_segs,
        dir_segs=dir_segs,
        have_public_set=have_public_set,
    )
    deprecated = _pick_deprecated(winner, other, secondary_is_private=other_is_private)
    return (
        winner
        if winner.deprecated == deprecated
        else replace(winner, deprecated=deprecated)  # type: ignore[type-var]
    )


def _merge_functions(
    a: Function,
    b: Function,
    *,
    header_segs: list[tuple[str, ...]],
    dir_segs: list[tuple[str, ...]],
    have_public_set: bool,
) -> Function | None:
    """Trivial-merge two same-``entity_key`` :class:`Function` declarations
    -- a plain redeclaration, or a difference confined to one or more
    parameters' ``default`` (ADR-050 D4's "declaration + redeclaration,
    differing only in an added default argument"). Any other difference
    (return type, params other than ``default``, virtuality, ...) is a
    genuine conflict -- **including two different non-``None`` defaults for
    the same parameter** (``f(int=1)`` vs. ``f(int=2)``): that is not "an
    added default argument", it is two TUs disagreeing on the default
    itself, and silently keeping one side would produce an arbitrary
    snapshot rather than surfacing the conflict (Codex review, PR #635) --
    **unless** the conflicting side is provably the strictly-less-public one
    (:func:`_other_is_strictly_less_public`), in which case the conflict is
    invisible to the library's actual public consumers and the public
    side's own default is kept instead of raising (Codex review, PR #635
    round 18; this check is therefore deferred until after ``base``/
    ``other`` below are known, rather than run unconditionally up front).
    """
    if len(a.params) != len(b.params):
        return None
    # Parameter *names* are not part of a C/C++ function's type -- `void
    # f(int value);` and `void f(int n);` are the identical declaration,
    # redeclared with cosmetically different names (both castxml and clang
    # preserve whatever the header spells) -- so they're blanked here
    # alongside `default`, the same "not ABI-relevant, don't let it block a
    # routine cross-TU redeclaration" treatment (Codex review, PR #635).
    # `contract_attributes` is blanked for the same reason as `deprecated`
    # below -- an additive attribute (e.g. `[[nodiscard]]`) one TU's
    # redeclaration carries and another doesn't is routine, not a
    # disagreement (Codex review, PR #635 round 11) -- and unioned back in
    # explicitly afterwards via `_merge_contract_attributes`.
    # Only the *comparison* ignores these -- the merged declaration's own
    # parameter names come from `base` below, not blanked ones.
    # replace_with_fact_sync (not raw replace()) keeps contract_attributes_fact
    # consistent with the blanked None value on both sides -- a raw replace()
    # would carry each side's own, possibly-differing, ORIGINAL fact forward
    # unchanged, which could make an otherwise-trivial redeclaration compare
    # unequal purely from Fact-status noise unrelated to any real field
    # (ADR-063 Phase 5).
    a_bare = _blank_provenance(
        replace_with_fact_sync(
            a,
            params=[replace(p, name="", default=None) for p in a.params],
            contract_attributes=None,
        )
    )
    b_bare = _blank_provenance(
        replace_with_fact_sync(
            b,
            params=[replace(p, name="", default=None) for p in b.params],
            contract_attributes=None,
        )
    )
    if a_bare != b_bare:
        return None
    attrs_ok, contract_attributes = _merge_contract_attributes(
        a.contract_attributes, b.contract_attributes
    )
    if not attrs_ok:
        return None
    base = _more_public_of(
        a,
        b,
        header_segs=header_segs,
        dir_segs=dir_segs,
        have_public_set=have_public_set,
    )
    # Parameter names (and every other non-default field) must come from
    # `base` -- the side actually selected as the merged declaration's
    # provenance/representative -- never unconditionally from `a`. Building
    # them from `a` regardless of which side won would attribute the
    # winning (possibly public) declaration's identity to a bare parameter
    # list still spelled the way the *other*, possibly-private, side spelled
    # it -- diff_symbols.py treats a header-backed parameter rename as
    # PARAM_RENAMED, so this could fabricate a false API-break finding
    # purely from which TU happened to sort first (Codex review, PR #635
    # round 4).
    other = b if base is a else a
    # `other_is_private` gates every "which side's optional fact wins"
    # decision below: a default argument, contract attribute, or
    # deprecation message that exists *only* on the provably-less-public
    # side must not leak onto the public-representative merged declaration
    # (Codex review, PR #635 rounds 17-18) -- see
    # `_other_is_strictly_less_public`.
    other_is_private = _other_is_strictly_less_public(
        base,
        other,
        header_segs=header_segs,
        dir_segs=dir_segs,
        have_public_set=have_public_set,
    )
    # Two different non-`None` defaults for the same parameter is a genuine
    # conflict (see the docstring above) -- unless `other` is provably the
    # private side, in which case its conflicting default is invisible to
    # public consumers and simply discarded in favor of `base`'s own,
    # rather than aborting the merge (Codex review, PR #635 round 18).
    if not other_is_private:
        for p_base, p_other in zip(base.params, other.params, strict=True):
            if (
                p_base.default is not None
                and p_other.default is not None
                and p_base.default != p_other.default
            ):
                return None
    # `deprecated` is blanked by `_blank_provenance` above (not required to
    # match for an ordinary redeclaration), and two differing non-`None`
    # messages are not a conflict (Codex review, PR #635 round 13) -- see
    # `_pick_deprecated`. A message only `other` carries must not leak onto
    # `base` when `other` is private (round 18).
    deprecated = _pick_deprecated(base, other, secondary_is_private=other_is_private)
    # A contract attribute only `other` declares must likewise not leak
    # onto `base` when `other` is private -- a private-only redeclaration's
    # `[[nodiscard]]` isn't visible to public callers either, and later
    # removing it would surface as a false
    # `FUNC_CONTRACT_ATTRIBUTE_REMOVED` against the public surface (Codex
    # review, PR #635 round 18). Calling-convention tokens are exempt: they
    # describe the actual compiled function's ABI regardless of which
    # header happens to spell them, so `_merge_contract_attributes`'s
    # conflict validation above already ran unconditionally, and the
    # (still real, still ABI-relevant) fact is kept either way.
    if other_is_private:
        contract_attributes = _suppress_private_only_attributes(
            contract_attributes, base.contract_attributes, other.contract_attributes
        )
    # A default argument only `other` declares must NOT be pulled onto
    # `base` when `other` is definitively the less-public side -- a default
    # argument grants callers a real capability (calling without that
    # parameter), and a private-only redeclaration adding one does not
    # extend that capability to the library's actual public consumers, who
    # never see it (Codex review, PR #635 round 17).
    merged_params: list[Param] = [
        replace(
            p_base,
            default=p_base.default
            if (p_base.default is not None or other_is_private)
            else p_other.default,
        )
        for p_base, p_other in zip(base.params, other.params, strict=True)
    ]
    return replace_with_fact_sync(
        base,
        params=merged_params,
        deprecated=deprecated,
        contract_attributes=contract_attributes,
        # `replace_with_fact_sync`'s own blanket "derive Fact.present(value)"
        # rule is wrong here specifically when the merge left
        # `contract_attributes` at `None`: per `_merge_contract_attributes`'s
        # own docstring, `None` means "neither side captured this", not
        # "confirmed no attributes" -- so the merged fact must stay
        # NOT_COLLECTED, never a fabricated PRESENT(None) (Codex review, PR
        # #982).
        contract_attributes_fact=(
            Fact.not_collected()
            if contract_attributes is None
            else Fact.present(contract_attributes)
        ),
    )


def _merge_variables(
    a: Variable,
    b: Variable,
    *,
    header_segs: list[tuple[str, ...]],
    dir_segs: list[tuple[str, ...]],
    have_public_set: bool,
) -> Variable | None:
    """Trivial-merge two same-``entity_key`` :class:`Variable` declarations
    -- identical (modulo provenance), or differing only in ``value`` (an
    ``extern`` declaration in one TU paired with the defining, initialized
    redeclaration in another). Two different non-``None`` values is a
    genuine conflict, the variable analogue of :func:`_merge_functions`'s
    conflicting-default-argument check -- **unless** the conflicting side
    is provably the strictly-less-public one
    (:func:`_other_is_strictly_less_public`), the same visibility carve-out
    :func:`_merge_functions` applies to its own conflicting-default check
    (Codex review, PR #635 round 18).
    """
    a_bare = _blank_provenance(replace(a, value=None))
    b_bare = _blank_provenance(replace(b, value=None))
    if a_bare != b_bare:
        return None
    base = _more_public_of(
        a,
        b,
        header_segs=header_segs,
        dir_segs=dir_segs,
        have_public_set=have_public_set,
    )
    other = b if base is a else a
    # `other_is_private` gates both the conflicting-`value` check and the
    # `deprecated` union below, mirroring `_merge_functions` (Codex review,
    # PR #635 round 18).
    other_is_private = _other_is_strictly_less_public(
        base,
        other,
        header_segs=header_segs,
        dir_segs=dir_segs,
        have_public_set=have_public_set,
    )
    if (
        not other_is_private
        and base.value is not None
        and other.value is not None
        and base.value != other.value
    ):
        return None
    # A `value` only `other` declares must NOT be pulled onto `base` when
    # `other` is definitively the less-public side -- an initializer only a
    # private redeclaration provides isn't visible to public consumers
    # either (the same leak `_merge_functions`'s default-argument union was
    # fixed against in round 17).
    value = base.value if (base.value is not None or other_is_private) else other.value
    # `deprecated` is blanked by `_blank_provenance` above (not required to
    # match), and two differing non-`None` messages are not a conflict
    # (Codex review, PR #635 round 13) -- see `_pick_deprecated`. A message
    # only `other` carries must not leak onto `base` when `other` is
    # private (round 18).
    deprecated = _pick_deprecated(base, other, secondary_is_private=other_is_private)
    return replace(base, value=value, deprecated=deprecated)


def _record_kinds_compatible(a_kind: str, b_kind: str) -> bool:
    """Whether a forward declaration's class-key is compatible with the
    other side's -- either the definition's, for
    :func:`_merge_types`'s opaque branches, or (potentially) another
    declaration's.

    Equal kinds are always compatible. ``struct``/``class`` are otherwise
    interchangeable: C++ allows forward-declaring a type with one class-key
    and defining it with the other (they're the same underlying entity,
    differing only in default member access/inheritance -- both GCC and
    Clang accept this, even under ``-pedantic-errors``). ``union`` is a
    genuinely different type category and never interchangeable with
    either (Codex review, PR #635 round 4).
    """
    return a_kind == b_kind or {a_kind, b_kind} <= {"struct", "class"}


def _merge_record_alignment(
    a_alignment: int | None, b_alignment: int | None
) -> tuple[bool, int | None]:
    """Union two :class:`RecordType` declarations' ``alignment_bits`` --
    the alignment analogue of :func:`_pick_deprecated`/every other
    optional-fact union in this module (``Param.default``,
    ``Variable.value``, ``contract_attributes``): at most one side
    committing to a non-``None`` value, or both agreeing, merges to
    whichever side actually captured the fact; two different non-``None``
    values are a genuine ABI conflict, returned as ``(False, None)``
    (Codex review, PR #635 round 15).

    Unlike ``fields``/``bases``/``is_abstract`` -- which castxml only
    populates for a complete definition -- ``alignment_bits`` is captured
    from castxml's ``align`` XML attribute *unconditionally*, including for
    an opaque/incomplete record (`abicheck/dumper_castxml.py`'s
    ``_build_record_type``), because an explicit
    ``__attribute__((aligned(N)))`` on a bare forward declaration is itself
    an ABI-relevant fact independent of the member layout: ``struct
    __attribute__((aligned(16))) X;`` in one TU and a naturally
    4-byte-aligned ``struct X { ... };`` definition in another are not
    interchangeable redeclarations of the same type. Merely checking
    compatibility and keeping the merge's chosen representative unchanged
    (round 15's first cut) isn't enough on its own: when the
    representative's own ``alignment_bits`` is ``None`` and the other
    side's is a real, captured value, that captured fact must still
    survive onto the merged result, not be silently dropped just because
    it happened to belong to the "losing" side of the provenance choice
    (Codex review, PR #635 round 16).
    """
    if a_alignment is None:
        return True, b_alignment
    if b_alignment is None:
        return True, a_alignment
    return a_alignment == b_alignment, a_alignment


def _merge_types(
    a: RecordType,
    b: RecordType,
    *,
    header_segs: list[tuple[str, ...]],
    dir_segs: list[tuple[str, ...]],
    have_public_set: bool,
) -> RecordType | None:
    """Trivial-merge two same-``entity_key`` :class:`RecordType` declarations
    -- ADR-050 D4's canonical "forward-declaration + definition" case:
    ``is_opaque`` (incomplete, no fields) paired with a complete definition
    merges to the definition, **provided the two class-keys are
    compatible** (see :func:`_record_kinds_compatible`) -- a `union X;`
    forward declaration is not compatible with a `struct X { ... };`
    definition even though both key on the bare name ``X`` (Codex review,
    PR #635), but `class X;` followed by `struct X { ... };` *is* valid,
    ordinary C++ (both compilers accept it -- ``class``/``struct`` are the
    same underlying entity, differing only in default member
    access/inheritance; round 4 of the same review). The definition's
    structural facts (fields, size, ...) always win, but its *provenance*
    prefers whichever side classifies as public (:func:`_with_more_public_provenance`)
    -- a public header commonly forward-declares a type whose full
    definition lives only in a private implementation header, and the
    merged entity must still read as public. Two complete (non-opaque)
    definitions must be identical (modulo provenance) to merge; if they
    disagree, that is a genuine ODR conflict.

    Two forward declarations that are *both* opaque merge the same way,
    checked only for class-key compatibility -- neither side has fields to
    prefer over the other's, so there is no "definition wins" side, but
    ``class X;`` in one TU and ``struct X;`` in another is exactly as valid
    a pair of redeclarations as ``class X;`` paired with a `struct X {
    ... };` definition, and was previously rejected as
    ``INCONSISTENT_DECLARATION`` purely because the class-key-compatibility
    check below was only reached when exactly one side was opaque (Codex
    review, PR #635 round 7). When the two (compatible) class-keys differ,
    the survivor's ``kind`` is picked by comparing the two spellings
    directly (``min("class", "struct")``), never by which side happens to
    be ``a``/``b`` -- passing ``a`` unconditionally would make the merged
    ``kind`` depend on which TU's name sorts first, so adding a third TU
    with an alphabetically-earlier ``tu_name`` that redundantly forward-
    declares the same type with the other (still-compatible) class-key
    would flip the reported ``kind`` between two snapshots that describe
    the identical set of declarations, producing a spurious
    ``SOURCE_LEVEL_KIND_CHANGED`` between them (Codex review, PR #635
    round 8).
    """
    if a.is_opaque and b.is_opaque:
        if not _record_kinds_compatible(a.kind, b.kind):
            return None
        alignment_ok, alignment_bits = _merge_record_alignment(
            a.alignment_bits, b.alignment_bits
        )
        if not alignment_ok:
            return None
        winner, loser = (a, b) if a.kind <= b.kind else (b, a)
        merged = _with_more_public_provenance(
            winner,
            loser,
            header_segs=header_segs,
            dir_segs=dir_segs,
            have_public_set=have_public_set,
        )
        return (
            merged
            if merged.alignment_bits == alignment_bits
            else replace(merged, alignment_bits=alignment_bits)
        )
    if a.is_opaque and not b.is_opaque:
        if not _record_kinds_compatible(a.kind, b.kind):
            return None
        alignment_ok, alignment_bits = _merge_record_alignment(
            a.alignment_bits, b.alignment_bits
        )
        if not alignment_ok:
            return None
        merged = _with_more_public_provenance(
            b,
            a,
            header_segs=header_segs,
            dir_segs=dir_segs,
            have_public_set=have_public_set,
        )
        return (
            merged
            if merged.alignment_bits == alignment_bits
            else replace(merged, alignment_bits=alignment_bits)
        )
    if b.is_opaque and not a.is_opaque:
        if not _record_kinds_compatible(a.kind, b.kind):
            return None
        alignment_ok, alignment_bits = _merge_record_alignment(
            a.alignment_bits, b.alignment_bits
        )
        if not alignment_ok:
            return None
        merged = _with_more_public_provenance(
            a,
            b,
            header_segs=header_segs,
            dir_segs=dir_segs,
            have_public_set=have_public_set,
        )
        return (
            merged
            if merged.alignment_bits == alignment_bits
            else replace(merged, alignment_bits=alignment_bits)
        )
    return _merge_identical_modulo_provenance(
        a,
        b,
        header_segs=header_segs,
        dir_segs=dir_segs,
        have_public_set=have_public_set,
    )


def _merge_enums(
    a: EnumType,
    b: EnumType,
    *,
    header_segs: list[tuple[str, ...]],
    dir_segs: list[tuple[str, ...]],
    have_public_set: bool,
) -> EnumType | None:
    """Trivial-merge two same-``entity_key`` :class:`EnumType` declarations
    -- the enum analogue of :func:`_merge_types`: an underlying-type-only
    forward declaration (no ``members``) paired with the full definition
    merges to the definition, **provided the two agree on
    ``underlying_type``/``is_scoped``** -- ``enum E : int;`` is not
    compatible with ``enum E : unsigned { X };`` even though the forward
    declaration has no members to compare (Codex review, PR #635). Like
    :func:`_merge_types`, the definition's ``members`` always win but its
    provenance prefers whichever side is public
    (:func:`_with_more_public_provenance`). Two non-empty member lists must
    agree (modulo provenance) to merge.
    """
    if not a.members and b.members:
        if a.underlying_type != b.underlying_type or a.is_scoped != b.is_scoped:
            return None
        return _with_more_public_provenance(
            b,
            a,
            header_segs=header_segs,
            dir_segs=dir_segs,
            have_public_set=have_public_set,
        )
    if not b.members and a.members:
        if a.underlying_type != b.underlying_type or a.is_scoped != b.is_scoped:
            return None
        return _with_more_public_provenance(
            a,
            b,
            header_segs=header_segs,
            dir_segs=dir_segs,
            have_public_set=have_public_set,
        )
    return _merge_identical_modulo_provenance(
        a,
        b,
        header_segs=header_segs,
        dir_segs=dir_segs,
        have_public_set=have_public_set,
    )
