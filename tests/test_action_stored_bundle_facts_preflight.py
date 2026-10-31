"""The Action's pre-install check of a stored BundleFacts OLD side's formats.

The bug class: ``action/validate-inputs.sh`` checks every ``format`` value
against the CLI's own generated choice sets before the multi-minute
dependency install -- except one. A stored ``BundleFacts`` OLD side renders
only ``STORED_RELEASE_FORMATS``, and deciding that an operand *is* such a
document needs the CLI's content classifier, so ``format: sarif`` on that
shape passed preflight and failed in the CLI after install (known gap "A
stored-BundleFacts baseline's narrower format set is not pre-checked by the
Action", closed 2026-10-31).

The check now runs the CLI's own classifier from the Action's source tree
(``action/stored_bundle_facts_probe.py``), so there is no second copy of it.
The oracle below is the CLI itself: for every operand shape and every
``format`` the Action offers, the preflight rejects exactly when ``abicheck
compare`` rejects with its stored-format usage error -- not the classifier
these tests would otherwise be grading against itself.
"""

from __future__ import annotations

import gzip
import importlib.util
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from _workflow_exec import bash_executable, require_bash
from click.testing import CliRunner
from test_action_validate_inputs import _VALIDATOR_INPUT_VARS

from abicheck.cli import main
from abicheck.model import AbiSnapshot
from abicheck.serialization import save_bundle_facts, snapshot_to_json
from abicheck.workflows.bundle_facts_capture import capture_bundle_facts

REPO = Path(__file__).resolve().parents[1]
VALIDATE_SH = REPO / "action" / "validate-inputs.sh"
PROBE = REPO / "action" / "stored_bundle_facts_probe.py"

#: The values the Action's ``format`` input offers (action.yml).
_ACTION_FORMATS = ("json", "markdown", "sarif", "html", "junit", "review", "oneline")


def _stored(path: Path, **kwargs: str) -> Path:
    save_bundle_facts(capture_bundle_facts({}), path, **kwargs)
    return path


def _operands(root: Path) -> dict[str, Path]:
    """Every shape the OLD side can take, stored or not."""
    root.mkdir(parents=True, exist_ok=True)
    snapshot = root / "snapshot.json"
    snapshot.write_text(
        snapshot_to_json(AbiSnapshot(library="liba.so", version="1")),
        encoding="utf-8",
    )
    snapshot_gz = root / "snapshot.json.gz"
    snapshot_gz.write_bytes(gzip.compress(snapshot.read_bytes()))
    text = root / "notes.txt"
    text.write_text("not a document\n", encoding="utf-8")
    wheel = root / "pkg-1.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as zf:
        zf.writestr("pkg/__init__.py", "")
    return {
        "stored-json": _stored(root / "facts.json"),
        "stored-gzip": _stored(root / "facts.json.gz", compression="gzip"),
        "stored-archive": _stored(root / "facts.bundlefacts.zip", format="archive"),
        "stored-no-suffix": _stored(root / "baseline"),
        "snapshot": snapshot,
        "snapshot-gzip": snapshot_gz,
        "text": text,
        "wheel": wheel,
    }


def _new_release(root: Path) -> Path:
    new = root / "new"
    new.mkdir(parents=True, exist_ok=True)
    (new / "liba.json").write_text(
        snapshot_to_json(AbiSnapshot(library="liba.so", version="2")),
        encoding="utf-8",
    )
    return new


def _validate(env_extra: dict[str, str], cwd: Path) -> subprocess.CompletedProcess[str]:
    require_bash()
    env = os.environ.copy()
    for name in _VALIDATOR_INPUT_VARS:
        env.pop(name, None)
    env.update(env_extra)
    return subprocess.run(
        [bash_executable(), str(VALIDATE_SH)],
        capture_output=True,
        text=True,
        env=env,
        cwd=cwd,
        check=False,
    )


def _cli_refuses_as_stored(old: Path, new: Path, fmt: str, out: Path) -> bool:
    result = CliRunner().invoke(
        main, ["compare", str(old), str(new), "-o", f"{fmt}={out}"]
    )
    refused = result.exit_code == 64 and "stored-bundle-facts" in result.output
    assert refused or result.exit_code != 64, result.output
    return refused


@pytest.fixture(scope="module")
def shapes(tmp_path_factory: pytest.TempPathFactory) -> tuple[dict[str, Path], Path]:
    root = tmp_path_factory.mktemp("stored-preflight")
    return _operands(root / "old"), _new_release(root)


@pytest.mark.parametrize("fmt", _ACTION_FORMATS)
@pytest.mark.parametrize(
    "shape",
    [
        "stored-json",
        "stored-gzip",
        "stored-archive",
        "stored-no-suffix",
        "snapshot",
        "snapshot-gzip",
        "text",
        "wheel",
    ],
)
@pytest.mark.parametrize("via", ["old-library", "abi-baseline"])
def test_preflight_refuses_exactly_what_the_cli_refuses(
    shapes: tuple[dict[str, Path], Path],
    tmp_path: Path,
    shape: str,
    fmt: str,
    via: str,
) -> None:
    operands, new = shapes
    old = operands[shape]
    cli_refuses = _cli_refuses_as_stored(old, new, fmt, tmp_path / f"out.{fmt}")
    side = {"INPUT_OLD_LIBRARY": str(old)}
    if via == "abi-baseline":
        side = {"INPUT_ABI_BASELINE": str(old)}
    result = _validate(
        {
            "INPUT_MODE": "compare",
            "INPUT_FORMAT": fmt,
            "INPUT_NEW_LIBRARY": str(new),
            **side,
        },
        cwd=tmp_path,
    )
    preflight_refuses = result.returncode != 0
    assert preflight_refuses == cli_refuses, result.stdout + result.stderr
    if preflight_refuses:
        assert "stored BundleFacts old side" in result.stdout
        for allowed in ("json", "markdown", "junit", "oneline", "html"):
            assert allowed in result.stdout


def test_a_stored_old_side_is_refused_before_any_release_fan_out(
    shapes: tuple[dict[str, Path], Path], tmp_path: Path
) -> None:
    # A single-file NEW side: the CLI still narrows by the OLD side alone.
    operands, new = shapes
    result = _validate(
        {
            "INPUT_MODE": "compare",
            "INPUT_FORMAT": "sarif",
            "INPUT_OLD_LIBRARY": str(operands["stored-json"]),
            "INPUT_NEW_LIBRARY": str(new / "liba.json"),
        },
        cwd=tmp_path,
    )
    assert result.returncode == 1
    assert "stored BundleFacts old side" in result.stdout


def test_the_untrusted_checkout_cannot_shadow_the_classifier(
    shapes: tuple[dict[str, Path], Path], tmp_path: Path
) -> None:
    """The validator runs with the PR checkout as its working directory. A
    planted ``sitecustomize.py`` or ``abicheck/`` package there must never be
    imported: ``-I`` keeps the CWD off ``sys.path`` and skips site hooks,
    and the probe adds only the Action's own tree."""
    operands, new = shapes
    sentinel = tmp_path / "pwned"
    payload = f"open({str(sentinel)!r}, 'w').write('x')\n"
    (tmp_path / "sitecustomize.py").write_text(payload, encoding="utf-8")
    shadow = tmp_path / "abicheck"
    (shadow / "workflows").mkdir(parents=True)
    (shadow / "__init__.py").write_text(payload, encoding="utf-8")
    (shadow / "workflows" / "__init__.py").write_text(payload, encoding="utf-8")
    result = _validate(
        {
            "INPUT_MODE": "compare",
            "INPUT_FORMAT": "review",
            "INPUT_OLD_LIBRARY": str(operands["stored-json"]),
            "INPUT_NEW_LIBRARY": str(new),
        },
        cwd=tmp_path,
    )
    assert not sentinel.exists()
    assert result.returncode == 1
    assert "stored BundleFacts old side" in result.stdout


@pytest.mark.parametrize("shape", ["stored-json", "stored-archive", "snapshot", "text"])
def test_the_probe_needs_no_site_packages(
    shapes: tuple[dict[str, Path], Path], shape: str
) -> None:
    """The classifier's import closure is the standard library, which is what
    lets it run before ``pip install``: ``-S`` drops site-packages entirely."""
    operands, _new = shapes
    proc = subprocess.run(
        [sys.executable, "-I", "-S", str(PROBE), str(REPO), str(operands[shape])],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == (0 if shape.startswith("stored") else 1), proc.stderr


def _probe_module():
    spec = importlib.util.spec_from_file_location("_stored_probe", PROBE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_an_interpreter_below_the_package_floor_cannot_decide(
    shapes: tuple[dict[str, Path], Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    probe = _probe_module()
    operands, _new = shapes
    argv = ["probe", str(REPO), str(operands["stored-json"])]
    assert probe.main(argv) == 0
    monkeypatch.setattr(probe, "_MIN_PYTHON", (sys.version_info[0] + 1, 0))
    assert probe.main(argv) == 2


def test_an_unimportable_tree_cannot_decide_and_the_preflight_defers(
    shapes: tuple[dict[str, Path], Path], tmp_path: Path
) -> None:
    """Exit 2 means "unknown": the preflight lets the run through and the CLI
    enforces the narrowing after install, never a guessed refusal."""
    operands, _new = shapes
    proc = subprocess.run(
        [sys.executable, "-I", str(PROBE), str(tmp_path), str(operands["stored-json"])],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 2
    assert _probe_module().main(["probe"]) == 2


def test_the_probe_answers_like_the_cli_routing(
    shapes: tuple[dict[str, Path], Path],
) -> None:
    from abicheck.workflows.bundle_compare_operand import (
        looks_like_stored_bundle_facts,
    )

    probe = _probe_module()
    operands, new = shapes
    for path in [*operands.values(), new]:
        expected = 0 if looks_like_stored_bundle_facts(path) else 1
        assert probe.main(["probe", str(REPO), str(path)]) == expected, path


def test_the_classifier_import_closure_is_the_standard_library() -> None:
    """The pin the probe relies on: importing the classifier loads nothing
    but the standard library and ``abicheck`` itself. A heavy import added
    to that closure fails here, in CI, rather than turning every Action run
    into a silent "cannot decide"."""
    code = (
        "import sys\n"
        f"sys.path.insert(0, {str(REPO)!r})\n"
        "before = set(sys.modules)\n"
        "import abicheck.workflows.bundle_compare_operand\n"
        "loaded = {m.split('.')[0] for m in set(sys.modules) - before}\n"
        "extra = sorted(loaded - set(sys.stdlib_module_names) - {'abicheck'})\n"
        "print(','.join(extra))\n"
    )
    proc = subprocess.run(
        [sys.executable, "-I", "-S", "-c", code],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "", f"non-stdlib imports: {proc.stdout.strip()}"
