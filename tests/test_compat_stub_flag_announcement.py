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

"""Every ABICC flag ``compat`` accepts without acting on must say so.

``abicheck compat`` is a drop-in replacement for abi-compliance-checker, so
it deliberately *accepts* ABICC options it does not implement rather than
dying on an unknown option -- deleting them would break the drop-in promise
for every existing ABICC command line. The price of that leniency is the
failure mode this module exists to prevent: a flag that silently changes
nothing turns a request for a narrower or stricter analysis into a clean
result the caller never asked for. Accepting it is fine; accepting it
*quietly* is not.

Two announcement mechanisms already exist and are both legitimate:
``_P2_STUB_FLAGS`` (a flag with no effect at all -> ``Warning: ...``) and
``_emit_compat_info_notes`` (a flag accepted with limited or informational
effect, whose value is worth echoing back -> ``Note: ...``). What did not
exist is anything holding the *set* of accepted-but-inert options to either
one: both registries are hand-maintained, and a newly added hidden stub
that reached neither would be silently ignored with nothing failing.

So this module enumerates the real Click parameters of the real commands
and, for each hidden one, **runs the real command** and checks the flag
names itself in the output. Deriving the set from the commands rather than
restating it here is the point -- a hand-listed set would be a third
registry to drift against the two it is meant to police.

Every case goes through `compat_group`, including the informational ones.
An earlier revision called `_emit_compat_info_notes` directly for those,
reasoning that a real run reaches it only after loading both sides; that is
wrong (it runs *before* input loading, which is why a `compat check` with
nonexistent operands still prints the note first), and the shortcut was
unsound as well as unnecessary -- rewiring the command to pass
`count_symbols=None` silenced the CLI while all 32 tests stayed green
(Codex review). What the announcement helper does in isolation is not the
claim; what the command a user types does is.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.compat._helpers import _P2_STUB_FLAGS
from abicheck.compat.cli import compat_group


def _combined(result) -> str:
    """Both streams. The `Warning:` announcements go to stdout and the
    `Note:` ones to stderr; a stdout-only read observed an empty string for
    a flag that is in fact announced, which would have read as a failure of
    the product rather than of the capture.
    """
    try:
        stderr = result.stderr
    except (ValueError, AttributeError):  # click versions that merge them
        stderr = ""
    return result.output + (stderr or "")


def _hidden_params(command_name: str) -> list:
    command = compat_group.commands[command_name]
    return [param for param in command.params if getattr(param, "hidden", False)]


def _all_hidden() -> list[tuple[str, object]]:
    return [
        (command_name, param)
        for command_name in sorted(compat_group.commands)
        for param in _hidden_params(command_name)
    ]


ALL_HIDDEN = _all_hidden()

#: The longest spelling of each hidden option, e.g. ``-count-all-symbols``.
#: Announcements name the option the user typed, not the Python destination.
_IDS = [f"{command}:{param.name}" for command, param in ALL_HIDDEN]


def _sample_value(param) -> object:
    """A value that counts as "the user set this flag"."""
    return True if param.is_flag else "probe"


def _minimal_invocation(command_name: str, tmp_path: Path) -> list[str]:
    """The required options each command needs before Click will hand
    control to the command body at all.

    Click rejects a missing required option (and a ``-dump`` descriptor that
    does not exist) during parameter processing, i.e. *before* the body's
    stub-flag warning runs -- so probing a stub flag without these would
    only ever observe a usage error, and the test would pass or fail for
    reasons unrelated to the announcement. The referenced files stay
    deliberately unusable past that point: the run still fails, just later,
    which is the whole reason the warning has to come first.
    """
    if command_name == "dump":
        descriptor = tmp_path / "descriptor.xml"
        descriptor.write_text("<descriptor/>\n", encoding="utf-8")
        return ["-lib", "probe", "-dump", str(descriptor)]
    if command_name == "check":
        return [
            "-lib",
            "probe",
            "-old",
            str(tmp_path / "old.json"),
            "-new",
            str(tmp_path / "new.json"),
        ]
    raise AssertionError(
        f"`compat {command_name}` is new to this module; give it a minimal "
        f"invocation so its hidden options can be probed too."
    )


def test_the_commands_actually_declare_hidden_stub_options() -> None:
    """Vacuity guard: every test below is parametrized over the discovered
    hidden options, so an empty (or mis-discovered) set would make them all
    pass while checking nothing -- the failure AGENTS.md's matrix-test rule
    names, and the reason the discovery itself is asserted first.
    """
    assert len(ALL_HIDDEN) >= 20, (
        f"only {len(ALL_HIDDEN)} hidden compat options discovered -- either "
        f"the ABICC-compat stub surface shrank drastically (a product change) "
        f"or this module's discovery no longer matches how they are declared."
    )


@pytest.mark.parametrize(("command_name", "param"), ALL_HIDDEN, ids=_IDS)
def test_every_hidden_stub_option_is_announced_when_set(
    command_name: str, param, tmp_path: Path
) -> None:
    """Setting an accepted-but-inert option must produce output naming it.

    Executed against the real announcement code -- the registries are the
    implementation, not the oracle, so a flag present in a registry whose
    announcement never fires still fails here.
    """
    # An announcement names whichever spelling its author chose (`-static`
    # for the option also spelled `-static-libs`), so any of the declared
    # ones counts -- the claim is that the user is told, not which alias the
    # message picks.
    spellings = list(param.opts)
    spelling = max(spellings, key=len)

    result = CliRunner().invoke(
        compat_group,
        [
            command_name,
            *_minimal_invocation(command_name, tmp_path),
            spelling,
            *([] if param.is_flag else ["probe"]),
        ],
        catch_exceptions=False,
    )
    output = _combined(result)
    assert "no such option" not in output.lower(), (
        f"`compat {command_name}` rejects {spelling}, which it declares -- an "
        f"ABICC command line using it now dies instead of running.\n{output}"
    )

    assert any(name in output for name in spellings), (
        f"`compat {command_name} {spelling}` produced no message naming it "
        f"({output!r}). An accepted option that changes nothing and says "
        f"nothing reports a narrower analysis as the requested one."
    )


@pytest.mark.parametrize("command_name", sorted(compat_group.commands))
def test_no_stub_announcement_fires_when_nothing_is_set(
    command_name: str, tmp_path: Path
) -> None:
    """Negative control. Without it, a command that unconditionally printed
    every stub message would satisfy the test above while telling the user
    nothing about their actual invocation.
    """
    runner = CliRunner()
    result = runner.invoke(
        compat_group,
        [command_name, *_minimal_invocation(command_name, tmp_path)],
        catch_exceptions=False,
    )
    for param in _hidden_params(command_name):
        if param.name not in _P2_STUB_FLAGS:
            continue
        spelling = max(param.opts, key=len)
        assert spelling not in _combined(result), (
            f"`compat {command_name}` announces {spelling} on a run that never "
            f"set it: {result.output!r}"
        )


def test_quiet_suppresses_the_stub_warnings_but_not_the_acceptance(
    tmp_path: Path,
) -> None:
    """`-quiet` is ABICC's own flag and may silence the notice, but must not
    change whether the option is accepted -- otherwise the drop-in promise
    would depend on verbosity.
    """
    runner = CliRunner()
    base = _minimal_invocation("check", tmp_path)
    loud = runner.invoke(
        compat_group, ["check", *base, "-quick"], catch_exceptions=False
    )
    quiet = runner.invoke(
        compat_group, ["check", *base, "-quiet", "-quick"], catch_exceptions=False
    )
    assert "-quick" in loud.output
    assert "-quick: quick analysis" not in quiet.output
    # Neither run may fail *because of* the stub flag: both get as far as the
    # same missing-input error, so acceptance is verbosity-independent.
    assert "no such option" not in (loud.output + quiet.output).lower()


def test_every_registry_entry_still_names_a_real_option() -> None:
    """The stale direction: an entry left behind after its option was
    removed announces a flag the CLI would now reject outright, which is a
    more confusing failure than either the flag working or being unknown.
    """
    declared = {param.name for _command, param in ALL_HIDDEN}
    stale = sorted(set(_P2_STUB_FLAGS) - declared)
    assert not stale, (
        f"_P2_STUB_FLAGS names {stale}, which no compat command declares as a "
        f"hidden option any more -- remove the entries."
    )


#: Which hidden options each command accepts today, per command rather than
#: pooled. Five of `dump`'s stubs also exist on `check`, so a set of bare
#: parameter *names* cannot see one being dropped from one command while the
#: other still declares it -- an existing ABICC `dump` line would then start
#: failing with "no such option" and nothing here would notice (Codex
#: review). Frozen for the same reason the Action's tombstones are: a
#: derived set never catches its own members disappearing.
_EXPECTED_HIDDEN_SURFACE = {
    "check": frozenset(
        {
            "check",
            "check_private_abi",
            "count_all_symbols",
            "count_symbols",
            "cpp_compatible",
            "cxx_incompatible",
            "disable_constants_check",
            "extended",
            "extra_dump",
            "extra_info",
            "force",
            "mingw_compatible",
            "quick",
            "skip_added_constants",
            "skip_removed_constants",
            "skip_typedef_uncover",
            "skip_unidentified",
            "sort_dump",
            "static_libs",
            "tolerance",
            "tolerant",
            "xml_format",
        }
    ),
    "dump": frozenset({"check", "extra_dump", "extra_info", "sort_dump", "xml_format"}),
}


@pytest.mark.parametrize("command_name", sorted(_EXPECTED_HIDDEN_SURFACE))
def test_no_command_has_quietly_dropped_an_accepted_abicc_option(
    command_name: str,
) -> None:
    """Each command keeps its own accepted-option surface.

    Dropping one is a break in the drop-in promise for every ABICC command
    line that uses it -- so, like the Action's tombstones, removal has to be
    a deliberate edit here with a reason, not a deletion no test observes.
    """
    actual = {param.name for param in _hidden_params(command_name)}
    missing = sorted(_EXPECTED_HIDDEN_SURFACE[command_name] - actual)
    assert not missing, (
        f"`compat {command_name}` no longer accepts {missing}; an ABICC "
        f"command line passing one now fails with 'no such option'. If the "
        f"removal is intended, drop the name from _EXPECTED_HIDDEN_SURFACE "
        f"and say in the PR why no ABICC caller can still pass it."
    )


@pytest.mark.parametrize("command_name", sorted(_EXPECTED_HIDDEN_SURFACE))
def test_the_expected_hidden_surface_has_not_gone_stale(command_name: str) -> None:
    """The converse: a newly added stub is announced-checked immediately by
    the derived tests above, but should be listed here too, or its own later
    removal goes unnoticed.
    """
    actual = {param.name for param in _hidden_params(command_name)}
    unlisted = sorted(actual - _EXPECTED_HIDDEN_SURFACE[command_name])
    assert not unlisted, (
        f"`compat {command_name}` declares hidden options {unlisted} that "
        f"_EXPECTED_HIDDEN_SURFACE does not list, so dropping one later would "
        f"fail nothing. Add them."
    )
