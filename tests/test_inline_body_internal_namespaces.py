# SPDX-License-Identifier: Apache-2.0
"""The inline-accessor rename detector reads the run's internal namespaces.

Bug class: a setting the run resolves and one consumer never receives. The
post-processing step called ``detect_inline_body_renamed_member`` without the
policy file's ``internal_namespaces``, so the detector used its own
three-name default (``detail``/``impl``/``internal``): a project declaring
``internal_namespaces: [priv]`` never got
``inline_body_references_renamed_member``, and neither did ``__detail::`` or
``_impl::`` with no configuration, although the shared default every other
internal-namespace check reads names both.

Oracles, independent of the detector:

* the documented convention, written out here: the configured list when the
  policy file states one, else the five default names;
* agreement with ``internal_type_leaks_via_public_api``, the sibling
  detector that already reads the run's convention and fires on the same
  fixture exactly when its pimpl type is internal.
"""

from __future__ import annotations

import itertools

import pytest

from abicheck.checker import compare
from abicheck.checker_policy import ChangeKind
from abicheck.model import AbiSnapshot, Function, RecordType, TypeField, Visibility
from abicheck.policy_file import PolicyFile

_DOCUMENTED_DEFAULT = ("detail", "impl", "internal", "__detail", "_impl")
_SEGMENTS = ("detail", "impl", "internal", "__detail", "_impl", "priv", "core")
_CONFIGURED = (None, ("priv",), ("detail",), ("priv", "_impl"))


def _pair(segment: str) -> tuple[AbiSnapshot, AbiSnapshot]:
    impl = f"mylib::{segment}::descriptor_impl"

    def snap(field: str) -> AbiSnapshot:
        getter = Function(
            name="mylib::descriptor::get_class_count",
            mangled="_ZNK5mylib10descriptor15get_class_countEv",
            return_type="int",
            visibility=Visibility.PUBLIC,
            is_inline=True,
        )
        holder = RecordType(
            name="mylib::descriptor",
            kind="class",
            size_bits=128,
            fields=[TypeField(name="impl_", type=f"std::shared_ptr<{impl}>")],
        )
        hidden = RecordType(
            name=impl,
            kind="class",
            size_bits=64,
            fields=[TypeField(name=field, type="int")],
        )
        return AbiSnapshot(
            library="lib", version="1.0", functions=[getter], types=[hidden, holder]
        )

    return snap("class_count_"), snap("n_classes_")


@pytest.mark.parametrize(
    ("segment", "configured"), list(itertools.product(_SEGMENTS, _CONFIGURED))
)
def test_the_detector_follows_the_run_s_convention(
    segment: str, configured: tuple[str, ...] | None
) -> None:
    policy_file = (
        PolicyFile(
            internal_namespaces=list(configured), internal_namespaces_stated=True
        )
        if configured is not None
        else None
    )
    old, new = _pair(segment)
    kinds = {c.kind for c in compare(old, new, policy_file=policy_file).changes}

    internal = segment in (configured or _DOCUMENTED_DEFAULT)
    fired = ChangeKind.INLINE_BODY_REFERENCES_RENAMED_MEMBER in kinds
    assert fired is internal
    assert fired is (ChangeKind.INTERNAL_TYPE_LEAKS_VIA_PUBLIC_API in kinds)


def test_the_oracle_covers_both_outcomes_and_a_configured_only_name() -> None:
    outcomes = {
        segment in (configured or _DOCUMENTED_DEFAULT)
        for segment, configured in itertools.product(_SEGMENTS, _CONFIGURED)
    }
    assert outcomes == {True, False}
    assert "priv" not in _DOCUMENTED_DEFAULT
