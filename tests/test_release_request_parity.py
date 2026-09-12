# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""The release request, through the CLI and through ``abicheck.service``.

ADR-061 gap D's completion test, at the *typed-API* surface this time.
``tests/test_release_compare_request.py`` already proved the resolution is
reachable as one ordinary function call and that a CLI run and a direct call
resolve the same plan. What it could not prove was the half its own module
docstring recorded as remaining scope: that the resolution is reachable
**from ``abicheck.service``**, with typed errors, rather than only from a
module classified ``frontends``.

Both halves are behavioural, not structural. A test asserting that
``release_inputs.py`` contains no ``import click`` would pass while the
function it guards behaved differently from the CLI -- and a file-text
assertion standing in for an executed behaviour is precisely the escape
``AGENTS.md`` names (#705 -> #758). So:

* the **agreement** tests resolve the same operands twice, once by driving
  the real ``compare`` CLI and capturing the plan it actually used, once
  through ``service.resolve_release_compare``, and compare the results;
* the **error** tests drive genuinely malformed operands through both and
  state what each surface is contractually required to raise -- a typed
  :class:`~abicheck.errors.ReleaseOperandError` subclass for the API caller,
  and for the CLI one the *same message* under the *same exit code* it
  produced before any of this moved. The two exit codes (``1`` for a fact
  about the operand's content, ``64`` for a fact about what the caller asked
  for) are a real distinction the typed hierarchy keeps: collapsing them
  into one error silently turns every content error into a usage error,
  which is what a first version of this change did.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner
from test_compare_release import _invoke, _snap, _write_snap

from abicheck import service
from abicheck.cli import main
from abicheck.errors import (
    ReleaseOperandContentError,
    ReleaseOperandError,
    ReleaseOperandUsageError,
)
from abicheck.serialization import snapshot_to_json


def _release_pair(
    tmp_path: Path, *, libs: tuple[str, ...] = ("libfoo",)
) -> tuple[Path, Path]:
    old_dir, new_dir = tmp_path / "old", tmp_path / "new"
    old_dir.mkdir()
    new_dir.mkdir()
    for lib in libs:
        _write_snap(old_dir / f"{lib}.json", _snap(library=f"{lib}.so"))
        _write_snap(new_dir / f"{lib}.json", _snap(library=f"{lib}.so"))
    return old_dir, new_dir


def _captured_cli_plan(*args: str):
    """Drive the real CLI and return the ``ReleaseComparePlan`` it used.

    Spying on the one production call site rather than re-resolving: the
    point is what the CLI *actually* resolved, which a second resolution
    performed by the test could only approximate. The site is the CLI
    *facade* (``frontends.cli.release_compare_request``) -- the module
    ``cli_compare_release`` lazily imports from -- not the engine owner
    behind it: patching the owner would leave the facade's own already-bound
    reference untouched and capture nothing.
    """
    from abicheck.frontends.cli import release_compare_request as boundary

    captured = []
    real = boundary.resolve_release_compare_plan

    def _spy(request, **kwargs):
        plan = real(request, **kwargs)
        captured.append(plan)
        return plan

    with patch.object(boundary, "resolve_release_compare_plan", side_effect=_spy):
        result = _invoke(*args)
    assert captured, f"the CLI never resolved a plan: {result}"
    return captured[0], result


class TestTheApiAndTheCliResolveTheSameRequest:
    """One request shape, two front ends, one answer."""

    @pytest.mark.parametrize("libs", [("libfoo",), ("libfoo", "libbar")])
    def test_scope_gate_and_markers_agree(
        self, tmp_path: Path, libs: tuple[str, ...]
    ) -> None:
        """Parametrized over cardinality as well: a one-member release and a
        two-member one must both agree with the API, since cardinality is an
        input to this resolution rather than a mode of it."""
        old_dir, new_dir = _release_pair(tmp_path, libs=libs)

        cli_plan, result = _captured_cli_plan(
            "compare", str(old_dir), str(new_dir), "--format", "json"
        )
        assert result[0] in (0, 2, 4), result[1]

        api_plan = service.resolve_release_compare(
            service.ReleaseCompareRequest(old_dir=old_dir, new_dir=new_dir)
        )
        try:
            assert api_plan.scope.matched_keys == cli_plan.scope.matched_keys
            assert api_plan.compare_keys == cli_plan.compare_keys
            assert api_plan.gate == cli_plan.gate
            assert api_plan.old_headers == cli_plan.old_headers
            assert api_plan.new_headers == cli_plan.new_headers
            assert api_plan.old_unclassified == cli_plan.old_unclassified
            assert api_plan.new_unclassified == cli_plan.new_unclassified
            assert api_plan.warnings == cli_plan.warnings
        finally:
            service.cleanup_release_compare_plan(api_plan)

    def test_a_gate_option_reaches_both_the_same_way(self, tmp_path: Path) -> None:
        """Not just the defaults: a request field that changes the resolved
        gate must change it identically on both surfaces. Without this, the
        agreement above could hold for a request nobody ever varies."""
        old_dir, new_dir = _release_pair(tmp_path)

        cli_plan, _ = _captured_cli_plan(
            "compare",
            str(old_dir),
            str(new_dir),
            "--format",
            "json",
            "--severity-preset",
            "default",
        )
        api_plan = service.resolve_release_compare(
            service.ReleaseCompareRequest(
                old_dir=old_dir, new_dir=new_dir, severity_preset="default"
            )
        )
        try:
            assert cli_plan.gate.severity is not None, "the preset must be in effect"
            assert api_plan.gate == cli_plan.gate
        finally:
            service.cleanup_release_compare_plan(api_plan)


class TestTheApiRaisesTypedErrorsAndTheCliTranslatesThem:
    """The same malformed operand, seen from both sides."""

    @staticmethod
    def _empty_dirs(tmp_path: Path) -> tuple[Path, Path]:
        """Two directories holding nothing a release comparison can read.

        The narrowest operand that reaches the resolution and fails inside
        it -- which is what makes it the right probe for the error contract
        (a nonexistent path would be rejected by Click's own `exists=True`
        long before any of this runs).
        """
        old_dir, new_dir = tmp_path / "old", tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        (old_dir / "notes.txt").write_text("not an ABI input", encoding="utf-8")
        (new_dir / "notes.txt").write_text("not an ABI input", encoding="utf-8")
        return old_dir, new_dir

    def test_the_api_raises_a_typed_error(self, tmp_path: Path) -> None:
        old_dir, new_dir = self._empty_dirs(tmp_path)
        with pytest.raises(ReleaseOperandContentError) as excinfo:
            service.resolve_release_compare(
                service.ReleaseCompareRequest(old_dir=old_dir, new_dir=new_dir)
            )
        assert "No supported ABI inputs found" in str(excinfo.value)

    def test_the_typed_error_is_a_validation_error(self, tmp_path: Path) -> None:
        """So every existing ``except ValidationError`` usage-error
        translation already covers it, and a caller that only knows the base
        vocabulary is not surprised by a new top-level type."""
        from abicheck.errors import AbicheckError, ValidationError

        assert issubclass(ReleaseOperandError, ValidationError)
        assert issubclass(ReleaseOperandError, AbicheckError)
        assert issubclass(ReleaseOperandError, ValueError)
        # A caller that only knows the base class catches both kinds, which
        # is what makes the CLI's exit-code split below an implementation
        # detail of the front end rather than a second vocabulary.
        assert issubclass(ReleaseOperandContentError, ReleaseOperandError)
        assert issubclass(ReleaseOperandUsageError, ReleaseOperandError)

    def test_a_content_error_is_exit_1_with_the_same_message(
        self, tmp_path: Path
    ) -> None:
        """The translation is the CLI boundary's whole remaining job, so the
        message is checked against the API's own rather than a literal copied
        into this test -- and the exit code against ``1``, which is what this
        operand produced before the resolution moved engine-side."""
        old_dir, new_dir = self._empty_dirs(tmp_path)
        try:
            service.resolve_release_compare(
                service.ReleaseCompareRequest(old_dir=old_dir, new_dir=new_dir)
            )
        except ReleaseOperandError as exc:
            expected = str(exc)
        else:  # pragma: no cover - the API contract above pins this
            raise AssertionError("the API did not raise")

        # Via a runner of our own: a `click.ClickException`'s message goes to
        # stderr, which `_invoke`'s shared helper does not capture.
        result = CliRunner().invoke(main, ["compare", str(old_dir), str(new_dir)])
        assert result.exit_code == 1, result.output
        assert expected in (result.output + (result.stderr or ""))

    def test_a_usage_error_is_exit_64_with_the_same_message(
        self, tmp_path: Path
    ) -> None:
        """The other half of the split, so neither exit code can drift into
        the other. Driven by raising the usage-family error at its real raise
        site: the only thing that produces it for real is an ambiguous
        stored-package variant, and constructing a multi-variant
        ``ProjectSnapshot`` package here just to observe a translation would
        test the package writer, not the translation."""
        from abicheck.workflows import release_inputs

        old_dir, new_dir = _release_pair(tmp_path)
        message = "variant is ambiguous (e.g. --variant old=v1)"

        def _raise(*_args: object, **_kwargs: object) -> None:
            raise ReleaseOperandUsageError(message)

        with patch.object(
            release_inputs, "resolve_release_package_side", side_effect=_raise
        ):
            with pytest.raises(ReleaseOperandUsageError):
                service.resolve_release_compare(
                    service.ReleaseCompareRequest(old_dir=old_dir, new_dir=new_dir)
                )
            code, out = _invoke("compare", str(old_dir), str(new_dir))
        assert code == 64, out
        assert message in out

    def test_no_click_error_escapes_the_api_surface(self, tmp_path: Path) -> None:
        """The failure this split exists to prevent, stated directly: a
        Python caller must never receive a ``click`` exception, which it has
        no reason to know about and cannot catch by any documented name."""
        import click

        old_dir, new_dir = self._empty_dirs(tmp_path)
        with pytest.raises(ReleaseOperandError) as excinfo:
            service.resolve_release_compare(
                service.ReleaseCompareRequest(old_dir=old_dir, new_dir=new_dir)
            )
        assert not isinstance(excinfo.value, click.ClickException)
        assert not isinstance(excinfo.value, click.exceptions.UsageError)

    def test_an_ambiguous_library_match_is_a_clean_exit_1(self, tmp_path: Path) -> None:
        """The third error the resolution can raise, and the one most easily
        lost in the move.

        Release input resolution reaches ``binary_utils.build_match_map`` --
        the pure primitive -- directly now, rather than through
        ``cli_helpers_compare._build_match_map``'s Click-translating wrapper,
        so the typed ``AmbiguousLibraryMatchError`` it raises has to be
        translated at this boundary instead. Without that it escaped
        uncaught: still exit ``1``, but with no message naming the tie it
        refused to break, which is exactly the "clean error" property the
        wrapper existed for.
        """
        import gzip

        from abicheck.errors import AmbiguousLibraryMatchError

        old_dir, new_dir = tmp_path / "old", tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        payload = snapshot_to_json(_snap(library="libfoo.so"))
        # A plain snapshot and a stale compressed sibling: indistinguishable
        # except by storage encoding, which is the real ADR-059 tie.
        (old_dir / "libfoo.abicheck.json").write_text(payload, encoding="utf-8")
        (old_dir / "libfoo.abicheck.json.gz").write_bytes(
            gzip.compress(payload.encode())
        )
        (new_dir / "libfoo.abicheck.json").write_text(payload, encoding="utf-8")

        with pytest.raises(AmbiguousLibraryMatchError) as excinfo:
            service.resolve_release_compare(
                service.ReleaseCompareRequest(old_dir=old_dir, new_dir=new_dir)
            )

        result = CliRunner().invoke(main, ["compare", str(old_dir), str(new_dir)])
        assert result.exit_code == 1, result.output
        assert str(excinfo.value) in (result.output + (result.stderr or ""))
