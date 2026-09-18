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

"""``_pyelftools_exported_symbols`` must resolve names through the buffer.

**Bug class.** A performance helper lands, its documentation says the
expensive path now uses it, and a *second* path doing the same expensive
thing is never connected -- so the optimization is believed complete while
the reported hotspot still pays the original cost. Here
``elf_metadata``'s dynamic-symbol walk was wrapped in
``buffered_string_table`` while ``dumper_elf_symbols._extract_symbols``,
which builds its own ``ELFFile`` and walks **both** ``.dynsym`` and
``.symtab``, kept resolving every ``Symbol.name`` with one seek-and-read
per name.

**General invariant**, stated so it cannot regress for either table or for
a malformed input: for *any* ELF this function accepts, the result with
buffering engaged equals the result with buffering disabled, and the
buffer is observed to engage for every symbol section actually walked.
The equivalence half is asserted against a control that disables the
mechanism rather than against a recorded expectation, so it holds for
inputs nobody enumerated; the engagement half exists because an
equivalence test whose mechanism silently no-ops compares the slow path
with itself and passes no matter what (AGENTS.md, "A differential test
must prove both of its configurations actually ran").
"""

from __future__ import annotations

import contextlib
import pathlib
import shutil
import subprocess

import pytest

from abicheck.dumper_elf_symbols import _pyelftools_exported_symbols
from abicheck.errors import SnapshotError

_ELF_MAGIC = b"\x7fELF"


def _build_so(tmp_path, names, *, hidden=(), static_only=(), weak=(), absolute=()):
    """A real **ELF** shared object exporting *names*, or ``None``.

    ``None`` covers two genuinely absent capabilities -- no compiler, and a
    compiler that builds but does not emit ELF (macOS emits Mach-O, Windows
    PE) -- which the caller turns into a skip. A compiler that ran and
    *failed* is neither: that is a broken fixture or toolchain and must
    surface with its own details rather than vanish as a skip (bug class
    ``guard.absent_capability_vs_real_failure``), which is what the
    ``returncode`` branch below is for. Mirrors the same two-way guard in
    ``tests/test_elf_string_table.py``'s ``_minimal_so``.
    """
    cc = shutil.which("gcc") or shutil.which("cc") or shutil.which("clang")
    if cc is None:
        return None
    body = []
    for n in names:
        body.append(f"int {n}(int a){{return a+{len(n)};}}")
    for n in hidden:
        body.append(
            f'__attribute__((visibility("hidden"))) int {n}(int a){{return a;}}'
        )
    for n in weak:
        # A *weak* global export. Without one, a mutation that drops
        # `STB_WEAK` from the binding filter changes no result and the
        # differential below cannot see it -- which mutation testing
        # showed was the case before these were added.
        body.append(f"__attribute__((weak)) int {n}(int a){{return a*2;}}")
    for n in absolute:
        # A *global* absolute symbol. The reserved-index filter is only
        # observable through one: the `SHN_ABS` entries gcc emits on its
        # own (`crtstuff.c`, the file symbols) are all `STB_LOCAL`, so the
        # binding filter excludes them first and a mutation deleting
        # `SHN_ABS` from the skip set changes nothing. Mutation testing
        # showed exactly that before this was added.
        body.append(f'__asm__(".globl {n}\\n.set {n}, 0x1234\\n");')
    for n in static_only:
        body.append(f"static int {n}(int a){{return a;}}")
        # Keep the static alive so it reaches .symtab.
        body.append(f"int use_{n}(int a){{return {n}(a);}}")
    src = tmp_path / "x.c"
    src.write_text("\n".join(body) + "\n")
    so = tmp_path / "libx.so"
    cmd = [cc, "-shared", "-fPIC", "-g", "-o", str(so), str(src)]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        from tests.test_cross_platform_integration import _require_compile_success

        _require_compile_success(
            "cc", cmd, src.read_text(), proc, optional_feature=None
        )
    if not so.exists():
        return None
    with open(so, "rb") as fh:
        if fh.read(4) != _ELF_MAGIC:
            # Mach-O or PE: this host does not build ELF, which *is* an
            # absent capability for a test about ELF symbol tables.
            return None
    return so


@contextlib.contextmanager
def _fast_path_disabled(monkeypatch):
    """Control configuration: the bulk decoder present but always declining.

    This is how the *legacy* `iter_symbols` loop is reached at all. Once
    the fast path handles every well-formed table on this host, nothing
    else executes that loop -- and it is the fallback the whole
    "unsupported tables keep pyelftools' behaviour" argument rests on, so
    leaving it unexecuted would mean the safety net is never tested.
    """

    def declines(_section, _elffile):
        return None

    monkeypatch.setattr(
        "abicheck.extract.elf_symbol_fastpath.iter_symbol_fields", declines
    )
    yield


@contextlib.contextmanager
def _buffering_disabled(monkeypatch):
    """Control configuration: the helper present but never engaging."""

    @contextlib.contextmanager
    def never(_strtab):
        yield False

    monkeypatch.setattr(
        "abicheck.extract.elf_string_table.buffered_string_table", never
    )
    yield


class _EngagementSpy:
    """Wraps the real helper and records what it was handed, and whether it took."""

    def __init__(self, real):
        self._real = real
        self.engagements = 0
        self.calls = 0
        self.sections = []

    @contextlib.contextmanager
    def __call__(self, strtab):
        self.calls += 1
        self.sections.append(getattr(strtab, "name", None))
        with self._real(strtab) as engaged:
            if engaged:
                self.engagements += 1
            yield engaged


class TestExtractSymbolsUsesTheBuffer:
    def test_the_buffer_engages_for_every_symbol_section_walked(
        self, tmp_path, monkeypatch
    ) -> None:
        """Observe the mechanism: both tables, each through its own string table."""
        so = _build_so(tmp_path, ["alpha", "beta", "gamma_fn"], static_only=["priv"])
        if so is None:
            pytest.skip("no compiler producing an ELF shared object on this host")
        import abicheck.extract.elf_string_table as helper

        spy = _EngagementSpy(helper.buffered_string_table)
        monkeypatch.setattr(helper, "buffered_string_table", spy)

        dynamic, static = _pyelftools_exported_symbols(so)

        # .dynsym and .symtab -- two walks, two engagements.
        assert spy.calls == 2, spy.sections
        assert spy.engagements == 2, spy.sections
        # Each section resolved through its *own* associated table, not one
        # looked up by name: .dynsym -> .dynstr, .symtab -> .strtab.
        assert spy.sections == [".dynstr", ".strtab"], spy.sections
        # Vacuity guard: the walk actually recovered this fixture's exports.
        assert {"alpha", "beta", "gamma_fn"} <= dynamic
        assert {"alpha", "beta", "gamma_fn"} <= static

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"names": ["alpha", "beta"]},
            {"names": ["only_one"]},
            {"names": ["a", "bb", "ccc", "dddd"], "hidden": ["secret_fn"]},
            {"names": ["pub"], "static_only": ["priv_a", "priv_b"]},
            {"names": [f"sym_{i:03d}" for i in range(200)]},
            {"names": ["x" * 180, "y" * 40]},
        ],
        ids=["pair", "single", "hidden", "static", "many", "long-names"],
    )
    def test_buffered_result_equals_unbuffered_result(
        self, tmp_path, monkeypatch, kwargs
    ) -> None:
        """The invariant, over sibling shapes -- not just the reported input.

        Hidden-visibility, static-only, many-symbol and long-name cases are
        each independently chosen: the filtering this function performs
        (``SHN_UNDEF``/``SHN_ABS``, binding, visibility, ABI relevance) must
        be unchanged by where names are read from.
        """
        so = _build_so(tmp_path, **kwargs)
        if so is None:
            pytest.skip("no compiler producing an ELF shared object on this host")

        buffered = _pyelftools_exported_symbols(so)
        with _buffering_disabled(monkeypatch):
            unbuffered = _pyelftools_exported_symbols(so)

        assert buffered == unbuffered
        # Vacuity guard: an implementation returning empty sets would pass
        # the equality above against itself.
        assert buffered[0], "fixture exported nothing; equality proves nothing"
        for hidden_name in kwargs.get("hidden", ()):
            assert hidden_name not in buffered[0]

    def test_truncated_input_behaviour_is_unchanged(
        self, tmp_path, monkeypatch
    ) -> None:
        """Malformed input must fail the same way with and without the buffer.

        The helper falls through to pyelftools for a table it cannot read
        whole, so a truncated file must not start succeeding, and must not
        start raising a different error.
        """
        so = _build_so(tmp_path, ["alpha", "beta"])
        if so is None:
            pytest.skip("no compiler producing an ELF shared object on this host")
        blob = so.read_bytes()
        truncated = tmp_path / "libtrunc.so"
        truncated.write_bytes(blob[: len(blob) // 3])

        buffered_exc = None
        try:
            buffered_result = _pyelftools_exported_symbols(truncated)
        except SnapshotError as exc:
            buffered_exc, buffered_result = type(exc), None

        with _buffering_disabled(monkeypatch):
            unbuffered_exc = None
            try:
                unbuffered_result = _pyelftools_exported_symbols(truncated)
            except SnapshotError as exc:
                unbuffered_exc, unbuffered_result = type(exc), None

        assert buffered_exc == unbuffered_exc
        assert buffered_result == unbuffered_result


class TestTheLegacyLoopStillAgrees:
    """The fallback must produce the same answer as the fast path.

    **Bug class.** A fast path is added with a fallback for what it
    cannot decode; the fast path then handles every input the tests use,
    so the fallback -- the thing that makes the change safe -- silently
    stops being executed at all. A defect in it is then invisible until
    a user hits an ELF variant the fast path declines, which is by
    construction the unusual, hard-to-debug case.

    **General invariant**: for every fixture shape, the result with the
    bulk decoder engaged equals the result with it declining, so the two
    implementations of the same filter cannot drift apart. Asserted as a
    differential against a *control configuration*, and paired with an
    observation that each configuration really ran -- otherwise this is
    the vacuous comparison AGENTS.md warns about.

    Each filter needs an input that makes it observable, which mutation
    testing (not review) established: a weak export for the binding
    filter, and a *global* absolute symbol for the reserved-index filter,
    since gcc's own `SHN_ABS` entries are all `STB_LOCAL` and the binding
    filter excludes them first. Both mutations survived before those
    fixtures existed.

    **One filter is deliberately not covered here.** The visibility check
    (`vis not in _HIDDEN_VIS`) cannot be reached through a gcc-built
    shared object at all: the linker localizes hidden symbols, which is
    what hidden visibility means for a `.so`, so no `STB_GLOBAL` or
    `STB_WEAK` symbol in such a file ever carries `STV_HIDDEN` -- probed
    directly, including via a `.hidden` assembler directive on a pure-asm
    global, which the linker still converted to `STB_LOCAL`. The fast
    path's own visibility handling is exercised exhaustively against
    pyelftools in `tests/test_elf_symbol_fastpath.py` (every binding by
    every visibility), so the decoding is covered; what is uncovered is
    the *legacy loop's* redundant copy of the same predicate, on
    pre-existing code this change did not touch.
    """

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"names": ["alpha", "beta"]},
            {"names": ["a", "bb", "ccc"], "hidden": ["secret_fn"]},
            {"names": ["pub"], "static_only": ["priv_a"]},
            {"names": [f"sym_{i:03d}" for i in range(120)]},
            # The two shapes that make the binding/visibility filters
            # observable at all. Without them a legacy loop that dropped
            # every weak symbol, or kept every hidden one, agreed with the
            # fast path on every fixture and the mutation survived.
            {"names": ["strong"], "weak": ["weak_fn", "weak_two"]},
            {"names": ["pub"], "absolute": ["abs_marker"]},
            {
                "names": ["pub"],
                "weak": ["weak_fn"],
                "hidden": ["secret_fn"],
                "static_only": ["priv_a"],
                "absolute": ["abs_marker"],
            },
        ],
        ids=["pair", "hidden", "static", "many", "weak", "absolute", "mixed"],
    )
    def test_the_two_decoders_agree(self, tmp_path, monkeypatch, kwargs) -> None:
        so = _build_so(tmp_path, **kwargs)
        if so is None:
            pytest.skip("no compiler producing an ELF shared object on this host")

        fast = _pyelftools_exported_symbols(so)
        with _fast_path_disabled(monkeypatch):
            legacy = _pyelftools_exported_symbols(so)

        assert fast == legacy
        assert fast[0], "fixture exported nothing; the equality proves nothing"
        for hidden_name in kwargs.get("hidden", ()):
            assert hidden_name not in legacy[0]
        # Vacuity guard with teeth: a weak export must actually be present
        # in what both decoders returned, or "they agree" says nothing
        # about the binding filter. `.symtab` is checked too, since that is
        # where a hidden or local symbol reaches the walk at all.
        for abs_name in kwargs.get("absolute", ()):
            assert abs_name not in fast[0] and abs_name not in legacy[0], (
                f"{abs_name} is SHN_ABS and must be skipped by both decoders"
            )
        for weak_name in kwargs.get("weak", ()):
            assert weak_name in fast[0], (
                f"{weak_name} absent from the dynamic set; this fixture does not "
                "exercise the weak-binding filter and the comparison is vacuous"
            )
            assert weak_name in legacy[0]

    def test_each_configuration_really_ran(self, tmp_path, monkeypatch) -> None:
        """Observe the mechanism, not just the agreeing output.

        Without this, a fast path that had silently stopped engaging
        would make every comparison above compare the legacy loop with
        itself.
        """
        so = _build_so(tmp_path, ["alpha", "beta"])
        if so is None:
            pytest.skip("no compiler producing an ELF shared object on this host")
        import abicheck.extract.elf_symbol_fastpath as fastpath

        calls = {"n": 0, "declined": 0}
        real = fastpath.iter_symbol_fields

        def counting(section, elffile):
            calls["n"] += 1
            result = real(section, elffile)
            if result is None:
                calls["declined"] += 1
            return result

        monkeypatch.setattr(fastpath, "iter_symbol_fields", counting)
        _pyelftools_exported_symbols(so)
        assert calls["n"] == 2, "the bulk decoder was not consulted for both tables"
        assert calls["declined"] == 0, "it declined a table it should have decoded"

    def test_a_string_table_without_an_accessor_falls_back(
        self, tmp_path, monkeypatch
    ) -> None:
        """No `get_string` to resolve names with -> the legacy loop.

        Distinct from the decoder declining: the entries decode fine, but
        there is no way to turn `st_name` into a name, so the fast path
        must hand back rather than invent one.
        """
        so = _build_so(tmp_path, ["alpha", "beta"])
        if so is None:
            pytest.skip("no compiler producing an ELF shared object on this host")

        import abicheck.extract.elf_string_table as helper

        def no_table(_section):
            return None

        monkeypatch.setattr(helper, "string_table_of", no_table)
        # Names still resolve through pyelftools' own accessor in the
        # legacy loop, so the result is unchanged.
        assert _pyelftools_exported_symbols(so)[0] >= {"alpha", "beta"}


class TestTheFixtureGuardItself:
    """The skip guard is load-bearing, so it is tested rather than trusted.

    Every test above is vacuous on a host where `_build_so` returns
    `None`. That makes the guard's two directions a correctness concern of
    their own, and exactly the `guard.absent_capability_vs_real_failure`
    class: a guard that widened to swallow real compile failures would
    turn this whole module green-and-empty everywhere, and one that
    narrowed would reintroduce the macOS/Windows failure it exists for.
    Both directions are asserted, against generated non-ELF magics rather
    than the one format that happened to break first — the original guard
    was written for "no compiler" alone and let a *successful* non-ELF
    build through on two platforms.
    """

    @pytest.mark.parametrize(
        ("magic", "format_name"),
        [
            (b"\xcf\xfa\xed\xfe", "Mach-O 64-bit LE"),
            (b"\xce\xfa\xed\xfe", "Mach-O 32-bit LE"),
            (b"\xca\xfe\xba\xbe", "Mach-O universal"),
            (b"MZ\x90\x00", "PE/COFF"),
            (b"!<ar", "static archive"),
        ],
    )
    def test_a_successful_non_elf_build_is_an_absent_capability(
        self, tmp_path, monkeypatch, magic, format_name
    ) -> None:
        """A compiler that runs, succeeds, and emits something else -> skip.

        This is the real macOS/Windows mode: `returncode` is 0 and the file
        exists, so every check the guard originally had passed.
        """
        real_run = subprocess.run

        def stamping_run(cmd, **kwargs):
            result = real_run(cmd, **kwargs)
            out = pathlib.Path(cmd[cmd.index("-o") + 1])
            if out.exists():
                blob = bytearray(out.read_bytes())
                blob[0 : len(magic)] = magic
                out.write_bytes(bytes(blob))
            return result

        if not (shutil.which("gcc") or shutil.which("cc") or shutil.which("clang")):
            # Distinguished *before* calling, deliberately: keying the skip
            # off a `None` return would make the assertion below vacuous on
            # a compiler-less host and -- worse -- would turn a genuinely
            # broken guard (one that returned the stamped path) into a skip
            # rather than a failure, which is the very masking this class
            # is about.
            pytest.skip("no compiler on this host")
        monkeypatch.setattr(subprocess, "run", stamping_run)
        built = _build_so(tmp_path, ["alpha", "beta"])
        assert built is None, (
            f"a successful build emitting {format_name} was not treated as "
            "an absent capability; the ELF-magic guard did not engage"
        )

    def test_an_elf_build_is_not_skipped(self, tmp_path) -> None:
        """Vacuity guard: the guard must not reject a *genuine* ELF.

        Whether this host emits ELF is established independently -- by
        compiling one directly and reading its magic -- rather than by
        asking `_build_so`, because on that route a guard that returned
        `None` unconditionally would skip here too and silently reduce
        every test in this module to a no-op. That exact mutation
        (`if True:` in place of the magic comparison) leaves the rest of
        the file green, so this is the only assertion that catches it.
        """
        cc = shutil.which("gcc") or shutil.which("cc") or shutil.which("clang")
        if cc is None:
            pytest.skip("no compiler on this host")
        probe_src = tmp_path / "probe.c"
        probe_src.write_text("int probe(void){return 0;}\n")
        probe_so = tmp_path / "libprobe.so"
        proc = subprocess.run(
            [cc, "-shared", "-fPIC", "-o", str(probe_so), str(probe_src)],
            capture_output=True,
        )
        if proc.returncode != 0 or not probe_so.exists():
            pytest.skip("this host's compiler cannot build a shared object")
        if probe_so.read_bytes()[:4] != _ELF_MAGIC:
            pytest.skip("this host's compiler does not emit ELF")

        # This host demonstrably builds ELF, so the guard must let it through.
        built = _build_so(tmp_path, ["alpha"])
        assert built is not None, (
            "the ELF-magic guard rejected a genuine ELF build; every test in "
            "this module would now skip and assert nothing"
        )
        assert built.read_bytes()[:4] == _ELF_MAGIC

    def test_a_real_compile_failure_still_raises(self, tmp_path) -> None:
        """The other direction: a broken fixture must never become a skip."""
        if shutil.which("gcc") or shutil.which("cc") or shutil.which("clang"):
            with pytest.raises(BaseException) as excinfo:
                # Invalid C, so the compiler runs and rejects it.
                _build_so(tmp_path, ["int int not_a_name("])
            assert not isinstance(excinfo.value, type(pytest.skip.Exception())), (
                "a real compile failure was converted into a skip"
            )
        else:  # pragma: no cover - host without any compiler
            pytest.skip("no compiler on this host")
