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

"""`compare`'s default format must reach every operand shape it accepts.

Bug class ``cli.default_format_unreachable_operand``
(``tests/regressions/manifest_report.py``).

Several of `compare`'s operand shapes render a *restricted* set of formats:
a directory/package release fan-out produces json/markdown/junit/oneline,
and a stored-bundle-facts comparison produces json/markdown. Each checks the
**resolved** format, not the requested one. So a change to the command's
default format is not a presentation change for those paths -- it decides
whether they run at all, and a default outside a path's set turns every
invocation of it into a usage error (exit 64) before any comparison happens.

That is exactly what a default flip to the bounded `terminal` projection
did: `compare OLD_DIR NEW_DIR` and every stored-pair invocation exited 64.
Each restricted set lived beside its own check, so each broke independently
and nothing tied them to the command's actual default.

These tests state the property the individual fixes only satisfy one path at
a time: **whatever `compare`'s default format is, every operand shape must
still run under it.** A future default change either keeps that true or
fails here, naming the shape it stranded -- rather than being discovered one
operand at a time.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.cli import main

_EXIT_USAGE_ERROR = 64


def _compare_default_format() -> str:
    """`compare`'s own default export format, read off the live command.

    There is no `--format` Click parameter to read a default from: the
    format is part of the repeatable `-o FORMAT=DESTINATION` export set, and
    its default is captured in that option's callback closure
    (`frontends.cli.options.export.export_options`). The same value is
    stated in the option's generated help ("Default: <fmt>=-."), which is
    what this reads.

    Read from the live command rather than restated here on purpose: a test
    that hard-codes the default cannot notice the default changing, which is
    the event this whole module exists to survive.
    """
    for param in main.commands["compare"].params:
        if param.name == "exports":
            match = re.search(r"Default: (\S+)=-\.", param.help or "")
            assert match is not None, (
                "compare's -o/--output help no longer states its default "
                "format; this module can no longer read it"
            )
            return match.group(1)
    raise AssertionError("compare has no -o/--output export parameter")


def _elf_stub(path: Path) -> Path:
    """A file that classifies as a single-library operand."""
    path.write_bytes(b"\x7fELF" + b"\x00" * 60)
    return path


#: The marker that makes an operand classify as a stored BundleFacts
#: document. Taken from the producer's own artifact contract rather than
#: invented here -- a stub the dispatcher does not recognize would make
#: every stored-pair case below pass vacuously against the *live* dispatch
#: (it did, in the first version of this module).
_STUB_BUNDLE_FACTS_JSON = (
    '{"artifact_type": "abicheck.bundle-facts", "schema_version": 2, '
    '"per_library_snapshots": {}}'
)


def _stored_bundle_facts(path: Path) -> Path:
    path.write_text(_STUB_BUNDLE_FACTS_JSON)
    return path


def _single_pair(tmp_path: Path) -> list[str]:
    old = _elf_stub(tmp_path / "old.so")
    new = _elf_stub(tmp_path / "new.so")
    return [str(old), str(new)]


def _directory_pair(tmp_path: Path) -> list[str]:
    old_dir = tmp_path / "old_dir"
    new_dir = tmp_path / "new_dir"
    old_dir.mkdir()
    new_dir.mkdir()
    _elf_stub(old_dir / "libfoo.so")
    _elf_stub(new_dir / "libfoo.so")
    return [str(old_dir), str(new_dir)]


def _stored_pair(tmp_path: Path) -> list[str]:
    return [
        str(_stored_bundle_facts(tmp_path / "old.bundlefacts.json")),
        str(_stored_bundle_facts(tmp_path / "new.bundlefacts.json")),
    ]


#: Every operand shape `compare` dispatches differently on. Each name is the
#: shape a reviewer would recognize; each builder produces the minimal
#: operands that reach that dispatch.
OPERAND_SHAPES = {
    "single_pair": _single_pair,
    "directory_pair": _directory_pair,
    "stored_bundle_facts_pair": _stored_pair,
}


@pytest.mark.parametrize("shape", sorted(OPERAND_SHAPES))
def test_the_default_format_is_never_a_usage_error_for_any_operand_shape(
    shape: str, tmp_path: Path
) -> None:
    """The invariant: the default format must not strand an operand shape.

    Asserted on the exit code and the message, not on a successful
    comparison -- these are stub operands, so the run is expected to fail
    for its own analysis reasons. What it must never do is reject the
    *format* the user never chose.
    """
    args = OPERAND_SHAPES[shape](tmp_path)
    result = CliRunner().invoke(main, ["compare", *args])
    fmt = _compare_default_format()
    assert result.exit_code != _EXIT_USAGE_ERROR or "is not available" not in (
        result.output
    ), (
        f"compare's default format {fmt!r} is rejected by the {shape!r} "
        f"operand path as a usage error, so no comparison can run:\n"
        f"{result.output}"
    )


@pytest.mark.parametrize("shape", sorted(OPERAND_SHAPES))
def test_a_directory_only_export_does_not_strand_the_inserted_summary_target(
    shape: str, tmp_path: Path
) -> None:
    """The axis a set-level `explicit` test cannot reach.

    `-o json=out/` is a *directory* export, so `build_export_set` inserts an
    extra document target of its own to carry the run's summary -- one the
    user never typed, still holding the command default. The export set now
    reports `explicit=True`, so a fallback keyed on that flag skips the whole
    set and leaves the inserted target on a format the path cannot render.
    The run then fails as a usage error despite every format the user
    actually named being supported.
    """
    args = OPERAND_SHAPES[shape](tmp_path)
    out_dir = tmp_path / "reports"
    out_dir.mkdir()
    result = CliRunner().invoke(main, ["compare", *args, "-o", f"json={out_dir}/"])
    assert result.exit_code != _EXIT_USAGE_ERROR or "is not available" not in (
        result.output
    ), (
        f"a directory-only export stranded the inserted summary target on "
        f"compare's default format {_compare_default_format()!r} for the "
        f"{shape!r} operand path:\n{result.output}"
    )


@pytest.mark.parametrize("shape", sorted(OPERAND_SHAPES))
def test_an_explicitly_requested_unsupported_format_is_still_a_usage_error(
    shape: str, tmp_path: Path
) -> None:
    """The fallback must not become a silent accept-anything.

    The fix for the above is to fall an *unrequested* default back into the
    path's own set. A fallback that also swallowed an explicit request would
    render a different format than the one asked for -- the silent-wrong-
    artifact failure the restricted-format checks exist to prevent. `sarif`
    is unsupported by both restricted paths and supported by the single-pair
    one, so this also pins that the single pair keeps accepting it.
    """
    args = OPERAND_SHAPES[shape](tmp_path)
    result = CliRunner().invoke(
        main, ["compare", *args, "-o", f"sarif={tmp_path}/out.sarif"]
    )
    if shape == "single_pair":
        assert "is not available" not in result.output
        return
    assert result.exit_code == _EXIT_USAGE_ERROR, result.output
    assert "is not available" in result.output


def test_the_operand_shapes_really_dispatch_differently(tmp_path: Path) -> None:
    """Guard the guard.

    If every shape above collapsed to one dispatch path, the parametrization
    would look thorough while testing one thing. Each restricted path is
    identified by the distinct refusal it gives for an explicitly requested
    unsupported format.
    """
    runner = CliRunner()
    messages = set()
    for shape, build in OPERAND_SHAPES.items():
        if shape == "single_pair":
            continue
        sub = tmp_path / shape
        sub.mkdir()
        args = build(sub)
        result = runner.invoke(main, ["compare", *args, "-o", f"sarif={sub}/out.sarif"])
        messages.add(result.output.split("Error:")[-1].strip()[:80])
    assert len(messages) >= 2, (
        "the restricted operand paths gave indistinguishable refusals, so "
        f"they may not be dispatching separately: {messages}"
    )
