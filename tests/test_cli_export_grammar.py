# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""The one export request's contract, stated as invariants (plan slice 7m).

``-o FORMAT=DESTINATION`` replaced four mechanisms (``--format``, the
path-only ``-o``, ``--write FORMAT=PATH``, ``--output-dir``). What makes that
a *simplification* rather than a rename is that a set of properties now holds
for every export, uniformly -- so this module tests the grammar over
**generated** format/destination combinations rather than one example per
retired flag, per AGENTS.md's primitive-level rule. A fixed-input test here
would only foreclose the permutation it names, and the permutations are
exactly what the old four-mechanism surface could not answer consistently.

The invariants, and why each is a real risk rather than a restatement of the
implementation:

* **Rendering is destination-independent.** Where an artifact goes cannot
  change what it says -- the failure this forecloses is a "primary" target
  rendering differently from a "secondary" one, which is precisely what the
  retired ``--write`` did (always unfiltered, whatever ``--format`` was
  asked for).
* **Rendering is permutation-independent** (plan §7 F-19): the canonical
  result block is byte-identical across export permutations, and so is the
  exit code. Asking for more artifacts is not an analysis input.
* **Every requested artifact is produced.** No export is silently dropped,
  which is the plan's own non-goal ("never ignoring supplied input").
* **Malformed and conflicting requests are usage errors (64), never
  guesses**: no ``=``, an unknown format, two exports to one destination,
  two exports to stdout.
* **The retired spellings are gone with no alias.**

`compare` is the command under test for the report-shaped invariants (it has
the richest format set and the only fan-out), with the other export-carrying
commands swept for the grammar-level ones -- a rule that held only on
``compare`` would be a per-command grammar again.
"""

from __future__ import annotations

import itertools
import json
import os
import re
from pathlib import Path

import click
import pytest
from click.testing import CliRunner

from abicheck.cli import main
from abicheck.frontends.cli.options.export import (
    ExportSet,
    ExportTarget,
    build_export_set,
    parse_export_operand,
)

_EXIT_USAGE_ERROR = 64


#: Every command carrying the export request, with the formats it declares.
#: Read off Click rather than restated, so a command added to (or dropped
#: from) the grammar cannot silently stop being swept here.
def _export_commands() -> list[tuple[tuple[str, ...], tuple[str, ...]]]:
    found: list[tuple[tuple[str, ...], tuple[str, ...]]] = []

    def walk(cmd: object, path: tuple[str, ...]) -> None:
        commands = getattr(cmd, "commands", None)
        if commands:
            for name, sub in commands.items():
                walk(sub, (*path, name))
            return
        for param in getattr(cmd, "params", []):
            formats = getattr(param, "export_formats", None)
            if formats:
                found.append((path, tuple(formats)))

    walk(main, ())
    return found


EXPORT_COMMANDS = _export_commands()
COMPARE_FORMATS = dict(EXPORT_COMMANDS)[("compare",)]

#: Formats whose rendered document is deterministic enough to compare
#: byte-for-byte across two runs. Excluded: none today -- every renderer
#: reads the same envelope -- but the timestamp-bearing keys JSON carries are
#: normalized away by :func:`_canonical_result`.
RENDERABLE = tuple(f for f in COMPARE_FORMATS if f != "oneline")


@pytest.fixture(scope="module")
def snapshot_pair(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    """Two stored snapshots with a real, breaking difference between them.

    Stored snapshots (not live binaries) on purpose: this module is about the
    export grammar, and a snapshot pair makes every run in it cheap enough to
    sweep permutations rather than sample them.
    """
    from abicheck.model import AbiSnapshot, Function, Param
    from abicheck.serialization import write_snapshot

    def snap(functions: list[Function]) -> AbiSnapshot:
        return AbiSnapshot(
            library="libexport.so.1",
            version="1.0",
            functions=functions,
        )

    def fn(name: str) -> Function:
        return Function(
            name=name,
            mangled=name,
            return_type="void",
            params=[Param(name="a", type="int")],
        )

    root = tmp_path_factory.mktemp("export-grammar")
    old = root / "old.abi.json"
    new = root / "new.abi.json"
    write_snapshot(snap([fn("kept"), fn("removed")]), old)
    write_snapshot(snap([fn("kept")]), new)
    return old, new


def _run(args: list[str]) -> object:
    return CliRunner().invoke(main, args)


def _resolve(path: tuple[str, ...]) -> click.Command:
    cmd: click.Command = main
    for segment in path:
        cmd = cmd.commands[segment]  # type: ignore[attr-defined]
    return cmd


def _parse_error(path: tuple[str, ...], args: list[str]) -> str:
    """Parse *args* against the real command and return the usage error.

    Through ``make_context`` rather than a full ``invoke``: the export
    request must be rejected during *parsing*, before any operand is
    resolved or any analysis begins, and these commands have required
    arguments that a full invocation would have to satisfy with fixtures
    irrelevant to the grammar under test. ``UsageError`` is what
    ``_AbicheckGroup`` maps to exit 64, which
    ``TestRetiredSpellings`` checks end-to-end.
    """
    cmd = _resolve(path)
    with pytest.raises(click.UsageError) as excinfo:
        cmd.make_context(path[-1], args, resilient_parsing=False)
    return str(excinfo.value)


def _canonical_result(text: str, fmt: str) -> str:
    """The part of a rendered report that must not vary across exports.

    Timestamps and absolute destination paths legitimately differ between two
    runs (and, for a path, between two exports of one run), so they are
    normalized out -- everything else is the canonical result block F-19 pins.
    """
    text = re.sub(r"\d{4}-\d{2}-\d{2}T[\d:.+\-]+", "<ts>", text)
    text = re.sub(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", "<ts>", text)
    if fmt in ("json", "sarif"):
        try:
            doc = json.loads(text)
        except json.JSONDecodeError:
            return text
        for key in ("generated_at", "timestamp", "generated"):
            if isinstance(doc, dict):
                doc.pop(key, None)
        return json.dumps(doc, sort_keys=True, indent=2)
    return text


class TestOperandGrammar:
    """The parser's own contract, over generated operands."""

    @pytest.mark.parametrize("fmt", ["json", "markdown", "sarif", "junit"])
    @pytest.mark.parametrize(
        "dest", ["-", "report.out", "nested/dir/report.out", "with=equals.out"]
    )
    def test_format_and_destination_round_trip(self, fmt: str, dest: str) -> None:
        target = parse_export_operand(
            f"{fmt}={dest}", ["json", "markdown", "sarif", "junit"]
        )
        assert target.fmt == fmt
        if dest == "-":
            assert target.to_stdout
        else:
            # Split on the FIRST '=' only: a destination containing further
            # '=' survives intact rather than being truncated at it.
            assert target.destination == Path(dest)

    def test_windows_destination_survives_the_split(self) -> None:
        """A drive letter carries a colon, never an '=' -- so the first-'='
        rule leaves a Windows path whole. Asserted on the parse, not on a
        real filesystem, so it holds on every platform the CLI runs on."""
        target = parse_export_operand(r"json=C:\out\report.json", ["json"])
        assert target.fmt == "json"
        assert str(target.destination) == r"C:\out\report.json"

    @pytest.mark.parametrize(
        "operand",
        [
            "report.json",  # the retired path-only spelling
            "json",  # a bare format
            "=report.json",  # no format
            "json=",  # no destination
            "-",  # a bare stdout marker with no format
        ],
    )
    def test_malformed_operand_is_a_usage_error(self, operand: str) -> None:
        import click

        with pytest.raises(click.BadParameter):
            parse_export_operand(operand, ["json", "markdown"])

    @pytest.mark.parametrize("fmt", ["yaml", "xml", "JSON", "jsonl", ""])
    def test_unknown_format_is_a_usage_error(self, fmt: str) -> None:
        import click

        with pytest.raises(click.BadParameter):
            parse_export_operand(f"{fmt}=out.txt", ["json", "markdown"])

    def test_collision_is_rejected_for_every_ordering(self) -> None:
        """Two exports to one destination, in either order and whichever
        formats -- the rule is about the destination, not about which flag
        occurrence 'wins', which is what the retired last-flag-wins pair got
        wrong."""
        import click

        for a, b in itertools.permutations(["json", "markdown", "junit"], 2):
            with pytest.raises(click.BadParameter, match="both export to"):
                build_export_set(
                    [f"{a}=same.out", f"{b}=same.out"],
                    ["json", "markdown", "junit"],
                    default_format="markdown",
                )

    def test_collision_sees_through_two_spellings_of_one_path(self) -> None:
        import click

        with pytest.raises(click.BadParameter, match="both export to"):
            build_export_set(
                ["json=a/../b.out", "markdown=b.out"],
                ["json", "markdown"],
                default_format="markdown",
            )

    def test_two_stdout_exports_are_rejected(self) -> None:
        import click

        for a, b in itertools.permutations(["json", "markdown", "junit"], 2):
            with pytest.raises(click.BadParameter, match="standard output"):
                build_export_set(
                    [f"{a}=-", f"{b}=-"],
                    ["json", "markdown", "junit"],
                    default_format="markdown",
                )

    def test_one_stdout_export_alongside_files_is_fine(self) -> None:
        exports = build_export_set(
            ["json=-", "markdown=a.md", "junit=b.xml"],
            ["json", "markdown", "junit"],
            default_format="markdown",
        )
        assert [t.fmt for t in exports.documents] == ["json", "markdown", "junit"]
        assert exports.primary.fmt == "json"
        assert exports.primary.to_stdout

    def test_no_request_defaults_to_the_command_default_on_stdout(self) -> None:
        exports = build_export_set([], ["json", "markdown"], default_format="markdown")
        assert exports.targets == (
            ExportTarget(fmt="markdown", destination=None, spelling=""),
        )
        assert not exports.explicit

    def test_directory_only_request_keeps_the_summary_on_stdout(self) -> None:
        """The retired ``--output-dir`` wrote per-library reports *and* left
        the summary on stdout. A directory-only export set must not silently
        drop that summary."""
        exports = build_export_set(
            ["json=reports/"],
            ["json", "markdown"],
            default_format="markdown",
            supports_directory=True,
            directory_formats=["json"],
        )
        assert [t.fmt for t in exports.documents] == ["markdown"]
        assert exports.documents[0].to_stdout
        assert [str(t.destination) for t in exports.directories] == ["reports"]

    def test_directory_destination_needs_a_command_with_a_fan_out(self) -> None:
        import click

        with pytest.raises(click.BadParameter, match="names a directory"):
            build_export_set(["json=reports/"], ["json"], default_format="json")

    def test_directory_destination_rejects_a_format_the_fan_out_cannot_produce(
        self,
    ) -> None:
        import click

        with pytest.raises(click.BadParameter, match="per-component fan-out"):
            build_export_set(
                ["markdown=reports/"],
                ["json", "markdown"],
                default_format="markdown",
                supports_directory=True,
                directory_formats=["json"],
            )

    def test_two_directory_exports_are_rejected(self) -> None:
        import click

        with pytest.raises(click.BadParameter, match="one per-component export"):
            build_export_set(
                ["json=a/", "json=b/"],
                ["json"],
                default_format="json",
                supports_directory=True,
                directory_formats=["json"],
            )


class TestGrammarAcrossEveryExportingCommand:
    """The grammar-level rules hold on every command that carries ``-o``.

    Swept over the real command set (read off Click above) rather than
    restated per command: a rule that held only on ``compare`` would be a
    per-command grammar again, which is the thing this slice removed.
    """

    def test_every_exporting_command_declares_the_same_metavar(self) -> None:
        assert EXPORT_COMMANDS, "no command declares an export request"
        for path, _formats in EXPORT_COMMANDS:
            cmd = main
            for segment in path:
                cmd = cmd.commands[segment]  # type: ignore[attr-defined]
            (param,) = [p for p in cmd.params if getattr(p, "export_formats", None)]
            assert param.opts == ["-o", "--output"], path
            assert param.metavar == "FORMAT=DESTINATION", path
            assert param.multiple, path

    @pytest.mark.parametrize("path,formats", EXPORT_COMMANDS)
    def test_path_only_destination_is_rejected(
        self, path: tuple[str, ...], formats: tuple[str, ...], tmp_path: Path
    ) -> None:
        message = _parse_error(path, ["-o", str(tmp_path / "out.txt")])
        assert "FORMAT=DESTINATION" in message

    @pytest.mark.parametrize("path,formats", EXPORT_COMMANDS)
    def test_unknown_format_is_rejected(
        self, path: tuple[str, ...], formats: tuple[str, ...]
    ) -> None:
        assert "not a renderable format" in _parse_error(path, ["-o", "yaml=-"])

    @pytest.mark.parametrize("path,formats", EXPORT_COMMANDS)
    def test_two_stdout_exports_are_rejected(
        self, path: tuple[str, ...], formats: tuple[str, ...]
    ) -> None:
        if len(formats) < 2:  # pragma: no cover - every command has two today
            pytest.skip("command declares one format")
        message = _parse_error(path, ["-o", f"{formats[0]}=-", "-o", f"{formats[1]}=-"])
        assert "standard output" in message


class TestRetiredSpellings:
    """Every retired spelling exits 64, with no alias, on every command."""

    @pytest.mark.parametrize("path,_formats", EXPORT_COMMANDS)
    @pytest.mark.parametrize(
        "flag", ["--format", "--write", "--output-dir", "--max-findings-per-library"]
    )
    def test_retired_flag_is_gone(
        self, path: tuple[str, ...], _formats: tuple[str, ...], flag: str
    ) -> None:
        result = _run([*path, flag, "json"])
        assert result.exit_code == _EXIT_USAGE_ERROR, result.output
        assert "No such option" in result.output

    def test_no_command_declares_a_retired_spelling(self) -> None:
        """Not just "the flag errors": the parameter must not exist anywhere,
        including hidden -- a hidden alias would pass the invocation test
        above while keeping the second mechanism alive."""

        retired = {"--format", "--write", "--output-dir", "--max-findings-per-library"}
        offenders: list[str] = []

        def walk(cmd: click.Command, path: str) -> None:
            commands = getattr(cmd, "commands", None)
            if commands:
                for name, sub in commands.items():
                    walk(sub, f"{path} {name}".strip())
                return
            # `compat` is frozen by ADR-068 D7 and excluded from this phase;
            # it declares none of these anyway, which this assertion proves
            # rather than assumes.
            for param in cmd.params:
                for spelling in param.opts + param.secondary_opts:
                    if spelling in retired:
                        offenders.append(f"{path} {spelling}")

        walk(main, "")
        assert offenders == []


class TestExportsAreOneAnalysis:
    """F-19 and its neighbours, over generated export permutations."""

    def test_the_result_does_not_depend_on_the_destination(
        self, snapshot_pair: tuple[Path, Path], tmp_path: Path
    ) -> None:
        """Same format, two destinations: byte-identical, and the exit code
        with it. This is the property the retired ``--write`` broke."""
        old, new = snapshot_pair
        to_stdout = _run(["compare", str(old), str(new), "-o", "json=-"])
        dest = tmp_path / "report.json"
        to_file = _run(["compare", str(old), str(new), "-o", f"json={dest}"])
        assert to_stdout.exit_code == to_file.exit_code
        assert _canonical_result(to_stdout.output, "json") == _canonical_result(
            dest.read_text(encoding="utf-8"), "json"
        )

    @pytest.mark.parametrize("fmt", RENDERABLE)
    def test_a_format_renders_the_same_in_every_permutation(
        self, fmt: str, snapshot_pair: tuple[Path, Path], tmp_path: Path
    ) -> None:
        """*fmt* is exported alone, first among several, and last among
        several. All three must agree byte-for-byte, and all three must exit
        the same -- asking for more artifacts is not an analysis input."""
        old, new = snapshot_pair
        others = [f for f in RENDERABLE if f != fmt][:2]

        def run_with(extra: list[str], tag: str) -> tuple[int, str]:
            dest = tmp_path / f"{fmt}-{tag}.out"
            result = _run(
                ["compare", str(old), str(new), "-o", f"{fmt}={dest}", *extra]
            )
            assert dest.exists(), result.output
            return result.exit_code, _canonical_result(dest.read_text(encoding="utf-8"), fmt)

        alone = run_with([], "alone")
        before = run_with(
            [
                arg
                for i, other in enumerate(others)
                for arg in ("-o", f"{other}={tmp_path / f'{fmt}-b{i}.out'}")
            ],
            "first",
        )
        # The same set, requested in the opposite order.
        after_args = [
            arg
            for i, other in enumerate(reversed(others))
            for arg in ("-o", f"{other}={tmp_path / f'{fmt}-a{i}.out'}")
        ]
        after = run_with(after_args, "last")

        assert alone == before == after

    def test_every_requested_artifact_is_produced(
        self, snapshot_pair: tuple[Path, Path], tmp_path: Path
    ) -> None:
        """No export is silently dropped -- plan §Non-goals' "never ignoring
        supplied input", checked over the whole renderable set at once rather
        than one format at a time."""
        old, new = snapshot_pair
        destinations = {fmt: tmp_path / f"all-{fmt}.out" for fmt in RENDERABLE}
        args = [
            arg for fmt, dest in destinations.items() for arg in ("-o", f"{fmt}={dest}")
        ]
        result = _run(["compare", str(old), str(new), *args])
        assert result.exit_code == 4, result.output
        missing = [fmt for fmt, dest in destinations.items() if not dest.exists()]
        assert missing == []
        assert all(dest.read_text(encoding="utf-8").strip() for dest in destinations.values())

    def test_two_exports_of_one_format_are_identical(
        self, snapshot_pair: tuple[Path, Path], tmp_path: Path
    ) -> None:
        old, new = snapshot_pair
        a, b = tmp_path / "a.json", tmp_path / "b.json"
        result = _run(
            ["compare", str(old), str(new), "-o", f"json={a}", "-o", f"json={b}"]
        )
        assert result.exit_code == 4, result.output
        assert a.read_text(encoding="utf-8") == b.read_text(encoding="utf-8")

    def test_a_display_filter_applies_to_every_export(
        self, snapshot_pair: tuple[Path, Path], tmp_path: Path
    ) -> None:
        """The one deliberate behaviour change this slice makes, pinned so it
        cannot drift back: ``--view show=`` means the same thing for every
        export. The retired ``--write`` rendered unfiltered regardless, an
        asymmetry with no answer once both artifacts come from one operand."""
        old, new = snapshot_pair
        first, second = tmp_path / "one.json", tmp_path / "two.json"
        result = _run(
            [
                "compare",
                str(old),
                str(new),
                "--view",
                "show=breaking",
                "-o",
                f"json={first}",
                "-o",
                f"json={second}",
            ]
        )
        assert result.exit_code == 4, result.output
        for path in (first, second):
            doc = json.loads(path.read_text(encoding="utf-8"))
            assert doc["show_only_filter"] == "breaking"
            # Narrowing the display never removes the accounting: the
            # complete picture stays available in every machine projection.
            assert "filtered_summary" in doc

    def test_the_exit_code_does_not_depend_on_the_export_set(
        self, snapshot_pair: tuple[Path, Path], tmp_path: Path
    ) -> None:
        old, new = snapshot_pair
        codes = set()
        for n in range(1, len(RENDERABLE) + 1):
            args = [
                arg
                for fmt in RENDERABLE[:n]
                for arg in ("-o", f"{fmt}={tmp_path / f'exit-{n}-{fmt}.out'}")
            ]
            codes.add(_run(["compare", str(old), str(new), *args]).exit_code)
        assert codes == {4}


class TestDryRunAndExports:
    def test_dry_run_rejects_a_file_export(
        self, snapshot_pair: tuple[Path, Path], tmp_path: Path
    ) -> None:
        old, new = snapshot_pair
        result = _run(
            [
                "compare",
                str(old),
                str(new),
                "--dry-run",
                "-o",
                f"json={tmp_path / 'x.json'}",
            ]
        )
        assert result.exit_code == _EXIT_USAGE_ERROR, result.output
        assert "--dry-run" in result.output

    def test_dry_run_allows_a_stdout_export(
        self, snapshot_pair: tuple[Path, Path]
    ) -> None:
        old, new = snapshot_pair
        result = _run(["compare", str(old), str(new), "--dry-run", "-o", "json=-"])
        assert result.exit_code == 0, result.output


class TestExportSetShape:
    """``ExportSet``'s own accessors, which the rest of the CLI reads."""

    def test_primary_prefers_stdout_then_request_order(self) -> None:
        exports = build_export_set(
            ["json=a.json", "markdown=-", "junit=b.xml"],
            ["json", "markdown", "junit"],
            default_format="markdown",
        )
        assert exports.primary.fmt == "markdown"
        assert [t.fmt for t in exports.secondaries] == ["json", "junit"]

        no_stdout = build_export_set(
            ["json=a.json", "junit=b.xml"],
            ["json", "junit"],
            default_format="markdown",
        )
        assert no_stdout.primary.fmt == "json"
        assert [t.fmt for t in no_stdout.secondaries] == ["junit"]

    def test_documents_and_directories_partition_the_targets(self) -> None:
        exports = build_export_set(
            ["json=reports/", "markdown=-", "junit=b.xml"],
            ["json", "markdown", "junit"],
            default_format="markdown",
            supports_directory=True,
            directory_formats=["json"],
        )
        assert set(exports.documents) | set(exports.directories) == set(exports.targets)
        assert not set(exports.documents) & set(exports.directories)

    def test_expand_export_kwargs_preserves_every_target(self) -> None:
        """The compare stack still takes one ``fmt``/``output`` pair plus a
        list; the expansion must lose nothing, whichever shape the set has."""
        from abicheck.frontends.cli.options.export import expand_export_kwargs

        for operands in (
            ["json=-"],
            ["json=a.json", "markdown=-"],
            ["markdown=-", "json=a.json", "junit=b.xml"],
            ["json=reports/", "markdown=-"],
        ):
            exports = build_export_set(
                operands,
                ["json", "markdown", "junit"],
                default_format="markdown",
                supports_directory=True,
                directory_formats=["json"],
            )
            kwargs: dict[str, object] = {"exports": exports}
            expand_export_kwargs(kwargs)
            recovered = [(kwargs["fmt"], kwargs["output"])] + list(
                kwargs["secondary_writes"]  # type: ignore[arg-type]
            )
            assert sorted(str(t) for t in recovered) == sorted(
                str((t.fmt, t.destination)) for t in exports.documents
            )
            expected_dir = (
                exports.directories[0].destination if exports.directories else None
            )
            assert kwargs["output_dir"] == expected_dir


def test_export_set_is_hashable_and_frozen() -> None:
    """A resolved export request is a value, not mutable state -- so nothing
    downstream can quietly add or drop an artifact after validation."""
    exports = ExportSet(targets=(ExportTarget(fmt="json", destination=None),))
    with pytest.raises(AttributeError):
        exports.targets = ()  # type: ignore[misc]


class TestDirectoryExportOwnsItsTree:
    """A directory export claims *everything* under it, not just its own path.

    The fan-out generates one report per component plus ``summary.json``
    there, and those names come from the operand's inventory -- unknowable
    at parse time. So the contract is containment, stated here over
    generated nestings rather than over the one reported operand pair
    (``-o json=reports/ -o markdown=reports/libfoo.json``), which was
    accepted and let the Markdown document overwrite the per-library JSON.

    The oracle is deliberately a second derivation -- a resolved-posix string
    prefix -- not the ``Path.parents`` containment the implementation folds
    over.
    """

    #: (directory operand, file destination) pairs. Half land inside the
    #: directory at varying depth, half are near-misses chosen to catch a
    #: prefix test that forgot the separator (``reports2``) or that treated
    #: an ancestor as containment.
    _DESTINATIONS = (
        "reports/libfoo.json",
        "reports/summary.json",
        "reports/nested/deep/out.json",
        "reports/./libfoo.json",
        "reports/../reports/libfoo.json",
        "reports2/libfoo.json",
        "reportsx.json",
        "elsewhere/libfoo.json",
        "out.json",
        "../sibling.json",
    )

    @staticmethod
    def _oracle_is_inside(root: Path, destination: Path) -> bool:
        root_text = root.resolve().as_posix().rstrip("/") + "/"
        return destination.resolve().as_posix().startswith(root_text)

    @pytest.mark.parametrize("destination", _DESTINATIONS)
    @pytest.mark.parametrize("directory_first", [True, False])
    def test_containment_decides_regardless_of_order(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        destination: str,
        directory_first: bool,
    ) -> None:
        workdir = tmp_path / "work"
        workdir.mkdir()
        monkeypatch.chdir(workdir)

        operands = [f"json=reports{os.sep}", f"markdown={destination}"]
        if not directory_first:
            operands.reverse()

        inside = self._oracle_is_inside(Path("reports"), Path(destination))
        if inside:
            with pytest.raises(click.BadParameter) as excinfo:
                build_export_set(
                    operands,
                    ["json", "markdown"],
                    default_format="markdown",
                    supports_directory=True,
                    directory_formats=["json"],
                )
            assert "per-component reports" in str(excinfo.value)
        else:
            exports = build_export_set(
                operands,
                ["json", "markdown"],
                default_format="markdown",
                supports_directory=True,
                directory_formats=["json"],
            )
            assert len(exports.targets) == 2

    def test_the_oracle_is_not_vacuous(self) -> None:
        """Guard the guard: an oracle stuck at one answer would make every
        case above pass while asserting nothing."""
        answers = {
            self._oracle_is_inside(Path("reports"), Path(d)) for d in self._DESTINATIONS
        }
        assert answers == {True, False}

    def test_stdout_export_is_never_inside_a_directory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``-`` has no path, so the containment rule must skip it rather
        than resolving it as the relative file named ``-``."""
        monkeypatch.chdir(tmp_path)
        exports = build_export_set(
            [f"json=reports{os.sep}", "markdown=-"],
            ["json", "markdown"],
            default_format="markdown",
            supports_directory=True,
            directory_formats=["json"],
        )
        assert exports.primary.to_stdout


def test_no_export_operand_was_committed_as_a_repository_file() -> None:
    """An export written to the *raw* operand text (``-o json=-`` creating a
    file literally named ``json=-``) is what an unfaithful test stub
    produces, and eight such artifacts reached a commit on this branch
    before review caught them. Named as a class rather than as those eight
    paths: any tracked file whose name is a bare format or carries the
    ``FORMAT=`` prefix is this mistake, wherever in the tree it lands.
    """
    import subprocess

    repo_root = Path(__file__).resolve().parent.parent
    tracked = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split("\0")

    formats = {
        "json",
        "markdown",
        "sarif",
        "html",
        "junit",
        "review",
        "oneline",
        "text",
    }
    offenders = [
        path
        for path in tracked
        if path
        and (
            Path(path).name in formats
            or Path(path).name.split("=", 1)[0] in formats
            and "=" in Path(path).name
        )
    ]
    assert offenders == [], (
        "these tracked files look like an export operand written verbatim as a "
        f"filename rather than parsed into FORMAT and DESTINATION: {offenders}"
    )
