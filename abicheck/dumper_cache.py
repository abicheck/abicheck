# SPDX-License-Identifier: Apache-2.0
"""Best-effort AST cache path helpers."""

from __future__ import annotations

import contextvars
import json
import logging
import os
import shutil
import sys
import tempfile
import threading
from collections.abc import Callable, Iterator
from concurrent.futures import Future, TimeoutError as FutureTimeoutError
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar, cast

if TYPE_CHECKING:
    from xml.etree.ElementTree import Element

from defusedxml import ElementTree as DefusedET

from . import deadline
from .storage.acyclic_json import gc_paused
from .storage.ast_size_observer import report_ast_size
from .storage.derived_ast import offer_derived_ast_source

log = logging.getLogger(__name__)

#: Whether the calling thread is inside a scope where writing to the AST
#: memo is actually worthwhile (mirrors ``deadline.py``'s own
#: ``contextvars.ContextVar`` propagation pattern). Default ``False``: a
#: direct :func:`abicheck.dumper.dump` caller with no downstream
#: ``service._attach_header_graph`` consumer (``appcompat.
#: check_app_compatibility``, a direct Python-API caller selecting the
#: clang backend) would otherwise memoize an AST nothing will ever pop,
#: holding a potentially multi-GB tree indefinitely for no benefit (Codex
#: review). ``service.run_dump`` activates this around its own primary
#: ELF/PE/Mach-O dump call, since that is the one shape where
#: ``_attach_header_graph`` really does follow and consume the memo.
_ast_memoize_scope: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "_ast_memoize_scope", default=False
)

#: The single pending AST handoff for the *current thread*, or ``None``.
#: ``(backend, key, root)`` -- a plain per-thread slot (``contextvars``),
#: never a shared dict keyed only by content hash (Codex review, fresh
#: evidence): ``service.compare``'s default worker pool
#: (``ABICHECK_PARALLEL_EXTRACTION``) dumps the old and new sides
#: *concurrently* in separate threads, and the AST cache key has no
#: binary/side component -- comparing two versions against the *same*
#: ``-H`` header set (the common case) makes both sides' primary passes
#: compute the identical key. A shared dict would let one side's
#: single-consumption pop steal the other side's entry (or one side's
#: fresh write silently clobber the other's, mid-flight), so one graph
#: pass falls back to a full disk re-read/re-parse regardless -- the exact
#: regression this reuse exists to remove, on top of a plausible
#: wrong-AST hazard if the two sides' headers ever do differ. A per-thread
#: slot sidesteps the whole class of bug structurally: each worker thread
#: only ever sees its own write, never another thread's, no matter what
#: the two computed keys happen to be. Does not cross a
#: ``ThreadPoolExecutor`` boundary on its own (same caveat as
#: ``deadline``'s own ContextVar) -- that's exactly the point here, not a
#: gap: each pooled worker thread gets its own independent slot.
_ast_memo_slot: contextvars.ContextVar[tuple[str, str, Any] | None] = (
    contextvars.ContextVar("_ast_memo_slot", default=None)
)

_T = TypeVar("_T")


#: How many distinct identity-keyed context groups (in practice: parsed AST
#: roots and everything derived from one) a request keeps resident at once.
#: Beyond this the least-recently-used group is released *whole* -- see
#: :meth:`AstAcquisitionScope.retain` for why it must be whole, and why
#: releasing is safe at all.
#:
#: The bound is deliberately generous rather than tuned: releasing a group
#: costs a re-parse, so a value too low trades a large amount of time for a
#: little memory. It exists to stop a long fan-out over many distinct header
#: sets from growing without limit, not to keep the resident set small.
MAX_RETAINED_CONTEXT_GROUPS = 8

#: How many *ungrouped* completed acquisition results a request keeps
#: resident at once.
#:
#: The group bound above covers only entries created with a ``group=``,
#: i.e. keys derived from ``id()`` of a context object. The other kind --
#: a content-derived effective-AST-cache key, which is how ``dumper.py``
#: acquires the raw parsed root itself -- was never bounded at all: its
#: ``Future`` holds the parsed root as its *result*, so every distinct
#: header set a release fan-out touched kept one full raw AST alive in
#: ``_entries`` for the life of the request, however long ago its last
#: consumer finished.
#:
#: That is not a small residual. A measured six-member release retained 24
#: raw roots this way while ``group_stats()`` reported the expected eight
#: retained groups and sixteen releases -- the bookkeeping was correct and
#: described the wrong half of the table. Evicting the completed ungrouped
#: entries took the live root count to eight.
#:
#: Releasing one is safe for the same reason releasing a group is: every
#: entry here is a pure cache, and a later consumer re-runs the producer
#: for an equal result. It is safe *in particular* against the id-reuse
#: hazard :meth:`AstAcquisitionScope.retain` exists to prevent, because a
#: group holds its own strong reference to the object it is keyed on --
#: dropping a content-keyed entry that happens to hold the same object
#: cannot free it while any id-keyed entry still mentions it.
#:
#: Bounded rather than flushed. Flushing between members was measured and
#: rejected: it reparsed (18 legacy model constructions became 28) without
#: reducing the peak, because the peak is one member's own working set, not
#: the accumulation. An LRU keeps the shared-header case -- every member
#: parsing the *same* header set -- at exactly one resident root, while a
#: fan-out over genuinely distinct header sets stops accumulating.
MAX_RETAINED_RAW_ENTRIES = 4


class AstAcquisitionScope:
    """Request-owned, per-key coordination for expensive header acquisition.

    Values are retained only for the lifetime of the owning dump/compare
    request.  The lock protects the small future table; producers run after
    releasing it, so unrelated compiler contexts proceed independently.
    ``Future`` gives waiters the producer's exact completion/failure while a
    cancelled waiter cannot cancel work another member still needs.

    **Why anything is retained at all.** Some acquisition keys are derived
    from ``id()`` of a parsed AST root (``dumper_clang``'s template-parameter
    indexes, ``extract/header_ast_fields``' neutral ``SemanticIR``
    normalization). CPython reuses an address once its object is freed, so a
    live entry keyed on a dead object's id could be served for a *different*
    root that happened to land at the same address -- a silent wrong answer,
    not a slow one. ``retain`` is what makes that impossible, by keeping the
    object alive as long as any key mentioning its id exists.

    That guarantee used to be bought with unbounded growth: every retained
    root stayed resident for the whole request, so a release fan-out over N
    distinct header sets held N parsed ASTs at once and never released one,
    however long ago its last consumer finished. The retention is now
    *grouped*: an entry created under a group is recorded with it, and a
    group is released as a unit -- object and derived entries together, under
    the lock -- so no key mentioning a freed id can ever survive its object.
    Releasing is otherwise harmless because every entry here is a pure
    cache: a later consumer re-runs the producer and gets an equal result,
    paying time rather than reading something wrong.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: dict[tuple[str, str], Future[Any]] = {}
        #: id -> (object, keys created under it). Insertion order is the LRU
        #: order; a group is refreshed on every use.
        self._groups: dict[int, tuple[Any, set[tuple[str, str]]]] = {}
        #: Keys with no owning group, in least-recently-used order. Bounded
        #: separately by MAX_RETAINED_RAW_ENTRIES -- see that constant.
        self._ungrouped: dict[tuple[str, str], None] = {}
        self.released_groups = 0
        self.released_entries = 0

    def retain(self, value: Any) -> None:
        """Keep an identity-keyed context object alive for this request.

        Idempotent per object: this is called once per *member* parse, and
        single-flight means many members legitimately share one root, so an
        appending list grew with the member count while holding the same few
        objects.
        """

        with self._lock:
            token = self._touch_group_locked(value)
            self._evict_groups_locked(protect=token)

    def _touch_group_locked(self, value: Any) -> int:
        """Create or refresh *value*'s group, returning its token.

        Re-inserting an existing group moves it to the most-recently-used
        end while preserving the keys already recorded under it, which is
        what makes the bound an LRU rather than a FIFO: the root a fan-out
        keeps coming back to must not be evicted on schedule and reparsed.
        """
        token = id(value)
        existing = self._groups.pop(token, None)
        keys = existing[1] if existing is not None else set()
        # Re-inserting moves it to the most-recently-used end.
        self._groups[token] = (value, keys)
        return token

    def _evict_groups_locked(self, protect: int | None = None) -> None:
        """Release least-recently-used groups until the bound is met.

        *protect* is the group the caller is in the middle of populating,
        and it must be exempt. Least-recently-used order alone is not
        enough: when every older candidate holds an in-flight producer the
        scan falls through to the newest group -- the caller's own -- and
        releasing it drops the group while ``run`` goes on to create the
        entry, leaving a key mentioning a no-longer-retained object's id.
        That is the same orphan the release policy exists to prevent, and
        it is *not* hypothetical: an earlier version of this method
        reasoned that the just-touched group "is the most recent and can
        never evict itself", which is true only while some older group is
        releasable. The table-wide invariant test caught it.
        """
        while len(self._groups) > MAX_RETAINED_CONTEXT_GROUPS:
            for token in list(self._groups):
                if token == protect:
                    continue
                if self._release_group_locked(token):
                    break
            else:
                # Nothing releasable: every candidate has an in-flight
                # producer (or is the caller's own). Exceeding the bound is
                # the correct outcome -- the alternative is a wrong answer
                # rather than a large one -- and it is reclaimed as soon as
                # a producer finishes.
                return

    def _release_group_locked(self, token: int) -> bool:
        """Drop one group's object *and* every entry keyed off its id.

        Both halves or neither, and that is load-bearing. Dropping the
        object while leaving an id-mentioning entry behind is the exact
        hazard ``retain`` exists to prevent: the object can then be freed,
        a new one can land on its address, and ``run`` will serve it the
        stale entry -- a wrong answer, and an unrecoverable one, since the
        key set that would have identified the orphan went with the group.

        So a group holding an *in-flight* entry is not released at all, and
        this returns ``False``. An in-flight Future cannot be discarded (a
        waiter is blocked on that exact object and would wait forever for a
        result nobody will set), and if it is kept then its key mentions
        this object's id, so the object must be kept too. An earlier
        version of this method popped the group unconditionally and dropped
        only the *completed* entries, which is precisely that hazard --
        caught in review, not by the test that asserted the surviving
        Future and never noticed the object had stopped being retained.
        """
        entry = self._groups.get(token)
        if entry is None:
            return False
        _obj, keys = entry
        if any(
            (future := self._entries.get(key)) is not None and not future.done()
            for key in keys
        ):
            return False
        del self._groups[token]
        for key in keys:
            self._entries.pop(key, None)
            self._ungrouped.pop(key, None)
        self.released_groups += 1
        return True

    def _touch_ungrouped_locked(self, lookup: tuple[str, str]) -> None:
        """Mark *lookup* as ungrouped and most-recently-used."""
        self._ungrouped.pop(lookup, None)
        self._ungrouped[lookup] = None

    def _grouped_keys_locked(self) -> set[tuple[str, str]]:
        keys: set[tuple[str, str]] = set()
        for _obj, group_keys in self._groups.values():
            keys |= group_keys
        return keys

    def _evict_ungrouped_locked(self, protect: tuple[str, str] | None = None) -> None:
        """Drop least-recently-used *completed* ungrouped entries.

        Three things are never evicted, and each is load-bearing:

        * an entry a producer is still running (``not future.done()``) -- a
          waiter is blocked on that exact ``Future`` object and would wait
          for a result nobody would set;
        * *protect*, the caller's own entry, which is about to be published
          and whose eviction would make this call's single-flight
          guarantee vacuous for a concurrent sibling;
        * a key some group also owns. A key is never in both tables today,
          but if one ever were, the group's release is what keeps the
          id-keyed invariant, and quietly removing the entry from under it
          would strand the group's bookkeeping.

        Unlike a group release this drops only the entry, never an object:
        an ungrouped key is content-derived, so nothing is keyed on the
        result's identity and there is nothing to keep alive. The result
        becomes collectable once its real consumers let go, which is the
        whole point.
        """
        if len(self._ungrouped) <= MAX_RETAINED_RAW_ENTRIES:
            return
        grouped = self._grouped_keys_locked()
        for key in list(self._ungrouped):
            if len(self._ungrouped) <= MAX_RETAINED_RAW_ENTRIES:
                return
            if key == protect or key in grouped:
                continue
            future = self._entries.get(key)
            if future is not None and not future.done():
                continue
            self._entries.pop(key, None)
            del self._ungrouped[key]
            self.released_entries += 1

    def group_stats(self) -> dict[str, int]:
        """Counts for a memory trace: retained groups, entries, releases.

        Exists so a trace can attribute request-resident memory to *shared
        raw ASTs* rather than to per-member evidence or output buffers --
        the distinction a Python CPU profile cannot make and the one that
        decides which of the two is worth compacting next.
        """
        with self._lock:
            return {
                "retained_groups": len(self._groups),
                "entries": len(self._entries),
                "released_groups": self.released_groups,
                "retained_raw_entries": len(self._ungrouped),
                "released_raw_entries": self.released_entries,
            }

    def run(
        self, backend: str, key: str, producer: Callable[[], _T], group: Any = None
    ) -> _T:
        lookup = (backend, key)
        with self._lock:
            if group is not None:
                # Record the association *before* the entry exists, so a
                # release can never see a key it does not know the owner of.
                token = self._touch_group_locked(group)
                self._groups[token][1].add(lookup)
                # The group owns this key now; it must not also sit in the
                # ungrouped LRU, whose eviction knows nothing about the
                # object identity this key is derived from.
                self._ungrouped.pop(lookup, None)
                # Evict here too, not only in `retain`: an id-keyed entry
                # can be the *first* thing a group is created by, so
                # bounding only the `retain` path left `run`-created groups
                # growing without limit. `protect` keeps this group, whose
                # entry is created just below, out of the candidate set.
                self._evict_groups_locked(protect=token)
            future = self._entries.get(lookup)
            if future is None:
                future = Future()
                self._entries[lookup] = future
                owns_production = True
            else:
                owns_production = False
            if group is None:
                # A content-keyed entry: refresh its LRU position (so a
                # header set every member shares is never the one evicted)
                # and bound the ungrouped half of the table.
                self._touch_ungrouped_locked(lookup)
                self._evict_ungrouped_locked(protect=lookup)
        if not owns_production:
            deadline.check()
            left = deadline.remaining()
            try:
                result = future.result(timeout=left)
            except FutureTimeoutError:
                if future.done():
                    return cast("_T", future.result())
                # Do not cancel the shared producer: another member may still
                # need it.  The waiter's own request deadline is nevertheless
                # authoritative and must bound queue time as well as compiler
                # time.
                left_after = deadline.remaining()
                raise deadline.DeadlineExceeded(
                    left_after if left_after is not None else 0.0
                )
            deadline.check()
            return cast("_T", result)
        try:
            result = producer()
        except BaseException as exc:
            future.set_exception(exc.with_traceback(None))
            # A failed acquisition is retryable within the request.  Removing
            # only our own entry avoids racing a later successful producer.
            with self._lock:
                if self._entries.get(lookup) is future:
                    del self._entries[lookup]
                self._ungrouped.pop(lookup, None)
                # A settled producer can make its group releasable, so retry
                # the bound here -- see `set_result` below for why.
                self._evict_groups_locked()
                self._evict_ungrouped_locked()
            raise
        future.set_result(result)
        with self._lock:
            # Retry the bound now that this producer has settled. Admission
            # alone cannot enforce it: a release fan-out starts many members
            # at once, so at admission time *every* candidate group can hold
            # an in-flight Future, nothing is releasable, and the bound is
            # deliberately exceeded (correctness over size). Without this
            # retry that overflow persisted until scope exit unless some
            # later acquisition happened to come along -- so the peak AST
            # set stayed resident for the whole request, which is precisely
            # what the bound exists to prevent. `protect` is not passed:
            # this group's entry is already published, so it is an ordinary
            # eviction candidate like any other.
            self._evict_groups_locked()
            # Same argument for the ungrouped half, and it matters more
            # there: a fan-out admits many members at once, so at admission
            # time every older candidate can still be in flight and nothing
            # is releasable. Without this retry the raw roots stayed
            # resident for the whole request -- exactly the accumulation
            # MAX_RETAINED_RAW_ENTRIES exists to stop.
            self._evict_ungrouped_locked()
        return result


_ast_acquisition_scope: contextvars.ContextVar[AstAcquisitionScope | None] = (
    contextvars.ContextVar("_ast_acquisition_scope", default=None)
)


@contextmanager
def ast_acquisition_scope() -> Iterator[AstAcquisitionScope]:
    """Install one request-local shared acquisition table.

    Nested workflow layers reuse the existing table rather than hiding it.
    Context copies used by release workers consequently share the same table.
    """

    existing = _ast_acquisition_scope.get()
    if existing is not None:
        yield existing
        return
    scope = AstAcquisitionScope()
    token = _ast_acquisition_scope.set(scope)
    try:
        yield scope
    finally:
        _ast_acquisition_scope.reset(token)


def run_ast_acquisition(
    backend: str, key: str, producer: Callable[[], _T], group: Any = None
) -> _T:
    """Run *producer* once per request and effective AST cache key.

    *producer* must cover **every** source of that AST -- a warm disk-cache
    decode as much as a real frontend run. A cheap path placed ahead of this
    call still returns an equal value, so nothing observable breaks, but it
    returns a *separate object* each time: the decode repeats per member and
    per worker group, identity-keyed downstream work repeats with it
    (``extract/header_ast_fields.py`` keys neutral ``SemanticIR``
    normalization on ``id(root)``), a follow-up consumer of the same artifact
    (``service._attach_header_graph``) re-reads the cache the primary pass
    just decoded, and every copy stays resident for the request. Whatever the
    producer *resolved* travels with the result too -- the post-retry language
    mode, the DPC++ frontend context, CastXML's selected compiler -- so a
    waiter never re-derives a stale answer of its own.

    Pass *group* whenever *key* is derived from ``id()`` of a context
    object: it ties this entry to that object's retention group, so the two
    are released together and no key mentioning a freed id can outlive it
    (see :class:`AstAcquisitionScope`). A content-derived *key* -- an
    effective AST cache key, say -- needs no group and should not pass one:
    it stays valid regardless of which objects are still resident.

    One consequence worth keeping in mind when changing a producer: because
    its exact parsed object is published to later consumers, a lossy
    transformation that used to be private to it no longer is (see
    ``dumper_clang_errors._streaming_prune_enabled``, which is disabled inside
    an acquisition scope for exactly that reason).
    """

    scope = _ast_acquisition_scope.get()
    if scope is None:
        return producer()
    return scope.run(backend, key, producer, group=group)


def run_ast_acquisition_offering_entry(
    backend: str,
    key: str,
    entry_path: Path | Callable[[_T], Path],
    producer: Callable[[], _T],
) -> _T:
    """:func:`run_ast_acquisition` for a producer whose result is ``(ast, ...)``.

    A producer that runs reaches :func:`load_cached_ast`, which offers the
    cache entry to an open ``derived_ast_scope``. A result served from the
    request's retained table skips that call -- so the final derived-AST
    consumer (the header-graph attach) was never told the entry path, could
    not take a stored sidecar, and had nowhere to store the projection it
    then computed. This makes the same offer the memo-slot hit makes, and
    substitutes the superseded marker for the tree when the consumer took it.
    """

    ran = False

    def _tracked() -> _T:
        nonlocal ran
        ran = True
        return producer()

    result = run_ast_acquisition(backend, key, _tracked)
    if ran:
        return result
    # The entry the producer actually wrote, which a result can determine:
    # clang's C->C++ self-heal caches under the retry mode's key, not the
    # requested one, and a sidecar must sit beside the real entry.
    #
    # Offered only while that entry exists: a result whose inputs changed
    # mid-acquisition is never written (`identities_stable`), and a sidecar
    # stored or read beside a missing entry could later be paired with a
    # different AST cached under that key.
    path = entry_path(result) if callable(entry_path) else entry_path
    if not path.is_file():
        return result
    superseded = offer_derived_ast_source(path, is_cache_entry=True, tree_in_hand=True)
    if superseded is None:
        return result
    return cast("_T", (superseded, *cast("tuple[Any, ...]", result)[1:]))


def resolve_request_memoization(memoize: bool | None) -> bool:
    """Whether a header-AST parse should write this thread's memo slot.

    ``None`` means "decide from context" -- :func:`ast_memoize_scope`'s own
    answer, the pre-existing default. A request-local acquisition scope then
    overrides it to ``False``: inside one, the request-keyed table returned by
    :func:`run_ast_acquisition` is *the* handoff to a later consumer, and the
    per-thread slot would be a second, never-consumed reference holding a
    potentially multi-GB tree for the life of the thread. Outside a scope the
    legacy one-shot handoff is untouched, so a direct ``dumper.dump()`` caller
    behaves exactly as before.
    """

    resolved = ast_memoize_active() if memoize is None else memoize
    return resolved and not ast_acquisition_active()


def ast_acquisition_active() -> bool:
    """Whether the current execution context shares request acquisition."""

    return _ast_acquisition_scope.get() is not None


def ast_acquisition_stats() -> dict[str, int] | None:
    """This request's acquisition-table counts, or ``None`` outside a scope.

    The attribution half of the memory work: a release peak has to be
    ascribed either to *shared raw ASTs* (this table) or to per-member
    evidence and output buffers, and nothing else can tell the two apart.
    Both halves of the table are reported -- the grouped, id-keyed one and
    the content-keyed one -- because reporting only the first is exactly how
    24 resident raw roots hid behind eight retained groups.
    """

    scope = _ast_acquisition_scope.get()
    return None if scope is None else scope.group_stats()


def retain_ast_context_object(value: Any) -> None:
    """Prevent ``id(value)`` reuse while request-local normalized facts exist."""

    scope = _ast_acquisition_scope.get()
    if scope is not None:
        scope.retain(value)


@contextmanager
def ast_memoize_scope() -> Iterator[None]:
    """Mark the current thread's AST parses as worth memoizing in-process.

    Clears this thread's pending slot if the scoped operation raises, since
    a failure partway through (e.g. snapshot construction fails after the
    header AST parse itself succeeded) means no downstream
    ``_attach_header_graph`` will ever run to consume it -- leaving it set
    would hold a potentially multi-GB tree in this thread for however long
    the thread itself lives afterward (Codex review).
    """
    active_token = _ast_memoize_scope.set(True)
    try:
        yield
    except BaseException:
        _ast_memo_slot.set(None)
        raise
    finally:
        _ast_memoize_scope.reset(active_token)


def ast_memoize_active() -> bool:
    """Whether :func:`ast_memoize_scope` is active on the calling thread."""
    return _ast_memoize_scope.get()


def store_cached_ast(key: str, backend: str, root: Any) -> None:
    """Memoize an already-parsed AST *root* for (*backend*, *key*) in the
    calling thread's own pending slot (see :data:`_ast_memo_slot`'s own
    docstring) -- overwrites whatever this thread's slot already held, if
    anything (there is at most one legitimate pending handoff at a time)."""
    _ast_memo_slot.set((backend, key, root))


def load_cached_ast(
    key: str,
    backend: str,
    cache_path: Path,
    *,
    memoize: bool = True,
    on_disk_load: Callable[[Any], Any] | None = None,
) -> Any | None:
    """Return a previously-parsed AST for (*backend*, *key*), or ``None``.

    *cache_path* is the caller's own already-resolved :func:`_cache_path`
    result, not recomputed here -- callers that patch ``_cache_path`` in
    their own module namespace (as several tests do) must have that
    override actually govern the disk path this function reads, which a
    second, independent ``_cache_path`` call from this module could not see.

    *on_disk_load* -- applied to a tree decoded from the disk cache, before
    it is memoized, and to nothing else: a memo-slot hit is the very object a
    previous caller already processed. The clang backend passes
    ``extract.headers.clang.locations.materialize_locations`` (a callback,
    since this storage-layer module may not import ``extract``).

    *memoize* -- ``False`` for a caller that is itself the *final* consumer
    of this AST (the header-graph attach step, when the primary snapshot
    pass used ``castxml`` and never wrote a memo entry of its own): a disk
    hit there has no further same-thread reader to hand off to, so setting
    this thread's slot would just hold a potentially multi-GB tree for no
    benefit (Codex review). The primary snapshot pass's own call keeps the
    default ``True`` -- that write *is* the intended handoff
    :func:`store_cached_ast`'s docstring describes.

    Checks this thread's own pending slot first (see :data:`_ast_memo_slot`'s
    own docstring) -- consuming it only when its ``(backend, key)`` matches
    the request (a mismatch means this thread's slot holds something
    unrelated, e.g. a stale leftover from an earlier, differently-shaped
    call in a reused worker thread; left alone rather than guessed at) --
    falling back to the on-disk cache, deadline-checked the same way the
    original inline disk-cache read was (once before the parse, so an
    already-exceeded deadline skips it, and once after, since parsing a
    huge cached AST can itself eat the rest of the budget). A
    corrupt/unreadable cache file is evicted, same as before. Never a
    staleness risk: this only ever returns a value this same thread already
    validated (from disk or a fresh parse) under this identical
    content-addressed key.

    A slot hit is itself deadline-checked too, even though it does no real
    work -- a caller relying on ``--budget``/``deadline.deadline_scope`` to
    bound the whole scan must see a consistently-enforced deadline on every
    path back out of this function, not just the ones that happen to be
    expensive (mirrors the disk-cache-hit contract PR #591 established).
    """
    slot = _ast_memo_slot.get()
    if slot is not None and slot[0] == backend and slot[1] == key:
        _ast_memo_slot.set(None)
        deadline.check()
        # The tree is already in hand, but a final consumer may still have a
        # stored derived form that is cheaper than projecting it -- and must
        # learn the entry path either way, or it can never store one.
        superseded = offer_derived_ast_source(
            cache_path, is_cache_entry=True, tree_in_hand=True
        )
        if superseded is not None:
            return superseded
        return slot[2]
    # A derived-artifact consumer gets the entry's path before anything is
    # read, which is the whole point: the parse this function would otherwise
    # do is the cost being avoided. Checked ahead of `cache_path.exists()`
    # because a derived entry is self-sufficient -- it stays valid for this
    # key even if the AST itself was evicted, and re-parsing then would be a
    # pure loss.
    superseded = offer_derived_ast_source(cache_path, is_cache_entry=True)
    if superseded is not None:
        deadline.check()
        return superseded
    if not cache_path.exists():
        return None
    deadline.check()
    try:
        text = cache_path.read_text(encoding="utf-8")
        with gc_paused():  # a tree has no cycles: storage.acyclic_json
            root = json.loads(text)
    except (ValueError, OSError):
        cache_path.unlink(missing_ok=True)
        return None
    report_ast_size(len(text))
    del text
    deadline.check()  # loading a huge cached AST can eat the rest of the budget
    if on_disk_load is not None:
        root = on_disk_load(root)
    if memoize:
        store_cached_ast(key, backend, root)
    return root


def read_cached_castxml(cached: Path) -> Element | None:
    """Parse a cached castxml XML tree, discarding the entry if it is unusable.

    Returns ``None`` (having unlinked *cached*) when the file cannot be parsed,
    so the caller falls through to a fresh run rather than failing on a
    truncated or corrupt cache entry. The CastXML counterpart of
    :func:`load_cached_ast`; lives here, next to it, rather than in
    ``dumper.py`` (which re-exports it under its historical private name).
    """
    try:
        root = DefusedET.parse(str(cached)).getroot()
    except Exception:
        root = None
    if root is None:
        cached.unlink(missing_ok=True)
        return None
    return cast("Element", root)


def _atomic_copy(src: Path, dst: Path) -> None:
    """Copy *src* into *dst* via a same-directory temp file + ``os.replace``.

    Same atomicity rationale as :func:`_atomic_write` (a concurrent reader
    never sees a torn file), but streams the copy (``shutil.copyfileobj``)
    instead of reading *src* fully into a Python ``bytes`` object first — the
    L2 clang AST-dump cache write is exactly the case this matters for: the
    JSON tree it is caching can be hundreds of MB to multiple GB for a
    pathological header (P0 SVS field report), and the caller already holds
    one in-memory copy of it (the parsed dict) — a second full-size ``bytes``
    copy just to write the cache would double peak memory for no reason.
    """
    fd, tmp_name = tempfile.mkstemp(
        dir=str(dst.parent), prefix=f".{dst.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "wb") as out, open(src, "rb") as inp:
            shutil.copyfileobj(inp, out)
        os.replace(tmp_name, dst)
    except OSError:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _atomic_write(path: Path, data: bytes) -> None:
    """Write *data* to *path* via a same-directory temp file + ``os.replace``.

    Plain ``open(path, "wb")``/``shutil.copy2`` can leave a torn file behind if
    two processes race to populate the same cache key (e.g. comparing two
    releases that share an unchanged header tree, with old/new extracted
    concurrently) — a reader would then see a partially-written file instead
    of a clean cache miss. ``os.replace`` is atomic on both POSIX and Windows,
    so a concurrent reader always sees either the old (absent) or the new
    (complete) file, never something in between.
    """
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp_name, path)
    except OSError:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _cache_path(key: str, backend: str = "castxml") -> Path:
    # One sub-directory + file extension per backend so castxml XML and clang
    # JSON caches live side by side without clashing.
    ext = "json" if backend == "clang" else "xml"
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA")
        cache_dir = (
            Path(local) / "abi_check" / backend
            if local
            else Path.home() / "AppData" / "Local" / "abi_check" / backend
        )
    else:
        xdg_cache = os.environ.get("XDG_CACHE_HOME")
        base = Path(xdg_cache) if xdg_cache else Path.home() / ".cache"
        cache_dir = base / "abi_check" / backend
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        fallback = Path(tempfile.gettempdir()) / "abi_check" / backend
        log.warning(
            "AST cache directory %s is unavailable (%s); using %s",
            cache_dir,
            exc,
            fallback,
        )
        fallback.mkdir(parents=True, exist_ok=True)
        cache_dir = fallback
    return cache_dir / f"{key}.{ext}"
