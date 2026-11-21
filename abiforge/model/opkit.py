"""The edit-operator type and the role-editing helpers every operator module shares.

``EditOp`` is documented in ``abiforge.operators``; the operator tables
live there (core seeds), in ``abiforge.operators.elf`` (``c_elf``), in
``abiforge.operators.cxx`` (``cxx_more``) and in
``abiforge.operators.platform`` (ELF, loader and toolchain facts on
``c_elf``, ``cxx_more`` and the gated ``c_elf_alt``/``cxx_alt``).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace

from abiforge.model.config import (
    LEVEL_SET,
    Config,
    Flags,
    Standard,
    StdRange,
    describe_standards,
    has_feature,
    in_standards,
)
from abiforge.model.expect import (
    BinaryLicence,
    Expectation,
    Rule,
    binary_depth_cap,
    flag_invisible_layout,
    observability_floor,
)
from abiforge.model.finding import EntityClaim
from abiforge.model.seeds import RoleText, Seed
from abiforge.model.space import feature_standards

Roles = Mapping[str, RoleText]
Edit = Callable[[Roles], dict[str, RoleText]]


def _no_edit(_: Roles) -> dict[str, RoleText]:
    return {}


#: The evidence atoms that record a build's flags: DWARF (producer switches) and build data.
BUILD_RECORD: frozenset[str] = frozenset({"D", "BD"})


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
    # Language features (``abiforge.model.config.FEATURES``) the edit needs.
    features: tuple[str, ...] = ()
    # The standards the edit is defined in, beyond its features (whose ranges
    # come from ``space.FEATURE_STANDARDS``): an edit that a later standard
    # removed, or that needs one only the edit's own text introduces, names
    # the range and its citation here. Empty = no standard restriction.
    standards: tuple[StdRange, ...] = ()
    # Config-dependent ground truth, first match wins.
    rules: tuple[Rule, ...] = ()
    # Extra applicability (None = applicable, else the reason it is not).
    precondition: Callable[[Config], str | None] | None = None
    # A rebuild edit: the new side is built with ``rebuild(flags)``.
    rebuild: Callable[[Flags], Flags] | None = None
    # Per-side evidence: the new side is built and dumped at this evidence
    # level ("binary", "dwarf") instead of the config's, so the pair carries
    # asymmetric evidence (one side's headers or debug info absent). Applies
    # only to a config whose evidence is richer (``requires``).
    new_evidence: str | None = None
    # Needs the producer's C++ library to be libstdc++; probed at run time by
    # ``abiforge.run.toolchains.capability_gap``, never at collection.
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
    # Host CPUs (``platform.machine()``, lower-cased) the edit's asm or ``-m``
    # switches need: the corpus builds natively, so another host makes the
    # cell unavailable. Probed by ``capability_gap``, never in ``requires``,
    # so the cell list (and the PR floor check built from it) is host-free.
    host_arch: frozenset[str] = frozenset()
    # The language-space constructs the edit exercises, when its roles do not
    # say it (``abiforge.model.space.op_constructs``); empty = read the roles.
    constructs: tuple[str, ...] = ()
    # The evidence atoms that carry the edit when it is a build-flag edit
    # (``-fpack-struct``, ``-fshort-enums``): the flag is in neither the binary
    # nor a distributed header, so a configuration sees the edit only if it
    # records the build (DWARF ``D`` or build data ``BD``). Empty = the edit is
    # carried by the binary or by the header text itself.
    carrier: frozenset[str] = frozenset()
    # Verdict ceilings the edit may take only when an independent binary fact
    # is confirmed (``model.expect.BinaryLicence``): an -O2 rebuild may be a
    # RISK only where nm shows the canary import actually lost.
    licences: tuple[BinaryLicence, ...] = ()
    # Per-entity claims (``model.finding.EntityClaim``): what the report must
    # attribute to the entity the edit changed, and must not attribute to an
    # entity it left alone. Judged only on structured findings.
    claims: tuple[EntityClaim, ...] = ()
    # How many witness functions (``run.witness``) to pin: public functions
    # the edit provably leaves alone and ``nm`` finds in both libraries, each
    # a forbidden claim. 0 = none (every operator written before claims).
    witnesses: int = 0
    # Config-dependent exact ground truth (``model.exact``): the expectation
    # fields (``assertions``, ``expected``, verdict bounds) this config's
    # layout model gives. Applied before ``rules``; ``precondition`` names the
    # configs the model does not cover, so none is guessed.
    exact: Callable[[Config], Mapping[str, object]] | None = None

    def applies_to(self, seed: Seed) -> bool:
        if seed.gate_role is not None and seed.gate_role not in self.roles:
            return False
        return all(r in seed.roles for r in self.roles)

    def standard_groups(self) -> tuple[tuple[StdRange, ...], ...]:
        """The range groups the edit must lie in, one per restriction (all must hold).

        A group is a feature's ranges or the edit's own ``standards``; the edit
        is in standard *S* only when *S* lies in every group.
        """
        groups = [feature_standards(f) for f in self.features]
        if self.standards:
            groups.append(self.standards)
        return tuple(groups)

    def consumer_gap(self, std: Standard) -> str | None:
        """Why a consumer built with *std* cannot compile the edit's header, or None.

        Only the edit's language features gate a consumer. The consumer compiles
        the *old* header (``exec``), which a standard range does not change: an
        edit that a later standard defines (an inline variable under C++14, say)
        leaves the old declaration valid in C++14, so that consumer's source
        break must still be recorded rather than read as infeasible.
        """
        for feat in self.features:
            if not has_feature(std, feat):
                return f"the edited header needs {feat}, which the consumer's {std.name} lacks"
        return None

    def standard_ok(self, std: Standard) -> bool:
        """Whether the edit is defined in *std* (every range group contains it)."""
        return all(in_standards(group, std) for group in self.standard_groups())

    def requires(self, config: Config) -> str | None:
        for group in self.standard_groups():
            if not in_standards(group, config.std):
                return (
                    f"{config.std.name} is outside the standards this edit is "
                    f"defined in: {describe_standards(group)}"
                )
        if (
            self.new_evidence is not None
            and config.evidence_set <= LEVEL_SET[self.new_evidence]
        ):
            return (
                f"the new side is dumped at {self.new_evidence} evidence; "
                f"{config.evidence} evidence is no richer"
            )
        if self.precondition is not None:
            return self.precondition(config)
        return None

    def new_side_config(self, config: Config) -> Config:
        """The config the new side is built and dumped under (less evidence, if any)."""
        if self.new_evidence is None:
            return config
        return config.with_(evidence=self.new_evidence, stripped=False)

    def base(self) -> Expectation:
        return Expectation(
            expected=self.expected,
            any_of=self.any_of,
            verdict_min=self.verdict_min,
            verdict_max=self.verdict_max,
            claims=self.claims,
        )

    def expect(
        self, config: Config, facts: Mapping[str, bool] | None = None
    ) -> Expectation:
        """The ground truth for *config*: the base, the first matching rule, then the
        kinds the configuration observes (``observability_floor``, plan B1), then
        the licences *facts* (the binary oracle's, ``run.binfacts``) confirm,
        then ADR-095's stripped-headers layout-flag rule."""
        return flag_invisible_layout(
            self._expect_at(config, facts),
            config,
            lambda other: self._expect_at(other, facts),
        )

    def _expect_at(
        self, config: Config, facts: Mapping[str, bool] | None
    ) -> Expectation:
        exp = self.base()
        if self.exact is not None:
            exp = replace(exp, **self.exact(config))  # type: ignore[arg-type]
        for rule in self.rules:
            if rule.when(config):
                exp = rule.apply(exp)
                break
        floored = observability_floor(exp, config, carrier=self.carrier)
        for licence in self.licences:
            floored = licence.apply(floored, facts or {})
        return self.with_derived(binary_depth_cap(floored, config), config.objfmt)

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
# The layout kinds abicheck reports for a record edit. The DWARF layout diff
# also emits a STRUCT_* twin of each (STRUCT_SIZE_CHANGED, ...), but
# ``compare/finding_dedup`` folds it into the TYPE_* finding of the same
# record whenever the type model reports one -- every corpus cell does -- so
# the twin is not a kind an edit here can be expected to produce, and naming
# it as an alternative would credit coverage nothing checks.
_SIZE = frozenset({"TYPE_SIZE_CHANGED"})
# An enum's size: abicheck names the underlying-size change; the -fshort-enums
# flag itself (ENUM_SIZE_FLAG_CHANGED) only with build data (see the rules).
_ENUM_SIZE = frozenset({"ENUM_UNDERLYING_SIZE_CHANGED"})
_OFFSET = frozenset({"TYPE_FIELD_OFFSET_CHANGED"})
_ALIGN = frozenset({"TYPE_ALIGNMENT_CHANGED"})
_FIELD_TYPE = frozenset({"TYPE_FIELD_TYPE_CHANGED"})


def _binary(c: Config) -> bool:
    return c.evidence == "binary"


def _cxx_binary(c: Config) -> bool:
    return c.evidence == "binary" and c.std.lang == "c++"


def _binary_mangled(*kinds: str) -> Rule:
    # From symbols alone the change shows as the old mangled name gone; a tool
    # that demangles and pairs may name the specific change instead. A mangled
    # name exists only in C++: a C library's same change is not a symbol removal.
    return Rule(
        _cxx_binary,
        {"expected": frozenset(), "any_of": (_REMOVED | frozenset(kinds),)},
        "binary evidence sees the old mangled symbol disappear (or pairs it by demangled name)",
    )
