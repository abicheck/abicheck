"""ADR-062 D2 enforcement: a change to extraction or resolution semantics must
come with a generation bump.

Bug class: ``EXTRACTOR_GENERATION``/``RESOLVER_GENERATION`` were bumped by hand,
so a change to what an extractor records, or what a resolver derives, shipped
under the old generation and a stored package read as if it were today's
answer. This test records a content fingerprint of the scoped modules next to
the generation it was recorded at. A semantic edit changes the fingerprint; the
test then fails until the author either bumps the generation or deliberately
re-records the fingerprint with a stated reason (see
``scripts/record_generation_fingerprints.py``).

The oracle is ``hashlib`` over the scoped files, written out here rather than
taken from ``generation_scope``'s own helpers.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from abicheck.storage import generation_scope as scope, versioning

REPO_ROOT = scope.REPO_ROOT


def _recorded() -> dict:
    """The committed fingerprint record."""
    return json.loads(scope.RECORD_PATH.read_text(encoding="utf-8"))


def _live(axis: str) -> dict[str, str]:
    """The current per-file digests of *axis*'s scope."""
    return scope.file_digests(scope.scope_files(axis))


def _fix_hint(axis: str) -> str:
    """The two re-record commands a failing author needs."""
    return (
        f"\n  If extraction/resolution semantics changed: bump the {axis.upper()}_"
        f"GENERATION constant in abicheck/storage/versioning.py, then run\n"
        f"    python scripts/record_generation_fingerprints.py --axis {axis} --bump\n"
        "  If the edit does NOT change what is recorded or derived, re-record "
        "without a bump and say why:\n"
        f"    python scripts/record_generation_fingerprints.py --axis {axis} "
        '--no-semantic-change --reason "..."'
    )


@pytest.mark.parametrize("axis", ["extractor", "resolver", "source"])
def test_recorded_fingerprint_matches_the_scoped_source(axis: str) -> None:
    """A scoped edit without a re-record fails with the changed files named."""
    recorded = _recorded()[axis]
    live = _live(axis)
    if recorded["files"] == live:
        return
    before, after = recorded["files"], live
    changed = sorted(
        p for p in set(before) | set(after) if before.get(p) != after.get(p)
    )
    pytest.fail(
        f"{axis} scope changed since its fingerprint was recorded "
        f"(recorded at generation {recorded['generation']}). Changed files:\n  "
        + "\n  ".join(changed)
        + _fix_hint(axis)
    )


@pytest.mark.parametrize("axis", ["extractor", "resolver", "source"])
def test_recorded_generation_is_the_constant_in_versioning(axis: str) -> None:
    """The recorded generation equals the versioning constant."""
    constant = getattr(versioning, f"{axis.upper()}_GENERATION")
    recorded = _recorded()[axis]["generation"]
    assert recorded == constant, (
        f"versioning.{axis.upper()}_GENERATION is {constant} but the fingerprint "
        f"was recorded at {recorded}. Re-record it with "
        f"scripts/record_generation_fingerprints.py --axis {axis} (--bump or "
        "--no-semantic-change)."
    )


def test_every_resolver_named_module_is_classified_exactly_once() -> None:
    """A new resolver/reachability/alias/provider module cannot be silently
    left out of the fingerprint: it must be listed as semantic, or listed as
    non-semantic with a reason."""
    package = REPO_ROOT / "abicheck"
    extract_root = package / "extract"
    named = {
        p.relative_to(REPO_ROOT).as_posix()
        for p in package.rglob("*.py")
        if "__pycache__" not in p.parts
        and any(word in p.stem for word in scope.RESOLVER_NAME_WORDS)
        and extract_root not in p.parents
    }
    semantic = set(scope.RESOLVER_SEMANTIC_FILES)
    non_semantic = set(scope.RESOLVER_NON_SEMANTIC_FILES)

    assert not semantic & non_semantic, sorted(semantic & non_semantic)
    assert named <= semantic | non_semantic, sorted(named - (semantic | non_semantic))
    assert not {
        p for p in semantic | non_semantic if p.startswith("abicheck/extract/")
    }, "extract/ files belong to the extractor scope, not the resolver lists"
    for path in semantic | non_semantic:
        assert (REPO_ROOT / path).is_file(), f"listed but missing: {path}"
    for path, reason in scope.RESOLVER_NON_SEMANTIC_FILES.items():
        assert reason.strip(), f"empty reason for {path}"


def test_extractor_scope_covers_every_extract_module() -> None:
    """Every ``extract/`` module is in scope, so a new one cannot be missed."""
    files = scope.scope_files("extractor")
    on_disk = sorted(
        p.relative_to(REPO_ROOT).as_posix()
        for p in (REPO_ROOT / "abicheck" / "extract").rglob("*.py")
        if "__pycache__" not in p.parts
    )
    assert set(on_disk) <= set(files), sorted(set(on_disk) - set(files))


def test_source_scope_covers_every_source_extractor_module() -> None:
    """Every L4 backend module is in the source scope, so a new one cannot be missed."""
    files = set(scope.scope_files("source"))
    on_disk = {
        p.relative_to(REPO_ROOT).as_posix()
        for p in (REPO_ROOT / scope.SOURCE_EXTRACTOR_DIR).rglob("*.py")
        if "__pycache__" not in p.parts
    }
    assert on_disk, "source_extractors/ has no modules; the scope path is wrong"
    # The resolver axis owns its listed modules, so they are the one exception.
    owned_by_resolver = on_disk & set(scope.RESOLVER_SEMANTIC_FILES)
    assert on_disk - owned_by_resolver <= files, sorted(
        (on_disk - owned_by_resolver) - files
    )
    assert owned_by_resolver == {"abicheck/buildsource/source_extractors/resolver.py"}


def test_source_scope_names_only_existing_files() -> None:
    """A listed L4/L5 producer that was renamed or deleted is a scope error."""
    for path in scope.SOURCE_TIER_FILES:
        assert (REPO_ROOT / path).is_file(), f"listed but missing: {path}"
    assert len(set(scope.SOURCE_TIER_FILES)) == len(scope.SOURCE_TIER_FILES)


def test_source_axis_is_disjoint_from_extractor_and_resolver() -> None:
    """An L4/L5 edit must not move the extractor axis, and the reverse.

    Otherwise a source-tier change would drift every stored L2 package on the
    extractor axis, which is the false drift ADR-062 D2's source amendment
    exists to prevent.
    """
    source = set(scope.scope_files("source"))
    extractor = set(scope.scope_files("extractor"))
    resolver = set(scope.scope_files("resolver"))
    assert not source & extractor, sorted(source & extractor)
    assert not source & resolver, sorted(source & resolver)
    assert not any(p.startswith("abicheck/buildsource/") for p in extractor)


def test_fingerprint_moves_for_any_scoped_edit_and_not_for_an_unscoped_one() -> None:
    """Oracle: a sha256 over path + content, computed here directly."""
    axis_files = scope.scope_files("resolver")
    sample = [axis_files[0], axis_files[-1]]
    base = scope.file_digests(axis_files)

    def oracle(digests: dict[str, str]) -> str:
        """Fingerprint over *digests* computed independently of the implementation."""
        h = hashlib.sha256(b"abicheck-generation-fingerprint-v1\0")
        for path in sorted(digests):
            h.update(path.encode() + b"\0" + digests[path].encode() + b"\0")
        return h.hexdigest()

    assert scope.fingerprint(base) == oracle(base)
    for path in sample:
        edited = dict(base)
        edited[path] = hashlib.sha256(b"edited " + path.encode()).hexdigest()
        assert scope.fingerprint(edited) != scope.fingerprint(base), path
    # A file outside every scope never enters the digest set.
    unscoped = REPO_ROOT / "abicheck" / "report" / "render_markdown.py"
    assert unscoped.as_posix().replace(REPO_ROOT.as_posix() + "/", "") not in base


def test_record_file_is_present_and_well_formed() -> None:
    """Each record carries its fields, a consistent fingerprint and repo-relative paths."""
    data = _recorded()
    for axis in ("extractor", "resolver", "source"):
        entry = data[axis]
        assert set(entry) >= {"generation", "fingerprint", "files", "reason"}
        assert entry["fingerprint"] == scope.fingerprint(entry["files"])
        assert isinstance(entry["files"], dict) and entry["files"]
        # Stored paths are repo-relative, never absolute.
        assert all(not Path(p).is_absolute() for p in entry["files"])


@pytest.mark.parametrize(
    "raw",
    [
        b"def f():\n    return 1\n",
        b"def f():\r\n    return 1\r\n",
        b"def f():\r\n    return 1\n",
        b"x = '\r'\n",  # a lone CR is content, not a line ending
    ],
)
def test_digest_is_the_same_for_any_checkout_line_endings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, raw: bytes
) -> None:
    """Oracle: sha256 of the LF-normalized bytes, computed here directly.

    A Windows checkout with autocrlf rewrites every LF to CRLF. The recorded
    fingerprint must not move for that, or the gate reports drift that is not
    a semantic change.
    """
    monkeypatch.setattr(scope, "REPO_ROOT", tmp_path)
    (tmp_path / "mod.py").write_bytes(raw)
    lf = raw.replace(b"\r\n", b"\n")
    expected = hashlib.sha256(lf).hexdigest()
    assert scope.file_digests(["mod.py"]) == {"mod.py": expected}


def test_unknown_generation_axis_is_refused() -> None:
    """An axis name that is not one of the three is an error, not an empty scope."""
    with pytest.raises(ValueError, match="unknown generation axis 'bogus'"):
        scope.scope_files("bogus")


def test_a_missing_scoped_file_is_recorded_as_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A deleted or renamed scoped module is a recorded change, not a dropped file."""
    monkeypatch.setattr(scope, "REPO_ROOT", tmp_path)
    (tmp_path / "present.py").write_bytes(b"x = 1\n")
    digests = scope.file_digests(["present.py", "gone.py"])
    assert digests["gone.py"] == "MISSING"
    assert digests["present.py"] == hashlib.sha256(b"x = 1\n").hexdigest()


def test_load_record_is_empty_before_the_first_recording(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(scope, "RECORD_PATH", tmp_path / "generation_fingerprints.json")
    assert scope.load_record() == {}


def test_write_record_round_trips_through_load_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "generation_fingerprints.json"
    monkeypatch.setattr(scope, "RECORD_PATH", path)
    scope.write_record({"source": {"generation": 1}})
    assert scope.load_record() == {"source": {"generation": 1}}
    assert path.read_text(encoding="utf-8").endswith("\n")
