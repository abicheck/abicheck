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

"""Two invariants an SVS PR-387 findings audit falsified, and the bug classes
behind them.

**More evidence must not erase a supported public binary-contract loss.** A
class's vtable/typeinfo (``_ZTV``/``_ZTI``/``_ZTS``/``_ZTT``) is emitted by the
compiler, declared by no header, and bound by every old consumer that
constructs, destroys, ``dynamic_cast``es or throws the type. A header-aware
comparison builds its variable map from the header AST, so such an object was
in no map on either side and its disappearance was reported by nobody -- while
the identical binary-only comparison reported BREAKING. Adding relevant headers
turned a real break into a clean pass.

**A header-defined template owes no dynamic export.** The obligation check
asked whether the *display* name looked templated, and a header backend can
report a bare display name for an entity whose mangling says otherwise, so an
address-takeable header-defined function template acquired an unconditional
export obligation it cannot owe.

The tests are written against the invariants rather than the two reported
inputs: the export-reconciliation cases sweep every ABI-support object family
and an ordinary data export together, and the obligation cases drive the real
Itanium parser over generated manglings rather than one remembered string.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from abicheck.model.change_catalog.kinds import ChangeKind

# --------------------------------------------------------------------------
# 1. Export reconciliation: headers must not erase a public object loss
# --------------------------------------------------------------------------

_FOO_HEADER = """#pragma once
class Foo {
public:
    virtual ~Foo();
    virtual int value() const;
    int plain() const;
};
extern int foo_counter;
"""

_FOO_SOURCE = """#include "foo.h"
Foo::~Foo() {}
int Foo::value() const { return 42; }
int Foo::plain() const { return 1; }
int foo_counter = 0;
"""


#: The export-reconciliation fixtures below are ELF-specific by construction,
#: not merely ELF-flavoured: they build the single-variable experiment with a
#: GNU-ld version script (`-Wl,--version-script`) and a SONAME
#: (`-Wl,-soname`), neither of which Mach-O's ld64 accepts, and the behaviour
#: under test is a symbol's presence in `.dynsym` -- which is what the
#: detector itself reads (`snapshot.elf`). On macOS the compile fails
#: outright rather than the assertion being interesting, so this is a real
#: platform restriction on the fixture, not a tolerated failure. The
#: obligation tests further down carry no such restriction: vague linkage is
#: a language property, and they run everywhere a compiler does.
elf_fixture_only = pytest.mark.skipif(
    sys.platform != "linux",
    reason="the fixture needs GNU-ld version scripts and an ELF .dynsym",
)


def _require_gpp() -> None:
    if shutil.which("g++") is None:  # pragma: no cover - environment guard
        pytest.skip("needs a real g++ toolchain")


#: The toolchain-oracle test below asks a real compiler and a real `nm` about
#: *Itanium* manglings -- the `I…E` template-argument production is the whole
#: subject. Windows is excluded rather than papered over: that lane builds with
#: the MSVC developer environment active (the workflow runs `msvc-dev-cmd`),
#: where the compile does not succeed at all and MSVC's own mangling scheme is
#: a different question entirely. Nothing is lost on that platform --
#: `test_template_declaration_never_acquires_an_export_obligation` pins the
#: exact manglings, ELF- and Mach-O-spelled, with no toolchain at all, and runs
#: everywhere; and `names_a_template_specialization`'s MSVC path is covered by
#: `test_unparseable_mangling_falls_back_to_the_display_name`.
itanium_toolchain_only = pytest.mark.skipif(
    sys.platform == "win32",
    reason="asks a real compiler about Itanium manglings; that lane is MSVC",
)


def _build_pair(tmp_path: Path, localized: tuple[str, ...]) -> tuple[Path, Path]:
    """Build old/new ``libfoo.so`` from byte-identical sources and headers,
    differing only in which symbols the NEW link localizes.

    A version script is what makes this a clean single-variable experiment:
    the public header, the sources, the SONAME and every method are identical
    on both sides, so anything the comparison reports is the export change and
    nothing else.
    """
    (tmp_path / "foo.h").write_text(_FOO_HEADER, encoding="utf-8")
    (tmp_path / "foo.cpp").write_text(_FOO_SOURCE, encoding="utf-8")
    script = tmp_path / "hide.map"
    script.write_text(
        "{ local: " + " ".join(s + ";" for s in localized) + " };\n", encoding="utf-8"
    )
    common = [
        "g++",
        "-shared",
        "-fPIC",
        "-O0",
        "-g0",
        "foo.cpp",
        "-Wl,-soname,libfoo.so.1",
    ]
    old = tmp_path / "libfoo_old.so"
    new = tmp_path / "libfoo_new.so"
    subprocess.run([*common, "-o", str(old)], cwd=tmp_path, check=True)
    subprocess.run(
        [*common, "-o", str(new), f"-Wl,--version-script={script}"],
        cwd=tmp_path,
        check=True,
    )
    return old, new


def _compare_json(tmp_path: Path, old: Path, new: Path, *args: str) -> tuple[int, dict]:
    """Run the real ``abicheck compare`` CLI and return (exit code, report).

    Through the CLI deliberately: the audited defect was that one production
    entry point disagreed with another on the same inputs, which is not
    observable from a detector called directly.
    """
    from click.testing import CliRunner

    from abicheck.cli import main

    out = tmp_path / "report.json"
    result = CliRunner().invoke(
        main,
        ["compare", str(old), str(new), *args, "-o", f"json={out}"],
        catch_exceptions=False,
    )
    payload = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
    return result.exit_code, payload


def _kinds(report: dict) -> set[str]:
    return {c.get("kind") for c in report.get("changes") or []}


def _symbols_of_kind(report: dict, kind: str) -> set[str]:
    return {
        c.get("symbol") for c in report.get("changes") or [] if c.get("kind") == kind
    }


#: Every symbol family the reconciliation path has to cover, swept together
#: rather than one vtable case: a fix that preserved ``_ZTV`` and still lost
#: ``_ZTI``/``_ZTS`` or an ordinary data export would be the same defect in a
#: new place, which is exactly how this bug class reproduces.
_LOSS_CASES = {
    "vtable": ("_ZTV3Foo",),
    "typeinfo": ("_ZTI3Foo",),
    "typeinfo_name": ("_ZTS3Foo",),
    "ordinary_data": ("foo_counter",),
    "whole_abi_support_group": ("_ZTV3Foo", "_ZTI3Foo", "_ZTS3Foo"),
}


@elf_fixture_only
@pytest.mark.integration
@pytest.mark.parametrize("case", sorted(_LOSS_CASES), ids=sorted(_LOSS_CASES))
def test_public_object_loss_survives_adding_headers(tmp_path: Path, case: str) -> None:
    """The invariant: for every lost public export, the header-aware verdict is
    no weaker than the binary-only verdict on the same two binaries.

    Stated as a comparison between the two runs rather than as an expected
    finding list, because the defect was precisely that the two disagreed --
    an oracle naming one expected kind would have passed against a build that
    reported the loss in only one of the two modes.
    """
    _require_gpp()
    localized = _LOSS_CASES[case]
    old, new = _build_pair(tmp_path, localized)

    binary_code, binary_report = _compare_json(tmp_path, old, new)
    header_code, header_report = _compare_json(tmp_path, old, new, "-H", str(tmp_path))

    assert binary_code == 4, binary_report.get("verdict")
    assert binary_report["verdict"] == "BREAKING"
    # The whole point: headers may add evidence, never subtract a break.
    assert header_code == binary_code
    assert header_report["verdict"] == "BREAKING"
    # And the lost symbol is named, not merely counted. The *kind* is
    # deliberately not pinned: a header-declared datum (``foo_counter``) is
    # owned by the declaration-aware variable diff, which knows more and says
    # `var_visibility_changed`; an undeclared ABI-support object has only the
    # export table to go on and gets the weak `var_removed_elf_only`. Pinning
    # one kind would assert which detector answered rather than that the loss
    # survived, and the defect was entirely about a loss not surviving.
    reported = {c.get("symbol") for c in header_report.get("changes") or []}
    assert set(localized) <= reported


@elf_fixture_only
@pytest.mark.integration
def test_unchanged_inputs_stay_unchanged_with_headers(tmp_path: Path) -> None:
    """The vacuity guard for the sweep above: the same machinery must report
    nothing when nothing was localized. Without this, a detector that flagged
    every exported object would pass every case above."""
    _require_gpp()
    old, _new = _build_pair(tmp_path, ("_ZTV3Foo",))
    code, report = _compare_json(tmp_path, old, old, "-H", str(tmp_path))
    assert code == 0
    assert report["verdict"] == "NO_CHANGE"
    assert not report.get("changes")


@elf_fixture_only
@pytest.mark.integration
def test_loss_survives_dump_and_serialized_compare(tmp_path: Path) -> None:
    """The fix must live in the model, not in one direct-binary CLI path.

    ``dump`` each side to a snapshot with headers, then ``compare`` the two
    snapshots -- the workflow where no binary is re-read at comparison time,
    so anything recovered by re-probing a file on disk is unavailable and only
    a fact that survived serialization can still be seen.
    """
    _require_gpp()
    from click.testing import CliRunner

    from abicheck.cli import main

    old, new = _build_pair(tmp_path, ("_ZTV3Foo", "_ZTI3Foo", "_ZTS3Foo"))
    runner = CliRunner()
    snaps = []
    for binary in (old, new):
        snap = tmp_path / f"{binary.stem}.json"
        res = runner.invoke(
            main,
            ["dump", str(binary), "-H", str(tmp_path), "-o", str(snap)],
            catch_exceptions=False,
        )
        assert snap.exists(), res.output
        snaps.append(snap)

    code, report = _compare_json(tmp_path, snaps[0], snaps[1])
    assert code == 4, report
    assert report["verdict"] == "BREAKING"
    assert "_ZTV3Foo" in _symbols_of_kind(report, "var_removed_elf_only")


# --------------------------------------------------------------------------
# 2. Export obligations: a header-defined template owes no dynamic export
# --------------------------------------------------------------------------

_TEMPLATE_HEADER = """#pragma once
enum class OptionalBool { Unset, True, False };
template <typename T> bool is_specified(T v) { return v != T::Unset; }
"""


@itanium_toolchain_only
@pytest.mark.integration
@pytest.mark.parametrize(
    ("decl", "call", "force"),
    [
        # The audited case: a function template whose specialization's address
        # the consumer takes. Its mangling carries template arguments; its
        # display name, as a backend can report it, does not.
        (
            "template <typename T> bool tpl(T v) { return v == v; }",
            "&tpl<int>",
            "",
        ),
        # A member of a class template -- same vague linkage, a different
        # mangling shape (the template block is on an inner component).
        (
            "template <typename T> struct Box { bool ok(T v) const { return v == v; } };",
            "&Box<int>::ok",
            # An explicit instantiation in the *consumer's own* translation
            # unit. Taking a pointer-to-member's address alone does not force
            # GCC to emit the member at -O0, so without this the test gathered
            # no mangling to check and passed vacuously -- and the explicit
            # instantiation makes the point sharper rather than weaker: the
            # consumer emits the definition itself, from the header, with no
            # library on the link line at all.
            "template struct Box<int>;",
        ),
        # A variable template: the third vague-linkage shape, and the one the
        # variable-side obligation check reads.
        (
            "template <typename T> T zero = T();",
            "&zero<int>",
            "template int zero<int>;",
        ),
    ],
    ids=["function_template", "class_template_member", "variable_template"],
)
def test_header_defined_template_needs_no_library_export(
    tmp_path: Path, decl: str, call: str, force: str
) -> None:
    """The oracle is the toolchain, not our own model: build a consumer that
    takes the address of the specialization with **no library at all** on the
    link line. If it links and runs, the library owes no export for it and any
    ``public_not_exported`` obligation is false by construction.

    Independent of the check under test -- it is the compiler's answer to the
    same question, which is what makes it an oracle rather than a restatement
    of the implementation.
    """
    _require_gpp()
    header = tmp_path / "api_defs.h"
    header.write_text("#pragma once\n" + decl + "\n", encoding="utf-8")
    consumer = tmp_path / "consumer.cpp"
    # The address is routed through a `volatile` sink. The first version wrote
    # `auto p = &Box<int>::ok; return p ? 0 : 1;`, which the compiler
    # constant-folds even at -O0, so the specialization was never emitted and
    # the test's own evidence-gathering found nothing to check -- a vacuous
    # pass waiting to happen.
    consumer.write_text(
        '#include "api_defs.h"\n' + force + "\n" + "int main() {\n"
        f"    auto p = {call};\n"
        "    void *volatile sink = (void *)&p;\n"
        "    return sink == nullptr ? 1 : 0;\n"
        "}\n",
        encoding="utf-8",
    )
    built = subprocess.run(
        ["g++", "-O0", "-fno-inline", "-o", "consumer", "consumer.cpp"],
        cwd=tmp_path,
        capture_output=True,
    )
    assert built.returncode == 0, built.stderr.decode()
    ran = subprocess.run(
        [str(tmp_path / "consumer")], cwd=tmp_path, capture_output=True, timeout=60
    )
    assert ran.returncode == 0, ran.stderr.decode()

    # Now the model's own answer, over the mangling the toolchain emitted for
    # that exact entity -- read out of the built object rather than written
    # down here, so the test cannot drift from what a real compiler produces.
    from abicheck.buildsource.template_linkage import (
        names_a_template_specialization,
    )

    nm = subprocess.run(
        ["nm", "-C", "--defined-only", str(tmp_path / "consumer")],
        capture_output=True,
        text=True,
    )
    raw = subprocess.run(
        ["nm", "--defined-only", str(tmp_path / "consumer")],
        capture_output=True,
        text=True,
    )
    assert nm.returncode == 0 and raw.returncode == 0
    # Which defined symbols are template specializations is decided by the
    # *demangler* -- an independent authority -- and then paired back to the
    # raw mangling by address. Selecting them with our own parser instead
    # would make the assertion below circular, and an earlier version that
    # guessed from the mangled text ("contains I, ends with E") silently
    # matched nothing for `_ZNK3BoxIiE2okEi`, leaving the case vacuous.
    by_address = {}
    for line in raw.stdout.splitlines():
        parts = line.split()
        # `_Z` on ELF, `__Z` on Mach-O: Darwin's linker prepends one
        # underscore to every global symbol. Missing that was not a silent
        # wrong answer -- `assert instantiations` below caught the empty
        # gather on macOS, which is exactly what that guard is for.
        if len(parts) == 3 and parts[2].startswith(("_Z", "__Z")):
            by_address[parts[0]] = parts[2]
    instantiations = []
    for line in nm.stdout.splitlines():
        parts = line.split(maxsplit=2)
        if len(parts) == 3 and "<" in parts[2] and parts[0] in by_address:
            instantiations.append((by_address[parts[0]], parts[2]))
    assert instantiations, f"{nm.stdout}\n---\n{raw.stdout}"
    for mangled, demangled in instantiations:
        # The bare display name is the shape that produced the false finding:
        # the obligation must be declined from the mangling even when the
        # display spelling carries no angle brackets at all.
        bare = demangled.split("<")[0].split()[-1].rsplit("::", 1)[-1]
        assert "<" not in bare
        assert names_a_template_specialization(bare, mangled) is True, (
            mangled,
            demangled,
        )


def test_real_missing_export_is_still_an_obligation() -> None:
    """The positive control, and the thing a "suppress templates" fix would
    break: an ordinary declared, non-inline, non-template public function that
    the binary does not export is still a missing required export."""
    from abicheck.buildsource.template_linkage import (
        names_a_template_specialization,
    )

    assert names_a_template_specialization("connect", "_Z7connectv") is False
    assert names_a_template_specialization("Api::run", "_ZN3Api3runEv") is False


def test_unparseable_mangling_falls_back_to_the_display_name() -> None:
    """A non-Itanium spelling has no structural answer, so the display-name
    check must still run rather than the unknown being read as "not a
    template" -- an unknown that silently means False is how the original
    defect behaved."""
    from abicheck.buildsource.template_linkage import (
        names_a_template_specialization,
    )

    assert names_a_template_specialization("vec<int>", "?vec@@YAXXZ") is True
    assert names_a_template_specialization("plain", "?plain@@YAXXZ") is False


def test_bare_display_name_template_declines_the_obligation() -> None:
    """The exact shape the audit reported, at the unit level: a parser-supplied
    bare display name whose mangling is a template specialization.

    Its companion above establishes the same fact through a real compiler and
    a real consumer; this one pins it for the case where no toolchain is
    available, and against the *pair* rather than either half, since the whole
    defect was the two disagreeing.
    """
    from abicheck.buildsource.template_linkage import names_a_template_specialization

    assert (
        names_a_template_specialization(
            "is_specified", "_Z12is_specifiedI12OptionalBoolEbT_"
        )
        is True
    )
    # And the display name alone genuinely carries no such signal -- the
    # vacuity guard for the assertion above.
    assert "<" not in "is_specified"


def test_end_to_end_public_not_exported_ignores_a_header_defined_template() -> None:
    """Through ``run_crosschecks``, the real check, rather than the predicate:
    a public-header function template the binary does not export must produce
    no ``public_not_exported`` finding, while an ordinary public function in
    the same snapshot, equally unexported, still must."""
    from abicheck.buildsource.cross_source_checks import run_crosschecks
    from abicheck.elf_metadata import ElfMetadata, ElfSymbol
    from abicheck.model import AbiSnapshot, Function, ScopeOrigin

    template = Function(
        name="is_specified",
        mangled="_Z12is_specifiedI12OptionalBoolEbT_",
        return_type="bool",
        origin=ScopeOrigin.PUBLIC_HEADER,
    )
    ordinary = Function(
        name="connect",
        mangled="_Z7connectv",
        return_type="void",
        origin=ScopeOrigin.PUBLIC_HEADER,
    )
    # An export table that is captured and real, carrying some *other*
    # symbol: "exports nothing" and "no table observed" are different
    # facts, and the check must be answering the first one here.
    snap = AbiSnapshot(
        library="libfoo.so",
        version="1.0",
        # `from_headers` is what tells the check the declarations came from a
        # real public-header parse; without it there is no promise to compare
        # the export table against and the check correctly stays silent, which
        # would have made the negative assertion below vacuous.
        from_headers=True,
        elf=ElfMetadata(machine="x86-64", symbols=[ElfSymbol(name="_Z5otherv")]),
    )
    snap.functions = [template, ordinary]
    result = run_crosschecks(snap)
    findings = [c for c in result.findings if c.kind is ChangeKind.PUBLIC_NOT_EXPORTED]
    symbols = {c.symbol for c in findings}
    assert "_Z12is_specifiedI12OptionalBoolEbT_" not in symbols
    assert "_Z7connectv" in symbols, symbols


@pytest.mark.parametrize(
    ("name", "mangled"),
    [
        # Display name and mangling agree it is a specialization. This case
        # used to live in `test_cross_source_checks.py`'s
        # `test_public_not_exported_excludes_non_exporting_decls`
        # parametrization as `name="vec<int>"` with the mangling left at the
        # ordinary `_Z3barv` -- an incoherent object no parser produces, whose
        # two halves said different things. It moved here, beside the
        # bare-display-name shape it belongs with, rather than being made
        # coherent in place: `test_cross_source_checks.py` is on a `no_growth`
        # debt baseline, and these cases have an owner now.
        ("vec<int>", "_Z3vecIiEvv"),
        # The same entity as a backend can really report it.
        ("is_specified", "_Z12is_specifiedI12OptionalBoolEbT_"),
        # Template-ness on an inner component (member of a class template).
        ("Box::ok", "_ZNK3BoxIiE2okEi"),
        # The same manglings as Darwin's linker decorates them, with one
        # leading underscore prepended. Pinned here rather than left to the
        # macOS CI lane so the spelling is covered on every platform: a
        # Mach-O-sourced `Function.mangled` that kept the decoration and was
        # read as "not a template" would reintroduce the false obligation for
        # exactly the entities this fix exists for.
        ("is_specified", "__Z12is_specifiedI12OptionalBoolEbT_"),
        ("Box::ok", "__ZNK3BoxIiE2okEi"),
    ],
    ids=[
        "coherent_spelling",
        "bare_display_name",
        "inner_component",
        "macho_decorated_bare_name",
        "macho_decorated_inner_component",
    ],
)
def test_template_declaration_never_acquires_an_export_obligation(
    name: str, mangled: str
) -> None:
    """A declaration the mangling says is a template specialization owes no
    dynamic export, however its display name happens to be spelled."""
    from abicheck.buildsource.cross_source_checks import (
        _has_export_obligation,
        _var_has_export_obligation,
    )
    from abicheck.model import AccessLevel, Function, ScopeOrigin, Variable

    fn = Function(
        name=name,
        mangled=mangled,
        return_type="void",
        origin=ScopeOrigin.PUBLIC_HEADER,
        access=AccessLevel.PUBLIC,
    )
    assert _has_export_obligation(fn) is False
    var = Variable(
        name=name,
        mangled=mangled,
        type="int",
        origin=ScopeOrigin.PUBLIC_HEADER,
        access=AccessLevel.PUBLIC,
    )
    assert _var_has_export_obligation(var) is False


def test_undeclared_function_export_loss_is_reported_not_crashed() -> None:
    """The function half of the same reconciliation, at the unit level.

    It exists because the integration sweep above localizes only data symbols,
    and the function path took a different branch: `func_removed_elf_only`
    registers no `description_template`, so emitting it without an explicit
    `description=` raised `ValueError` out of `make_change` -- a crashed
    comparison rather than a missing finding, and every test in this file
    passed while that was true. A symbol-class sweep is the cheap guard.
    """
    from abicheck.compare.undeclared_exports import _diff_undeclared_exports
    from abicheck.elf_metadata import ElfMetadata, ElfSymbol
    from abicheck.model import AbiSnapshot

    def snap(symbols: list[ElfSymbol]) -> AbiSnapshot:
        return AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            elf=ElfMetadata(machine="x86-64", symbols=symbols),
        )

    kept = ElfSymbol(name="_Z4keepv")
    lost_func = ElfSymbol(name="_Z4gonev")
    lost_data = ElfSymbol(name="_ZTV3Foo", sym_type="object")
    old = snap([kept, lost_func, lost_data])
    new = snap([kept])

    changes = _diff_undeclared_exports(old, new)
    by_symbol = {c.symbol: c for c in changes}
    assert set(by_symbol) == {"_Z4gonev", "_ZTV3Foo"}, by_symbol
    assert by_symbol["_Z4gonev"].kind.value == "func_removed_elf_only"
    assert by_symbol["_ZTV3Foo"].kind.value == "var_removed_elf_only"
    # Every emitted finding carries a rendered description -- the property the
    # missing template violated, stated over the whole result rather than for
    # the one kind that happened to break.
    for change in changes:
        assert change.description, change


def test_a_declared_symbol_is_left_to_the_declaration_aware_diff() -> None:
    """The complement, and the guard against this detector double-reporting:
    a symbol either side's headers declare is not its business, however it
    changed."""
    from abicheck.compare.undeclared_exports import _diff_undeclared_exports
    from abicheck.elf_metadata import ElfMetadata, ElfSymbol
    from abicheck.model import AbiSnapshot, Function, ScopeOrigin

    declared = Function(
        name="gone",
        mangled="_Z4gonev",
        return_type="void",
        origin=ScopeOrigin.PUBLIC_HEADER,
    )

    def snap(symbols: list[ElfSymbol], functions: list[Function]) -> AbiSnapshot:
        s = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            elf=ElfMetadata(machine="x86-64", symbols=symbols),
        )
        s.functions = functions
        return s

    old = snap([ElfSymbol(name="_Z4gonev")], [declared])
    new = snap([], [declared])
    assert _diff_undeclared_exports(old, new) == []


# --------------------------------------------------------------------------
# 3. Identity: the new kind's must-merge / must-not-merge pair
# --------------------------------------------------------------------------
#
# `var_removed_elf_only` joins two mappings that decide whether two findings
# are one event: `finding_identity._EQUIVALENT_CHANGE_CATEGORIES` (so a
# `finding_id:` suppression written when one run had header evidence keeps
# matching when the next one does not) and `diff_filtering`'s cross-detector
# dedup category. Both directions are independent claims, and testing only
# "these collapse" is satisfied by a mapping that collapses everything -- so
# the distinctness half is asserted over the same mechanism, not assumed.


def _removal_change(kind_value: str, symbol: str):
    from abicheck.checker_types import Change
    from abicheck.model.change_catalog.kinds import ChangeKind as _CK

    return Change(
        kind=_CK(kind_value),
        symbol=symbol,
        description=f"{kind_value}: {symbol}",
    )


def test_the_two_evidence_tiers_of_one_removal_share_an_identity() -> None:
    """Must-merge: the same lost data symbol, seen once with header evidence
    and once without, is one event -- so a suppression written against either
    spelling keeps matching the other."""
    from abicheck.finding_identity import resolve_change_identity

    weak = resolve_change_identity(_removal_change("var_removed_elf_only", "_ZTV3Foo"))
    strong = resolve_change_identity(_removal_change("var_removed", "_ZTV3Foo"))
    assert weak.primary_id == strong.primary_id


@pytest.mark.parametrize(
    ("left", "right", "why"),
    [
        (
            ("var_removed_elf_only", "_ZTV3Foo"),
            ("var_removed_elf_only", "_ZTV3Bar"),
            "two different types' vtables are two losses",
        ),
        (
            ("var_removed_elf_only", "_ZTV3Foo"),
            ("var_removed_elf_only", "_ZTI3Foo"),
            "one type's vtable and its typeinfo are distinct objects",
        ),
        (
            ("var_removed_elf_only", "_Z4gonev"),
            ("func_removed_elf_only", "_Z4gonev"),
            "a data loss and a function loss are different events even at the "
            "same name -- the two categories must not be merged",
        ),
        (
            ("var_removed_elf_only", "_ZTV3Foo"),
            ("var_added_elf_only", "_ZTV3Foo"),
            "the same symbol going and coming are opposite events",
        ),
    ],
    ids=[
        "distinct_owners",
        "distinct_objects",
        "distinct_symbol_class",
        "opposite_direction",
    ],
)
def test_genuinely_distinct_losses_keep_distinct_identities(
    left: tuple[str, str], right: tuple[str, str], why: str
) -> None:
    """Must-not-merge, and the vacuity guard for the test above: a mapping
    that collapsed everything would satisfy the must-merge claim alone.

    The last case is the one the audit warns about directly -- a derived
    class's own export loss must not be erased because a related object also
    changed.
    """
    from abicheck.finding_identity import resolve_change_identity

    a = resolve_change_identity(_removal_change(*left))
    b = resolve_change_identity(_removal_change(*right))
    assert a.primary_id != b.primary_id, why


def test_dedup_collapses_the_tier_pair_and_keeps_distinct_losses() -> None:
    """The same pair through the real cross-detector dedup rather than the
    identity resolver, since that is the consumer whose behaviour users see:
    one symbol reported at both tiers collapses to one finding, while two
    genuinely different lost symbols both survive."""
    from abicheck.diff_filtering import _deduplicate_cross_detector

    duplicated = [
        _removal_change("var_removed", "_ZTV3Foo"),
        _removal_change("var_removed_elf_only", "_ZTV3Foo"),
        _removal_change("var_removed_elf_only", "_ZTV3Bar"),
    ]
    kept = _deduplicate_cross_detector(duplicated)
    symbols = [c.symbol for c in kept]
    assert sorted(symbols) == ["_ZTV3Bar", "_ZTV3Foo"], symbols


# --------------------------------------------------------------------------
# 4. The false positive the removal half introduced, and its negative control
# --------------------------------------------------------------------------


def _ctor_snapshot(symbols: list[str], *, declare_widget: bool):
    """A header-aware snapshot exporting *symbols*, optionally declaring the
    class ``Widget`` the way a real header parse does.

    The constructor deliberately enters ``function_map`` under the synthetic
    placeholder key a real backend uses (`__abicheck_ctor__Widget()`), never
    an Itanium mangling -- reproducing the shape that made every declared
    class's ctors and dtors invisible to a literal name match.
    """
    from abicheck.elf_metadata import ElfMetadata, ElfSymbol
    from abicheck.model import AbiSnapshot, Function, RecordType, ScopeOrigin

    snap = AbiSnapshot(
        library="libwidget.so",
        version="1.0",
        from_headers=True,
        elf=ElfMetadata(machine="x86-64", symbols=[ElfSymbol(name=n) for n in symbols]),
    )
    if declare_widget:
        snap.types = [RecordType(name="Widget", kind="class")]
        snap.functions = [
            Function(
                name="Widget",
                mangled="__abicheck_ctor__Widget()",
                return_type="",
                origin=ScopeOrigin.PUBLIC_HEADER,
            )
        ]
    return snap


def test_an_inlined_ctor_of_a_declared_class_is_not_an_export_loss() -> None:
    """Rebuilding byte-identical source at a higher optimization level is not
    an ABI change, and it is what makes a class's out-of-line ctor/dtor copies
    come and go: GCC exports `_ZN6WidgetC1Ev`/`C2Ev` at -O0 and inlines both
    away at -O2.

    The removal half reported two BREAKING removals for exactly that, because
    a header-parsed constructor never enters `function_map` under any Itanium
    mangling -- the backends key it under a synthetic placeholder, since one
    declaration corresponds to several ABI symbols (C1/C2/C3, D0/D1/D2). So
    this was not specific to `Widget`: every ctor and dtor of every declared
    class looked undeclared. Caught by
    `tests/test_cross_compiler_fp.py::TestOptimizationLevelFP`, whose win32
    `xfail` already named the class ("MinGW -O2 inlines constructors away
    from PE exports").
    """
    from abicheck.compare.undeclared_exports import _diff_undeclared_exports

    old = _ctor_snapshot(
        ["_ZN6WidgetC1Ev", "_ZN6WidgetC2Ev", "_ZN6WidgetD1Ev"], declare_widget=True
    )
    new = _ctor_snapshot([], declare_widget=True)
    assert _diff_undeclared_exports(old, new) == []


def test_a_ctor_of_an_undeclared_class_is_still_an_export_loss() -> None:
    """The negative control, and the thing a blanket "skip every ctor/dtor"
    fix would break: the exemption is owner-declared, not ctor-shaped. A class
    no header declares has no declaration-aware diff to hand the symbol to."""
    from abicheck.compare.undeclared_exports import _diff_undeclared_exports

    old = _ctor_snapshot(["_ZN6WidgetC1Ev"], declare_widget=False)
    new = _ctor_snapshot([], declare_widget=False)
    reported = {c.symbol for c in _diff_undeclared_exports(old, new)}
    assert reported == {"_ZN6WidgetC1Ev"}


def test_the_exemption_does_not_reach_vtables_or_typeinfo() -> None:
    """The exemption must stay disjoint from the objects this detector exists
    for. A vtable is an Itanium *special name*, so `itanium_scope_components`
    declines to parse it and the ctor/dtor predicate cannot reach it -- but
    that disjointness is a property worth pinning, not inferring, since both
    symbols name the same declared class."""
    from abicheck.compare.undeclared_exports import _diff_undeclared_exports

    old = _ctor_snapshot(
        ["_ZN6WidgetC1Ev", "_ZTV6Widget", "_ZTI6Widget"], declare_widget=True
    )
    new = _ctor_snapshot([], declare_widget=True)
    reported = {c.symbol for c in _diff_undeclared_exports(old, new)}
    assert reported == {"_ZTV6Widget", "_ZTI6Widget"}
