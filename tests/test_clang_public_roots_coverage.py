"""Coverage-focused unit tests for the clang public-root helpers.

Exercises the *pure* halves of
``abicheck.buildsource.source_extractors.clang_public_roots`` (no clang
invocation) with synthetic inputs: crafted ``CompileUnit`` argv for the
include-search flag scanner, small on-disk header trees for the sampler, and
hand-built path lists for the mirror/strip helpers. Each test asserts the
concrete returned value, not merely that the code ran.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from abicheck.buildsource.build_evidence import CompileUnit
from abicheck.buildsource.source_extractors import clang_public_roots as m

# -- _header_samples: filesystem sampling (lines 94, 97) ---------------------


def test_header_samples_skips_non_header_files(tmp_path: Path) -> None:
    """A non-header file in the tree is filtered out; only headers are sampled."""
    (tmp_path / "api.h").write_text("int a;\n")
    (tmp_path / "notes.txt").write_text("not a header\n")
    (tmp_path / "data.bin").write_bytes(b"\x00\x01")

    is_file_root, samples = m._header_samples(str(tmp_path))

    assert is_file_root is False
    # The .txt / .bin files hit the ``continue`` (line 94) and are excluded.
    assert samples == [Path("api.h")]


def test_header_samples_stops_at_sample_limit(tmp_path: Path) -> None:
    """More than the sample cap of headers returns exactly the cap, is_file_root False."""
    total = m._PUBLIC_ROOT_SAMPLE_LIMIT + 5
    for i in range(total):
        (tmp_path / f"h{i:04d}.h").write_text("int x;\n")

    is_file_root, samples = m._header_samples(str(tmp_path))

    # Early ``return False, out`` (line 97) once the cap is reached.
    assert is_file_root is False
    assert len(samples) == m._PUBLIC_ROOT_SAMPLE_LIMIT
    # os.walk sorts filenames, so the first cap-many sorted names are kept.
    assert samples[0] == Path("h0000.h")
    assert Path(f"h{total - 1:04d}.h") not in samples


# -- _compile_unit_include_roots: flag scanning (lines 140-144, 148, 153-156)


def test_include_roots_gnu_joined_and_nonmatching_flag() -> None:
    """GNU joined -iquote/-idirafter parse (140-144) and a non-matching flag (else, 156)."""
    cu = CompileUnit(
        id="cu://x",
        argv=[
            "g++",
            "-iquote/inc/q",
            "-idirafter/inc/d",
            "-fvisibility=hidden",
        ],
        language="CXX",
        # Carried through into replay_flags; it matches no include family, so it
        # falls through to the terminal ``else: i += 1`` (line 156).
        abi_relevant_flags=["-fvisibility=hidden"],
    )

    roots = m._compile_unit_include_roots(cu)
    raws = [raw for raw, _ in roots]

    assert "/inc/q" in raws  # joined -iquote<dir> (lines 139-141)
    assert "/inc/d" in raws  # joined -idirafter<dir> (lines 142-144)
    # The non-include flag contributes no include root.
    assert "-fvisibility=hidden" not in raws


def test_include_roots_gnu_separate_iquote() -> None:
    """Separate-operand -iquote <dir> spelling (lines 136-138)."""
    cu = CompileUnit(
        id="cu://sep",
        argv=["gcc", "-iquote", "/inc/sep"],
        language="C",
    )

    raws = [raw for raw, _ in m._compile_unit_include_roots(cu)]

    assert "/inc/sep" in raws


def test_include_roots_msvc_joined_and_separate() -> None:
    """MSVC /I dir (145-147) and joined /Idir (148, 153-154) under cl.exe mode."""
    cu = CompileUnit(
        id="cu://y",
        argv=["cl.exe", "/I", "sep\\dir", "/Ijoin\\dir"],
        language="CXX",
    )

    raws = [raw for raw, _ in m._compile_unit_include_roots(cu)]

    assert "sep\\dir" in raws  # /I <dir> separate operand (lines 145-147)
    assert "join\\dir" in raws  # /Idir joined (lines 148, 153-154)


def test_include_roots_msvc_flag_ignored_in_gnu_mode() -> None:
    """A /I flag is NOT treated as an include dir when the compiler is GNU."""
    cu = CompileUnit(
        id="cu://gnu-noimsvc",
        argv=["gcc", "/Ishould-not-parse"],
        language="C",
    )

    raws = [raw for raw, _ in m._compile_unit_include_roots(cu)]

    # replay_extra_flags only carries /I in MSVC mode, so nothing is added here.
    assert "should-not-parse" not in raws


# -- _strip_leading_sample_dir (lines 209-211) -------------------------------


def test_strip_leading_sample_dir_drops_single_segment_entries() -> None:
    """Single-segment samples are dropped (line 210 continue); deeper ones stripped."""
    stripped = m._strip_leading_sample_dir(
        [Path("pkg/api.h"), Path("top.h"), Path("a/b/c.h")]
    )

    # "top.h" has one part -> skipped; the others lose their leading dir.
    assert stripped == [Path("api.h"), Path("b/c.h")]


def test_strip_leading_sample_dir_all_single_segment_is_empty() -> None:
    """A list of only single-segment names strips to empty."""
    assert m._strip_leading_sample_dir([Path("only.h")]) == []


# -- _mirror_dir_candidate (line 221) ----------------------------------------


def test_mirror_dir_candidate_cache_with_prefix() -> None:
    """for_cache=True with a prefix returns the joined absolute cache key (line 221)."""
    result = m._mirror_dir_candidate(
        "inc", Path("/build/inc"), Path("api"), for_cache=True
    )

    assert result == str(Path("/build/inc") / "api")


def test_mirror_dir_candidate_cache_without_prefix() -> None:
    """for_cache=True, prefix=None returns the plain include dir (line 219)."""
    result = m._mirror_dir_candidate(
        "inc", Path("/build/inc"), None, for_cache=True
    )

    assert result == str(Path("/build/inc"))


# -- _mirror_candidates: the empty-matched guard (documents line 293) --------


def test_mirror_candidates_empty_matched_returns_empty() -> None:
    """No matched relative headers -> no equivalent-root spellings.

    With ``matched`` empty, ``_can_promote_whole_root`` is False (it needs >= 2
    matches), so the guard at line 289-290 returns ``[]`` before the terminal
    ``if matched:`` check. The final ``return []`` (line 293) is only reachable
    with a matched list that is both promotable (len >= 2) and empty, which is a
    contradiction -- it is defensive/dead. This test pins the reachable
    empty-result path.
    """
    out = m._mirror_candidates(
        "inc",
        Path("/build/inc"),
        samples=[Path("a.h"), Path("b.h")],
        matched=[],
        prefix=None,
        is_file_root=False,
        for_cache=False,
    )

    assert out == []


def test_mirror_candidates_promotes_whole_dir_when_enough_matches() -> None:
    """Two matched headers under a real dir yield a single whole-dir spelling (line 292)."""
    out = m._mirror_candidates(
        "inc",
        Path("/build/inc"),
        samples=[Path("a.h"), Path("b.h"), Path("c.h")],
        matched=[Path("a.h"), Path("b.h")],
        prefix=None,
        is_file_root=False,
        for_cache=False,
    )

    assert out == [m._dir_spelling(Path("inc"))]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
