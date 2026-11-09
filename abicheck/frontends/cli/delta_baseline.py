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

"""``report.delta_baseline``: read the file once, hold it for the run.

The same lifecycle as ``report.baseline_provenance``
(:mod:`.baseline_provenance`): ``compare`` reads the file before any analysis,
so a missing or malformed file is a usage error (exit 64), never a report
without the block; the record is held for the rest of the Click context and
read by the single-pair and the release JSON renders. A run with no key reads
``None`` and its report is byte-identical to one produced before the key
existed.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

import click

from ...report.delta_baseline import (
    DELTA_BASELINE_PATH,
    DeltaBaseline,
    DeltaBaselineError,
)
from .baseline_provenance import read_record_file, record_base_dir

__all__ = [
    "current_delta_baseline_block",
    "enter_delta_baseline",
    "load_delta_baseline",
]

_CURRENT: ContextVar[DeltaBaseline | None] = ContextVar(
    "abicheck_delta_baseline", default=None
)


def load_delta_baseline(value: str, *, base_dir: Path | None) -> DeltaBaseline:
    """The record in the file *value* names; :class:`click.UsageError` if unusable."""
    data = read_record_file(DELTA_BASELINE_PATH, value, base_dir=base_dir)
    try:
        return DeltaBaseline.from_mapping(data)
    except DeltaBaselineError as exc:
        raise click.UsageError(f"{DELTA_BASELINE_PATH}: {value}: {exc}") from None


@contextmanager
def _delta_scope(record: DeltaBaseline) -> Iterator[None]:
    token = _CURRENT.set(record)
    try:
        yield
    finally:
        _CURRENT.reset(token)


def enter_delta_baseline(
    ctx: click.Context,
    *,
    cfg_path: Path | None,
    project_cfg: object,
    config_overrides: Mapping[str, object] | None,
) -> None:
    """Read the run's delta-baseline record, if any, and hold it for *ctx*."""
    value = getattr(project_cfg, "report_delta_baseline", None)
    if not value:
        return
    base_dir = record_base_dir(
        DELTA_BASELINE_PATH, cfg_path=cfg_path, config_overrides=config_overrides
    )
    ctx.with_resource(_delta_scope(load_delta_baseline(str(value), base_dir=base_dir)))


def current_delta_baseline_block() -> dict[str, object] | None:
    """The report block for this run's record, or ``None`` when none was given."""
    record = _CURRENT.get()
    return None if record is None else record.to_report_dict()
