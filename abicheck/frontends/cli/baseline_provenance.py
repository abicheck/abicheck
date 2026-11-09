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

"""``report.baseline_provenance``: read the file once, hold it for the run.

``compare`` reads the file named by the key before any analysis, so a missing
or malformed file is a usage error (exit 64), never a report without the
block. The record is then held for the rest of the Click context, the same
way the run's performance profile and deadline are, and read by the two
places that build a JSON report: the single-pair result render
(``compare_result._report_compare_result``) and the release render
(``release_report.render_release_summary``). A run with no key reads ``None``
and its report is byte-identical to one produced before the key existed.

Path resolution follows the ADR-091 D4 amendment's rule: a path stated with
``--set`` (or the Action's ``set:``) is relative to the current directory; one
written in ``.abicheck.yml`` is relative to that file's directory. A leading
``@`` is optional.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

import click

from ...model.baseline_provenance import (
    BASELINE_PROVENANCE_PATH,
    BaselineProvenance,
    BaselineProvenanceError,
)

__all__ = [
    "current_baseline_provenance_block",
    "enter_baseline_provenance",
    "load_baseline_provenance",
    "read_record_file",
    "record_base_dir",
]

_CURRENT: ContextVar[BaselineProvenance | None] = ContextVar(
    "abicheck_baseline_provenance", default=None
)


def read_record_file(key: str, value: str, *, base_dir: Path | None) -> object:
    """The JSON document the file *value* names; :class:`click.UsageError` if unusable.

    Shared by every ``report.*`` key that names a caller-asserted record file
    (``report.baseline_provenance``, ``report.delta_baseline``). *key* names
    the config path in the error. *base_dir* is the directory a relative path
    resolves against (``None``: the current directory).
    """
    name = value[1:] if value.startswith("@") else value
    if not name:
        raise click.UsageError(f"{key}: no file named")
    path = Path(name)
    if base_dir is not None and not path.is_absolute():
        path = base_dir / path
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise click.UsageError(
            f"{key}: cannot read {path}: {exc.strerror or exc}"
        ) from None
    except ValueError as exc:
        raise click.UsageError(f"{key}: {path} is not valid JSON ({exc})") from None


def record_base_dir(
    key: str, *, cfg_path: Path | None, config_overrides: Mapping[str, object] | None
) -> Path | None:
    """Where a relative record path resolves (the ADR-091 D4 amendment's rule)."""
    from_set = key in (config_overrides or {})
    return None if from_set or cfg_path is None else cfg_path.parent


def load_baseline_provenance(
    value: str, *, base_dir: Path | None
) -> BaselineProvenance:
    """The record in the file *value* names; :class:`click.UsageError` if unusable."""
    data = read_record_file(BASELINE_PROVENANCE_PATH, value, base_dir=base_dir)
    try:
        return BaselineProvenance.from_mapping(data)
    except BaselineProvenanceError as exc:
        raise click.UsageError(f"{BASELINE_PROVENANCE_PATH}: {value}: {exc}") from None


@contextmanager
def _provenance_scope(record: BaselineProvenance) -> Iterator[None]:
    token = _CURRENT.set(record)
    try:
        yield
    finally:
        _CURRENT.reset(token)


def enter_baseline_provenance(
    ctx: click.Context,
    *,
    cfg_path: Path | None,
    project_cfg: object,
    config_overrides: Mapping[str, object] | None,
) -> None:
    """Read the run's provenance file, if any, and hold it for the rest of *ctx*."""
    value = getattr(project_cfg, "report_baseline_provenance", None)
    if not value:
        return
    base_dir = record_base_dir(
        BASELINE_PROVENANCE_PATH, cfg_path=cfg_path, config_overrides=config_overrides
    )
    record = load_baseline_provenance(str(value), base_dir=base_dir)
    ctx.with_resource(_provenance_scope(record))


def current_baseline_provenance_block() -> dict[str, object] | None:
    """The report block for this run's provenance, or ``None`` when none was given."""
    record = _CURRENT.get()
    return None if record is None else record.to_report_dict()
