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

"""What a ``compare`` run takes from the project config beyond its settings.

Two inputs a single-pair ``compare`` session
(``workflows/compare/session.py``) resolves from the selected ``.abicheck.yml``
under one trust rule -- a value that can narrow what gates or loosen the exit
code applies only from an operator-named ``--config`` -- and that the
directory/package fan-out and ``--no-baseline`` routes consult too. (The
project's ownership rules are ``workflows/ownership_request.py``'s.) Notes go to the caller's
*notify* sink; a refusal is a
:class:`~abicheck.workflows.run_input_errors.RunInputError`.

POST-manifest contract overlay
------------------------------

``compare``'s POST-manifest contract overlay, from project config.

``.abicheck.yml``'s ``contract.overlays.post_manifest`` (one-comparison-
product Phase 9c; ``buildsource/build_config_contract.py``) is the only
spelling: Phase 9d deleted ``--post-manifest``, which exits 64. This module
is the one place a ``compare`` route decides what to do with it:

- a single-pair ``compare`` applies it, a relative path resolved against the
  project root (``config_paths.project_root_for_config``) -- **only from an
  explicitly named** ``--config``. The overlay narrows what gates (an
  uncommitted export's change leaves the verdict), so an auto-discovered
  ``.abicheck.yml`` -- which a pull request can add or edit in the very
  checkout it is being judged on -- is not trusted to set it: the same trust
  boundary ``build.query``/``compile.compiler`` already sit behind, and the
  one the retired ``--post-manifest`` flag had by construction (only the
  operator typed it). A discovered value is noted on stderr, not applied;
  the composite Action strips it from a discovered config for the same
  reason (``action_config_overlay.strip_untrusted_execution_keys``);
- a route that cannot apply an overlay (the directory/package release
  fan-out, the ``--no-baseline`` audit) states on stderr that it was not
  applied. It is a property of the project, not of this invocation, so it is
  not a usage error there, and an unapplied narrowing overlay can only add
  findings, never hide one.

Acknowledgments (ADR-067 D5/D6)
-------------------------------

``compare``'s ADR-067 D5/D6 inputs: acknowledgment records, the
``breaks`` policy, and the additions-review gate.

Sources, in the order they apply:

- ``--acknowledgments FILE`` names the records for this run. It is an
  operator's own explicit input, so it is trusted, and it overrides
  ``.abicheck.yml``'s ``acknowledgment.file``.
- ``.abicheck.yml``'s ``acknowledgment.file`` applies **only from an
  explicitly named** ``--config``. A record accepts an addition or a break,
  which can lower a gating exit back to ``0``; an auto-discovered
  ``.abicheck.yml`` is one the pull request under review can edit, so it is
  not trusted to supply records. A discovered value is noted on stderr and
  not loaded -- the trust boundary the overlay section above states.
- ``.abicheck.yml``'s ``acknowledgment.breaks`` (``gate``/``accept``) has the
  same trust rule as the records: ``accept`` loosens the exit code, so only an
  explicit ``--config`` may set it.
- ``acknowledgment.unacknowledged_additions`` applies from any config,
  auto-discovered included: ``warn``/``block`` can only add a report section
  or raise a clean exit to ``1``, so a pull request setting it cannot hide
  anything.
- The directory/package fan-out forwards the same values to every member's
  ``CompareRequest`` (:func:`release_acknowledgment_inputs`).

A relative ``file`` resolves against the project root.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from ...errors import ValidationError
from ..acknowledgment_inputs import (
    CompareAcknowledgments,
    resolve_compare_acknowledgments,
)
from ..run_input_errors import RunInputError

if TYPE_CHECKING:
    from ...model import AbiSnapshot

__all__ = [
    "ACKNOWLEDGMENT_CONFIG_KEY",
    "POST_MANIFEST_CONFIG_KEY",
    "compare_acknowledgments_for",
    "note_unapplied_post_manifest",
    "post_manifest_allowlist_for",
    "release_acknowledgment_inputs",
    "resolve_post_manifest_path",
]

Notify = Callable[[str], None]

POST_MANIFEST_CONFIG_KEY = "contract.overlays.post_manifest"
ACKNOWLEDGMENT_CONFIG_KEY = "acknowledgments"


# ── POST-manifest contract overlay ──────────────────────────────────────────


def resolve_post_manifest_path(
    project_cfg: object, cfg_path: Path | None
) -> Path | None:
    """The configured manifest, project-root-relative paths resolved."""
    raw = getattr(project_cfg, "contract_post_manifest", None)
    if not raw:
        return None
    path = Path(raw)
    if path.is_absolute() or cfg_path is None:
        return path
    from ...config_paths import project_root_for_config

    return project_root_for_config(cfg_path) / path


def note_unapplied_post_manifest(
    project_cfg: object, *, route: str, reason: str, notify: Notify
) -> None:
    """For a *route* that applies no overlay: a stderr note when one is set."""
    if getattr(project_cfg, "contract_post_manifest", None):
        notify(
            f"Note: .abicheck.yml's {POST_MANIFEST_CONFIG_KEY} is not applied on {route} "
            f"({reason}); it applies to a single-pair compare."
        )


def post_manifest_allowlist_for(
    project_cfg: object,
    cfg_path: Path | None,
    old: AbiSnapshot,
    new: AbiSnapshot,
    *,
    config_explicit: bool,
    notify: Notify,
) -> set[str] | None:
    """The committed public surface of the configured overlay, or ``None``.

    The manifest *is* the authoritative public surface, so this drives
    FilterNonPublicSurface directly (no header provenance needed) -- private
    ``__pp_*`` kernel churn is demoted. Union with the binaries' committed
    (``pp_*``) exports so a *removed* wrapper -- absent from a new manifest --
    stays in-surface instead of being silently demoted. A document that does
    not load is exit 64 naming the config key.

    *config_explicit* is whether the operator named the config with
    ``--config``; a discovered config's overlay is noted and not applied
    (see the module docstring).
    """
    path = resolve_post_manifest_path(project_cfg, cfg_path)
    if path is None:
        return None
    if not config_explicit:
        notify(
            f"Note: the auto-discovered {cfg_path}'s {POST_MANIFEST_CONFIG_KEY} is not "
            "applied: a discovered config is not trusted to narrow the "
            "compared contract. Name the config with --config to apply it."
        )
        return None
    from ...post_manifest import contract_scope_allowlist, load_manifest

    try:
        manifest = load_manifest(path)
    except (ValueError, OSError) as exc:
        raise RunInputError.usage(f"{POST_MANIFEST_CONFIG_KEY} {path}: {exc}") from exc
    return contract_scope_allowlist(manifest, old, new)


# ── Acknowledgments ─────────────────────────────────────────────────────────


def _records_path(
    project_cfg: object,
    cfg_path: Path | None,
    *,
    override_path: Path | None,
    config_explicit: bool,
    notify: Notify,
) -> Path | None:
    """The records file this run loads, or ``None``.

    ``--acknowledgments`` wins outright. Otherwise the config key applies only
    from an explicit ``--config``; a discovered value is noted and dropped.
    """
    if override_path is not None:
        return override_path
    raw = getattr(getattr(project_cfg, "acknowledgment", None), "file", None)
    if not raw:
        return None
    if not config_explicit:
        _note_untrusted_config_key(cfg_path, "file", notify)
        return None
    path = Path(raw)
    if path.is_absolute() or cfg_path is None:
        return path
    from ...config_paths import project_root_for_config

    return project_root_for_config(cfg_path) / path


def _configured_breaks(
    project_cfg: object, cfg_path: Path | None, *, config_explicit: bool, notify: Notify
) -> str | None:
    """``acknowledgment.breaks`` when the config is explicit, else ``None``."""
    breaks = getattr(getattr(project_cfg, "acknowledgment", None), "breaks", None)
    if breaks is None:
        return None
    if not config_explicit:
        _note_untrusted_config_key(cfg_path, "breaks", notify)
        return None
    return str(breaks)


def compare_acknowledgments_for(
    project_cfg: object,
    cfg_path: Path | None,
    policy_file: object,
    *,
    config_explicit: bool,
    override_path: Path | None = None,
    notify: Notify,
) -> CompareAcknowledgments:
    """The single-pair ``compare``'s records and gate policy.

    A records file that does not load is exit 64 naming the config key (or
    the flag). Review warnings the records owe are echoed to stderr.
    """
    ack_cfg = getattr(project_cfg, "acknowledgment", None)
    records_path = _records_path(
        project_cfg,
        cfg_path,
        override_path=override_path,
        config_explicit=config_explicit,
        notify=notify,
    )
    try:
        acks = resolve_compare_acknowledgments(
            records_path,
            policy_file=policy_file,
            configured_action=getattr(ack_cfg, "unacknowledged_additions", None),
            configured_breaks=_configured_breaks(
                project_cfg, cfg_path, config_explicit=config_explicit, notify=notify
            ),
        )
    except ValidationError as exc:
        raise RunInputError.usage(f"{_source_label(override_path)}: {exc}") from exc
    for warning in acks.warnings:
        notify(f"Warning: {warning}")
    return acks


def release_acknowledgment_inputs(
    project_cfg: object,
    cfg_path: Path | None,
    *,
    config_explicit: bool,
    override_path: Path | None = None,
    notify: Notify,
) -> dict[str, object]:
    """The directory/package fan-out's ``CompareRequest`` fields, under the
    same trust rules as :func:`compare_acknowledgments_for`; each member's
    ``run_compare`` resolves them (and a ``--policy`` block's precedence) the
    way a single-pair compare does."""
    records_path = _records_path(
        project_cfg,
        cfg_path,
        override_path=override_path,
        config_explicit=config_explicit,
        notify=notify,
    )
    if records_path is not None and not records_path.is_file():
        raise RunInputError.usage(
            f"{_source_label(override_path)}: no such file: {records_path}"
        )
    ack_cfg = getattr(project_cfg, "acknowledgment", None)
    return {
        "acknowledgments_path": records_path,
        "acknowledgment_unacknowledged_additions": getattr(
            ack_cfg, "unacknowledged_additions", None
        ),
        "acknowledgment_breaks": _configured_breaks(
            project_cfg, cfg_path, config_explicit=config_explicit, notify=notify
        ),
    }


def _source_label(override_path: Path | None) -> str:
    """The name a usage error gives the input it is about: the flag, or the key."""
    return (
        "--acknowledgments"
        if override_path is not None
        else f"{ACKNOWLEDGMENT_CONFIG_KEY}.file"
    )


def _note_untrusted_config_key(cfg_path: Path | None, key: str, notify: Notify) -> None:
    notify(
        f"Note: the auto-discovered {cfg_path}'s {ACKNOWLEDGMENT_CONFIG_KEY}.{key} is not "
        "loaded: a discovered config is not trusted to accept findings or "
        "loosen the gate. Name the config with --config to apply it, or pass "
        "--acknowledgments for the records."
    )
