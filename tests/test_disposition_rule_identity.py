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

"""One rule, one identity: the audit and the D7 receipt must agree.

ADR-067's audit groups its records by the rule that disposed of them, and
ADR-049 D7's effective-configuration receipt lists the rules a run loaded.
Both answer "which rule is this", and they derived it separately until a
review found the consequence: the audit's copy was a hand-picked list of
*selector* fields, so two rules differing only in a **gate** field --
`reachability`, `allow_public_break`, `allow_unknown_reachability` -- rendered
one identity and were merged into a single audit row despite suppressing
different findings. That is the repository's "no second selector grammar"
rule failing in the small.

Split out of `tests/test_disposition_audit.py` when that file reached the
architecture gate's 1200-line test cap; the seam is a real one (rule
identity, not the ledger's conservation contract). Registered as a seed test
of the `policy.disposition_conservation` bug class.
"""

from __future__ import annotations

from abicheck.suppression import Suppression, SuppressionList


def test_every_matching_relevant_field_separates_two_rules() -> None:
    """The sharper form of the test above, and the gap it missed.

    Its fixture pair differed in `symbol_pattern`, so it passed against an
    identity that only looked at *selector* fields — a public-only waiver and
    a proven-unreachable-only waiver sharing one `symbol_pattern` and one
    label rendered a single identity and were merged into one audit row,
    even though they suppress different findings (Codex review).

    Stated over `Suppression`'s own field list rather than as one pair, so a
    field added later is covered without editing this test: for *every*
    matching-relevant field, a rule that sets it and an otherwise identical
    rule that does not must not collapse. The oracle is the field list
    itself, not the identity function.
    """
    import dataclasses
    from datetime import date

    from abicheck.policy.rule_identity import rule_identity

    # `symbol`/`symbol_pattern`/`type_pattern` are mutually exclusive, so they
    # cannot be varied against a shared baseline that already sets one; they
    # are checked below as three rules that must stay three.
    exclusive = ("symbol", "symbol_pattern", "type_pattern")
    probe_values = {
        "change_kind": "func_removed",
        "label": "L",
        "source_location": "a.h",
        "namespace": "ns",
        "cause_namespace": "ns",
        "reachability": "proven-unreachable-only",
        "allow_public_break": True,
        "allow_unknown_reachability": True,
        "binding": "weak",
        "finding_id": "0123456789abcdef",
        "expires": date(2030, 1, 1),
    }
    baseline = Suppression(member_name="base", reason="shared prose")
    base_identity = rule_identity(baseline)
    varied = [
        f.name
        for f in dataclasses.fields(Suppression)
        # `reason` is prose and is excluded from every identity by design;
        # `member_name` is the baseline's own selector, and `entity_namespace`
        # is a declared alias of `namespace` that cannot be set alongside it.
        if f.init
        and f.name not in {"reason", "member_name", "entity_namespace", *exclusive}
    ]
    assert set(varied) <= set(probe_values), (
        "a new Suppression field has no probe value here — add one rather "
        "than letting this test silently stop covering it"
    )
    for name in varied:
        variant = Suppression(
            member_name="base", reason="shared prose", **{name: probe_values[name]}
        )
        assert rule_identity(variant) != base_identity, (
            f"a rule differing only in {name!r} shares an identity with one "
            "that does not set it, so the audit would merge them"
        )

    exclusive_identities = {
        rule_identity(Suppression(reason="shared prose", **{name: "x"}))
        for name in exclusive
    }
    assert len(exclusive_identities) == len(exclusive), (
        "the three mutually exclusive selectors must not render one identity"
    )


def test_the_audit_and_the_receipt_derive_one_identity() -> None:
    """ "No second selector grammar" applied to rule identity: the ADR-049 D7
    receipt (`SuppressionList.rule_identities`) and the ADR-067 audit must
    answer the same string for the same rule, or two parts of one report
    disagree about which rule fired."""
    from abicheck.policy.disposition_ledger import _rule_identity

    rules = [
        Suppression(symbol_pattern=".*", label="same", reason="r"),
        Suppression(
            symbol_pattern=".*",
            label="same",
            reason="r",
            reachability="proven-unreachable-only",
        ),
        Suppression(
            symbol_pattern=".*", label="same", reason="r", allow_public_break=True
        ),
    ]
    receipt = SuppressionList(rules).rule_identities()
    assert list(receipt) == [_rule_identity(r) for r in rules]
    assert len(set(receipt)) == 3, "three distinct rules, three identities"
