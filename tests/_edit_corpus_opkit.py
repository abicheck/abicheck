"""The edit-operator type and the role-editing helpers every operator module shares.

``EditOp`` is documented in ``_edit_corpus_operators``; the operator tables
live there (core seeds), in ``_edit_corpus_operators_elf`` (``c_elf``), in
``_edit_corpus_operators_cxx`` (``cxx_more``) and in
``_edit_corpus_operators_platform`` (ELF, loader and toolchain facts on
``c_elf``, ``cxx_more`` and the gated ``c_elf_alt``/``cxx_alt``).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace

from tests._edit_corpus_config import Config, Flags, has_feature
from tests._edit_corpus_expect import Expectation, Rule, evidence_floor
from tests._edit_corpus_seeds import RoleText, Seed

Roles = Mapping[str, RoleText]
Edit = Callable[[Roles], dict[str, RoleText]]


def _no_edit(_: Roles) -> dict[str, RoleText]:
    return {}


@dataclass(frozen=True)
class EditOp:
    name: str
    roles: tuple[str, ...]
    edit: Edit
    why: str
    expected: frozenset[str] = frozenset()
    any_of: tuple[frozenset[str], ...] = ()
    verdict_min: str = "BREAKING"
    verdict_max: str | None = None
    # Roles this op re-spells; used only to check the edit actually changes text.
    touches: tuple[str, ...] = field(default=())
    # Weakest evidence that can show the edit: "binary" (exported symbols),
    # "dwarf" (types of exported entities), "headers" (declarations only).
    min_evidence: str | Callable[[Config], str] = "dwarf"
    # Language features (``_edit_corpus_config.FEATURES``) the edit needs.
    features: tuple[str, ...] = ()
    # Config-dependent ground truth, first match wins.
    rules: tuple[Rule, ...] = ()
    # Extra applicability (None = applicable, else the reason it is not).
    precondition: Callable[[Config], str | None] | None = None
    # A rebuild edit: the new side is built with ``rebuild(flags)``.
    rebuild: Callable[[Flags], Flags] | None = None
    # Needs the producer's C++ library to be libstdc++; probed at run time by
    # ``_edit_corpus_toolchains.capability_gap``, never at collection.
    needs_libstdcxx: bool = False
    # Compile/link flags the edit adds that a toolchain or linker may refuse
    # (a non-x86 target, an older binutils); probed at run time the same way,
    # and a refusing host makes the cell unavailable, not a build failure.
    probe_flags: tuple[str, ...] = ()
    # (macro, predefined): the toolchain default the edit assumes, e.g.
    # ("__CET__", True) for an edit that turns CET off. Probed at run time; a
    # toolchain whose default differs leaves the binary unchanged, so the
    # cell is unavailable rather than given a family-guessed expectation.
    default_macro: tuple[str, bool] | None = None
    # Host CPUs (``platform.machine()``) the edit's asm or ``-m`` switches
    # need: the corpus builds natively, so another host makes the cell
    # unavailable. Probed by ``capability_gap``, never in ``requires``, so the
    # cell list (and the PR floor check built from it) is host-independent.
    host_arch: frozenset[str] = frozenset()
    # The language-space constructs the edit exercises, when its roles do not
    # say it (``_edit_corpus_space.op_constructs``); empty = read the roles.
    constructs: tuple[str, ...] = ()

    def applies_to(self, seed: Seed) -> bool:
        if seed.gate_role is not None and seed.gate_role not in self.roles:
            return False
        return all(r in seed.roles for r in self.roles)

    def requires(self, config: Config) -> str | None:
        for feat in self.features:
            if not has_feature(config.std, feat):
                return f"needs {feat}, which {config.std.name} lacks"
        if self.precondition is not None:
            return self.precondition(config)
        return None

    def base(self) -> Expectation:
        return Expectation(
            expected=self.expected,
            any_of=self.any_of,
            verdict_min=self.verdict_min,
            verdict_max=self.verdict_max,
        )

    def expect(self, config: Config) -> Expectation:
        exp = self.base()
        floor = (
            self.min_evidence(config)
            if callable(self.min_evidence)
            else self.min_evidence
        )
        for rule in self.rules:
            if rule.when(config):
                exp = rule.apply(exp)
                floor = rule.min_evidence or floor
                break
        return self.with_derived(evidence_floor(exp, config, floor), config.objfmt)

    def with_derived(self, exp: Expectation, objfmt: str = "elf") -> Expectation:
        """*exp* plus the kinds that follow from it rather than from the edit.

        A binary break shipped under the same SONAME: abicheck's release
        advisory follows from the verdict, so an edit that must read BREAKING
        and leaves the SONAME alone owes ``SONAME_BUMP_RECOMMENDED`` too. Only
        an ELF library has a SONAME (``DT_SONAME``); a Mach-O install name or
        a PE DLL name gets no such advisory.
        """
        if (
            objfmt == "elf"
            and exp.observable
            and exp.verdict_min == "BREAKING"
            and "build.soname" not in self.roles
        ):
            return replace(exp, expected=exp.expected | {SONAME_ADVISORY})
        return exp

    def new_flags(self, flags: Flags) -> Flags:
        return self.rebuild(flags) if self.rebuild else flags


def _set(
    roles: Roles, role: str, header: str | None = None, source: str | None = None
) -> dict[str, RoleText]:
    """Replace one role's header and/or source text, keeping the other side."""
    old_h, old_c = roles[role]
    return {
        role: (old_h if header is None else header, old_c if source is None else source)
    }


def _merge(*parts: dict[str, RoleText]) -> dict[str, RoleText]:
    out: dict[str, RoleText] = {}
    for p in parts:
        out.update(p)
    return out


def _with(**kw: object) -> Callable[[Flags], Flags]:
    def rebuild(f: Flags) -> Flags:
        return replace(f, **kw)  # type: ignore[arg-type]

    return rebuild


# Removal of an exported function; *_ELF_ONLY is its name when the only
# evidence is the symbol table.
_REMOVED = frozenset({"FUNC_REMOVED", "FUNC_REMOVED_ELF_ONLY"})
SONAME_ADVISORY = "SONAME_BUMP_RECOMMENDED"
#: Kinds an expectation owes because of its verdict, not because of the edit
#: (``EditOp.with_derived``). Reported apart from declared and verified
#: coverage, so the headline counts only kinds some edit stands behind.
DERIVED_KINDS = frozenset({SONAME_ADVISORY})
_SIZE = frozenset({"TYPE_SIZE_CHANGED", "STRUCT_SIZE_CHANGED"})
_ENUM_SIZE = frozenset(
    {"TYPE_SIZE_CHANGED", "ENUM_UNDERLYING_SIZE_CHANGED", "ENUM_SIZE_FLAG_CHANGED"}
)
_OFFSET = frozenset({"STRUCT_FIELD_OFFSET_CHANGED", "TYPE_FIELD_OFFSET_CHANGED"})
_ALIGN = frozenset({"STRUCT_ALIGNMENT_CHANGED", "TYPE_ALIGNMENT_CHANGED"})
_FIELD_TYPE = frozenset({"STRUCT_FIELD_TYPE_CHANGED", "TYPE_FIELD_TYPE_CHANGED"})


def _binary(c: Config) -> bool:
    return c.evidence == "binary"


def _binary_mangled(*kinds: str) -> Rule:
    # From symbols alone the change shows as the old mangled name gone; a tool
    # that demangles and pairs may name the specific change instead.
    return Rule(
        _binary,
        {"expected": frozenset(), "any_of": (_REMOVED | frozenset(kinds),)},
        "binary evidence sees the old mangled symbol disappear (or pairs it by demangled name)",
    )


def _cxx_binary_else_dwarf(c: Config) -> str:
    # A C++ signature is in the mangled name; a C one is only in debug info.
    return "binary" if c.lang == "c++" else "dwarf"
