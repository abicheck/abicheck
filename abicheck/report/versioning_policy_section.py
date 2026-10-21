# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""The stated versioning policy's acceptance verdict, as a report fact.

Compute half only: :func:`compute_versioning_policy` resolves the verdict
once, and a renderer reads the resulting mapping. It is the same acceptance
the JSON ``release_recommendation.policy_acceptance`` carries, and it reads
only the run's verdict, never the finding set.
"""

from __future__ import annotations

from ..policy.versioning_policy import (
    evaluate_release_acceptance,
    stated_versioning_policy_of,
)


def compute_versioning_policy(result: object) -> dict[str, object] | None:
    """The stated versioning policy's verdict on this release, or ``None``.

    ``None`` when the run's policy file states no ``versioning:`` block (a
    built-in default is not a statement, so the verdict stays "not
    evaluated" rather than reporting a default the project never chose), or
    when *result* carries no verdict to accept.
    """
    policy = stated_versioning_policy_of(getattr(result, "policy_file", None))
    if policy is None or not hasattr(result, "verdict"):
        return None
    return evaluate_release_acceptance(result, policy).to_dict()  # type: ignore[arg-type]
