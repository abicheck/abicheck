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

"""CLI cleanup phase two, "PR B" effective-config parity, first slice.

Split out of ``test_effective_config_digest.py`` rather than appended to it
(ADR-061 no-growth debt ledger: that file, like ``cli_compare_release.py``/
``cli_compare_release_helpers.py``, is a ``debt.yaml``-tracked legacy module
already sitting at its adoption baseline, so a new behavior axis gets its own
focused module instead of growing it) -- see ``abicheck.cli_compare_receipt.
record_release_resolved_config``'s own docstring for what this covers and
why (an attempt to home this in ``abicheck.workflows.release_evaluation``
instead was reverted: the contract-context merge half needs real
``contract_context``/``contract_evidence`` objects, and
``scripts/check_architecture.py``'s ``unclassified-import`` check correctly
refuses a ``workflows/`` module importing either until they're ADR-061-
classified).

Duplicates the three small fixture helpers (`_identity`/
`_minimal_evaluation_config`/`_result`) from ``test_effective_config_digest.
py`` rather than importing them from that sibling test module -- each test
file in this suite is conventionally self-contained.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from abicheck.change_registry_types import Verdict
from abicheck.checker import DiffResult
from abicheck.compatibility_evaluation_config import (
    AssuranceConfig,
    CompatibilityEvaluationConfig,
    CompatibilityPolicyConfig,
    ContractConfig,
    EvidenceConfig,
    GateConfig,
    ImmutableIdentity,
    SurfaceConfig,
    ValueProvenance,
)
from abicheck.contract_relevance_types import ContractMode, SelectorLayer
from abicheck.effective_config_digest import (
    effective_config_digest,
    effective_config_fields,
)


def _identity(
    identity_id: str, version: int = 1, sha256: str = "digest"
) -> ImmutableIdentity:
    return ImmutableIdentity(id=identity_id, version=version, sha256=sha256)


def _minimal_evaluation_config(**overrides) -> CompatibilityEvaluationConfig:
    fields = dict(
        contract=ContractConfig(mode=ContractMode.PUBLIC),
        evidence=EvidenceConfig(),
        surface=SurfaceConfig(),
        assurance=AssuranceConfig(),
        policy=CompatibilityPolicyConfig(base=_identity("strict_abi")),
        gate=GateConfig(),
    )
    fields.update(overrides)
    return CompatibilityEvaluationConfig(**fields)


def _result(**overrides) -> DiffResult:
    base = dict(
        old_version="1.0",
        new_version="2.0",
        library="libtest.so.1",
        verdict=Verdict.NO_CHANGE,
    )
    base.update(overrides)
    return DiffResult(**base)


class TestReleaseFanOutStampsResolvedConfig:
    """CLI cleanup phase two, "PR B" effective-config parity: the directory/
    package release fan-out's own per-library digest stayed at the baseline
    tier even under ``--pack``, because ``cli_compare_release._run_compare_
    pair`` never stamped the release's already-resolved
    ``CompatibilityEvaluationConfig`` onto each library's own ``DiffResult``
    the way ``cli_compare_receipt.record_resolved_config`` does for
    single-pair ``compare`` -- see ``effective_config_digest``'s own module
    docstring, "Known, documented gap" section, for the full description.
    Closed by threading the resolved config through
    ``pack_application.PackApplication.resolved_config`` (set once, for the
    whole release, by the ``pack_application()`` factory both paths share)
    and stamping it in ``_run_compare_pair`` via
    ``abicheck.cli_compare_receipt.record_release_resolved_config``, right
    after ``service.run_compare`` returns. That function also merges into
    an existing ``contract_context`` (a release run given ``--contract``),
    which ``TestReleaseFanOutMergesContractContext`` below covers
    separately.
    """

    @staticmethod
    def _snap(version: str = "1.0"):
        from abicheck.model import AbiSnapshot

        return AbiSnapshot(library="libfoo.so", version=version)

    def _run_compare_pair_with(self, pack_application, tmp_path: Path):
        from abicheck.api_types import CompareResult

        fake_diff = _result()
        fake_result = CompareResult(
            diff=fake_diff,
            old_snapshot=self._snap("1.0"),
            new_snapshot=self._snap("2.0"),
        )
        old = tmp_path / "old.so"
        new = tmp_path / "new.so"
        old.write_bytes(b"")
        new.write_bytes(b"")
        with (
            patch("abicheck.service.run_compare", return_value=fake_result),
            patch(
                "abicheck.cli_compare_release_pairwise._normalize_binary_input",
                side_effect=lambda p: (p, None),
            ),
        ):
            from abicheck.cli_compare_release_pairwise import _run_compare_pair as _rcp

            returned = _rcp(
                old,
                new,
                [],
                [],
                [],
                [],
                "1.0",
                "2.0",
                "c++",
                None,
                "strict_abi",
                None,
                None,
                None,
                pack_application=pack_application,
            )
        assert returned is fake_result
        return fake_diff

    def test_no_pack_application_leaves_evaluation_config_unset(
        self, tmp_path: Path
    ) -> None:
        diff = self._run_compare_pair_with(None, tmp_path)
        assert diff.evaluation_config is None

    def test_pack_with_no_resolved_config_leaves_evaluation_config_unset(
        self, tmp_path: Path
    ) -> None:
        """A hand-built ``PackApplication`` with no ``resolved_config`` (the
        pre-this-fix shape, and what every direct-construction test/caller
        that doesn't go through the ``pack_application()`` factory still
        produces) must not be treated as "config resolved" -- staying at the
        baseline tier is correct for it, not a regression."""
        from abicheck.pack_application import PackApplication

        diff = self._run_compare_pair_with(
            PackApplication(policy_overrides={}), tmp_path
        )
        assert diff.evaluation_config is None

    def test_pack_application_stamps_the_resolved_config(self, tmp_path: Path) -> None:
        from abicheck.pack_application import PackApplication

        config = _minimal_evaluation_config()
        diff = self._run_compare_pair_with(
            PackApplication(policy_overrides={}, resolved_config=config),
            tmp_path,
        )
        assert diff.evaluation_config is config

    def test_stamped_config_reaches_the_rich_tier_digest(self) -> None:
        """End to end from the stamped attribute to the actual digest --
        proving the fix closes the documented gap, not just that an
        attribute got set."""
        result = _result()
        result.evaluation_config = _minimal_evaluation_config()
        fields = effective_config_fields(
            result, severity_config=None, exit_code_scheme="legacy"
        )
        assert fields["_tier"] == "contract"

    def test_two_pack_revisions_with_identical_assignments_differ_by_identity(
        self,
    ) -> None:
        """The exact scenario the documented gap names: two pack *revisions*
        that happen to project the same current field assignments must still
        produce different digests, because pack *identity* (id/version/
        sha256), not just the values it currently assigns, is part of the
        rich tier's ``packs`` field (``ContractConfig.packs`` here -- one of
        the three sections, alongside policy/gate, that carry a selected
        pack's ``ImmutableIdentity``)."""
        rev1 = _minimal_evaluation_config(
            contract=ContractConfig(
                mode=ContractMode.PUBLIC,
                packs=(_identity("ignore_removals", version=1),),
            )
        )
        rev2 = _minimal_evaluation_config(
            contract=ContractConfig(
                mode=ContractMode.PUBLIC,
                packs=(_identity("ignore_removals", version=2),),
            )
        )
        result1, result2 = _result(), _result()
        result1.evaluation_config = rev1
        result2.evaluation_config = rev2
        fields1 = effective_config_fields(
            result1, severity_config=None, exit_code_scheme="legacy"
        )
        fields2 = effective_config_fields(
            result2, severity_config=None, exit_code_scheme="legacy"
        )
        assert fields1["_tier"] == fields2["_tier"] == "contract"
        assert effective_config_digest(fields1) != effective_config_digest(fields2)

    def test_pack_application_factory_populates_resolved_config(self) -> None:
        """Direct unit test of the shared factory both single-pair `compare`
        (via `resolve_and_apply`) and the release fan-out (via
        `resolve_release_pack_application`) call -- confirms the field is
        wired at the source, not only through `_run_compare_pair`'s own
        stamp."""
        from abicheck.pack_application import pack_application

        config = _minimal_evaluation_config()
        application = pack_application(config, policy_file=None)
        assert application.resolved_config is config


class TestReleaseFanOutMergesContractContext:
    """Codex review, fresh evidence: the first cut of this fix only stamped
    the bare ``DiffResult.evaluation_config`` attribute. ``effective_config_
    digest.effective_config_fields`` prefers ``contract_context.
    evaluation_context.resolved_config`` over that bare attribute whenever a
    ``PersistedContractContext`` exists -- which a release comparison run
    with ``--contract`` builds per library, same as single-pair `compare` --
    so the rich tier stayed silently unreachable for exactly the `--pack`
    *and* `--contract` combination. ``cli_compare_receipt.
    record_release_resolved_config`` now also merges into an existing
    context via ``contract_context.with_resolved_config``, mirroring what
    ``record_resolved_config`` already does for single-pair `compare`."""

    @staticmethod
    def _persisted_context(resolved_config):
        from abicheck.contract_evidence import (
            ContractEvidenceBlock,
            EvaluationContextBlock,
            PersistedContractContext,
        )

        return PersistedContractContext(
            contract_evidence=ContractEvidenceBlock(),
            evaluation_context=EvaluationContextBlock(resolved_config=resolved_config),
        )

    def test_no_contract_context_only_stamps_the_bare_attribute(self) -> None:
        from abicheck.cli_compare_receipt import record_release_resolved_config

        config = _minimal_evaluation_config()
        diff = _result()
        record_release_resolved_config(diff, config)
        assert diff.evaluation_config is config
        assert getattr(diff, "contract_context", None) is None

    def test_existing_contract_context_is_merged_not_ignored(self) -> None:
        """The actual regression: without the merge, `effective_config_
        fields` reads the *old*, unmerged context's config and never sees
        the pack's identity at all."""
        from abicheck.cli_compare_receipt import record_release_resolved_config

        unmerged = _minimal_evaluation_config()
        pack_config = _minimal_evaluation_config(
            contract=ContractConfig(
                mode=ContractMode.PUBLIC,
                packs=(_identity("ignore_removals", version=1),),
            )
        )
        diff = _result()
        diff.contract_context = self._persisted_context(unmerged)

        record_release_resolved_config(diff, pack_config)

        fields = effective_config_fields(
            diff, severity_config=None, exit_code_scheme="legacy"
        )
        assert fields["_tier"] == "contract"
        # Reflects the merged (pack-carrying) config, not the stale one the
        # context was built with.
        assert "ignore_removals@1:digest" in fields["packs"]

    def test_no_config_is_a_complete_no_op(self) -> None:
        """No --pack at all (config=None): neither field may change, even
        when a contract_context already exists -- `record_release_resolved_
        config` must not fabricate a merge from nothing."""
        from abicheck.cli_compare_receipt import record_release_resolved_config

        original_config = _minimal_evaluation_config()
        diff = _result()
        original_ctx = self._persisted_context(original_config)
        diff.contract_context = original_ctx

        record_release_resolved_config(diff, None)

        assert diff.evaluation_config is None
        assert diff.contract_context is original_ctx


class TestReleaseFanOutPreservesObservedSuppressions:
    """Codex review, fresh evidence: unlike single-pair `compare`'s
    ``resolve_and_apply`` (which passes the real, already-loaded
    ``SuppressionList`` into the resolver), the release fan-out's own
    ``resolve_release_pack_application(_from_ctx)`` only ever passes a raw
    ``--suppress`` *path* -- and the resolver's own ``_suppression_source``
    helper returns ``None`` whenever no already-loaded object is given, path
    or not. So the release-wide ``config`` this module's
    ``record_release_resolved_config`` merges in always has
    ``suppressions=None``, regardless of whether ``--suppress`` is active --
    while each library's own ``contract_context`` (built per library by
    ``service.run_compare``) DID resolve the real one. A plain
    ``with_resolved_config`` merge would silently drop that real suppression
    digest/rule identities from the persisted receipt; this class covers the
    fix that restores them."""

    @staticmethod
    def _persisted_context(resolved_config):
        from abicheck.contract_evidence import (
            ContractEvidenceBlock,
            EvaluationContextBlock,
            PersistedContractContext,
        )

        return PersistedContractContext(
            contract_evidence=ContractEvidenceBlock(),
            evaluation_context=EvaluationContextBlock(resolved_config=resolved_config),
        )

    def test_observed_suppressions_survive_the_merge(self) -> None:
        from abicheck.cli_compare_receipt import record_release_resolved_config
        from abicheck.compatibility_evaluation_config import SuppressionConfig

        observed_suppressions = SuppressionConfig(
            sha256="sha256:observed", rules=("cxx_standard_floor_raised",)
        )
        observed = _minimal_evaluation_config(suppressions=observed_suppressions)
        # The release-wide config -- unconditionally suppressions=None, per
        # this class's own docstring, regardless of whether --suppress is
        # active for this release.
        release_wide_config = _minimal_evaluation_config(
            contract=ContractConfig(
                mode=ContractMode.PUBLIC,
                packs=(_identity("ignore_removals", version=1),),
            )
        )
        assert release_wide_config.suppressions is None

        diff = _result()
        diff.contract_context = self._persisted_context(observed)

        record_release_resolved_config(diff, release_wide_config)

        merged_config = diff.contract_context.evaluation_context.resolved_config
        assert merged_config.suppressions is observed_suppressions
        # The pack identity from the release-wide config must still be
        # present -- restoring suppressions must not undo the actual fix.
        assert merged_config.contract.packs == release_wide_config.contract.packs

    def test_configs_own_suppressions_win_when_it_has_one(self) -> None:
        """If the release-wide config ever *does* carry real suppressions
        (a future fix to resolve_release_pack_application, or a caller this
        module doesn't control), that real value must not be silently
        discarded in favor of the observed one."""
        from abicheck.cli_compare_receipt import record_release_resolved_config
        from abicheck.compatibility_evaluation_config import SuppressionConfig

        observed_suppressions = SuppressionConfig(sha256="sha256:observed", rules=())
        observed = _minimal_evaluation_config(suppressions=observed_suppressions)

        release_suppressions = SuppressionConfig(sha256="sha256:release", rules=())
        release_wide_config = _minimal_evaluation_config(
            suppressions=release_suppressions
        )

        diff = _result()
        diff.contract_context = self._persisted_context(observed)

        record_release_resolved_config(diff, release_wide_config)

        merged_config = diff.contract_context.evaluation_context.resolved_config
        assert merged_config.suppressions is release_suppressions

    def test_no_observed_suppressions_is_still_a_no_op(self) -> None:
        """Neither side has suppressions -- nothing to restore, and the
        merged config's suppressions must stay None rather than fabricating
        an empty SuppressionConfig."""
        from abicheck.cli_compare_receipt import record_release_resolved_config

        observed = _minimal_evaluation_config()
        assert observed.suppressions is None
        release_wide_config = _minimal_evaluation_config(
            contract=ContractConfig(
                mode=ContractMode.PUBLIC,
                packs=(_identity("ignore_removals", version=1),),
            )
        )

        diff = _result()
        diff.contract_context = self._persisted_context(observed)

        record_release_resolved_config(diff, release_wide_config)

        merged_config = diff.contract_context.evaluation_context.resolved_config
        assert merged_config.suppressions is None

    def test_evaluation_config_attribute_matches_the_merged_context(self) -> None:
        """Codex review, fresh evidence: an earlier revision stamped
        ``result.evaluation_config`` from the *pre-restoration* config
        (before the suppression-restore replace() below it ran), so a Python
        API consumer reading ``DiffResult.evaluation_config`` directly saw
        ``suppressions=None`` even on a release where suppressions genuinely
        applied -- while the very same result's ``contract_context`` carried
        the correctly-restored value. Two disagreeing "resolved" configs on
        one result. Pins that ``evaluation_config`` is stamped from the same,
        final (possibly suppression-restored) object as the merged context,
        never the pre-restoration one."""
        from abicheck.cli_compare_receipt import record_release_resolved_config
        from abicheck.compatibility_evaluation_config import SuppressionConfig

        observed_suppressions = SuppressionConfig(
            sha256="sha256:observed", rules=("cxx_standard_floor_raised",)
        )
        observed = _minimal_evaluation_config(suppressions=observed_suppressions)
        release_wide_config = _minimal_evaluation_config(
            contract=ContractConfig(
                mode=ContractMode.PUBLIC,
                packs=(_identity("ignore_removals", version=1),),
            )
        )
        assert release_wide_config.suppressions is None

        diff = _result()
        diff.contract_context = self._persisted_context(observed)

        record_release_resolved_config(diff, release_wide_config)

        merged_config = diff.contract_context.evaluation_context.resolved_config
        assert diff.evaluation_config is merged_config
        assert diff.evaluation_config.suppressions is observed_suppressions

    def test_observed_suppression_provenance_is_copied_when_present(self) -> None:
        """The restore copies the *observed* config's own provenance entry
        for the suppressions field, not just its value -- covers the branch
        where ``observed_config.provenance`` actually carries a
        ``SUPPRESSIONS_FIELD`` entry (every other test in this class builds
        an ``observed_config`` with the default empty provenance mapping, so
        this is the one exercising the copy rather than the pop-when-absent
        fallback)."""
        from abicheck.cli_compare_receipt import record_release_resolved_config
        from abicheck.compatibility_evaluation_config import SuppressionConfig

        observed_suppressions = SuppressionConfig(
            sha256="sha256:observed", rules=("cxx_standard_floor_raised",)
        )
        observed_suppression_provenance = ValueProvenance(
            layer=SelectorLayer.EXPLICIT_CLI,
            source_kind="suppression_file",
            sha256="sha256:observed",
            path="/tmp/observed-suppress.yml",
        )
        observed = _minimal_evaluation_config(
            suppressions=observed_suppressions,
            provenance={"suppressions": observed_suppression_provenance},
        )
        release_wide_config = _minimal_evaluation_config(
            contract=ContractConfig(
                mode=ContractMode.PUBLIC,
                packs=(_identity("ignore_removals", version=1),),
            )
        )
        assert release_wide_config.suppressions is None

        diff = _result()
        diff.contract_context = self._persisted_context(observed)

        record_release_resolved_config(diff, release_wide_config)

        merged_config = diff.contract_context.evaluation_context.resolved_config
        assert merged_config.suppressions is observed_suppressions
        assert merged_config.provenance["suppressions"] is observed_suppression_provenance
