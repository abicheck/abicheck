"""Build and run a single-pair ``compare`` session request in a test.

``workflows.compare.session.run_pair_session`` is the interface the native
``compare`` CLI drives; a test of what a pair resolves or classifies to
states the request here and reads the outcome, instead of patching the CLI's
own helpers. Every field defaults to what a bare ``compare OLD NEW`` with no
project config resolves to.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from abicheck.workflows.compare.session import run_pair_session
from abicheck.workflows.compare.session_request import (
    PairCompileInputs,
    PairEvidenceInputs,
    PairHeaderInputs,
    PairOperands,
    PairPolicyInputs,
    PairSessionOutcome,
    PairSessionRequest,
    ProjectContext,
    SessionHost,
)
from abicheck.workflows.compare_configuration import load_and_resolve_compare_config
from abicheck.workflows.dump.project_config import DumpDebugConfig


def pair_session_request(
    old: Path,
    new: Path,
    *,
    old_version: str = "old",
    new_version: str = "new",
    config: Path | None = None,
    stated: Iterable[str] = (),
    headers: dict[str, Any] | None = None,
    compile: dict[str, Any] | None = None,
    evidence: dict[str, Any] | None = None,
    debug: dict[str, Any] | None = None,
    policy: dict[str, Any] | None = None,
) -> PairSessionRequest:
    """A request for *old* vs *new*; each dict overrides that group's fields.

    *config* is both the named ``--config`` and the selected config (no
    discovery), so a test stays independent of the working directory.
    """
    project_cfg, resolved, sha = load_and_resolve_compare_config(
        config, severity_preset=None
    )
    return PairSessionRequest(
        operands=PairOperands(old, new, old_version, new_version),
        headers=PairHeaderInputs(**(headers or {})),
        compile=PairCompileInputs(**{"lang": "c++", **(compile or {})}),
        evidence=PairEvidenceInputs(
            debug=DumpDebugConfig(**(debug or {})), **(evidence or {})
        ),
        policy=PairPolicyInputs(**{"policy": "strict_abi", **(policy or {})}),
        project=ProjectContext(config, config, project_cfg, resolved, sha),
        stated_options=frozenset(stated),
    )


@dataclasses.dataclass
class SessionRun:
    """What one :func:`run_session` call returned and said."""

    outcome: PairSessionOutcome
    notes: list[str]
    closers: list[Any]


def run_session(request: PairSessionRequest) -> SessionRun:
    """Run *request*, collecting its notes and its close-time restorations."""
    notes: list[str] = []
    closers: list[Any] = []
    outcome = run_pair_session(request, SessionHost(notes.append, closers.append))
    return SessionRun(outcome, notes, closers)
