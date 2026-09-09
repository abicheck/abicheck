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
    _OLD_ONLY_DESTS,
    _SIDED_SINGLE_DESTS,
    _UNSUPPORTED_OPTIONS,
    _VIEW_DEFAULTS,
    _was_given,
)

_MODULE = (
    Path(__file__).resolve().parent.parent
    / "abicheck/frontends/cli/commands/compare_no_baseline.py"
)

#: Parameters rewritten or expanded before ``maybe_dispatch_no_baseline_compare``
#: runs, so the dispatch never sees the original dest.
#:
#: ``compare_cmd`` calls ``normalize_sided_options`` (which pops ``header``/
#: ``include``/``sources``/``build_info``/``dump_manifest``/``debug_root``/
#: ``probe_matrix``/``version``/``header_backend`` into ``old_*``/``new_*``
#: dests) and ``parse_view_tokens`` (which expands ``view`` into the
#: ``_VIEW_DEFAULTS`` dests) *before* dispatching. Each name below is
#: therefore consumed upstream, and its per-side/expanded results are what
#: this module must account for — which the other buckets do.
_CONSUMED_UPSTREAM = frozenset(
    {
        "header",
        "include",
        "sources",
        "build_info",
        "dump_manifest",
        "debug_root",
        "probe_matrix",
        "version",
        "view",
        "header_backend",
    }
)

#: Click-level parameters that belong to no command body.
_CLICK_LEVEL = frozenset({"help", "help_all"})

#: Parameters the dispatch handles structurally rather than by name — the
#: two operands it consumes itself, and the flag that selects it.
_DISPATCH_OWNED = frozenset({"old_input", "new_input", "no_baseline"})

#: ``-v/--verbose`` is deliberately not in any bucket below: it configures
#: logging, not analysis, and is a no-op that misleads nobody.
_PRESENTATION_ONLY = frozenset({"verbose"})


def _compare_params() -> set[str]:
    return {p.name for p in main.commands["compare"].params if p.name}


def _dests_read_by_module() -> set[str]:
    """Every kwargs dest the dispatch module actually reads.

    Parsed from the AST rather than by regex over the source, so a name that
    only appears in a docstring or comment (this module has many) is never
    miscounted as a real read — the exact mistake that would make this test
    pass while the option stayed dropped.
    """
    tree = ast.parse(_MODULE.read_text(encoding="utf-8"))
    dests: set[str] = set()
    for node in ast.walk(tree):
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
        | set(_VIEW_DEFAULTS)
        | set(_OLD_ONLY_DESTS)
        | set(_SIDED_SINGLE_DESTS)
        | _CONSUMED_UPSTREAM
        | _CLICK_LEVEL
        | _DISPATCH_OWNED
        | _PRESENTATION_ONLY
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
    """A declared-unsupported option must still be a real `compare` option.

    Otherwise the table accumulates entries for flags that no longer exist,
    and a reader cannot tell which rows are load-bearing.
    """
    stale = sorted(set(_UNSUPPORTED_OPTIONS) - _compare_params())
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
    source = _MODULE.read_text(encoding="utf-8")
    assert "_UNSUPPORTED_OPTIONS" in source
    assert re.search(r"accepted but never read|silently", source), (
        "the table must explain that it exists to prevent silently-dropped options"
    )
