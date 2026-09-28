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

"""``extract.ownership.classify``'s per-rules path memo.

``classify`` runs once per declaration, but everything it decides from the
declaring-file path is cached on the ``ResolvedOwnershipRules`` object. The
oracle here is the pre-memo ``classify``, kept verbatim below, run over
generated rules and generated site *sequences* against one rules object --
so later sites are answered from entries earlier ones created, which is the
case a wrong cache key would get wrong.
"""

from __future__ import annotations

import fnmatch
from pathlib import PurePosixPath

from hypothesis import given, settings, strategies as st

from abicheck.extract import ownership as own
from abicheck.extract.ownership import (
    DeclarationSite,
    OwnershipDecision,
    ResolvedOwnershipRules,
    classify,
    resolve_ownership_rules,
)
from abicheck.model.ownership_rules import (
    CONTRACT_EXTERNAL,
    CONTRACT_PRIVATE,
    CONTRACT_PUBLIC,
    CONTRACT_UNRESOLVED,
    OWNER_TARGET,
    OWNER_TOOLCHAIN,
    OWNER_UNRESOLVED,
    DependencyRoots,
    OwnershipRules,
)
from abicheck.provenance import is_system_header


def _reference_classify(
    site: DeclarationSite, rules: ResolvedOwnershipRules
) -> OwnershipDecision:
    """``classify`` as it was before the path memo (verbatim logic)."""
    parts = own._name_parts(site.qualified_name)
    if (
        site.artificial
        and len(parts) == 1
        and parts[0].startswith(own._BUILTIN_PREFIXES)
    ):
        return OwnershipDecision(OWNER_TOOLCHAIN, CONTRACT_EXTERNAL, "builtin")
    if not site.path:
        return OwnershipDecision(OWNER_UNRESOLVED, CONTRACT_UNRESOLVED, "no_file")
    segments = own._segments(site.path, rules.project_root)
    root = own._matching_root(segments, rules.roots)
    if root is None:
        if is_system_header(site.path):
            return OwnershipDecision(OWNER_TOOLCHAIN, CONTRACT_EXTERNAL, "system_path")
        return OwnershipDecision(OWNER_UNRESOLVED, CONTRACT_UNRESOLVED, "no_root")
    if root.owner != OWNER_TARGET:
        return OwnershipDecision(root.owner, CONTRACT_EXTERNAL, root.rule_id)
    diagnostics: tuple[str, ...] = ()
    name = own._name_parts(site.qualified_name)
    if name and name[0] in rules.dependency_names:
        diagnostics = (
            f"{site.qualified_name} is declared in a target file but in "
            f"dependency namespace {name[0]!r}; the file decides",
        )
    private = None
    base = PurePosixPath(*own._segments(".", rules.project_root))
    full = PurePosixPath(*segments)
    candidates = [str(full)]
    if full.is_relative_to(base):
        candidates.append(str(full.relative_to(base)))
    for pattern in rules.private_headers:
        if any(fnmatch.fnmatchcase(c, pattern) for c in candidates):
            private = f"private_header:{pattern}"
            break
    if private is None:
        for ns in rules.private_namespaces:
            if len(name) > len(ns) and name[: len(ns)] == ns:
                private = f"private_namespace:{'::'.join(ns)}"
                break
    if private is not None:
        return OwnershipDecision(OWNER_TARGET, CONTRACT_PRIVATE, private, diagnostics)
    return OwnershipDecision(OWNER_TARGET, CONTRACT_PUBLIC, root.rule_id, diagnostics)


_ROOTS = ["include", "include/lib", "src", "/opt/dep/include", "include/lib/third"]
_PATHS = [
    None,
    "",
    "include/a.h",
    "include/lib/a.h",
    "include/lib/detail/a.h",
    "include/lib/third/x.h",
    "/p/include/lib/a.h",
    "/p/src/impl.inl",
    "/opt/dep/include/d.h",
    "/usr/include/stdio.h",
    "other/a.h",
    "include/../src/b.h",
]
_NAMES = ["", "f", "ns::f", "dep::g", "ns::detail::h", "__builtin_x", "ns::detail"]


@st.composite
def _rules(draw: st.DrawFn) -> ResolvedOwnershipRules:
    roots = draw(st.lists(st.sampled_from(_ROOTS), unique=True, max_size=4))
    split = draw(st.integers(min_value=0, max_value=len(roots)))
    return resolve_ownership_rules(
        OwnershipRules(
            target_roots=tuple(roots[:split]),
            dependencies=(DependencyRoots("dep", tuple(roots[split:])),),
            private_headers=tuple(
                draw(
                    st.lists(
                        st.sampled_from(
                            ["*/detail/*", "src/*", "*.inl", "include/a.h"]
                        ),
                        unique=True,
                    )
                )
            ),
            private_namespaces=tuple(
                draw(
                    st.lists(st.sampled_from(["ns::detail", "dep", "ns"]), unique=True)
                )
            ),
        ),
        "/p",
    )


_sites = st.builds(
    DeclarationSite,
    path=st.sampled_from(_PATHS),
    qualified_name=st.sampled_from(_NAMES),
    artificial=st.booleans(),
)


@settings(max_examples=300, deadline=None)
@given(rules=_rules(), sites=st.lists(_sites, max_size=30))
def test_memoized_classify_equals_reference(
    rules: ResolvedOwnershipRules, sites: list[DeclarationSite]
) -> None:
    for site in sites:
        assert classify(site, rules) == _reference_classify(site, rules)


def test_memo_distinguishes_paths_and_is_per_rules_object() -> None:
    """Every input the cached answer depends on is part of its key: the
    path (two headers under one rules object), and the rules themselves
    (the memo lives on the rules object, so two rules objects never share
    an entry)."""
    public = resolve_ownership_rules(OwnershipRules(target_roots=("include",)), "/p")
    private = resolve_ownership_rules(
        OwnershipRules(target_roots=("include",), private_headers=("*/detail/*",)), "/p"
    )
    detail = DeclarationSite("include/detail/x.h", "f")
    plain = DeclarationSite("include/x.h", "f")

    assert classify(plain, private).contract == CONTRACT_PUBLIC
    assert classify(detail, private).contract == CONTRACT_PRIVATE
    assert classify(plain, private).contract == CONTRACT_PUBLIC
    assert classify(detail, public).contract == CONTRACT_PUBLIC
    assert set(private._path_memo) == {"include/detail/x.h", "include/x.h"}
    assert set(public._path_memo) == {"include/detail/x.h"}


def test_memo_is_not_part_of_rules_equality_or_hash() -> None:
    rules = OwnershipRules(target_roots=("include",))
    a = resolve_ownership_rules(rules, "/p")
    b = resolve_ownership_rules(rules, "/p")
    classify(DeclarationSite("include/x.h", "f"), a)
    assert a._path_memo and not b._path_memo
    assert a == b and hash(a) == hash(b)
