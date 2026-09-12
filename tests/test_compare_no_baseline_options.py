# SPDX-License-Identifier: Apache-2.0
"""Every ``compare`` option is wired to ``--no-baseline`` or declared unsupported.

The one defect this audit path has produced repeatedly is an option Click
parses, ``--help`` documents, and the command body never reads:
``--contract`` (a silently inert *gate*), ``--sources``/``--build-info``/
``--depth``/``--dry-run``, ``--write``, and
``--include-system-declarations`` — four separate rounds, each found by
reading the code rather than by a failing test, because a dropped option
produces no output at all to fail on.

Fixing each in turn leaves the *class* open: the next option added to
``compare`` inherits the same silence. This module inverts the rule so it
cannot recur. Every parameter `compare` declares must be in exactly one of:

* **read** by ``frontends/cli/commands/compare_no_baseline.py``;
* **consumed upstream**, before the dispatch ever sees it (the sided
  ``--header``/``--sources``/... families that
  ``cli_options.normalize_sided_options`` rewrites into per-side dests, and
  ``--view``, which ``parse_view_tokens`` expands);
* **declared unsupported** in that module's ``_UNSUPPORTED_OPTIONS``, which
  makes passing it a usage error rather than a no-op;
* **Click-level**, belonging to no command body (``--help``).

A new flag is therefore either wired or declared — never silent.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from abicheck.cli import main
from abicheck.frontends.cli.commands.compare_no_baseline import (
    _INERT_DESTS,
    _OLD_ONLY_DESTS,
    _SIDED_LABEL_DESTS,
    _SIDED_SINGLE_DESTS,
    _UNSUPPORTED_OPTIONS,
    _VIEW_DEFAULTS,
    _was_given,
)

#: The dispatch *pair*: the module that translates one invocation, and the
#: sibling that owns which invocations it refuses. Both are scanned, because
#: the exhaustiveness contract is about the path as a whole -- an option read
#: in either one is wired, and scanning only the first would have started
#: reporting false gaps the moment the rulings moved out of it.
_MODULES = tuple(
    Path(__file__).resolve().parent.parent / f"abicheck/frontends/cli/commands/{name}"
    for name in ("compare_no_baseline.py", "no_baseline_rulings.py")
)

#: Every destination ``cli_options.normalize_sided_options`` *generates*,
#: keyed by the raw parameter it replaces. ``compare_cmd`` calls that
#: function (and ``parse_view_tokens``) before dispatching, so the raw name
#: never reaches this module -- but the generated names do, and those are
#: what must be accounted for.
#:
#: Listing only the raw names here was a real hole, not a cosmetic one
#: (Codex review, P1): it made this test pass *because the option had been
#: renamed*, while the dispatch read none of the results. A bare
#: ``--dump-manifest`` was accepted and silently dropped -- even an invalid
#: manifest exited 0, auditing a different surface than the user asked for
#: -- and ``--version new=`` likewise. The generated destinations are now
#: expanded into the accounted-for set, so each one is separately either
#: read or declared unsupported.
#: Only the families ``compare`` actually declares -- it composes neither
#: ``--pdb`` nor the sided ``--ast-frontend``, so those destinations are
#: never generated for this command and listing them would assert against
#: a shape that does not exist here.
_NORMALIZED_DESTS: dict[str, tuple[str, ...]] = {
    "header": ("headers", "old_headers_only", "new_headers_only"),
    "dump_manifest": ("old_dump_manifest", "new_dump_manifest"),
    "include": ("includes", "old_includes_only", "new_includes_only", "include_labels"),
    "debug_root": ("debug_roots", "debug_roots_old", "debug_roots_new"),
    "sources": ("old_sources", "new_sources"),
    "build_info": ("old_build_info", "new_build_info"),
    "probe_matrix": ("probe_matrix_old", "probe_matrix_new"),
    "debug_info": ("debug_info1", "debug_info2"),
    "devel_pkg": ("devel_pkg1", "devel_pkg2"),
    "version": ("old_version", "new_version"),
    # Plan slice 7m: the one `-o FORMAT=DESTINATION` export request reaches
    # the callback as a single `exports` value and is expanded, before
    # dispatch, into the dest names every downstream consumer threads --
    # so each of those is separately either read here or declared
    # unsupported, exactly as this table's other entries are.
    "exports": ("fmt", "output", "secondary_writes", "output_dir"),
}

#: The raw names themselves, consumed before dispatch. Accounted for only
#: because :data:`_NORMALIZED_DESTS` accounts for what each becomes.
_CONSUMED_UPSTREAM = frozenset(_NORMALIZED_DESTS) | {"view"}

#: Click-level parameters that belong to no command body.
_CLICK_LEVEL = frozenset({"help", "help_all"})

#: Parameters the dispatch handles structurally rather than by name — the
#: two operands it consumes itself, and the flag that selects it.
_DISPATCH_OWNED = frozenset({"old_input", "new_input", "no_baseline"})

#: ``-v/--verbose`` is deliberately not in any bucket below: it configures
#: logging, not analysis, and is a no-op that misleads nobody.
_PRESENTATION_ONLY = frozenset({"verbose"})

#: Options declared with ``expose_value=False`` whose value is stashed on the
#: Click context by their own callback, so it never reaches ``kwargs`` and the
#: AST read-scan below cannot see it. They are guarded by
#: ``_reject_context_stashed_options``, and
#: :func:`test_every_context_stashed_option_is_really_rejected` drives the real
#: CLI to prove each one is -- without that, this bucket would be an escape
#: hatch that silently re-permits exactly the dropped-flag defect the module's
#: other tables exist to catch.
_CONTEXT_STASHED = frozenset({"variant"})


def _compare_params() -> set[str]:
    return {p.name for p in main.commands["compare"].params if p.name}


def _dests_read_by_module() -> set[str]:
    """Every kwargs dest the dispatch module actually reads.

    Parsed from the AST rather than by regex over the source, so a name that
    only appears in a docstring or comment (this module has many) is never
    miscounted as a real read — the exact mistake that would make this test
    pass while the option stayed dropped.
    """
    dests: set[str] = set()
    nodes = [
        node
        for module in _MODULES
        for node in ast.walk(ast.parse(module.read_text(encoding="utf-8")))
    ]
    for node in nodes:
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr not in ("get", "pop"):
            continue
        if not (isinstance(func.value, ast.Name) and func.value.id == "kwargs"):
            continue
        if node.args and isinstance(node.args[0], ast.Constant):
            value = node.args[0].value
            if isinstance(value, str):
                dests.add(value)
    return dests


def test_every_compare_option_is_wired_or_declared() -> None:
    """The exhaustiveness rule this module exists for."""
    accounted = (
        _dests_read_by_module()
        | set(_UNSUPPORTED_OPTIONS)
        | {dest for dests in _NORMALIZED_DESTS.values() for dest in dests}
        | set(_VIEW_DEFAULTS)
        | set(_OLD_ONLY_DESTS)
        | set(_SIDED_SINGLE_DESTS)
        | set(_SIDED_LABEL_DESTS)
        | _CONSUMED_UPSTREAM
        | _CLICK_LEVEL
        | _DISPATCH_OWNED
        | _PRESENTATION_ONLY
        | _CONTEXT_STASHED
        | set(_INERT_DESTS)
    )
    unaccounted = sorted(_compare_params() - accounted)
    assert not unaccounted, (
        "these `compare` options reach --no-baseline unaccounted for: "
        f"{unaccounted}. Wire each one in "
        "frontends/cli/commands/compare_no_baseline.py, or add it to that "
        "module's _UNSUPPORTED_OPTIONS so passing it is a usage error. An "
        "option that is neither is silently dropped, which is the defect "
        "this test exists to prevent."
    )


def test_no_unsupported_entry_is_stale() -> None:
    """A declared-unsupported entry must still name something reachable.

    Either a real `compare` parameter, or a destination
    ``normalize_sided_options`` generates from one -- the table has to hold
    both, since the dispatch only ever sees the generated name. Without this,
    the table accumulates entries for flags that no longer exist and a reader
    cannot tell which rows are load-bearing.
    """
    generated = {dest for dests in _NORMALIZED_DESTS.values() for dest in dests}
    stale = sorted(set(_UNSUPPORTED_OPTIONS) - _compare_params() - generated)
    assert not stale, f"_UNSUPPORTED_OPTIONS names non-existent options: {stale}"


def test_no_unsupported_entry_is_also_read() -> None:
    """An option cannot be both wired and declared unsupported.

    If it were, the usage error would fire before the wiring ever ran — the
    wiring would be dead code, and the table would be lying about it.
    """
    both = sorted(set(_UNSUPPORTED_OPTIONS) & _dests_read_by_module())
    assert not both, (
        f"these options are both read and declared unsupported: {both} — "
        "the usage error fires first, so the wiring is unreachable"
    )


def test_every_unsupported_entry_states_a_spelling_and_a_reason() -> None:
    """The usage error must name what the user typed and why it is refused."""
    for dest, (spelling, reason) in _UNSUPPORTED_OPTIONS.items():
        assert spelling.startswith("--"), f"{dest}: {spelling!r} is not a CLI spelling"
        assert len(reason) > 20, f"{dest}: reason is too terse to be useful"
        assert not reason.endswith("."), (
            f"{dest}: reason is interpolated mid-sentence, so it must not end "
            "with a period"
        )


@pytest.mark.parametrize("dest", sorted(_UNSUPPORTED_OPTIONS))
def test_every_declared_unsupported_option_really_is_rejected(dest: str) -> None:
    """The table is enforced, not merely written.

    Drives the real dispatch guard rather than re-reading the table: an
    entry whose dest never reaches ``_reject_unsupported_options`` (a typo,
    a renamed dest) would otherwise sit there looking authoritative while
    the option stayed silently accepted.
    """
    import click

    from abicheck.frontends.cli.commands.compare_no_baseline import (
        _reject_unsupported_options,
    )

    spelling = _UNSUPPORTED_OPTIONS[dest][0]
    with pytest.raises(click.UsageError) as excinfo:
        _reject_unsupported_options({dest: "a-value-the-user-typed"})
    assert spelling in str(excinfo.value)


def test_absent_options_are_not_rejected() -> None:
    """The guard fires on a stated value only — never on a Click default.

    Covers every "nothing was passed" spelling `compare` actually uses,
    including the `UNSET` sentinel, since treating one of those as "given"
    would reject an ordinary invocation that passed nothing at all.
    """
    from abicheck.frontends.cli.commands.compare_no_baseline import (
        _reject_unsupported_options,
    )

    for empty in (None, (), "", False):
        _reject_unsupported_options({dest: empty for dest in _UNSUPPORTED_OPTIONS})

    sentinels = {
        p.name: p.default
        for p in main.commands["compare"].params
        if p.name in _UNSUPPORTED_OPTIONS
    }
    assert sentinels, "expected to find the real Click defaults"
    _reject_unsupported_options(sentinels)


def test_was_given_agrees_with_every_real_click_default() -> None:
    """`_was_given` must answer False for every default `compare` declares.

    Checked against the live command rather than a hand-copied list, so a
    new "not given" spelling (another sentinel, a different empty
    container) cannot silently start reading as a stated value.
    """
    for param in main.commands["compare"].params:
        if param.name in _DISPATCH_OWNED or param.name in _CLICK_LEVEL:
            continue
        if param.default is None or param.default in ((), "", False):
            assert not _was_given(param.default), (
                f"{param.name}: default {param.default!r} reads as user-stated"
            )
        if repr(param.default).startswith("Sentinel."):
            assert not _was_given(param.default), (
                f"{param.name}: UNSET sentinel reads as user-stated"
            )


def test_module_documents_why_the_table_exists() -> None:
    """The table's own comment is the institutional memory here.

    A future reader deleting `_UNSUPPORTED_OPTIONS` as boilerplate is the
    realistic way this class reopens, so the reason is pinned as content,
    not left to review.
    """
    source = _MODULES[0].read_text(encoding="utf-8")
    assert "_UNSUPPORTED_OPTIONS" in source
    assert re.search(r"accepted but never read|silently", source), (
        "the table must explain that it exists to prevent silently-dropped options"
    )


def test_every_generated_destination_is_wired_or_declared() -> None:
    """The half that ``test_every_compare_option_is_wired_or_declared`` misses.

    That test asks about `compare`'s *declared parameters*. But
    ``normalize_sided_options`` renames several of them before the dispatch
    ever runs, so a raw name being "consumed upstream" says nothing about
    whether its *result* is read. Allowlisting the raw names alone let a bare
    ``--dump-manifest`` and ``--version new=`` through as silent no-ops --
    the very defect this module exists to make impossible.

    So every generated destination is checked separately, against the same
    rule: read by the dispatch, or named in ``_UNSUPPORTED_OPTIONS``. An
    ``old_``-scoped destination is also satisfied by the sided-input guards,
    since those refuse it outright.
    """
    read = _dests_read_by_module()
    guarded = (
        set(_OLD_ONLY_DESTS)
        | set(_SIDED_SINGLE_DESTS)
        | set(_SIDED_LABEL_DESTS)
        | set(_INERT_DESTS)
    )
    unaccounted = sorted(
        dest
        for dests in _NORMALIZED_DESTS.values()
        for dest in dests
        if dest not in read
        and dest not in _UNSUPPORTED_OPTIONS
        and dest not in guarded
        and dest not in _CLICK_LEVEL
    )
    assert not unaccounted, (
        "these destinations are produced by normalize_sided_options and then "
        f"silently dropped by the --no-baseline dispatch: {unaccounted}. Wire "
        "each one, or declare it in _UNSUPPORTED_OPTIONS so passing the option "
        "that produces it is a usage error."
    )


@pytest.mark.parametrize("dest", sorted(_CONTEXT_STASHED))
def test_every_context_stashed_option_is_really_rejected(
    dest: str, tmp_path: Path
) -> None:
    """A context-stashed option must be rejected, not merely bucketed.

    Driven through the real CLI rather than by inspecting a table: the value
    never reaches ``kwargs``, so every name-based check in this module is
    blind to it, and declaring it accounted-for is precisely how a silently
    dropped flag would slip past the exhaustiveness test above. ``--variant``
    did slip past — it arrived on `main` while this branch was open, was
    accepted on the audit path, and ran a normal comparison at exit 0.
    """
    from click.testing import CliRunner

    from abicheck.model import AbiSnapshot
    from abicheck.serialization import save_snapshot

    snapshot = tmp_path / "cand.abi.json"
    save_snapshot(AbiSnapshot(library="libx.so", version="1.0"), snapshot)

    spelling = "--" + dest.replace("_", "-")
    result = CliRunner().invoke(
        main, ["compare", "--no-baseline", str(snapshot), spelling, "v1"]
    )
    assert result.exit_code == 64, (
        f"{spelling} reached the audit and was silently accepted "
        f"(exit {result.exit_code}); it must be wired or be a usage error"
    )
    assert spelling in result.output
