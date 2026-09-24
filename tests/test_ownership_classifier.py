"""``extract.ownership.classify``: who owns a declaration, from its file.

The oracle is an independent table of (path, rules) -> expected owner and
contract, written out by hand -- never the classifier's own matching
helpers. The property tests state the plan's precedence rules as
invariants over generated roots and paths
(``docs/contribute/plans/target-ownership-and-extraction-scope.md``,
"Precedence rules").
"""

from __future__ import annotations

import itertools
from pathlib import Path

import pytest
from hypothesis import given, settings, strategies as st

from abicheck.extract.ownership import (
    DeclarationSite,
    OwnershipRuleError,
    classify,
    resolve_ownership_rules,
)
from abicheck.model.ownership_rules import DependencyRoots, OwnershipRules

_ROOT = Path("/proj")

#: The SVS shape: a target root with a vendored dependency inside it, and a
#: dependency outside it.
_SVS = OwnershipRules(
    target_roots=("include/svs",),
    dependencies=(
        DependencyRoots("fmt", ("include/svs/third-party/fmt",)),
        DependencyRoots("toml", ("/opt/toml/include",)),
    ),
    private_headers=("include/svs/*/detail/**",),
    private_namespaces=("svs::detail",),
)

# (path, qualified name, artificial) -> (owner, contract)
_ORACLE = [
    ("/proj/include/svs/core/api.h", "svs::Index", False, "target", "public"),
    ("include/svs/core/api.h", "svs::Index", False, "target", "public"),
    ("/proj/include/svs/core/detail/impl.h", "svs::X", False, "target", "private"),
    ("/proj/include/svs/core/api.h", "svs::detail::Y", False, "target", "private"),
    ("/proj/include/svs/core/api.h", "svs::detailed::Y", False, "target", "public"),
    ("/proj/include/svs/core/api.h", "svs::detail", False, "target", "public"),
    (
        "/proj/include/svs/third-party/fmt/format.h",
        "fmt::format",
        False,
        "dependency:fmt",
        "external",
    ),
    # A target type specialised inside the dependency's file stays the
    # dependency's: the file decides.
    (
        "/proj/include/svs/third-party/fmt/format.h",
        "svs::detail::Z",
        False,
        "dependency:fmt",
        "external",
    ),
    ("/opt/toml/include/toml.hpp", "toml::table", False, "dependency:toml", "external"),
    # Look-alike sibling directories are not under the root.
    ("/proj/include/svs-extra/x.h", "x", False, "unresolved", "unresolved"),
    ("/proj/include/svsx/x.h", "x", False, "unresolved", "unresolved"),
    ("/usr/include/c++/13/string", "std::string", False, "toolchain", "external"),
    ("/proj/src/local.h", "local", False, "unresolved", "unresolved"),
    (None, "anything", False, "unresolved", "unresolved"),
    # castxml attributes implicit builtins to the first file using them.
    ("/proj/include/svs/core/api.h", "__atomic_load", True, "toolchain", "external"),
    ("/proj/include/svs/core/api.h", "__builtin_expect", True, "toolchain", "external"),
    (
        "/proj/include/svs/core/api.h",
        "__sync_fetch_and_add",
        True,
        "toolchain",
        "external",
    ),
    # Not builtins: a user function spelled like one, or a scoped one.
    ("/proj/include/svs/core/api.h", "__atomic_load", False, "target", "public"),
    ("/proj/include/svs/core/api.h", "svs::__atomic_x", True, "target", "public"),
]


@pytest.mark.parametrize(("path", "name", "artificial", "owner", "contract"), _ORACLE)
def test_oracle_table(
    path: str | None, name: str, artificial: bool, owner: str, contract: str
) -> None:
    rules = resolve_ownership_rules(_SVS, _ROOT)
    decision = classify(DeclarationSite(path, name, artificial), rules)
    assert (decision.owner, decision.contract) == (owner, contract)


def test_a_target_declaration_in_a_dependency_namespace_is_a_diagnostic() -> None:
    rules = resolve_ownership_rules(_SVS, _ROOT)
    decision = classify(
        DeclarationSite("/proj/include/svs/core/fmt_glue.h", "fmt::formatter<svs::X>"),
        rules,
    )
    assert (decision.owner, decision.contract) == ("target", "public")
    assert len(decision.diagnostics) == 1
    assert "'fmt'" in decision.diagnostics[0]


def test_the_rule_id_names_the_rule_that_decided() -> None:
    rules = resolve_ownership_rules(_SVS, _ROOT)
    site = DeclarationSite("/proj/include/svs/core/detail/impl.h", "svs::X")
    assert classify(site, rules).rule_id == "private_header:include/svs/*/detail/**"
    site = DeclarationSite("/proj/include/svs/third-party/fmt/f.h", "fmt::x")
    assert classify(site, rules).rule_id.startswith("dependency:fmt:")


@pytest.mark.parametrize(
    "rules",
    [
        OwnershipRules(
            target_roots=("include",),
            dependencies=(DependencyRoots("d", ("include",)),),
        ),
        OwnershipRules(
            dependencies=(
                DependencyRoots("a", ("vendor",)),
                DependencyRoots("b", ("./vendor/",)),
            )
        ),
    ],
)
def test_a_root_claimed_by_two_owners_is_refused(rules: OwnershipRules) -> None:
    with pytest.raises(OwnershipRuleError, match="claimed by both"):
        resolve_ownership_rules(rules, _ROOT)


# ── properties ───────────────────────────────────────────────────────────────

_segment = st.sampled_from(["a", "b", "inc", "lib", "x1", "vendor", "detail"])
_rel_dir = st.lists(_segment, min_size=1, max_size=4).map("/".join)


def _decide(rules: OwnershipRules, path: str) -> tuple[str, str]:
    d = classify(DeclarationSite(path, "n"), resolve_ownership_rules(rules, _ROOT))
    return d.owner, d.contract


@st.composite
def _rules_and_path(draw: st.DrawFn) -> tuple[OwnershipRules, str]:
    roots = draw(st.lists(_rel_dir, min_size=1, max_size=4, unique=True))
    owners = draw(
        st.lists(
            st.sampled_from(["target", "d1", "d2"]),
            min_size=len(roots),
            max_size=len(roots),
        )
    )
    target = tuple(r for r, o in zip(roots, owners) if o == "target")
    deps = tuple(
        DependencyRoots(name, tuple(r for r, o in zip(roots, owners) if o == name))
        for name in ("d1", "d2")
        if name in owners
    )
    path = draw(_rel_dir) + "/h.h"
    return OwnershipRules(target_roots=target, dependencies=deps), path


def _expected_owner(rules: OwnershipRules, path: str) -> str:
    """Independent oracle: the owner of the longest root that is a
    directory-prefix of *path*, compared as '/'-separated strings."""
    claims = [(r, "target") for r in rules.target_roots] + [
        (r, f"dependency:{d.name}") for d in rules.dependencies for r in d.header_roots
    ]
    matching = [(r, o) for r, o in claims if path.startswith(r + "/")]
    if not matching:
        return "unresolved"
    return max(matching, key=lambda ro: ro[0].count("/"))[1]


@settings(max_examples=300, deadline=None)
@given(_rules_and_path())
def test_most_specific_root_wins(case: tuple[OwnershipRules, str]) -> None:
    rules, path = case
    assert _decide(rules, path)[0] == _expected_owner(rules, path)


@settings(max_examples=200, deadline=None)
@given(_rules_and_path(), st.randoms(use_true_random=False))
def test_rule_order_never_changes_the_decision(
    case: tuple[OwnershipRules, str], rnd
) -> None:
    rules, path = case
    shuffled_deps = [
        DependencyRoots(d.name, tuple(rnd.sample(d.header_roots, len(d.header_roots))))
        for d in rules.dependencies
    ]
    rnd.shuffle(shuffled_deps)
    shuffled = OwnershipRules(
        target_roots=tuple(rnd.sample(rules.target_roots, len(rules.target_roots))),
        dependencies=tuple(shuffled_deps),
    )
    assert _decide(shuffled, path) == _decide(rules, path)


@settings(max_examples=200, deadline=None)
@given(_rel_dir, _rel_dir)
def test_an_include_directory_never_grants_ownership(
    include_dir: str, target: str
) -> None:
    """A ``-I`` directory is compile context. Nothing but a configured root
    can make a file target-owned, so a file under an include directory that
    no root covers is never ``target``."""
    path = f"{include_dir}/h.h"
    rules = OwnershipRules(target_roots=(target,))
    owner = _decide(rules, path)[0]
    assert (owner == "target") == path.startswith(target + "/")


@pytest.mark.parametrize(
    "system_root", ["/usr/include/svs", "/usr/local/include/svs", "/usr/include"]
)
def test_an_explicit_root_beats_the_system_path_heuristic(system_root: str) -> None:
    path = f"{system_root}/core/api.h"
    assert _decide(OwnershipRules(), path) == ("toolchain", "external")
    assert _decide(OwnershipRules(target_roots=(system_root,)), path) == (
        "target",
        "public",
    )
    assert _decide(
        OwnershipRules(dependencies=(DependencyRoots("d", (system_root,)),)), path
    ) == ("dependency:d", "external")


@pytest.mark.parametrize(
    ("private_headers", "private_namespaces"),
    list(itertools.product([(), ("**",)], [(), ("a",)])),
)
def test_private_rules_never_narrow_a_dependency(
    private_headers: tuple[str, ...], private_namespaces: tuple[str, ...]
) -> None:
    rules = OwnershipRules(
        dependencies=(DependencyRoots("d", ("vendor",)),),
        private_headers=private_headers,
        private_namespaces=private_namespaces,
    )
    decision = classify(
        DeclarationSite("/proj/vendor/x.h", "a::b"),
        resolve_ownership_rules(rules, _ROOT),
    )
    assert (decision.owner, decision.contract) == ("dependency:d", "external")
