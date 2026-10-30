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

"""What a single-pair ``compare`` session is asked to do, and what it answers.

:func:`abicheck.workflows.compare.session.run_pair_session` takes a
:class:`PairSessionRequest` and returns a :class:`PairSessionOutcome`. The
request is *resolved input*: every value a front end read from its own
syntax (sided flags, ``-o``, ``--view``) and from the project config's
run settings is already a plain value here; nothing in it is re-derived from
a Click context. The one trace of the command line it keeps is
:attr:`PairSessionRequest.stated_options`, the option names the invocation
stated explicitly -- ADR-049's receipt and the ``compile:`` fold both decide
by *who said so*, not only by value.

The session's side effects go through :class:`SessionHost`: user-facing notes
(stderr on the CLI) and the run's lifetime (environment toggles restored when
the invocation closes).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ...checker_types import DiffResult
    from ...model import AbiSnapshot
    from ..dump.project_config import DumpDebugConfig
    from ..extraction import DumpManifest

__all__ = [
    "PairCompileInputs",
    "PairEvidenceInputs",
    "PairHeaderInputs",
    "PairOperands",
    "PairPolicyInputs",
    "PairSessionOutcome",
    "PairSessionRequest",
    "ProjectContext",
    "SessionHost",
]


@dataclass(frozen=True)
class SessionHost:
    """The session's two side channels, supplied by the front end."""

    #: A user-facing note or warning (the CLI writes it to stderr).
    notify: Callable[[str], None]
    #: Registers a restoration to run when the invocation closes (the CLI
    #: passes ``ctx.call_on_close``).
    on_close: Callable[[Callable[[], None]], object]


@dataclass(frozen=True)
class PairOperands:
    """The two operands, as given (a linker script is followed by the session)."""

    old: Path
    new: Path
    old_version: str
    new_version: str


@dataclass(frozen=True)
class PairHeaderInputs:
    """Header and include roots: both-sides values plus each side's own."""

    headers: tuple[Path, ...] = ()
    old_headers: tuple[Path, ...] = ()
    new_headers: tuple[Path, ...] = ()
    includes: tuple[Path, ...] = ()
    old_includes: tuple[Path, ...] = ()
    new_includes: tuple[Path, ...] = ()
    #: The run's one canonical header-exclusion rule set (flag or config).
    exclude_headers: tuple[str, ...] = ()
    include_labels: dict[Path, str] | None = None
    #: A parsed ``--dump-manifest`` per side (ADR-050 D3).
    old_dump_manifest: DumpManifest | None = None
    new_dump_manifest: DumpManifest | None = None


@dataclass(frozen=True)
class PairCompileInputs:
    """The L2 compile-context inputs the ``compile:`` block is folded over."""

    lang: str
    lang_explicit: bool = False
    header_backend: str = "auto"
    old_header_backend: str | None = None
    new_header_backend: str | None = None
    sysroot: Path | None = None
    nostdinc: bool = False
    gcc_options: str | None = None
    compiler_path: str | None = None
    compiler_prefix: str | None = None
    compiler_option_tokens: tuple[str, ...] = ()
    defines: tuple[str, ...] = ()
    frontend_context: str = "host"
    #: ``compare --set`` overrides (ADR-091 D4) laid over ``.abicheck.yml``
    #: before the ``compile:`` block is folded.
    config_overrides: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class PairEvidenceInputs:
    """Evidence beyond the operands, and the inputs that scope collecting it."""

    debug: DumpDebugConfig
    old_debug_roots: tuple[Path, ...] = ()
    new_debug_roots: tuple[Path, ...] = ()
    depth: str | None = None
    old_sources: Path | None = None
    new_sources: Path | None = None
    old_build_info: Path | None = None
    new_build_info: Path | None = None
    probe_matrix_old: Path | None = None
    probe_matrix_new: Path | None = None
    #: ``--since``/``--changed-path`` (the changed-path seed, ADR-043 D7).
    since: str | None = None
    changed_paths: tuple[str, ...] = ()
    #: ``--abi3`` as typed; ``python.abi3_floor`` supplies the default.
    abi3: str | None = None
    #: A development package given for a side (only warned about here).
    old_devel_package: Path | None = None
    new_devel_package: Path | None = None
    follow_deps: bool = False
    search_paths: tuple[Path, ...] = ()
    ld_library_path: str = ""
    include_dependencies: bool = False


@dataclass(frozen=True)
class PairPolicyInputs:
    """The policy, suppression, contract and gate inputs."""

    policy: str
    policy_file_path: Path | None = None
    suppress: Path | tuple[Path, ...] | None = None
    severity_preset: str | None = None
    pack_paths: tuple[Path, ...] = ()
    #: Which option selected *policy*, and the file it named (ADR-049 receipt).
    policy_selected_by: str | None = None
    policy_selected_path: Path | None = None
    policy_selected_sha: str | None = None
    contract_mode: str | None = None
    #: ADR-049: the evaluator always runs since Phase 7 (the CLI's own answer,
    #: and ``CompareRequest``'s default).
    contract_evaluation: bool = True
    #: ``--acknowledgments``; ``acknowledgment.file`` is the config spelling.
    acknowledgments: Path | None = None
    diagnostic_comparison: bool = False
    require_same_generation: bool = False


@dataclass(frozen=True)
class ProjectContext:
    """The selected ``.abicheck.yml`` and the settings resolved from it."""

    #: ``--config`` as given; ``None`` unless the operator named one.
    config: Path | None
    #: The selected config: *config*, else the auto-discovered one.
    path: Path | None
    #: The loaded document (a ``BuildConfig``), ``None`` without a config.
    document: Any
    #: ``ResolvedCompareConfig``: CLI flags merged over the document.
    resolved: Any
    sha256: str | None = None

    @property
    def config_explicit(self) -> bool:
        """Whether the operator named the config (only then is it trusted)."""
        return self.config is not None


@dataclass(frozen=True)
class PairSessionRequest:
    """One resolved single-pair ``compare``."""

    operands: PairOperands
    headers: PairHeaderInputs
    compile: PairCompileInputs
    evidence: PairEvidenceInputs
    policy: PairPolicyInputs
    project: ProjectContext
    #: Option names the invocation stated explicitly (not defaulted).
    stated_options: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class PairSessionOutcome:
    """A classified pair, and the resolved inputs its report and gate read."""

    result: DiffResult
    old: AbiSnapshot
    new: AbiSnapshot
    #: The operands the pair was resolved from (a linker script followed).
    old_input: Path
    new_input: Path
    #: The ADR-049 evaluation configuration the result was classified under.
    evaluation_config: Any
    #: The policy document after the pack and project-override folds.
    policy_file: Any
    #: The resolved compare config after the evaluation-config fold.
    resolved_config: Any
    suppression: Any
    #: The resolved gate's severity map (``None`` under the legacy scheme).
    report_severity: Any
    #: Why the ``--abi3`` evidence contract failed, if it did (ADR-064 exit 7).
    abi3_failure: str | None = None
