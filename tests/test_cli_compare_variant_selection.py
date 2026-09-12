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

"""`compare --variant`: the side-scoped variant selector and its remediation.

Split out of ``test_cli_compare_release_project_snapshot_package.py`` when
the additions for ADR-068 plan Phase 7j pushed that module past the
architecture contract's 1200-line test maximum (Codex review, PR #1184).
The split is by *contract axis*, not by line count: that module exercises
stored-versus-live release **parity** (does a stored `ProjectSnapshot`
package produce the findings a live directory would?), while everything
here is about the ``--variant`` **selector** itself -- its resolution
primitive, its CLI surface, and whether the errors it raises give advice
that actually works.

Fixture helpers are imported from the parent module rather than copied, so
the two stay on one definition of what a package fixture is.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_cli_compare_release_project_snapshot_package import (
    _invoke,
    _old_new_libraries,
    _sorted_outcomes,
    _write_directory,
    _write_package,
)

__all__ = [
    "_invoke",
    "_old_new_libraries",
    "_sorted_outcomes",
    "_write_directory",
    "_write_package",
]


class TestResolveSidedVariantProperties:
    """Primitive-level property tests for ``_resolve_sided_variant``.

    AGENTS.md's "Primitive-level property tests" rule: the ADR-068 Phase 7j
    merge of ``--old-variant``/``--new-variant`` into one side-scoped
    ``--variant`` rests entirely on this reusable base-plus-override
    resolver, so its contract is stated here as invariants over generated
    input rather than only through the two ``compare`` invocations above --
    a fixed-example test would only foreclose the exact token order it
    happens to name, and order-dependence is precisely the defect class
    this repo's own merge-primitive history (`_paired_stable_indices`)
    records.

    The oracle is deliberately *not* the implementation's own loop: each
    invariant is checked against an independently-stated rule about the
    last token that can reach a side ("the last ``both=``/``old=`` token
    wins for OLD"), computed by filtering the input, not by re-running the
    resolver.
    """

    @staticmethod
    def _resolve(pairs: list[tuple[str, str]]) -> tuple[str | None, str | None]:
        from abicheck.frontends.cli.options.release import _resolve_sided_variant

        return _resolve_sided_variant(pairs)

    @staticmethod
    def _oracle(pairs: list[tuple[str, str]]) -> tuple[str | None, str | None]:
        """Independent statement of the rule: for each side, the value of the
        last token that *addresses* that side (``both`` addresses both)."""
        old = next((v for s, v in reversed(pairs) if s in ("both", "old")), None)
        new = next((v for s, v in reversed(pairs) if s in ("both", "new")), None)
        return old, new

    @pytest.mark.parametrize(
        "pairs",
        [
            [],
            [("both", "v1")],
            [("old", "v1")],
            [("new", "v2")],
            [("old", "v1"), ("new", "v2")],
            [("new", "v2"), ("old", "v1")],
            [("both", "v1"), ("old", "v2")],
            [("old", "v2"), ("both", "v1")],
            [("both", "v1"), ("both", "v2")],
            [("old", "a"), ("old", "b")],
            [("new", "a"), ("new", "b")],
            [("both", "base"), ("old", "o"), ("new", "n")],
            [("old", "o"), ("both", "base"), ("new", "n")],
            [("old", "o"), ("new", "n"), ("both", "base")],
        ],
    )
    def test_matches_an_independently_stated_last_writer_rule(
        self, pairs: list[tuple[str, str]]
    ) -> None:
        assert self._resolve(pairs) == self._oracle(pairs)

    def test_exhaustive_over_every_short_token_sequence(self) -> None:
        """Small-domain exhaustive enumeration: every sequence of up to three
        tokens drawn from the full side vocabulary, with distinct values so a
        wrong-bucket write cannot coincidentally look correct."""
        import itertools

        sides = ("both", "old", "new")
        for length in range(4):
            for combo in itertools.product(sides, repeat=length):
                pairs = [(s, f"v{i}") for i, s in enumerate(combo)]
                assert self._resolve(pairs) == self._oracle(pairs), pairs

    def test_a_later_both_rebases_both_sides(self) -> None:
        assert self._resolve([("old", "o"), ("new", "n"), ("both", "b")]) == (
            "b",
            "b",
        )

    def test_a_per_side_override_survives_an_earlier_base(self) -> None:
        assert self._resolve([("both", "b"), ("new", "n")]) == ("b", "n")

    def test_the_two_side_buckets_are_independent(self) -> None:
        """Interleaving OLD-only and NEW-only tokens never lets one bucket's
        ordering affect the other's outcome."""
        olds = [("old", "o1"), ("old", "o2")]
        news = [("new", "n1"), ("new", "n2")]
        for interleaved in (
            olds + news,
            news + olds,
            [olds[0], news[0], olds[1], news[1]],
            [news[0], olds[0], news[1], olds[1]],
        ):
            assert self._resolve(interleaved) == ("o2", "n2"), interleaved

    def test_unset_stays_none_rather_than_a_synthesized_default(self) -> None:
        """Unlike ``--version`` (per-side defaults ``old``/``new``), an unset
        variant must stay ``None`` -- that is what selects a package's sole
        declared variant downstream, and a synthesized label would instead
        request a variant no package declares."""
        assert self._resolve([]) == (None, None)
        assert self._resolve([("old", "v1")]) == ("v1", None)


class TestVariantFlagSurface:
    """ADR-068 D5 / Phase 7j: the retired pair has no alias, and the new
    spelling is side-scoped exactly like every other two-sided input."""

    @pytest.mark.parametrize("flag", ["--old-variant", "--new-variant"])
    def test_retired_spellings_are_usage_errors(self, flag: str) -> None:
        ec, out = _invoke("compare", flag, "v1", "a", "b")
        assert ec == 64
        assert "no such option" in out.lower() or "usage" in out.lower()

    @pytest.mark.parametrize("value", ["", "old=", "new=", "both="])
    def test_an_empty_variant_id_is_a_usage_error(self, value: str) -> None:
        ec, out = _invoke("compare", "--variant", value, "a", "b")
        assert ec == 64
        assert "--variant" in out

    def test_the_flag_is_registered_once_and_side_scoped(self) -> None:
        import click

        from abicheck.cli import main

        opts = [
            p
            for p in main.commands["compare"].params
            if isinstance(p, click.Option) and p.name == "variant"
        ]
        assert [o.opts for o in opts] == [["--variant"]]
        assert opts[0].multiple is True
        assert "[old=|new=]" in opts[0].type.get_metavar(opts[0])


class TestRemediationNamesOnlyLiveFlags:
    """Codex review, PR #1184: a retired CLI spelling still named in a
    runtime remediation message.

    `project_snapshot_legacy.resolve_release_package_map`'s
    multi-variant ambiguity error told the caller to "pass an explicit
    variant id (--old-variant/--new-variant)". Phase 7j retired both
    spellings with no alias, so a user who followed the tool's own advice
    landed straight in an exit-64 usage error -- the CLI actively
    misdirecting them.

    The bug *class* is "a user-facing message advises a flag the command
    it is advising does not accept", so that is what these tests assert,
    not the one string. The oracle is independent of the implementation:
    every ``--flag`` token appearing in a message is checked against the
    live Click command's own accepted option set, which is where the
    message's advice actually has to land. A test pinned to the exact new
    wording would pass again the next time the wording changes to name
    some other dead flag.
    """

    @staticmethod
    def _suggested_flags(message: str) -> set[str]:
        """Every ``--flag`` token a message names.

        Deliberately a plain lexical sweep rather than anything that
        consults the option tables: an oracle built from the same source
        the implementation reads could not falsify it.
        """
        import re

        return set(re.findall(r"--[A-Za-z0-9][A-Za-z0-9-]*", message))

    @staticmethod
    def _accepted_options(command: str) -> set[str]:
        import click

        from abicheck.cli import main

        return {
            spelling
            for p in main.commands[command].params
            if isinstance(p, click.Option)
            for spelling in (*p.opts, *p.secondary_opts)
        }

    def _assert_advice_lands(self, message: str, command: str) -> set[str]:
        suggested = self._suggested_flags(message)
        accepted = self._accepted_options(command)
        dead = sorted(suggested - accepted)
        assert not dead, (
            f"the {command} run's own message advises {dead}, which "
            f"{command} does not accept -- following this remediation "
            "exits 64. Name a live spelling."
        )
        return suggested

    def test_the_oracle_itself_catches_a_retired_spelling(self) -> None:
        """Negative control. Without this, every assertion below would
        pass just as happily against a message naming nothing at all, or
        against an oracle whose set difference silently came out empty."""
        retired = "pass an explicit variant id (--old-variant/--new-variant)"
        assert self._suggested_flags(retired) == {"--old-variant", "--new-variant"}
        with pytest.raises(AssertionError, match="does not accept"):
            self._assert_advice_lands(retired, "compare")

    def test_the_oracle_accepts_a_live_spelling(self) -> None:
        """The complementary control: the check is not vacuously strict."""
        assert self._assert_advice_lands("try --variant old=v1", "compare") == {
            "--variant"
        }

    def _multi_variant_package(self, root: Path) -> None:
        from abicheck.project_snapshot_store import (
            read_project_manifest,
            write_project_manifest,
        )
        from abicheck.storage.package import PackageManifest, VariantRef

        old_libs, _ = _old_new_libraries()
        _write_package(root, {"liba.so": old_libs["liba.so"]}, variant_id="gcc13")
        existing = read_project_manifest(root)
        combined = PackageManifest(
            versions=existing.versions,
            variant_refs=existing.variant_refs
            + (VariantRef(variant_id="gcc14", artifact_ids=()),),
            artifact_refs=existing.artifact_refs,
        )
        write_project_manifest(root, combined)

    @staticmethod
    def _suggested_example(message: str) -> list[str]:
        """The argv fragment the message tells the user to run, if any.

        Parsed out of the `(e.g. --variant new=v1)` form the remediation
        uses, so the test can *execute* the advice rather than merely
        eyeballing it.
        """
        import re

        m = re.search(r"e\.g\. (--\S+ \S+|--\S+=\S+|--\S+)\)", message)
        return m.group(1).split() if m else []

    @pytest.mark.parametrize("ambiguous_side", ["old", "new"])
    def test_following_the_suggested_remediation_actually_works(
        self, tmp_path: Path, ambiguous_side: str
    ) -> None:
        """The strongest form of the invariant, and the one that catches
        what flag-existence alone cannot: **run the advice**.

        Codex review, PR #1184, second round. The first fix named the live
        `--variant` flag but hard-coded the side to `old=`, so when NEW was
        the multi-variant operand the message told the user to run
        something that fails just as hard -- a live flag pointed at the
        wrong side. A test that only checks "the suggested flag exists"
        passes on that bug, which is exactly what happened. So this one
        takes the message's own example, re-invokes `compare` with it, and
        asserts the variant ambiguity is actually resolved.

        Parametrized over which side is ambiguous, because the asymmetry
        *is* the defect: a bare `--variant ID` is not a safe fallback
        either (it applies to both sides, and the non-ambiguous side does
        not declare that id).
        """
        old_libs, new_libs = _old_new_libraries()
        old_pkg = tmp_path / "old_pkg"
        new_pkg = tmp_path / "new_pkg"
        if ambiguous_side == "old":
            self._multi_variant_package(old_pkg)
            _write_package(new_pkg, new_libs)
        else:
            _write_package(old_pkg, {"liba.so": old_libs["liba.so"]})
            self._multi_variant_package(new_pkg)

        ec, out = _invoke("compare", str(old_pkg), str(new_pkg), "-o", "json=-")
        assert ec == 64, out
        self._assert_advice_lands(out, "compare")

        example = self._suggested_example(out)
        assert example, f"the error offered no runnable remediation: {out}"
        assert example[0] == "--variant"
        # The side must be the ambiguous operand's, not a hard-coded one.
        assert example[1].startswith(f"{ambiguous_side}="), (
            f"advice names the wrong side: {example} while {ambiguous_side.upper()} "
            "is the ambiguous operand"
        )

        # Execute it. The ambiguity must be gone -- the run may still fail
        # for unrelated reasons, but never again on variant selection.
        ec2, out2 = _invoke(
            "compare", str(old_pkg), str(new_pkg), *example, "--format", "json"
        )
        assert "declares" not in out2 or "variant(s)" not in out2, (
            f"following the tool's own advice {example} still hit a variant "
            f"error:\n{out2}"
        )
        assert ec2 != 64, f"the suggested remediation is itself a usage error:\n{out2}"

    @pytest.mark.parametrize(
        "extra",
        [
            (),
            ("--variant", "old=nope"),
            ("--variant", "old="),
            ("--variant", "both=nope"),
        ],
        ids=["ambiguous", "unknown-id", "empty-id", "unknown-both"],
    )
    def test_every_variant_error_path_advises_only_live_flags(
        self, tmp_path: Path, extra: tuple[str, ...]
    ) -> None:
        """Several independently-chosen sibling paths through the same
        family, not just the one that was reported: ambiguity, an
        unknown per-side id, an empty id, and an unknown both-sides id
        each produce a different message from a different call site."""
        _, new_libs = _old_new_libraries()
        old_pkg = tmp_path / "old_pkg"
        new_pkg = tmp_path / "new_pkg"
        self._multi_variant_package(old_pkg)
        _write_package(new_pkg, new_libs)

        ec, out = _invoke(
            "compare", str(old_pkg), str(new_pkg), *extra, "--format", "json"
        )
        assert ec != 0
        self._assert_advice_lands(out, "compare")

    def test_a_package_declaring_zero_variants_still_errors_cleanly(
        self, tmp_path: Path
    ) -> None:
        """The empty-list guard on the remediation example. `len != 1`
        covers zero as well as many, so building the example by indexing
        the known ids would turn a clear ValueError into an IndexError on
        a package that declares none."""
        from abicheck.project_snapshot_legacy import (
            materialize_release_variant_artifacts,
        )
        from abicheck.project_snapshot_store import (
            read_project_manifest,
            write_project_manifest,
        )
        from abicheck.storage.package import PackageManifest

        old_libs, _ = _old_new_libraries()
        pkg = tmp_path / "pkg"
        _write_package(pkg, {"liba.so": old_libs["liba.so"]}, variant_id="v1")
        existing = read_project_manifest(pkg)
        write_project_manifest(
            pkg,
            PackageManifest(
                versions=existing.versions,
                variant_refs=(),
                # artifact_refs must go too: PackageManifest rejects an
                # artifact naming an undeclared variant, so "zero
                # variants" is only representable as an empty package.
                artifact_refs=(),
            ),
        )

        with pytest.raises(ValueError, match="declares 0 variant") as excinfo:
            materialize_release_variant_artifacts(
                pkg, variant_id=None, dest_root=tmp_path / "out"
            )
        # No example is offered when there is nothing to select, and the
        # message still names no dead flag.
        assert "e.g." not in str(excinfo.value)
        self._assert_advice_lands(str(excinfo.value), "compare")
