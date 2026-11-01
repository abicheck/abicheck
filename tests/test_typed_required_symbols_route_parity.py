"""``consumers.required_symbols`` decides the same on the CLI and the typed API.

Known gap "``consumers.required_symbols`` ``@FILE`` read errors are checked on
the CLI route only" (closed 2026-11-01). The typed route now accepts the key in
``CompareRequest.config_overrides`` -- the typed spelling of ``compare --set``
-- and resolves it through the CLI's own owners
(``workflows/compare/required_symbol_contract.py``), so there is one loader,
not a second copy.

Bug class ``frontend.route_parity_missing_cell``-style drift: a refusal or
result pinned on one front end only. The oracle is the CLI run:

* **Refusals.** For every way an ``@FILE`` entry is refused (missing,
  directory, unreadable, not UTF-8, two ``@FILE`` values), ``compare --set``
  exits 64 and the typed request raises ``CompareSettingsError`` with the same
  message -- before either side is read.
* **Results.** For several contracts (literal only, ``@FILE`` only, both, an
  empty file, every entrypoint kept), the typed result's rendered
  ``required_symbol_contract``, ``consumer_scope``, ``policy`` and ``verdict``
  equal the CLI report's, under the default policy (``plugin_abi`` selected
  by the contract) and under a policy the caller chose.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.cli import main
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.report.diff_report import to_json
from abicheck.serialization import snapshot_to_json
from abicheck.workflows.compare.execute import run_compare_request
from abicheck.workflows.compare_configuration import CompareSettingsError
from abicheck.workflows.request_inputs import InputSpec
from abicheck.workflows.service_requests import CompareRequest

KEY = "consumers.required_symbols"


def _fn(name: str) -> Function:
    return Function(
        name=name, mangled=name, return_type="int", visibility=Visibility.PUBLIC
    )


def _snapshots(tmp_path: Path) -> tuple[Path, Path]:
    old = AbiSnapshot(
        library="libplugin.so",
        version="1",
        functions=[_fn("plugin_init"), _fn("plugin_run"), _fn("helper")],
    )
    new = AbiSnapshot(
        library="libplugin.so",
        version="2",
        functions=[_fn("plugin_init"), _fn("helper"), _fn("extra")],
    )
    paths = []
    for name, snap in (("old.json", old), ("new.json", new)):
        path = tmp_path / name
        path.write_text(snapshot_to_json(snap), encoding="utf-8")
        paths.append(path)
    return paths[0], paths[1]


def _cli(tmp_path: Path, entries: list[str], *extra: str):
    old, new = _snapshots(tmp_path)
    report = tmp_path / "cli.json"
    result = CliRunner().invoke(
        main,
        [
            "compare",
            str(old),
            str(new),
            "--set",
            f"{KEY}={json.dumps(entries)}",
            *extra,
            "-o",
            f"json={report}",
        ],
    )
    return result, report


def _typed(tmp_path: Path, entries: list[str], **fields):
    old, new = _snapshots(tmp_path)
    return run_compare_request(
        CompareRequest(
            old=InputSpec(path=old),
            new=InputSpec(path=new),
            config_overrides={KEY: entries},
            **fields,
        )
    )


def _flat(text: str) -> str:
    return " ".join(text.split())


# -- refusals -----------------------------------------------------------------


def _refused_entries(tmp_path: Path, failure: str, monkeypatch) -> list[str]:
    target = tmp_path / "syms.txt"
    if failure == "missing":
        return [f"@{target}"]
    if failure == "directory":
        target.mkdir()
        return [f"@{target}"]
    if failure == "not UTF-8":
        target.write_bytes("café_sym\n".encode("latin-1"))
        return [f"@{target}"]
    if failure == "two @FILE values":
        other = tmp_path / "other.txt"
        target.write_text("a\n", encoding="utf-8")
        other.write_text("b\n", encoding="utf-8")
        return [f"@{target}", f"@{other}"]
    assert failure == "unreadable"
    # A root test process reads a `chmod 000` file, so the read is denied by
    # patching it for this one path (as tests/test_compare_settings_cli_translation.py does).
    target.write_text("plugin_run\n", encoding="utf-8")
    real_read_bytes = Path.read_bytes

    def denied(self: Path) -> bytes:
        if self == target:
            raise PermissionError(13, "Permission denied")
        return real_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", denied)
    return ["plugin_init", f"@{target}"]


_FAILURES = ["missing", "directory", "unreadable", "not UTF-8", "two @FILE values"]


@pytest.mark.parametrize("failure", _FAILURES)
def test_both_routes_refuse_a_bad_file_with_one_message(
    tmp_path: Path, failure: str, monkeypatch
) -> None:
    entries = _refused_entries(tmp_path, failure, monkeypatch)
    result, report = _cli(tmp_path, entries)
    assert result.exit_code == 64, result.output
    assert not report.exists()
    with pytest.raises(CompareSettingsError) as excinfo:
        _typed(tmp_path, entries)
    message = str(excinfo.value)
    assert message.startswith(KEY), message
    assert _flat(message) in _flat(result.output)


def test_the_typed_refusal_comes_before_either_side_is_read(
    tmp_path: Path, monkeypatch
) -> None:
    """A missing operand would fail differently; the contract is refused first,
    as the CLI refuses it before extraction."""
    missing = tmp_path / "absent.txt"
    with pytest.raises(CompareSettingsError, match="no such file"):
        run_compare_request(
            CompareRequest(
                old=InputSpec(path=tmp_path / "no-old.so"),
                new=InputSpec(path=tmp_path / "no-new.so"),
                config_overrides={KEY: [f"@{missing}"]},
            )
        )


# -- results ------------------------------------------------------------------


def _contracts(tmp_path: Path) -> dict[str, list[str]]:
    file_one = tmp_path / "one.txt"
    file_one.write_text("# host contract\nplugin_run\n\n", encoding="utf-8")
    kept = tmp_path / "kept.txt"
    kept.write_text("plugin_init\nhelper\n", encoding="utf-8")
    empty = tmp_path / "empty.txt"
    empty.write_text("# nothing\n", encoding="utf-8")
    return {
        "literal only": ["plugin_init", "plugin_run"],
        "file only": [f"@{file_one}"],
        "literal and file": ["plugin_init", f"@{file_one}"],
        "every entrypoint kept": [f"@{kept}"],
        "empty file and a literal": ["helper", f"@{empty}"],
    }


_COMPARED_FIELDS = ("required_symbol_contract", "consumer_scope", "policy", "verdict")


@pytest.mark.parametrize(
    "contract",
    [
        "literal only",
        "file only",
        "literal and file",
        "every entrypoint kept",
        "empty file and a literal",
    ],
)
@pytest.mark.parametrize("policy", [None, "strict_abi", "sdk_vendor"])
def test_typed_result_equals_the_cli_report(
    tmp_path: Path, contract: str, policy: str | None
) -> None:
    entries = _contracts(tmp_path)[contract]
    extra = ("--policy", policy) if policy else ()
    result, report = _cli(tmp_path, entries, *extra)
    assert result.exit_code in (0, 2, 4), result.output
    cli = json.loads(report.read_text(encoding="utf-8"))

    fields = (
        {"stated_settings": frozenset({"policy"}), "policy": policy} if policy else {}
    )
    typed = json.loads(to_json(_typed(tmp_path, entries, **fields).diff))

    for name in _COMPARED_FIELDS:
        assert typed.get(name) == cli.get(name), name
    # The contract really reached the result, on both routes.
    assert cli["required_symbol_contract"] is not None
    assert cli["policy"] == (policy or "plugin_abi")


def test_no_contract_leaves_the_typed_result_unscoped(tmp_path: Path) -> None:
    """The negative control: without the key, neither route records a
    contract, and the typed policy stays the default."""
    old, new = _snapshots(tmp_path)
    typed = json.loads(
        to_json(
            run_compare_request(
                CompareRequest(old=InputSpec(path=old), new=InputSpec(path=new))
            ).diff
        )
    )
    report = tmp_path / "cli.json"
    cli_run = CliRunner().invoke(
        main, ["compare", str(old), str(new), "-o", f"json={report}"]
    )
    assert cli_run.exit_code in (0, 2, 4), cli_run.output
    cli = json.loads(report.read_text(encoding="utf-8"))
    for name in _COMPARED_FIELDS:
        assert typed.get(name) == cli.get(name), name
    assert typed.get("required_symbol_contract") is None
    assert typed["policy"] == "strict_abi"
