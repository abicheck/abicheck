"""Coverage of the generated edit corpus: ChangeKind scope/ratchet and the language space.

Fast: no compiler. The declared side of the ChangeKind ratchet is computed
from the operators (``_edit_corpus_coverage.declared_kinds``) and held to the
committed ledger; the run side (``check_floor``) and the language-space
report are checked on hand-built records against independently counted
oracles.
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path

import pytest

from abicheck.model.change_catalog.kinds import ChangeKind
from tests import _edit_corpus_coverage as cov, _edit_corpus_space as space
from tests._edit_corpus_cells import seed_conflict
from tests._edit_corpus_config import (
    EVIDENCE,
    FAMILIES,
    FEATURES,
    HEADER_BACKENDS,
    STANDARD_BY_NAME,
    STANDARDS,
    Config,
    Consumer,
    Flags,
    Toolchain,
    default_config,
)
from tests._edit_corpus_expect import PASSING_CLASSES, ExecTruth, Expectation, PairTruth
from tests._edit_corpus_operators import OP_BY_NAME, OPERATORS
from tests._edit_corpus_opkit import SONAME_ADVISORY
from tests._edit_corpus_seeds import SEED_BY_NAME, SEEDS
from tests._edit_corpus_tiers import _PR

GCC = Toolchain("gcc", 13, "gcc", "g++")
CLANG = Toolchain("clang", 18, "clang", "clang++")


# --- ChangeKind scope -----------------------------------------------------------


def _all_ran(cfg: Config) -> frozenset[str]:
    """Every consumer of *cfg* ran (the execution oracle saw each pair)."""
    return frozenset(c.id for c in cfg.consumer_set)


def test_scope_classifies_every_changekind_once() -> None:
    sc = cov.scope()
    assert set(sc) == {k.name for k in ChangeKind}
    assert all(why is None or why.strip() for why in sc.values())


@pytest.mark.parametrize(
    "kind,in_scope",
    [
        # ELF/DWARF/header facts of one shared library: in scope.
        ("FUNC_REMOVED", True),
        ("STRUCT_SIZE_CHANGED", True),
        ("SONAME_CHANGED", True),
        ("SYMBOL_VERSION_DEFINED_REMOVED", True),
        ("TLS_VAR_SIZE_CHANGED", True),
        # Source-taxonomy kinds a public header alone shows.
        ("CONCEPT_TIGHTENED", True),
        ("INLINE_FUNCTION_REMOVED", True),
        # Values abicheck reads only from L4 sources.
        ("PUBLIC_MACRO_VALUE_CHANGED", False),
        ("CONSTEXPR_VALUE_CHANGED", False),
        # Other platforms, ecosystems and evidence levels: out.
        ("PE_MACHINE_CHANGED", False),
        ("MACHO_CPU_TYPE_CHANGED", False),
        ("COMPAT_VERSION_CHANGED", False),
        ("PYTHON_API_FUNCTION_REMOVED", False),
        ("SYCL_PLUGIN_REMOVED", False),
        ("KABI_SYMBOL_REMOVED", False),
        ("BUNDLE_LIBRARY_REMOVED", False),
        ("BUILD_CONTEXT_CHANGED", False),
        ("DECLARATION_MOVED", False),
    ],
)
def test_scope_rule_on_known_kinds(kind: str, in_scope: bool) -> None:
    assert (kind in cov.in_scope()) is in_scope, cov.scope()[kind]


def test_every_kind_is_in_exactly_one_scope_bucket() -> None:
    # Named buckets may not overlap; a kind no named bucket claims falls to
    # exactly one module bucket (its taxonomy module).
    tax = cov._taxonomy()
    for k in ChangeKind:
        held = cov.buckets_of(k.value, tax[k.value])
        assert len(held) == 1, (k.value, [b.id for b in held])
    named = [b for b in cov.SCOPE_BUCKETS if b.named]
    for a, b in itertools.combinations(named, 2):
        both = [k.value for k in ChangeKind if a.claims(k.value) and b.claims(k.value)]
        assert not both, (a.id, b.id, both)


def test_scope_report_accounts_for_the_whole_catalog() -> None:
    rep = cov.scope_report()
    buckets = rep["buckets"]  # type: ignore[index]
    assert rep["total"] == len(ChangeKind) == sum(b["count"] for b in buckets.values())
    assert (
        rep["in_scope"]
        == len(cov.in_scope())
        == sum(b["count"] for b in buckets.values() if b["in_scope"])
    )
    listed = [k for b in buckets.values() for k in b["kinds"]]
    assert sorted(listed) == sorted(k.name for k in ChangeKind)


def test_every_scope_bucket_holds_some_kind_and_says_why() -> None:
    # A bucket that holds nothing is dead text describing a scope it does not set.
    held = cov.scope_buckets()
    tax = cov._taxonomy()
    for b in cov.SCOPE_BUCKETS:
        assert held[b.id], b.id
        assert b.reason.strip(), b.id
        assert b.named != (b.module is not None)
        if b.module is not None:
            assert b.module in tax.values(), b.id
    for name in cov.HEADER_VISIBLE_SOURCE_KINDS:
        assert tax[name] == "source", f"{name} is not a source-taxonomy kind"


# --- declared coverage and the ledger -------------------------------------------


def _independent_declared() -> set[str]:
    """Every kind any operator's edit names, read off the dataclass fields."""
    out: set[str] = set()
    for op in OPERATORS:
        out |= set(op.expected)
        for g in op.any_of:
            out |= g
        for rule in op.rules:
            out |= set(rule.changes.get("expected", frozenset()))  # type: ignore[arg-type]
            for g in rule.changes.get("any_of", ()):  # type: ignore[union-attr]
                out |= g
    # Kinds owed by a verdict rather than an edit are not declared coverage;
    # the hand-listed _SONAME_ADVISORY_ORACLE below pins which edits owe them.
    return out - {SONAME_ADVISORY}


# Hand-listed from what each edit does, not from the derivation's formula: a
# binary break shipped under the same SONAME owes the release advisory; an
# addition, a risk, a source-only break or an edit of the SONAME itself does
# not; below the edit's evidence floor nothing is owed.
_SONAME_ADVISORY_ORACLE = {
    # op: (owed at binary evidence, owed at headers evidence)
    "func_remove": (True, True),
    "version_script_dropped": (True, True),
    "symbol_moved_version_node": (True, True),
    "struct_append_field": (False, True),  # a layout break: needs DWARF or headers
    "enum_change_value": (False, True),
    "func_add": (False, False),
    "rebuild_optimized": (False, False),
    "soname_changed": (False, False),
    "soname_dropped": (False, False),
    "char_made_unsigned": (False, False),  # a risk
    "header_constant_changed": (False, False),  # a source-only break
    "param_restrict_added": (False, False),
}


@pytest.mark.parametrize("op_name", sorted(_SONAME_ADVISORY_ORACLE))
@pytest.mark.parametrize("evidence", ["binary", "headers"])
@pytest.mark.parametrize(
    "platform", [("linux", "elf"), ("darwin", "macho"), ("win32", "pe")], ids=str
)
def test_the_soname_advisory_is_owed_by_breaks_under_an_unchanged_soname(
    op_name: str, evidence: str, platform: tuple[str, str]
) -> None:
    # Only ELF has a SONAME: a Mach-O or PE build owes no advisory (the
    # macOS integration job read none for every BREAKING cell).
    cfg = default_config("c", GCC).with_(evidence=evidence, platform=platform)
    owed = SONAME_ADVISORY in OP_BY_NAME[op_name].expect(cfg).expected
    want = (
        _SONAME_ADVISORY_ORACLE[op_name][evidence == "headers"] and platform[1] == "elf"
    )
    assert owed is want


def test_declared_kinds_match_an_independent_reading_of_the_operators() -> None:
    assert set(cov.declared_kinds()) == _independent_declared()


def test_declared_kinds_name_their_operators() -> None:
    for kind, ops in cov.declared_kinds().items():
        for name in ops:
            assert kind in cov.op_declared_kinds(OP_BY_NAME[name])


def test_ledger_declared_coverage_never_drops() -> None:
    """The ratchet: a kind the ledger records as declared must stay declared."""
    ledger = cov.load_ledger()
    dropped = set(ledger["declared_covered"]) - set(cov.declared_kinds())
    assert not dropped, (
        f"edit-corpus ChangeKind coverage dropped: {sorted(dropped)} are no longer "
        "declared by any operator. Restore the operator (or its expectation)."
    )


def test_ledger_records_every_gain() -> None:
    """Growth must be written down, so the floor rises with it."""
    ledger = cov.load_ledger()
    gained = (set(cov.declared_kinds()) & cov.in_scope()) - set(
        ledger["declared_covered"]
    )
    assert not gained, (
        f"{sorted(gained)} are newly declared: run "
        "`python -m tests._edit_corpus_coverage ledger --write`"
    )


def _unlisted_in_scope(
    ledger: dict, scope_set: set[str], declared: set[str]
) -> set[str]:
    """In-scope kinds no operator declares and the ledger does not list as
    uncovered, among the kinds the ledger knows (a catalog addition since the
    ledger was written is in none of its lists, and tolerated)."""
    known = (
        set(ledger["declared_covered"])
        | set(ledger["declared_uncovered"])
        | set(ledger["out_of_scope"])
    )
    return ((scope_set - declared) & known) - set(ledger["declared_uncovered"])


def test_ledger_uncovered_list_is_not_stale() -> None:
    ledger = cov.load_ledger()
    scope_set = cov.in_scope()
    declared = set(cov.declared_kinds())
    stale = [
        k for k in ledger["declared_uncovered"] if k in declared or k not in scope_set
    ]
    assert not stale, (
        f"ledger lists covered or out-of-scope kinds as uncovered: {stale}"
    )
    # Every in-scope kind is either declared or listed; a ChangeKind added to
    # the catalog after the ledger was written is the one tolerated exception
    # (it is in none of the ledger's lists) until the ledger is regenerated.
    # A kind a scope-bucket change moved into scope is in ``out_of_scope``, so
    # it is not mistaken for a new one.
    unlisted = _unlisted_in_scope(ledger, scope_set, declared)
    assert not unlisted, sorted(unlisted)
    moved_out = (
        set(ledger["declared_covered"]) | set(ledger["declared_uncovered"])
    ) - scope_set
    assert not moved_out, (
        f"kinds left the scope: regenerate the ledger {sorted(moved_out)}"
    )


def test_a_kind_moved_into_scope_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    ledger = cov.declared_ledger(cov.load_ledger())
    moved = ledger["out_of_scope"][0]
    real = cov.in_scope()
    monkeypatch.setattr(cov, "in_scope", lambda: real | {moved})
    unlisted = _unlisted_in_scope(ledger, cov.in_scope(), set(cov.declared_kinds()))
    assert unlisted == {moved}
    # A kind the ledger has never seen (new in the catalog) is tolerated.
    monkeypatch.setattr(cov, "in_scope", lambda: real | {"BRAND_NEW_KIND"})
    assert (
        _unlisted_in_scope(ledger, cov.in_scope(), set(cov.declared_kinds())) == set()
    )


def test_ledger_floors_are_declared_in_scope_kinds() -> None:
    ledger = cov.load_ledger()
    declared = set(cov.declared_kinds())
    for tier, kinds in ledger["verified_floor"].items():
        assert tier in cov.TIERS
        assert set(kinds) <= declared & cov.in_scope(), tier


def _pr_tier_declared() -> set[str]:
    """Kinds the PR-tier cells expect, from the cell list with stand-in toolchains."""
    from tests.test_abi_edit_corpus import default_matrix

    out: set[str] = set()
    for op, seed in default_matrix():
        for tc in (GCC, CLANG):
            out |= cov.expectation_kinds(op.expect(default_config(seed.lang, tc)))
    for op_name, seed_name, family, changes in _PR:
        seed = SEED_BY_NAME[seed_name]
        cfg = default_config(seed.lang, GCC if family == "gcc" else CLANG)
        for key, value in changes.items():
            if key == "std":
                cfg = cfg.with_(std=STANDARD_BY_NAME[str(value)])
            elif key == "consumer":
                other = CLANG if value == "clang" else GCC
                cfg = cfg.with_(consumers=(Consumer(toolchain=other),))
            else:
                cfg = cfg.with_(**{key: value})
        out |= cov.expectation_kinds(OP_BY_NAME[op_name].expect(cfg))
    return out


def test_pr_floor_is_reachable_from_the_pr_cells() -> None:
    # A floor kind no PR cell expects could never be verified by a PR run.
    floor = set(cov.load_ledger()["verified_floor"].get("pr", ()))
    assert floor <= _pr_tier_declared()


# --- records and the verified side ------------------------------------------------


def _rec(tier: str = "pr", **kw: object) -> cov.CellRecord:
    base: dict[str, object] = {
        "tier": tier,
        "cell": "c",
        "op": "o",
        "seed": "s",
        "config": "x",
        "status": "ran",
        "cls": "agree",
    }
    base.update(kw)
    return cov.CellRecord(**base)  # type: ignore[arg-type]


@pytest.mark.parametrize("cls", ["agree", "non-observable", "unobservable"])
def test_a_passing_cell_verifies_the_expected_kinds_it_produced(cls: str) -> None:
    r = _rec(cls=cls, expected=("A", "B"), produced=("A", "C"))
    assert r.verified == {"A"}
    assert r.incidental == {"C"}


@pytest.mark.parametrize(
    "status,cls",
    [
        ("ran", "wrong-kind"),
        ("ran", "false-clean"),
        ("ran", "refused"),
        ("ran", "known-xfail"),
        ("unavailable", ""),
        ("not-applicable", ""),
    ],
)
def test_a_failing_xfailed_or_skipped_cell_verifies_nothing(
    status: str, cls: str
) -> None:
    assert (
        _rec(status=status, cls=cls, expected=("A",), produced=("A",)).verified == set()
    )


def test_passing_classes_are_the_ones_that_verify() -> None:
    # Exhaustive over the reconcile vocabulary: exactly the passing classes verify.
    from tests._edit_corpus_expect import MISMATCH_CLASSES

    for cls in (*PASSING_CLASSES, *MISMATCH_CLASSES):
        got = _rec(cls=cls, expected=("A",), produced=("A",)).verified
        assert (got == {"A"}) is (cls in PASSING_CLASSES), cls


def test_records_round_trip_through_files(tmp_path: Path) -> None:
    op = OP_BY_NAME["struct_append_field"]
    cfg = default_config("c", GCC)
    r = cov.make_record(
        "nightly",
        op,
        "c_core",
        cfg,
        status="ran",
        cls="agree",
        verdict="BREAKING",
        produced={"TYPE_SIZE_CHANGED", "STRUCT_SIZE_CHANGED"},
        ran_consumers=_all_ran(cfg),
    )
    assert r.points
    path = cov.write_record(r, tmp_path)
    assert path is not None and path.parent == tmp_path
    (back,) = cov.load_records([tmp_path])
    assert back == r
    assert back.verified == {"TYPE_SIZE_CHANGED", "STRUCT_SIZE_CHANGED"}
    assert back.points == (
        "struct/c/c17/baseline/gcc/gcc/headers.castxml/insert_tail_shifts",
    )


def test_write_record_is_a_no_op_without_a_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(cov.COVERAGE_DIR_ENV, raising=False)
    assert cov.write_record(_rec()) is None


def test_unknown_record_fields_are_rejected() -> None:
    with pytest.raises(ValueError, match="unknown coverage record fields"):
        cov.CellRecord.from_json({**_rec().to_json(), "extra": 1})


def _ledger(**floors: list[str]) -> dict:
    return {"declared_covered": [], "declared_uncovered": [], "verified_floor": floors}


def test_floor_check_holds_the_merged_run_to_the_whole_floor() -> None:
    ledger = _ledger(pr=["A", "B"])
    # The run's cells expected A and B; only A verified -> B dropped.
    recs = [
        _rec(expected=("A",), produced=("A",)),
        _rec(cls="wrong-kind", expected=("B",)),
    ]
    assert cov.check_floor(recs, ledger, "pr") == [
        "B: in the pr verified floor, not verified by this run"
    ]
    # Two shards: one's skipped / known-xfail / any_of cells expect A and B
    # without verifying them; the other verifies both. Only the merge is judged.
    shard1 = [
        _rec(status="unavailable", cls="", expected=("A",)),
        _rec(cls="known-xfail", expected=("B",)),
    ]
    shard2 = [_rec(expected=("A", "B"), produced=("A", "B"))]
    assert cov.check_floor([*shard1, *shard2], ledger, "pr") == []
    assert len(cov.check_floor(shard1, ledger, "pr")) == 2
    # Another tier's records never satisfy this tier's floor.
    other = [_rec(tier="weekly", expected=("A", "B"), produced=("A", "B"))]
    assert len(cov.check_floor(other, ledger, "pr")) == 2


def test_report_counts_per_tier(tmp_path: Path) -> None:
    recs = [
        _rec(expected=("FUNC_REMOVED",), produced=("FUNC_REMOVED", "FUNC_ADDED")),
        _rec(status="unavailable", cls=""),
        _rec(tier="weekly", cls="wrong-kind", expected=("FUNC_ADDED",), produced=()),
    ]
    rep = cov.report(recs, _ledger())
    pr = rep["tiers"]["pr"]  # type: ignore[index]
    assert (pr["cells"], pr["ran"], pr["skipped"], pr["passing"]) == (2, 1, 1, 1)
    assert pr["verified"] == ["FUNC_REMOVED"]
    # Produced but not expected: listed with its cell count, never coverage.
    assert pr["produced_not_expected"] == {"FUNC_ADDED": 1}
    assert rep["tiers"]["weekly"]["verified"] == []  # type: ignore[index]
    assert rep["tiers"]["weekly"]["expected_not_verified"] == ["FUNC_ADDED"]  # type: ignore[index]
    text = cov.summary_markdown(rep)
    assert "| `pe_coff` | no | 6 |" in text  # the excluded buckets, with counts
    assert "Produced but not expected" in text and "| pr | `FUNC_ADDED` | 1 |" in text
    assert "| pr | 1 / 2 | 1 | 1 |" in text and "| weekly | 1 / 1 | 0 | 0 |" in text


def test_cli_reports_and_gates_the_floor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger_path = tmp_path / "ledger.json"
    ledger_path.write_text(json.dumps(_ledger(pr=["FUNC_REMOVED"])), encoding="utf-8")
    monkeypatch.setattr(cov, "LEDGER_PATH", ledger_path)
    good, bad = tmp_path / "good", tmp_path / "bad"
    cov.write_record(_rec(expected=("FUNC_REMOVED",), produced=("FUNC_REMOVED",)), good)
    cov.write_record(_rec(cls="wrong-kind", expected=("FUNC_REMOVED",)), bad)
    summary = tmp_path / "summary.md"
    assert (
        cov.main(
            ["report", str(good), "--summary", str(summary), "--check-floor", "pr"]
        )
        == 0
    )
    assert "### Edit corpus coverage" in summary.read_text(encoding="utf-8")
    assert (
        cov.main(
            [
                "report",
                str(bad),
                "--json",
                str(tmp_path / "r.json"),
                "--check-floor",
                "pr",
            ]
        )
        == 1
    )
    rep = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
    assert rep["floor_problems"]["pr"] == [
        "FUNC_REMOVED: in the pr verified floor, not verified by this run"
    ]
    # --raise-floor only ever adds.
    cov.write_record(
        _rec(
            tier="weekly", cell="w", expected=("FUNC_ADDED",), produced=("FUNC_ADDED",)
        ),
        good,
    )
    assert (
        cov.main(
            ["report", str(good), "--raise-floor", "weekly", "--summary", str(summary)]
        )
        == 0
    )
    floors = json.loads(ledger_path.read_text(encoding="utf-8"))["verified_floor"]
    assert floors == {"pr": ["FUNC_REMOVED"], "weekly": ["FUNC_ADDED"]}


# --- the language space -----------------------------------------------------------


def test_every_seed_role_has_a_construct() -> None:
    for seed in SEEDS:
        for role in seed.roles:
            assert space.role_construct(role) in space.CONSTRUCT_BY_NAME, role


def test_every_language_feature_has_a_construct() -> None:
    assert set(space.FEATURE_CONSTRUCT) == set(FEATURES)


def test_every_operator_maps_to_constructs_of_its_seeds_language() -> None:
    for op in OPERATORS:
        cons = space.op_constructs(op)
        assert cons, op.name
        for seed in SEEDS:
            if op.applies_to(seed):
                cfg = default_config(seed.lang, GCC)
                for name in cons:
                    assert seed.lang in space.CONSTRUCT_BY_NAME[name].langs, (
                        op.name,
                        name,
                    )
                assert all(
                    p.lang == seed.lang
                    for p in space.cell_points(op, cfg, _all_ran(cfg))
                )


def _year_oracle(construct: str, std: str) -> bool:
    """An independent table of when each gated construct first exists."""
    first = {
        "enum_fixed_underlying": {"c": 2023, "c++": 2011},
        "enum_class": {"c++": 2011},
        "inline_function": {"c": 1999, "c++": 2011},
        "concept": {"c++": 2020},
        "no_unique_address": {"c++": 2020},
        "noexcept_type": {"c++": 2017},
        "std_string": {"c++": 2011},
        "class_virtual": {"c++": 2011},
        "template": {"c++": 2011},
        "class": {"c++": 2011},
        "inheritance": {"c++": 2011},
        "inline_namespace": {"c++": 2011},
        "abi_tag": {"c++": 2011},
        "hidden_friend": {"c++": 2011},
        "restrict_pointer": {"c": 1999},
        "flexible_array": {"c": 1999},
        "atomic": {"c": 2011},
        "bit_int": {"c": 2023},
        "char8_t": {"c++": 2020},
    }.get(construct, {"c": 1989, "c++": 2011})
    s = STANDARD_BY_NAME[std]
    return s.lang in first and s.year >= first[s.lang]


def test_infeasibility_matches_an_independent_oracle_exhaustively() -> None:
    gcc_only = {"fabi_version"}
    cxx_only = {"cxx11_abi_0"}
    conflicts = {("std_string", "packed"), ("std_string", "short_enums")}
    total = 0
    for p in space.all_points():
        total += 1
        want = (
            STANDARD_BY_NAME[p.std].lang == p.lang
            and p.build in {"gcc", "clang"}
            and p.consumer in {"gcc", "clang"}
            and _year_oracle(p.construct, p.std)
            and not (p.config in gcc_only and p.build != "gcc")
            and not (p.config in cxx_only and p.lang != "c++")
            and (p.construct, p.config) not in conflicts
        )
        why = space.infeasible(p)
        assert (why is None) is want, (p.id, why)
        assert why is None or why.strip()
    n_cfg = 1 + 7 + 1
    assert total == (
        sum(len(space.edit_values(c.name)) for c in space.CONSTRUCTS)
        * len(STANDARDS)
        * n_cfg
        * len(FAMILIES) ** 2
        # headers counts once per header backend.
        * (len(EVIDENCE) - 1 + len(HEADER_BACKENDS))
    )


@pytest.mark.parametrize(
    "construct,std,config,family,reason",
    [
        (
            "concept",
            "c++17",
            "baseline",
            "gcc",
            "concept needs concepts, which c++17 lacks",
        ),
        (
            "class_virtual",
            "c17",
            "baseline",
            "gcc",
            "class_virtual is not a c construct",
        ),
        ("struct", "c17", "fabi_version", "clang", "-fabi-version is a gcc flag"),
        (
            "struct",
            "c17",
            "baseline",
            "msvc",
            "msvc does not target Linux ELF, the corpus's only platform",
        ),
    ],
)
def test_infeasible_points_say_why(
    construct: str, std: str, config: str, family: str, reason: str
) -> None:
    p = space.Point(
        construct, STANDARD_BY_NAME[std].lang, std, config, family, "gcc", "headers"
    )
    assert space.infeasible(p) == reason


@pytest.mark.parametrize(
    "flags,consumer,consumer_flags,want",
    [
        (Flags(), None, None, ("baseline",)),
        (Flags(opt="O2", short_enums=True), None, None, ("O2", "short_enums")),
        # The consumer's toolchain is an axis of its own, not a config feature.
        (Flags(), CLANG, None, ("baseline",)),
        (Flags(), None, Flags(short_enums=True), ("short_enums", "consumer_flags")),
        (Flags(abi_version=11, bits=32), None, None, ("m32", "fabi_version")),
    ],
)
def test_config_features_name_every_non_default_setting(
    flags: Flags,
    consumer: Toolchain | None,
    consumer_flags: Flags | None,
    want: tuple[str, ...],
) -> None:
    consumers = (
        (Consumer(toolchain=consumer, flags=consumer_flags),)
        if consumer is not None or consumer_flags is not None
        else ()
    )
    cfg = Config(
        std=STANDARD_BY_NAME["c++17"], producer=GCC, flags=flags, consumers=consumers
    )
    assert space.config_features(cfg) == want


def test_a_cell_with_several_consumers_covers_one_point_per_consumer_family() -> None:
    op = OP_BY_NAME["struct_append_field"]
    cfg = default_config("c", GCC).with_(
        consumers=(Consumer(), Consumer(toolchain=CLANG), Consumer(toolchain=CLANG))
    )
    points = space.cell_points(op, cfg, _all_ran(cfg))
    assert sorted((p.build, p.consumer) for p in points) == [
        ("gcc", "clang"),
        ("gcc", "gcc"),
    ]


@pytest.mark.parametrize("ran", [(), ("gcc",), ("clang",), ("gcc", "clang")], ids=str)
def test_only_a_pair_that_ran_exercises_a_point(ran: tuple[str, ...]) -> None:
    op = OP_BY_NAME["struct_append_field"]
    cfg = default_config("c", GCC).with_(
        consumers=(Consumer(), Consumer(toolchain=CLANG))
    )
    ids = frozenset(c.id for c in cfg.consumer_set if c.toolchain.family in ran)
    consumers = {p.consumer for p in space.cell_points(op, cfg, ids)}
    assert consumers == set(ran)
    rec = cov.make_record(
        "pr", op, "c_core", cfg, status="ran", cls="agree", ran_consumers=ids
    )
    assert {space.point_from_id(p).consumer for p in rec.points} == set(ran)


def _pair(consumer: str, status: str) -> PairTruth:
    return PairTruth(consumer=consumer, status=status)


@pytest.mark.parametrize(
    ("truth", "want"),
    [
        # No oracle, or one that could not run (a non-ELF host has no pairs):
        # no execution evidence, so no consumer covers a point.
        (None, set()),
        (ExecTruth(available=False, reason="host is macho"), set()),
        (
            ExecTruth(available=False, pairs=(_pair("a", "ran"),)),
            set(),
        ),
        (
            ExecTruth(
                available=True,
                pairs=(
                    _pair("a", "ran"),
                    _pair("b", "infeasible"),
                    _pair("c", "unavailable"),
                ),
            ),
            {"a"},
        ),
    ],
    ids=["no-oracle", "unavailable", "unavailable-with-pairs", "mixed"],
)
def test_ran_consumers_is_an_allow_list(
    truth: ExecTruth | None, want: set[str]
) -> None:
    assert cov.ran_consumers(truth) == want


def test_two_tests_running_the_same_cell_keep_distinct_records(tmp_path: Path) -> None:
    op = OP_BY_NAME["struct_append_field"]
    cfg = default_config("c", GCC)
    paths = {
        cov.write_record(
            cov.make_record("pr", op, "c_core", cfg, status="ran", source=src),
            tmp_path,
        )
        for src in ("default", "matrix")
    }
    assert len(paths) == 2
    assert len(cov.load_records([tmp_path])) == 2


def test_only_a_checked_tier_reports_floor_drops() -> None:
    ledger = _ledger(pr=["A"])
    shard = [_rec(expected=("B",), produced=("B",))]  # A is verified elsewhere
    assert "floor_problems" not in cov.report(shard, ledger)
    assert cov.report(shard, ledger, ["pr"])["floor_problems"] == {
        "pr": ["A: in the pr verified floor, not verified by this run"]
    }
    assert "Verified-floor drops" not in cov.summary_markdown(cov.report(shard, ledger))


def test_the_floor_check_fails_on_a_point_the_model_calls_infeasible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger_path = tmp_path / "ledger.json"
    ledger_path.write_text(json.dumps(_ledger(pr=[])), encoding="utf-8")
    monkeypatch.setattr(cov, "LEDGER_PATH", ledger_path)
    good = default_config("c", GCC)
    feasible = space.cell_points(
        OP_BY_NAME["struct_append_field"], good, _all_ran(good)
    )[0].id
    # A C++-only construct under C: the model calls it infeasible.
    bad = space.Point("concept", "c", "c17", "baseline", "gcc", "gcc", "headers").id
    assert space.infeasible(space.point_from_id(bad)) is not None
    for points, rc in (((feasible,), 0), ((feasible, bad), 1)):
        d = tmp_path / str(rc)
        cov.write_record(_rec(cell=str(rc), points=points), d)
        assert cov.main(["report", str(d), "--check-floor", "pr"]) == rc
    # Without --check-floor the same records only report.
    assert cov.main(["report", str(tmp_path / "1")]) == 0


def test_a_rebuild_cell_counts_the_flag_it_rebuilds_with() -> None:
    op = OP_BY_NAME["rebuild_short_enums"]
    cfg0 = default_config("c", GCC)
    (p,) = space.cell_points(op, cfg0, _all_ran(cfg0))
    assert (p.construct, p.config) == ("enum", "short_enums")


def test_the_generator_refuses_what_the_space_calls_a_conflict() -> None:
    # One rule, read by both: a seed containing std::string is refused when
    # built (seed_conflict) or consumed (pair_blocked) under -fpack-struct or
    # -fshort-enums, whatever the edited role.
    from tests._edit_corpus_cells import pair_blocked

    for seed in SEEDS:
        has_string = "std_string" in space.seed_constructs(seed)
        op = next(o for o in OPERATORS if o.applies_to(seed) and not o.features)
        for flags in (Flags(), Flags(pack=1), Flags(short_enums=True), Flags(opt="O2")):
            bad = flags.pack is not None or flags.short_enums
            std = STANDARD_BY_NAME["c++17" if seed.lang == "c++" else "c17"]
            build = Config(std=std, producer=GCC, flags=flags)
            assert (seed_conflict(seed, build) is not None) is (has_string and bad), (
                seed.name,
                flags,
            )
            consumed = Config(std=std, producer=GCC, consumers=(Consumer(flags=flags),))
            (consumer,) = consumed.consumer_set
            blocked = pair_blocked(op, seed, consumed, consumer)
            refused = blocked is not None and blocked[0] == "infeasible"
            assert refused is (has_string and bad), (seed.name, flags, blocked)
            assert (space.seed_flags_conflict(seed, flags) is not None) is (
                has_string and bad
            )


def test_space_report_against_a_hand_counted_space() -> None:
    feasible = space.feasible_points()
    some = sorted(feasible)[:5]
    rep = space.coverage(some, top=3)
    assert (rep["covered"], rep["feasible"]) == (5, len(feasible))
    # Per axis, covered counts sum to the covered total and feasible to the space.
    for axis, rows in rep["per_axis"].items():  # type: ignore[union-attr]
        assert sum(r["covered"] for r in rows.values()) == 5, axis
        assert sum(r["feasible"] for r in rows.values()) == len(feasible), axis
    assert len(rep["top_uncovered"]) == 3  # type: ignore[arg-type]
    assert not set(rep["top_uncovered"]) & {p.id for p in some}  # type: ignore[arg-type]
    assert rep["covered_but_infeasible"] == []


def test_a_covered_infeasible_point_is_reported_as_a_model_bug() -> None:
    bad = space.Point("concept", "c++", "c++11", "baseline", "gcc", "gcc", "headers")
    rep = space.coverage([bad])
    assert rep["covered"] == 0
    assert rep["covered_but_infeasible"] == [bad.id]


def test_top_uncovered_prefers_the_least_covered_axis_values() -> None:
    # Cover every feasible point of one construct; the gaps must not lead with it.
    struct_points = [p for p in space.feasible_points() if p.construct == "struct"]
    rep = space.coverage(struct_points, top=10)
    assert all(not pid.startswith("struct/") for pid in rep["top_uncovered"])  # type: ignore[union-attr]


def test_point_ids_round_trip() -> None:
    for p in itertools.islice(space.all_points(), 0, 2000, 97):
        assert space.point_from_id(p.id) == p
    with pytest.raises(ValueError):
        space.point_from_id("a/b")


def test_every_pr_tier_cell_runs_a_feasible_point() -> None:
    # The fixed PR cells, with stand-in toolchains: none is a point the model rejects.
    for op_name, seed_name, family, changes in _PR:
        seed = SEED_BY_NAME[seed_name]
        cfg = default_config(seed.lang, GCC if family == "gcc" else CLANG)
        for key, value in changes.items():
            if key == "std":
                cfg = cfg.with_(std=STANDARD_BY_NAME[str(value)])
            elif key == "consumer":
                cfg = cfg.with_(consumers=(Consumer(toolchain=CLANG),))
            else:
                cfg = cfg.with_(**{key: value})
        for p in space.cell_points(OP_BY_NAME[op_name], cfg, _all_ran(cfg)):
            assert space.infeasible(p) is None, (op_name, p.id, space.infeasible(p))


def test_expectation_kinds_reads_required_and_any_of() -> None:
    exp = Expectation(expected=frozenset({"A"}), any_of=(frozenset({"B", "C"}),))
    assert cov.expectation_kinds(exp) == {"A", "B", "C"}


def test_a_pair_rule_makes_its_build_consumer_pairs_infeasible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The hook the toolchain axes plug into: a rule rejecting gcc-built C++
    # consumed by clang removes exactly those points, with its reason.
    def rule(p: space.Point) -> str | None:
        if p.lang == "c++" and (p.build, p.consumer) == ("gcc", "clang"):
            return "test: libstdc++ build, libc++ consumer"
        return None

    before = space.feasible_points()
    monkeypatch.setattr(space, "PAIR_RULES", (rule,))
    after = space.feasible_points()
    removed = before - after
    assert removed and after <= before
    assert all(
        (p.lang, p.build, p.consumer) == ("c++", "gcc", "clang") for p in removed
    )
    assert (
        space.infeasible(next(iter(removed)))
        == "test: libstdc++ build, libc++ consumer"
    )


@pytest.mark.parametrize("seed_name", sorted(SEED_BY_NAME))
@pytest.mark.parametrize(
    "platform", [("linux", "elf"), ("darwin", "macho"), ("win32", "pe")], ids=str
)
def test_only_seeds_with_elf_link_roles_are_unavailable_off_elf(
    seed_name: str, platform: tuple[str, str]
) -> None:
    # Apple's ld rejects --version-script, -z and --as-needed (the macOS
    # integration job failed every c_elf cell at link time).
    from tests._edit_corpus_cells import elf_link_unavailable

    seed = SEED_BY_NAME[seed_name]
    cfg = default_config(seed.lang, GCC).with_(platform=platform)
    links_elf_only = any(
        r in seed.roles for r in ("build.vscript", "build.ldflags", "build.soname")
    )
    gap = elf_link_unavailable(seed, cfg)
    assert (gap is not None) is (links_elf_only and platform[1] != "elf"), gap


def test_a_derived_kind_is_never_declared_coverage() -> None:
    declared = cov.declared_kinds()
    assert SONAME_ADVISORY not in declared
    owing = {op.name for op in OPERATORS if SONAME_ADVISORY in cov.op_derived_kinds(op)}
    listed = {name for name, (_, headers) in _SONAME_ADVISORY_ORACLE.items() if headers}
    # Every hand-listed operator that owes the advisory is counted as owing it.
    assert listed <= owing
    rep = cov.report([])
    assert rep["derived"][SONAME_ADVISORY] == len(owing)


def test_a_passing_cell_reports_a_derived_kind_apart_from_coverage() -> None:
    op = OP_BY_NAME["struct_append_field"]
    cfg = default_config("c", GCC)
    rec = cov.make_record(
        "pr",
        op,
        "c_core",
        cfg,
        status="ran",
        cls="agree",
        verdict="BREAKING",
        produced=set(op.expect(cfg).expected) | {SONAME_ADVISORY},
    )
    assert SONAME_ADVISORY not in rec.verified
    assert rec.derived_verified == {SONAME_ADVISORY}
    assert SONAME_ADVISORY not in rec.incidental


@pytest.mark.parametrize("backend", HEADER_BACKENDS)
@pytest.mark.parametrize("evidence", EVIDENCE)
def test_header_backend_is_part_of_the_build_only_for_headers(
    evidence: str, backend: str
) -> None:
    base = default_config("c", GCC).with_(evidence=evidence)
    cfg = base.with_(header_backend=backend)
    if evidence != "headers":
        # No header is dumped: every backend spelling is the same build.
        assert cfg == base and cfg.build_id == base.build_id
        assert cfg.space_evidence == evidence
        return
    # castxml keeps every historical id; the others are distinct builds.
    assert (cfg.build_id == base.build_id) is (backend == "castxml")
    assert cfg.id.endswith("headers" if backend == "castxml" else f"headers.{backend}")
    assert cfg.space_evidence == f"headers.{backend}"
    assert cfg.space_evidence in space.SPACE_EVIDENCE


def test_an_unknown_header_backend_is_refused() -> None:
    with pytest.raises(ValueError, match="header_backend"):
        default_config("c", GCC).with_(header_backend="hybrid")
