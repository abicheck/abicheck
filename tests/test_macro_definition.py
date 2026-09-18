# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""ADR-074 unit contract for :mod:`abicheck.macro_definition`.

Written as *invariants over generated/enumerated input* rather than a list of
remembered examples, per AGENTS.md's "Primitive-level property tests": the
merge here is a reusable, general-purpose merge/dedupe primitive, and the
parser is the single validation choke point every ``-D`` flows through. The
oracles below are deliberately independent second derivations -- the round
trip is checked against ``str.partition`` and a hand-written token
concatenation, never against the module's own helpers.
"""

from __future__ import annotations

import itertools
import string

import pytest

from abicheck.model.macro_definition import (
    MacroDefinition,
    MacroDefinitionError,
    define_spellings_from_tokens,
    defines_receipt_line,
    macro_definition_tokens,
    merge_macro_definitions,
    parse_macro_definition,
    parse_macro_definitions,
)

# A small but genuinely varied accepted domain, enumerated rather than
# hand-picked per assertion, so every invariant below is checked against all
# of it instead of one remembered case.
ACCEPTED = [
    "A",
    "_",
    "_x9",
    "FEATURE_API",
    "PVXS_ENABLE_EXPERT_API",
    "A=",
    "A=1",
    "MODE=2",
    "N=0x1F",
    "N=-1",
    'S="quoted"',
    "T=a+b*c",
    "NAME=A=B",
    "NAME=A=B=C",
    "N=" + "=" * 5,
]


class TestParseAccepted:
    @pytest.mark.parametrize("text", ACCEPTED)
    def test_name_and_value_split_on_the_first_equals_only(self, text: str) -> None:
        """Oracle: ``str.partition('=')``, not the module's own splitting."""
        got = parse_macro_definition(text)
        name, sep, value = text.partition("=")
        assert got.name == name
        assert got.value == (value if sep else None)

    @pytest.mark.parametrize("text", ACCEPTED)
    def test_spelling_round_trips_the_input_exactly(self, text: str) -> None:
        assert parse_macro_definition(text).spelling == text

    @pytest.mark.parametrize("text", ACCEPTED)
    @pytest.mark.parametrize("style,prefix", [("gnu", "-D"), ("cl", "/D")])
    def test_renders_to_exactly_one_prefixed_token(
        self, text: str, style: str, prefix: str
    ) -> None:
        """D4: one definition is one argv token, always define-prefixed. The
        oracle is literal concatenation, independent of `token()`'s body."""
        token = parse_macro_definition(text).token(style)
        assert token == prefix + text
        assert len(token.split()) == 1

    def test_bare_name_and_empty_value_are_distinct_states(self) -> None:
        assert parse_macro_definition("A") != parse_macro_definition("A=")
        assert parse_macro_definition("A").value is None
        assert parse_macro_definition("A=").value == ""

    def test_unknown_token_style_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="unknown define token style"):
            MacroDefinition("A").token("msvc")


class TestParseRejected:
    #: Every rejected shape, with the substring its message must contain so a
    #: rejection cannot silently degrade into a generic one.
    REJECTED = [
        ("", "empty value"),
        ("=", "not a C identifier"),
        ("=1", "not a C identifier"),
        ("1BAD", "not a C identifier"),
        ("has-dash", "not a C identifier"),
        ("has.dot", "not a C identifier"),
        ("A B", "whitespace"),
        ("A=b c", "whitespace"),
        ("A=\tb", "whitespace"),
        ("A=b\nc", "whitespace"),
        ("F(x)=x", "function-like"),
        ("F(x, y)=x", "function-like"),
        ("F()", "function-like"),
        ("-DFOO", "not a C identifier"),
        ("/DFOO", "not a C identifier"),
        ("-UFOO", "not a C identifier"),
        ("-Xclang", "not a C identifier"),
        ("@response.txt", "not a C identifier"),
        ("--config=evil.cfg", "not a C identifier"),
        ("-fplugin=evil.so", "not a C identifier"),
        ("été", "not a C identifier"),
        ("Aé=1", "not a C identifier"),
    ]

    @pytest.mark.parametrize("text,expected", REJECTED)
    def test_rejected_with_its_own_message(self, text: str, expected: str) -> None:
        with pytest.raises(MacroDefinitionError) as exc:
            parse_macro_definition(text)
        assert expected in str(exc.value)

    @pytest.mark.parametrize(
        "text,hint",
        [
            ("-DFOO", "write -DFOO, not -D-DFOO"),
            ("-UFOO", "no --undefine counterpart"),
            ("@resp", "general compiler flags belong"),
            ("-Xclang", "general compiler flags belong"),
        ],
    )
    def test_common_mistakes_get_a_targeted_hint(self, text: str, hint: str) -> None:
        """Phase 5: the recurring mistakes must not fall through to the bare
        'not a C identifier' message."""
        with pytest.raises(MacroDefinitionError) as exc:
            parse_macro_definition(text)
        assert hint in str(exc.value)

    def test_every_rejected_input_raises_before_producing_any_token(self) -> None:
        """No rejected spelling may reach argv by any route (D4)."""
        for text, _ in self.REJECTED:
            with pytest.raises(MacroDefinitionError):
                parse_macro_definitions([text])

    def test_no_accepted_input_can_produce_a_second_argv_token(self) -> None:
        """The structural injection invariant, stated over the whole accepted
        domain rather than one adversarial example: whatever survives
        parsing, the rendered tail has exactly one token per definition and
        every one of them starts with the define switch."""
        for style, prefix in (("gnu", "-D"), ("cl", "/D")):
            tokens = macro_definition_tokens(parse_macro_definitions(ACCEPTED), style)
            assert len(tokens) == len(ACCEPTED)
            assert all(t.startswith(prefix) for t in tokens)
            assert all(len(t.split()) == 1 for t in tokens)


class TestMergeProperties:
    """The merge is a general-purpose by-key merge primitive; these state its
    contract as invariants, decoupled from any caller's domain logic."""

    def _names(self, defs: tuple[MacroDefinition, ...]) -> list[str]:
        return [d.name for d in defs]

    @pytest.mark.parametrize(
        "lower,higher",
        list(
            itertools.product(
                [[], ["A"], ["A", "B"], ["A=1", "B=2", "C"], ["A", "A=9"]],
                [[], ["A=2"], ["C=3"], ["A=2", "D"], ["D", "D=2"]],
            )
        ),
    )
    def test_result_holds_exactly_one_definition_per_name(
        self, lower: list[str], higher: list[str]
    ) -> None:
        merged = merge_macro_definitions(
            parse_macro_definitions(lower), parse_macro_definitions(higher)
        )
        names = self._names(merged)
        assert len(names) == len(set(names))
        # No name is invented and none is lost.
        assert set(names) == {s.partition("=")[0] for s in (*lower, *higher)}

    @pytest.mark.parametrize(
        "lower,higher",
        list(
            itertools.product(
                [["A=1", "B=2", "C"], ["A", "A=9", "B"]],
                [["A=2"], ["C=3", "A=7"], ["D"]],
            )
        ),
    )
    def test_higher_tier_wins_every_shared_name(
        self, lower: list[str], higher: list[str]
    ) -> None:
        merged = merge_macro_definitions(
            parse_macro_definitions(lower), parse_macro_definitions(higher)
        )
        by_name = {d.name: d for d in merged}
        # Oracle: last occurrence in `higher`, derived independently here.
        for spelling in higher:
            name, sep, value = spelling.partition("=")
            expected_last = [s for s in higher if s.partition("=")[0] == name][-1]
            assert by_name[name].spelling == expected_last
        # And a lower-tier name the higher tier never mentions keeps its own
        # last value.
        for spelling in lower:
            name = spelling.partition("=")[0]
            if name in {s.partition("=")[0] for s in higher}:
                continue
            assert (
                by_name[name].spelling
                == [s for s in lower if s.partition("=")[0] == name][-1]
            )

    def test_overridden_name_moves_to_the_higher_tier_position(self) -> None:
        """D3: re-emitted last, which is what makes it beat a raw -DNAME a
        lower tier renders after its own defines."""
        merged = merge_macro_definitions(
            parse_macro_definitions(["A=1", "B=2"]), parse_macro_definitions(["A=2"])
        )
        assert self._names(merged) == ["B", "A"]

    def test_empty_higher_tier_is_the_identity(self) -> None:
        for lower in ([], ["A"], ["A=1", "B"], ["A", "A=2"]):
            parsed = parse_macro_definitions(lower)
            merged = merge_macro_definitions(parsed, ())
            # Within-tier last-wins still collapses a repeat, but nothing
            # else changes and the order of survivors is preserved.
            assert self._names(merged) == list(dict.fromkeys(self._names(parsed)))

    def test_merge_is_idempotent_against_its_own_result(self) -> None:
        lower = parse_macro_definitions(["A=1", "B=2", "C"])
        higher = parse_macro_definitions(["A=2", "D"])
        once = merge_macro_definitions(lower, higher)
        assert merge_macro_definitions(once, higher) == once

    def test_result_does_not_depend_on_dict_iteration_order(self) -> None:
        """A merge keyed by name must not leak the construction order of any
        internal mapping -- checked by rebuilding the same tiers from a
        shuffled-but-equivalent construction."""
        lower = ["A=1", "B=2", "C=3", "D=4"]
        higher = ["C=9", "A=8"]
        expected = merge_macro_definitions(
            parse_macro_definitions(lower), parse_macro_definitions(higher)
        )
        for _ in range(5):
            assert (
                merge_macro_definitions(
                    tuple(MacroDefinition(*s.partition("=")[::2]) for s in lower),
                    tuple(MacroDefinition(*s.partition("=")[::2]) for s in higher),
                )
                == expected
            )

    def test_tokens_preserve_merge_order(self) -> None:
        merged = merge_macro_definitions(
            parse_macro_definitions(["A=1", "B"]), parse_macro_definitions(["A=2", "C"])
        )
        assert macro_definition_tokens(merged) == ["-DB", "-DA=2", "-DC"]


class TestDefineSpellingsFromTokens:
    """What the --dry-run receipt reads back off a rendered argv tail. The
    expectations here are anchored to *real gcc behaviour*, probed directly
    (gcc 13.3), not to what the reader happens to do."""

    def test_extracts_both_styles_and_ignores_everything_else(self) -> None:
        assert define_spellings_from_tokens(
            ["-std=gnu11", "-DA", "/DB=2", "-I", "/inc", "-UC", "-DD=x=y"]
        ) == ("A", "B=2", "D=x=y")

    def test_a_separated_D_consumes_the_next_token(self) -> None:
        assert define_spellings_from_tokens(["-D", "FEATURE", "-DB=1"]) == (
            "FEATURE",
            "B=1",
        )

    def test_a_separated_D_swallowing_a_flag_defines_nothing(self) -> None:
        """`-D -DD=1` defines NEITHER `-DD` nor `D`: gcc consumes the next
        token as the (malformed) macro name and discards it. Verified
        against gcc 13.3 -- `gcc -E -D -DD=1 -dM` lists no `D` and no
        `-DD`. An earlier revision of this reader treated the second token
        as its own attached `-DD=1`, i.e. reported a macro the compiler
        never defines."""
        assert define_spellings_from_tokens(["-D", "-DD=1"]) == ()

    @pytest.mark.parametrize(
        "tokens",
        [
            ["/I", "/Deps/include"],
            ["-I", "/Development/sdk"],
            ["-DFOO", "/Deps/include"],
        ],
    )
    def test_a_path_operand_beginning_with_D_is_not_a_macro(
        self, tokens: list[str]
    ) -> None:
        """`/Deps/include` merely starts with `/D`; to gcc it is a linker
        input file, not a definition (probed). Only operands that really
        parse as a macro definition are reported."""
        assert "eps/include" not in define_spellings_from_tokens(tokens)
        assert "evelopment/sdk" not in define_spellings_from_tokens(tokens)

    def test_one_entry_per_macro_last_wins(self) -> None:
        """A lower-precedence `-DA=9` from `compile.options` followed by the
        winning CLI `-DA=2` must be reported as the effective value alone --
        naming both would name a value the compiler discards."""
        assert define_spellings_from_tokens(["-DA=9", "-DB", "-DA=2"]) == ("A=2", "B")

    def test_the_collapse_keeps_first_appearance_order(self) -> None:
        """Ordering here is a *receipt* concern, not a semantic one: each
        macro keeps the position it first appeared at and carries its
        effective value. (The "re-emit last" rule in `merge_compile_config`
        is about argv, where position decides last-wins; once collapsed to
        one entry per name there is no race left to lose.)"""
        assert define_spellings_from_tokens(["-DZ=1", "-DA=1", "-DZ=2", "-DA=2"]) == (
            "Z=2",
            "A=2",
        )

    @pytest.mark.parametrize("style", ["gnu", "cl"])
    def test_round_trips_every_distinctly_named_definition(self, style: str) -> None:
        """Round trip over the accepted domain reduced to one entry per name
        -- ACCEPTED deliberately holds several spellings of `A`/`N`/`NAME`,
        which the reader now collapses last-wins by design."""
        defs = merge_macro_definitions((), parse_macro_definitions(ACCEPTED))
        assert list(
            define_spellings_from_tokens(macro_definition_tokens(defs, style))
        ) == [d.spelling for d in defs]


class TestDefinesReceiptLine:
    """The one renderer both `dump`'s and `compare`'s --dry-run receipts use.

    Unit-tested directly rather than only through the integration lane: that
    lane is excluded from the coverage gate, so a shared renderer exercised
    only there reads as untested -- and, more to the point, a formatting
    change would be caught only by a test that needs a real compiler.
    """

    def test_none_when_nothing_is_defined(self) -> None:
        """The receipt must read exactly as it did before ADR-074 for a run
        with no macros -- an empty `defines:` line would be new noise on
        every existing invocation."""
        assert defines_receipt_line([]) is None
        assert defines_receipt_line(["-std=gnu11", "-I", "/inc"]) is None

    def test_renders_the_effective_set_in_one_line(self) -> None:
        assert defines_receipt_line(["-std=gnu11", "-DA", "-DB=2"]) == "defines: A, B=2"

    def test_renders_the_collapsed_value_not_every_token(self) -> None:
        assert defines_receipt_line(["-DA=9", "-DA=2"]) == "defines: A=2"


def test_every_ascii_identifier_character_class_is_covered() -> None:
    """Vacuity guard on the accepted domain above: the identifier rule is
    exhaustively enumerated over the small domain of single characters, so a
    regex accidentally widened (or narrowed) to a constant is caught."""
    accepted_first = {c for c in map(chr, range(128)) if not _rejects(c)}
    assert accepted_first == set(string.ascii_letters) | {"_"}
    accepted_rest = {c for c in map(chr, range(128)) if not _rejects("A" + c)}
    assert accepted_rest == set(string.ascii_letters + string.digits) | {"_", "="}


def _rejects(text: str) -> bool:
    try:
        parse_macro_definition(text)
    except MacroDefinitionError:
        return True
    return False


class TestFrontendSpellingMatrix:
    """ADR-074's compatibility matrix, pinned against the code that actually
    emits each spelling -- so the table in the ADR cannot drift from the
    repository without a test failing.

    The CastXML rows in particular: CastXML passes user arguments through to
    its bundled Clang in GNU driver mode regardless of which
    ``--castxml-cc-<id>`` emulation was selected, which is why this codebase
    has always emitted an unconditional ``-D`` there while spelling only the
    language standard per id. That is the claim the (unverified-on-Windows)
    CastXML+MSVC row rests on, so it is asserted here rather than left as
    prose.
    """

    @staticmethod
    def _unit(compiler: str):
        from abicheck.buildsource.build_evidence import CompileUnit

        return CompileUnit(
            id="u1",
            source="a.cpp",
            compiler=compiler,
            argv=(compiler, "-c", "a.cpp"),
            language="c++",
            standard="c++17",
            defines={"FEATURE_API": "", "MODE": "2"},
        )

    @pytest.mark.parametrize("compiler", ["g++", "clang++", "cl.exe"])
    def test_castxml_always_spells_defines_gnu_style(self, compiler: str) -> None:
        from abicheck.buildsource.source_extractors.castxml import (
            build_castxml_command,
        )

        cmd = build_castxml_command(
            self._unit(compiler),
            __import__("pathlib").Path("a.cpp"),
            __import__("pathlib").Path("out.xml"),
        )
        assert "-DFEATURE_API" in cmd
        assert "-DMODE=2" in cmd
        assert not any(t.startswith("/D") for t in cmd)
        # ...while the *standard* genuinely is per-emulation-id, which is what
        # makes "defines are not" a real claim rather than an oversight.
        assert ("/std:c++17" in cmd) == (compiler == "cl.exe")

    @pytest.mark.parametrize(
        "compiler,prefix", [("g++", "-D"), ("clang++", "-D"), ("cl.exe", "/D")]
    )
    def test_direct_clang_replay_spells_defines_per_driver_mode(
        self, compiler: str, prefix: str
    ) -> None:
        """The one place ``/D`` is correct: a driver genuinely invoked in
        cl mode (L4 compile-database replay). No CLI ``-D`` reaches it -- the
        L2 header backends both drive a GNU-style driver."""
        from abicheck.buildsource.source_extractors.clang import _clang_context_args

        args, _msvc = _clang_context_args(self._unit(compiler), None)
        assert f"{prefix}FEATURE_API" in args
        assert f"{prefix}MODE=2" in args
