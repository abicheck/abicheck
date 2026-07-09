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

"""Unit tests for the ADR-039 collection layer (header_conditionals).

The scanner harvests ``build_context_defines`` + ``conditional_fields`` from
header source and a compile DB so the reconciler works on real ``dump`` output,
not just hand-built fixtures. Best-effort and *conservative*: it records only
unambiguous single-positive-guard member fields, and skips everything else.
"""

from __future__ import annotations

import json

from abicheck.header_conditionals import (
    collect_build_context,
    defines_from_compile_db,
    defines_from_flags,
    scan_conditional_fields,
)


def _guard(reg, record, field):
    return reg.get(record, {}).get(field)


# ── define extraction ────────────────────────────────────────────────────────


def test_defines_from_flags_all_forms():
    got = defines_from_flags(["-DA", "-DB=1", "-D", "C", "-Iinc", "-O2", "-Dbad name"])
    assert got == {"A", "B", "C"}


def test_defines_from_flags_honors_undefine_and_order():
    # -U cancels a prior -D (last wins); a later -D re-adds.
    assert defines_from_flags(["-DKEEP", "-UKEEP"]) == set()
    assert defines_from_flags(["-UKEEP", "-DKEEP"]) == {"KEEP"}
    assert defines_from_flags(["-D", "A", "-U", "A", "-DB"]) == {"B"}


def test_defines_from_flags_applies_over_initial_set():
    """*initial* seeds the active set; the flags are applied on top (in order), so
    a ``-U`` overrides an already-active macro (Codex review #498)."""
    assert defines_from_flags(["-UKEEP"], initial={"KEEP"}) == set()
    assert defines_from_flags(["-DEXTRA"], initial={"KEEP"}) == {"KEEP", "EXTRA"}
    # the passed-in set is not mutated
    seed = {"KEEP"}
    defines_from_flags(["-UKEEP"], initial=seed)
    assert seed == {"KEEP"}


def test_defines_from_compile_db_intersects_commands(tmp_path):
    """A macro is trusted only when *every* command defines it (conservative)."""
    db = tmp_path / "compile_commands.json"
    db.write_text(
        json.dumps(
            [
                {"command": "cc -DCOMMON -DONLY_A -c a.c"},
                {"arguments": ["cc", "-DCOMMON", "-DONLY_B", "-c", "b.c"]},
            ]
        )
    )
    # COMMON is in both; ONLY_A / ONLY_B are ambiguous → excluded.
    assert defines_from_compile_db(db) == {"COMMON"}


def test_defines_from_compile_db_applies_source_filter(tmp_path):
    """``--compile-db-filter`` narrows entries before intersecting, so a guard
    defined only by the selected TU is harvested (Codex review #498)."""
    db = tmp_path / "compile_commands.json"
    db.write_text(
        json.dumps(
            [
                {"directory": "/b", "file": "/b/keep.c", "command": "cc -DKEEP -c keep.c"},
                {"directory": "/b", "file": "/b/other.c", "command": "cc -c other.c"},
            ]
        )
    )
    # Without a filter, the intersection drops KEEP (other.c lacks it).
    assert defines_from_compile_db(db) == set()
    # Filtering to keep.c selects only that TU → KEEP is trusted.
    assert defines_from_compile_db(db, "keep.c") == {"KEEP"}
    # A relative pattern matches the directory-relative path too.
    assert defines_from_compile_db(db, "*.c") == set()  # matches both → intersect
    # A filter that matches nothing falls back to all entries (conservative).
    assert defines_from_compile_db(db, "nope.c") == set()


def test_defines_from_compile_db_tolerates_malformed_command(tmp_path):
    """A compile ``command`` with an unbalanced quote is skipped, not fatal."""
    db = tmp_path / "compile_commands.json"
    db.write_text(json.dumps([{"command": 'cc -DOK "unbalanced -c a.c'}]))
    assert defines_from_compile_db(db) == set()


def test_compile_entry_matcher_and_command_splitter_edges():
    from abicheck.header_conditionals import _compile_entry_matches, _split_command

    assert _split_command(["already", "a", "list"]) == []  # non-str → []
    assert _split_command("cc -c a.c") == ["cc", "-c", "a.c"]
    # non-string file, file outside directory (ValueError), and no directory key
    assert _compile_entry_matches({"file": 123}, "*.c") is False
    assert _compile_entry_matches({"file": "/x/a.c", "directory": "/y"}, "a.c") is False
    assert _compile_entry_matches({"file": "/x/a.c"}, "a.c") is False
    assert _compile_entry_matches({"file": "/x/a.c"}, "/x/a.c") is True
    # A file outside the build directory matches a CWD-relative filter, mirroring
    # build_context._entry_matches_filter (Codex review #498).
    from pathlib import Path

    cwd_file = str(Path.cwd() / "src" / "libfoo" / "a.c")
    assert _compile_entry_matches({"file": cwd_file, "directory": "/elsewhere"}, "src/libfoo/*") is True
    assert _compile_entry_matches({"file": cwd_file}, "src/libfoo/*") is True
    # A *relative* file is resolved against directory before matching, so an
    # absolute filter matches (mirrors build_context.CompileEntry; Codex #498).
    rel_entry = {"file": "src/libfoo/a.c", "directory": "/build/proj"}
    assert _compile_entry_matches(rel_entry, "/build/proj/src/libfoo/*") is True
    # and the directory-relative filter still works on the resolved path
    assert _compile_entry_matches(rel_entry, "src/libfoo/*") is True
    assert _compile_entry_matches(rel_entry, "other/*") is False


def test_defines_from_compile_db_undefine_excludes_from_intersection(tmp_path):
    db = tmp_path / "compile_commands.json"
    db.write_text(
        json.dumps(
            [
                {"command": "cc -DKEEP -c a.c"},
                {"command": "cc -DKEEP -UKEEP -c b.c"},
            ]
        )
    )
    assert defines_from_compile_db(db) == set()


def test_defines_from_compile_db_malformed_is_empty(tmp_path):
    bad = tmp_path / "compile_commands.json"
    bad.write_text("{ not valid json")
    assert defines_from_compile_db(bad) == set()
    obj = tmp_path / "obj.json"
    obj.write_text('{"not": "a list"}')
    assert defines_from_compile_db(obj) == set()
    assert defines_from_compile_db(tmp_path / "missing.json") == set()
    empty = tmp_path / "empty.json"
    empty.write_text("[]")
    assert defines_from_compile_db(empty) == set()


def test_defines_from_compile_db_accepts_build_directory(tmp_path):
    """A build *directory* is normalised to ``<dir>/compile_commands.json`` so the
    documented ``dump … -p build/`` form works (Codex review #498)."""
    (tmp_path / "compile_commands.json").write_text(
        json.dumps([{"command": "cc -DKEEP -c a.c"}])
    )
    assert defines_from_compile_db(tmp_path) == {"KEEP"}
    defines, _ = collect_build_context([], tmp_path)
    assert defines == {"KEEP"}


def test_defines_from_compile_db_skips_nondict_entries(tmp_path):
    db = tmp_path / "compile_commands.json"
    db.write_text(json.dumps(["not-a-dict", {"command": "cc -DOK -c a.c"}]))
    assert defines_from_compile_db(db) == {"OK"}


# ── conditional-field scan: what IS recorded ─────────────────────────────────


def test_scan_records_ifdef_guarded_field():
    src = "struct Config {\n int version;\n#ifdef KEEP\n int legacy;\n#endif\n};"
    reg = scan_conditional_fields(src)
    assert _guard(reg, "Config", "legacy") == {
        "guard": "KEEP",
        "type": "int",
        "is_bitfield": False,
        "bitfield_bits": None,
        "access": "public",  # struct default
        "is_const": False,
        "is_volatile": False,
        "is_mutable": False,
        "is_last": True,  # legacy is the final member of Config
    }
    # the unconditional field is not registered
    assert "version" not in reg.get("Config", {})


def test_scan_if_defined_paren_and_space_forms():
    paren = scan_conditional_fields("class W {\n#if defined(X)\n int a;\n#endif\n};")
    assert _guard(paren, "W", "a")["guard"] == "X"
    space = scan_conditional_fields("class W {\n#if defined Y\n int a;\n#endif\n};")
    assert _guard(space, "W", "a")["guard"] == "Y"


def test_scan_captures_type_bitfield_and_pointer():
    src = (
        "struct S {\n#ifdef G\n unsigned int mode;\n int flags : 3;\n"
        " char *name;\n#endif\n};"
    )
    reg = scan_conditional_fields(src)["S"]
    assert reg["mode"]["type"] == "unsigned int"
    assert reg["flags"]["is_bitfield"] and reg["flags"]["bitfield_bits"] == 3
    assert reg["name"]["type"] == "char *"


def test_scan_strips_comments():
    src = "struct S {\n#ifdef G\n int a; /* c */ // trailing\n#endif\n};"
    assert _guard(scan_conditional_fields(src), "S", "a")["type"] == "int"


def test_scan_lifts_cv_qualifiers_into_structured_bits():
    """``const``/``volatile``/``mutable`` are lifted out of the type string into
    structured bits so the registry declaration matches the model's
    ``TypeField`` shape (Codex review #498, P2). Otherwise a ``const int``→``int``
    change on a guarded field would be reconciled away as NO_CHANGE."""
    src = "struct S {\n#ifdef G\n const int legacy;\n#endif\n};"
    entry = _guard(scan_conditional_fields(src), "S", "legacy")
    assert entry["type"] == "int"
    assert entry["is_const"] is True
    assert entry["is_volatile"] is False
    assert entry["is_mutable"] is False
    vol = _guard(
        scan_conditional_fields("struct S {\n#ifdef G\n volatile int v;\n#endif\n};"),
        "S",
        "v",
    )
    assert vol["type"] == "int" and vol["is_volatile"] is True


def test_scan_records_access_default_by_keyword():
    """A field's C++ access defaults from the record keyword — public for
    ``struct``/``union``, private for ``class`` (Codex review #498, P2)."""
    st = scan_conditional_fields("struct S {\n#ifdef G\n int a;\n#endif\n};")
    assert _guard(st, "S", "a")["access"] == "public"
    un = scan_conditional_fields("union U {\n#ifdef G\n int a;\n#endif\n};")
    assert _guard(un, "U", "a")["access"] == "public"
    cl = scan_conditional_fields("class C {\n#ifdef G\n int a;\n#endif\n};")
    assert _guard(cl, "C", "a")["access"] == "private"


def test_scan_tracks_access_labels():
    """An access label (``public:``) switches the recorded access for fields that
    follow it in the same body."""
    src = (
        "class C {\n int hidden;\npublic:\n#ifdef G\n int shown;\n#endif\n"
        "private:\n#ifdef G\n int again_hidden;\n#endif\n};"
    )
    reg = scan_conditional_fields(src)["C"]
    assert reg["shown"]["access"] == "public"
    assert reg["again_hidden"]["access"] == "private"


def test_scan_split_forward_declaration_does_not_capture_next_record():
    """A forward declaration split across lines (``struct S`` then ``;``) must not
    leave a stale pending opener that captures the *next* record's body: ``T``'s
    guarded field must be keyed to ``T``, never ``S`` (Codex review #498, P1)."""
    src = (
        "struct S\n;\n"
        "struct T {\n#ifdef G\n int guarded;\n#endif\n};"
    )
    reg = scan_conditional_fields(src)
    assert "guarded" in reg.get("T", {})
    assert "S" not in reg  # nothing is misattributed to the forward-declared S
    # A *real* split definition (no terminating ``;``) still opens S's body.
    defn = scan_conditional_fields("struct S\n{\n#ifdef G\n int a;\n#endif\n};")
    assert "a" in defn.get("S", {})


# ── conditional-field scan: what is NOT recorded (conservative) ──────────────


def test_scan_records_field_inside_include_guarded_header():
    """A classic ``#ifndef H`` / ``#define H`` file include guard is transparent,
    so a guarded field in the (near-universal) include-guarded header is still
    recorded (Codex review #498)."""
    src = (
        "#ifndef CONFIG_H\n#define CONFIG_H\n"
        "struct Config {\n int version;\n#ifdef KEEP\n int legacy;\n#endif\n};\n"
        "#endif\n"
    )
    reg = scan_conditional_fields(src)
    assert _guard(reg, "Config", "legacy")["guard"] == "KEEP"


def test_scan_include_guard_does_not_make_unguarded_field_recorded():
    """The include guard is transparent, not a positive guard: an *unguarded*
    field in an include-guarded header is still not registered."""
    src = (
        "#ifndef CONFIG_H\n#define CONFIG_H\n"
        "struct Config {\n int always;\n};\n#endif\n"
    )
    assert scan_conditional_fields(src) == {}


def test_scan_nested_ifdef_inside_include_guard_not_recorded():
    """Two real positive guards nested inside the include guard is still 'nested'
    (not a single positive guard) → not recorded."""
    src = (
        "#ifndef H\n#define H\nstruct S {\n#ifdef A\n#ifdef B\n int x;\n"
        "#endif\n#endif\n};\n#endif\n"
    )
    assert scan_conditional_fields(src) == {}


def test_include_guard_macro_detection():
    from abicheck.header_conditionals import _include_guard_macro

    assert _include_guard_macro(["#ifndef H", "#define H", "int x;"]) == "H"
    # #define name must match the #ifndef name
    assert _include_guard_macro(["#ifndef H", "#define OTHER"]) is None
    # code before the guard → not a whole-file guard
    assert _include_guard_macro(["struct S {};", "#ifndef H", "#define H"]) is None
    # a lone #ifndef with no following #define is not an include guard
    assert _include_guard_macro(["#ifndef H", "int x;"]) is None
    # only an #ifndef and nothing after (loop ends), or an empty file
    assert _include_guard_macro(["#ifndef H"]) is None
    assert _include_guard_macro([]) is None


def test_scan_records_simple_negative_guard():
    """A simple ``#ifndef GUARD`` field is recorded with ``negative: True`` so the
    reconciler can prune it from a build that defines GUARD (Codex review #498)."""
    neg = "struct S {\n#ifndef NO_PAD\n int pad;\n#endif\n};"
    entry = _guard(scan_conditional_fields(neg), "S", "pad")
    assert entry["guard"] == "NO_PAD" and entry["negative"] is True


def test_include_guard_recognized_after_pragma_once():
    """A leading ``#pragma once`` before a classic ``#ifndef H`` / ``#define H``
    wrapper must not stop include-guard recognition — otherwise the wrapper is
    treated as a real negative guard and an inner ``#ifdef KEEP`` field (now
    double-guarded) is dropped, leaving no reconcilable evidence (Codex review
    #498, P2)."""
    from abicheck.header_conditionals import _include_guard_macro

    assert _include_guard_macro(["#pragma once", "#ifndef H", "#define H"]) == "H"
    src = (
        "#pragma once\n#ifndef H\n#define H\nstruct S {\n#ifdef KEEP\n int legacy;\n#endif\n};\n#endif\n"
    )
    entry = _guard(scan_conditional_fields(src), "S", "legacy")
    assert entry["guard"] == "KEEP"  # single guard — the file wrapper is transparent


def test_scan_negative_guard_distinct_from_include_guard():
    """Inside an include-guarded header, a real ``#ifndef FEATURE`` field is still
    recorded as a negative guard (the file guard is the transparent one)."""
    src = (
        "#ifndef H\n#define H\nstruct S {\n#ifndef FEATURE\n int pad;\n#endif\n};\n"
        "#endif\n"
    )
    entry = _guard(scan_conditional_fields(src), "S", "pad")
    assert entry["guard"] == "FEATURE" and entry["negative"] is True


def test_scan_skips_compound_guards():
    """Compound / expression guards remain opaque — not recordable either way."""
    comp = "struct S {\n#if defined(A) && defined(B)\n int x;\n#endif\n};"
    assert scan_conditional_fields(comp) == {}
    expr = "struct S {\n#if VER > 2\n int x;\n#endif\n};"
    assert scan_conditional_fields(expr) == {}
    # a positive #ifdef entry carries no ``negative`` key
    pos = scan_conditional_fields("struct S {\n#ifdef G\n int a;\n#endif\n};")
    assert "negative" not in _guard(pos, "S", "a")


def test_scan_skips_nested_guards():
    src = "struct S {\n#ifdef A\n#ifdef B\n int x;\n#endif\n#endif\n};"
    assert scan_conditional_fields(src) == {}


def test_scan_skips_else_branch():
    src = "struct S {\n#ifdef A\n int a;\n#else\n int b;\n#endif\n};"
    reg = scan_conditional_fields(src)
    assert _guard(reg, "S", "a") is not None
    assert _guard(reg, "S", "b") is None


def test_scan_skips_guard_undefined_by_header():
    """A header-local ``#undef GUARD`` before the guarded region means the build
    really prunes the field, so it must not be recorded as reconcilable even
    though a compile DB might define GUARD (Codex review #498)."""
    src = "struct S {\n#undef G\n#ifdef G\n int gone;\n#endif\n};"
    assert scan_conditional_fields(src) == {}


def test_scan_records_guard_redefined_after_undef():
    """A later ``#define GUARD`` re-activates the macro, so a field guarded after
    the re-definition is recorded again."""
    src = "struct S {\n#undef G\n#define G 1\n#ifdef G\n int back;\n#endif\n};"
    assert _guard(scan_conditional_fields(src), "S", "back")["guard"] == "G"


def test_scan_conditional_define_does_not_reactivate_undef():
    """A ``#define`` inside an ``#ifdef OTHER`` branch the scanner cannot evaluate
    must NOT reactivate a previously ``#undef``'d guard — a build without OTHER
    really keeps the macro undefined, so the field stays unrecorded (Codex #498)."""
    src = (
        "struct S {\n#undef G\n#ifdef OTHER\n#define G 1\n#endif\n"
        "#ifdef G\n int gone;\n#endif\n};"
    )
    assert scan_conditional_fields(src) == {}


def test_scan_conditional_undef_marks_negative_guard_ambiguous():
    """A ``#undef GUARD`` inside an ``#ifdef OTHER`` branch the scanner cannot
    evaluate makes GUARD's state build-context dependent: whether the ``#undef``
    fires depends on OTHER, which the context-free scan does not know. A later
    ``#ifndef GUARD`` field is recorded ``ambiguous`` so the reconciler keeps
    (never reconciles) findings on its type — neither adding it back nor pruning
    it can be proven correct (Codex review #498, P1)."""
    src = (
        "struct S {\n#ifdef OTHER\n#undef KEEP\n#endif\n"
        "#ifndef KEEP\n int legacy;\n#endif\n};"
    )
    entry = _guard(scan_conditional_fields(src), "S", "legacy")
    assert entry["guard"] == "KEEP" and entry["negative"] is True
    assert entry["ambiguous"] is True


def test_scan_conditional_undef_marks_positive_guard_ambiguous():
    """The symmetric positive case: ``#undef KEEP`` inside ``#ifdef OTHER`` then a
    ``#ifdef KEEP`` field. When the build defines both OTHER and KEEP the branch is
    active, the ``#undef`` fires, and the field is really pruned — so recording it
    as plainly active-KEEP would let the reconciler add a pruned field back. The
    field is flagged ``ambiguous`` instead (Codex review #498, P1)."""
    src = (
        "struct S {\n#ifdef OTHER\n#undef KEEP\n#endif\n"
        "#ifdef KEEP\n int x;\n#endif\n};"
    )
    entry = _guard(scan_conditional_fields(src), "S", "x")
    assert entry["guard"] == "KEEP" and entry.get("negative") is None
    assert entry["ambiguous"] is True


def test_scan_conditional_define_marks_guard_ambiguous():
    """A conditional ``#define`` makes the macro's state build-context dependent
    too, so a field guarded by it is ``ambiguous`` (Codex review #498, P1)."""
    src = (
        "struct S {\n#ifdef OTHER\n#define KEEP 1\n#endif\n"
        "#ifdef KEEP\n int x;\n#endif\n};"
    )
    entry = _guard(scan_conditional_fields(src), "S", "x")
    assert entry["ambiguous"] is True


def test_scan_clean_guard_is_not_ambiguous():
    """A guard whose macro is never touched by a conditional ``#undef``/``#define``
    carries no ``ambiguous`` flag — normal reconcilable evidence."""
    src = "struct S {\n#ifdef KEEP\n int x;\n#endif\n};"
    assert "ambiguous" not in _guard(scan_conditional_fields(src), "S", "x")


def test_scan_guarded_field_after_include_is_ambiguous():
    """A scanned header cannot follow ``#include``s, so an included file could
    ``#undef`` the guard the real build sees. A guarded field *after* any include
    is flagged ``ambiguous`` (its guard state is unprovable), so the reconciler
    keeps its type (Codex review #498, P1)."""
    src = '#include "config.h"\nstruct S {\n#ifdef KEEP\n int legacy;\n#endif\n};'
    assert _guard(scan_conditional_fields(src), "S", "legacy")["ambiguous"] is True
    # A system include has the same unprovable effect.
    sysinc = "#include <stddef.h>\nstruct S {\n#ifdef KEEP\n int x;\n#endif\n};"
    assert _guard(scan_conditional_fields(sysinc), "S", "x")["ambiguous"] is True
    # An include *after* the guarded field does not affect it.
    after = 'struct S {\n#ifdef KEEP\n int y;\n#endif\n};\n#include "late.h"'
    assert "ambiguous" not in _guard(scan_conditional_fields(after), "S", "y")


def test_scan_records_is_last_for_terminal_field():
    """A guarded field that is the final member of its record is flagged
    ``is_last: True``; one with a member after it is ``False`` (Codex review
    #498, P1). ``is_last`` counts *all* members, guarded or not."""
    trailing = "struct S {\n int version;\n#ifdef KEEP\n int legacy;\n#endif\n};"
    assert _guard(scan_conditional_fields(trailing), "S", "legacy")["is_last"] is True
    mid = "struct S {\n#ifdef KEEP\n int legacy;\n#endif\n int tail;\n};"
    assert _guard(scan_conditional_fields(mid), "S", "legacy")["is_last"] is False
    first = "struct S {\n#ifdef KEEP\n int legacy;\n#endif\n int version;\n int tail;\n};"
    assert _guard(scan_conditional_fields(first), "S", "legacy")["is_last"] is False


def test_scan_is_last_counts_unparsed_members():
    """A member ``_parse_field`` cannot decode — an array ``int tail[4];`` or a
    default-initialised ``int x = 0;`` — still advances the position counter, so a
    guarded field *before* it is not wrongly marked terminal (Codex review #498,
    P1). Otherwise a real reorder of ``legacy`` and ``tail`` could reconcile away."""
    arr = "struct S {\n#ifdef KEEP\n int legacy;\n#endif\n int tail[4];\n};"
    assert _guard(scan_conditional_fields(arr), "S", "legacy")["is_last"] is False
    init = "struct S {\n#ifdef KEEP\n int legacy;\n#endif\n int x = 0;\n};"
    assert _guard(scan_conditional_fields(init), "S", "legacy")["is_last"] is False
    method = "struct S {\n#ifdef KEEP\n int legacy;\n#endif\n void run();\n};"
    assert _guard(scan_conditional_fields(method), "S", "legacy")["is_last"] is False
    # A genuinely-terminal guarded field (only the record's `};` follows) stays last.
    last = "struct S {\n int version;\n#ifdef KEEP\n int legacy;\n#endif\n};"
    assert _guard(scan_conditional_fields(last), "S", "legacy")["is_last"] is True


def test_scan_top_level_undef_suppresses_negative_guard():
    """A **top-level** ``#undef KEEP`` genuinely undefines the macro for every
    build, so a later ``#ifndef KEEP`` field is always present — it must NOT be
    recorded as a negative guard, else the reconciler would prune a field that is
    really there (Codex review #498, P1)."""
    src = "struct S {\n#undef KEEP\n#ifndef KEEP\n int legacy;\n#endif\n};"
    assert scan_conditional_fields(src) == {}


def test_scan_undef_after_field_does_not_suppress_it():
    """An ``#undef`` *after* a guarded field (later in file order) does not
    retroactively suppress the field recorded before it — the build saw the field
    while the macro was still active."""
    src = "struct S {\n#ifdef G\n int early;\n#endif\n#undef G\n};"
    assert _guard(scan_conditional_fields(src), "S", "early")["guard"] == "G"


def test_scan_skips_methods_and_assignments():
    src = (
        "struct S {\n#ifdef G\n void run(int x);\n int init = 0;\n int ok;\n#endif\n};"
    )
    reg = scan_conditional_fields(src)["S"]
    assert "run" not in reg and "init" not in reg
    assert "ok" in reg


def test_scan_keys_namespaced_record_by_qualified_name():
    """A record inside ``namespace api`` is keyed ``api::S`` so the evidence
    matches the qualified ``RecordType.name`` a DWARF snapshot uses (Codex #498)."""
    src = "namespace api {\nstruct S {\n#ifdef G\n int legacy;\n#endif\n};\n}"
    reg = scan_conditional_fields(src)
    assert "S" not in reg
    assert _guard(reg, "api::S", "legacy")["guard"] == "G"


def test_scan_keys_nested_namespaces_and_records():
    """Nested namespaces and an enclosing record both contribute qualifier
    segments (``a::b::Outer::Inner``)."""
    src = (
        "namespace a {\nnamespace b {\nstruct Outer {\nstruct Inner {\n"
        "#ifdef G\n int x;\n#endif\n};\n};\n}\n}"
    )
    reg = scan_conditional_fields(src)
    assert _guard(reg, "a::b::Outer::Inner", "x")["guard"] == "G"


def test_scan_distinguishes_same_name_records_in_different_namespaces():
    """Two ``S`` records in different namespaces stay separate registry keys
    rather than being conflated under a bare ``S``."""
    src = (
        "namespace a {\nstruct S {\n#ifdef G\n int a_field;\n#endif\n};\n}\n"
        "namespace b {\nstruct S {\n#ifdef G\n int b_field;\n#endif\n};\n}"
    )
    reg = scan_conditional_fields(src)
    assert set(reg) == {"a::S", "b::S"}
    assert "a_field" in reg["a::S"] and "b_field" in reg["b::S"]


def test_scan_qualified_namespace_opener():
    """A ``namespace a::b {`` opener contributes both segments."""
    src = "namespace a::b {\nstruct S {\n#ifdef G\n int x;\n#endif\n};\n}"
    assert _guard(scan_conditional_fields(src), "a::b::S", "x")["guard"] == "G"


def test_scan_namespace_brace_on_next_line():
    """``namespace api`` then ``{`` on the following line still opens the scope."""
    src = "namespace api\n{\nstruct S {\n#ifdef G\n int x;\n#endif\n};\n}"
    assert _guard(scan_conditional_fields(src), "api::S", "x")["guard"] == "G"


def test_scan_ignores_namespace_alias():
    """A namespace *alias* (``namespace short = a::b;``) opens no scope."""
    src = "namespace short = a::b;\nstruct S {\n#ifdef G\n int x;\n#endif\n};"
    reg = scan_conditional_fields(src)
    assert set(reg) == {"S"}
    assert _guard(reg, "S", "x")["guard"] == "G"


def test_scan_skips_anonymous_namespace():
    """A record in an anonymous namespace has internal linkage / no stable public
    name, so its guarded fields are never recorded."""
    src = "namespace {\nstruct S {\n#ifdef G\n int x;\n#endif\n};\n}"
    assert scan_conditional_fields(src) == {}


def test_scan_ignores_using_namespace_directive():
    """``using namespace std;`` must not be treated as a scope opener."""
    src = "using namespace std;\nstruct S {\n#ifdef G\n int x;\n#endif\n};"
    assert _guard(scan_conditional_fields(src), "S", "x")["guard"] == "G"


def test_scan_ignores_guarded_field_outside_record_body():
    # A guarded declaration at file scope is not a record field.
    src = "#ifdef G\nint global_thing;\n#endif\nstruct S { int a; };"
    assert scan_conditional_fields(src) == {}


def test_scan_forward_declaration_does_not_open_a_record():
    src = "struct Fwd;\nstruct S {\n#ifdef G\n int a;\n#endif\n};"
    reg = scan_conditional_fields(src)
    assert "Fwd" not in reg and _guard(reg, "S", "a") is not None


def test_scan_record_name_on_its_own_line():
    """``struct Name`` then ``{`` on the next line still opens the body."""
    src = "struct S\n{\n#ifdef G\n int a;\n#endif\n};"
    assert _guard(scan_conditional_fields(src), "S", "a")["guard"] == "G"


def test_scan_field_after_endif_is_unconditional():
    """A field after the ``#endif`` is unconditional and not registered."""
    src = "struct S {\n#ifdef G\n int a;\n#endif\n int b;\n};"
    reg = scan_conditional_fields(src)
    assert _guard(reg, "S", "a") is not None
    assert _guard(reg, "S", "b") is None


def test_scan_tolerates_blank_lines_and_other_directives():
    """Blank lines and non-conditional directives (``#define``/``#include``) are
    skipped without disturbing the guard/record tracking."""
    src = (
        "\nstruct S {\n#define LOCAL 1\n#include <x.h>\n\n#ifdef G\n int a;\n#endif\n};"
    )
    assert _guard(scan_conditional_fields(src), "S", "a")["guard"] == "G"


def test_parse_field_helper_edge_cases():
    from abicheck.header_conditionals import _parse_field

    assert _parse_field("void run(int)") is None  # has (
    assert _parse_field("int x = 0") is None  # has =
    assert _parse_field("justoneword") is None  # no type/name split
    assert _parse_field("int return") is None  # keyword name
    assert _parse_field("int x") == ("x", "int", False, None, False, False, False)
    assert _parse_field("int flags : 3") == ("flags", "int", True, 3, False, False, False)
    # a ``::`` before a trailing digit is not a bit-field
    assert _parse_field("ns::T value") == ("value", "ns::T", False, None, False, False, False)
    # cv/mutable qualifiers are lifted out of the type into structured bits
    assert _parse_field("const int c") == ("c", "int", False, None, True, False, False)
    assert _parse_field("mutable long m") == ("m", "long", False, None, False, False, True)


# ── collect_build_context (headers + db) ─────────────────────────────────────


def test_collect_build_context_scans_headers_and_reads_db(tmp_path):
    h = tmp_path / "config.h"
    h.write_text(
        "struct Config {\n int version;\n#ifdef KEEP\n int legacy;\n#endif\n};"
    )
    db = tmp_path / "compile_commands.json"
    db.write_text(json.dumps([{"command": "cc -DKEEP -c config.c"}]))
    defines, registry = collect_build_context([h], db, extra_flags=["-DEXTRA"])
    assert defines == {"KEEP", "EXTRA"}
    assert _guard(registry, "Config", "legacy")["guard"] == "KEEP"


def test_collect_build_context_extra_flag_overrides_db_define(tmp_path):
    """A user ``-UKEEP`` extra flag overrides a compile-DB ``-DKEEP`` — the real
    parse applies extra flags after the DB options, so KEEP is not active and its
    guarded field is never reconciled (Codex review #498)."""
    h = tmp_path / "config.h"
    h.write_text("struct Config {\n#ifdef KEEP\n int legacy;\n#endif\n};")
    db = tmp_path / "compile_commands.json"
    db.write_text(json.dumps([{"command": "cc -DKEEP -c config.c"}]))
    defines, _ = collect_build_context([h], db, extra_flags=["-UKEEP"])
    assert "KEEP" not in defines


def test_collect_build_context_forced_include_marks_fields_ambiguous(tmp_path):
    """A forced include (``-include``/``-imacros``) is preprocessed before the
    header and could ``#undef`` a guard the scan never sees, so every scanned
    guarded field is flagged ``ambiguous`` (Codex review #498, P1)."""
    h = tmp_path / "config.h"
    h.write_text("struct Config {\n#ifdef KEEP\n int legacy;\n#endif\n};")
    # forced include via user --gcc-option pass-through
    _, reg = collect_build_context([h], None, extra_flags=["-DKEEP", "-include", "prelude.h"])
    assert _guard(reg, "Config", "legacy")["ambiguous"] is True
    # and via a compile-DB command line
    db = tmp_path / "compile_commands.json"
    db.write_text(json.dumps([{"command": "cc -DKEEP -include prelude.h -c config.c"}]))
    _, reg2 = collect_build_context([h], db)
    assert _guard(reg2, "Config", "legacy")["ambiguous"] is True
    # no forced include → normal reconcilable evidence
    _, reg3 = collect_build_context([h], None, extra_flags=["-DKEEP"])
    assert "ambiguous" not in _guard(reg3, "Config", "legacy")
    # a source_filter selecting the forced-include TU still detects it
    db2 = tmp_path / "cc2.json"
    db2.write_text(
        json.dumps(
            [
                {"file": "keep.c", "directory": str(tmp_path), "command": "cc -include p.h -c keep.c"},
                {"file": "other.c", "directory": str(tmp_path), "command": "cc -c other.c"},
            ]
        )
    )
    _, regf = collect_build_context([h], db2, source_filter="keep.c")
    assert _guard(regf, "Config", "legacy")["ambiguous"] is True
    # a malformed (non-list) compile DB is treated as no forced include
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"not": "a list"}))
    _, regb = collect_build_context([h], bad, extra_flags=["-DKEEP"])
    assert "ambiguous" not in _guard(regb, "Config", "legacy")


def test_collect_build_context_skips_unreadable_header(tmp_path):
    defines, registry = collect_build_context(
        [tmp_path / "does-not-exist.h"], None, extra_flags=["-DK"]
    )
    assert defines == {"K"}
    assert registry == {}


def test_user_define_flags_combines_tokens_and_gcc_options():
    """The dump collects the user's global flags in the **same order the real dump
    applies them**: the ``--gcc-options`` string first, then the repeatable
    ``--gcc-option`` tokens (``dumper._castxml_cmd`` order), so ``-D``/``-U`` of the
    same macro resolve identically on both sides (Codex review #498)."""
    from abicheck.cli_dump_helpers import _user_define_flags

    assert _user_define_flags((), None) == []
    assert _user_define_flags(("-DA",), None) == ["-DA"]
    # --gcc-options (-UKEEP -DB) is applied before the --gcc-option token (-DA).
    assert _user_define_flags(("-DA",), "-UKEEP -DB") == ["-UKEEP", "-DB", "-DA"]
    # Order-sensitivity: --gcc-options=-DKEEP then --gcc-option=-UKEEP must leave
    # KEEP inactive (token last wins), matching dumper.py.
    from abicheck.header_conditionals import defines_from_flags

    assert defines_from_flags(_user_define_flags(("-UKEEP",), "-DKEEP")) == set()
    # a malformed --gcc-options (unbalanced quote) is skipped, not fatal
    assert _user_define_flags(("-DA",), '"oops') == ["-DA"]


def test_user_gcc_options_override_db_define_end_to_end(tmp_path):
    """A user ``--gcc-options=-UKEEP`` reaches the collector and overrides a
    compile-DB ``-DKEEP``, so KEEP is inactive in ``build_context_defines``."""
    from abicheck.cli_dump_helpers import _attach_build_context, _user_define_flags
    from abicheck.model import AbiSnapshot

    h = tmp_path / "config.h"
    h.write_text("struct Config {\n#ifdef KEEP\n int legacy;\n#endif\n};")
    db = tmp_path / "compile_commands.json"
    db.write_text(json.dumps([{"command": "cc -DKEEP -c config.c"}]))

    snap = AbiSnapshot(library="lib", version="1")
    flags = _user_define_flags((), "-UKEEP")
    _attach_build_context(snap, db, [h], flags)
    assert "KEEP" not in snap.build_context_defines


def test_attach_build_context_populates_snapshot(tmp_path):
    """The dump-path helper harvests defines + scans headers and attaches both to
    the snapshot; an empty harvest leaves the defaults untouched."""
    from abicheck.cli_dump_helpers import _attach_build_context
    from abicheck.model import AbiSnapshot

    h = tmp_path / "config.h"
    h.write_text(
        "struct Config {\n int version;\n#ifdef KEEP\n int legacy;\n#endif\n};"
    )
    db = tmp_path / "compile_commands.json"
    db.write_text(json.dumps([{"command": "cc -DKEEP -c config.c"}]))

    snap = AbiSnapshot(library="lib", version="1")
    _attach_build_context(snap, db, [h], ["-DEXTRA"])
    assert snap.build_context_defines == {"KEEP", "EXTRA"}
    assert snap.conditional_fields["Config"]["legacy"]["guard"] == "KEEP"

    # No build evidence → snapshot defaults are left untouched.
    empty = AbiSnapshot(library="lib", version="1")
    _attach_build_context(empty, tmp_path / "missing.json", [tmp_path / "gone.h"], [])
    assert empty.build_context_defines == set()
    assert empty.conditional_fields == {}


# ── end-to-end: scanned evidence drives the reconciler ───────────────────────


def test_scanned_evidence_reconciles_context_free_false_positive(tmp_path):
    """A header scanned by the collection layer produces exactly the evidence the
    reconciler needs to clear a context-free-pruning false positive."""
    from abicheck.checker import Verdict, compare
    from abicheck.model import (
        AbiSnapshot,
        Function,
        RecordType,
        ScopeOrigin,
        TypeField,
        Visibility,
    )

    header = (
        "struct Config {\n int version;\n"
        "#ifdef CONFIG_KEEP_LEGACY\n int legacy;\n#endif\n};"
    )
    h = tmp_path / "config.h"
    h.write_text(header)
    db = tmp_path / "compile_commands.json"
    db.write_text(json.dumps([{"command": "cc -DCONFIG_KEEP_LEGACY -c config.c"}]))
    defines, registry = collect_build_context([h], db)

    def _fn():
        return Function(
            name="mk",
            mangled="mk",
            return_type="Config *",
            params=[],
            visibility=Visibility.PUBLIC,
            origin=ScopeOrigin.PUBLIC_HEADER,
        )

    def _snap(version, fields):
        s = AbiSnapshot(
            library="lib",
            version=version,
            from_headers=True,
            types=[
                RecordType(
                    name="Config",
                    kind="struct",
                    size_bits=64,
                    fields=fields,
                    origin=ScopeOrigin.PUBLIC_HEADER,
                )
            ],
            functions=[_fn()],
        )
        s.build_context_defines = set(defines)
        s.conditional_fields = registry
        return s

    # v1 declared `legacy` unconditionally; v2's context-free parse pruned it.
    old = _snap(
        "1",
        [TypeField(name="version", type="int"), TypeField(name="legacy", type="int")],
    )
    new = _snap("2", [TypeField(name="version", type="int")])

    assert compare(old, new, scope_to_public_surface=True).verdict == Verdict.BREAKING
    reconciled = compare(
        old, new, scope_to_public_surface=True, reconcile_build_context=True
    )
    assert reconciled.verdict == Verdict.NO_CHANGE
    assert reconciled.reconciled_count == 1
