# Copyright 2026 Nikolay Petrov
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

"""Build-context capture from compile_commands.json (ADR-020a).

Parses a JSON Compilation Database (Clang standard) to extract the exact
compiler flags, defines, include paths, and language standard used to build
each translation unit.  This eliminates "header parse drift" — the most
common source of ABI tool inaccuracy — by binding header AST extraction
to the real build context.

Usage::

    from abicheck.build_context import load_compile_db, build_context_for_header

    db = load_compile_db(Path("build/compile_commands.json"))
    ctx = build_context_for_header(db, Path("include/foo.h"))
    # ctx.defines, ctx.include_paths, ctx.language_standard, ...
"""

from __future__ import annotations

import json
import logging
import os
import re
import shlex
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path

from .errors import ValidationError

_logger = logging.getLogger(__name__)

# Flags that take a following argument (next token is the value).
_FLAGS_WITH_ARG = frozenset(
    {
        "-I",
        "-isystem",
        "-include",
        "-isysroot",
        "--sysroot",
        "-target",
        "--target",
        "-x",
        "-std",
        "-MF",
        "-MQ",
        "-MT",
        "-o",
        "-c",
    }
)

# Regex for combined -Dfoo=bar or -Dfoo
_DEFINE_RE = re.compile(r"^-D(.+?)(?:=(.*))?$")
_UNDEF_RE = re.compile(r"^-U(.+)$")
_INCLUDE_RE = re.compile(r"^-I(.+)$")
_ISYSTEM_RE = re.compile(r"^-isystem(.+)$")
_STD_RE = re.compile(r"^-std=(.+)$")
_TARGET_RE = re.compile(r"^--?target=(.+)$")
_SYSROOT_RE = re.compile(r"^--sysroot=(.+)$")
_VISIBILITY_RE = re.compile(r"^-fvisibility=(.+)$")


#: GNU ``@response-file`` expansion depth cap (``ld``/``ar``/make-generated
#: response files can ``@include`` one another) — bounds the recursion below
#: against a self-referential or absurdly deep chain rather than assuming
#: real build systems never nest them.
_MAX_RESPONSE_FILE_DEPTH = 4

#: Aggregate cap on the number of response files actually *read* across one
#: top-level expansion call, independent of *_MAX_RESPONSE_FILE_DEPTH*. The
#: depth cap alone does not bound total work: a response file containing many
#: ``@self-reference`` tokens re-expands on every occurrence, so a shallow
#: depth cap still permits combinatorial blowup (a 100-token self-referential
#: file can produce roughly 100**_MAX_RESPONSE_FILE_DEPTH token reads before
#: depth stops it) -- exploitable against an untrusted compile database (e.g.
#: a PR checkout scanned in CI) as a hang/OOM (Codex review). Generous for any
#: real nested-response-file build (a handful of files, rarely more than one
#: level deep) while several orders of magnitude below a blowup.
_MAX_RESPONSE_FILE_EXPANSIONS = 64

#: Aggregate cap on the number of *tokens* actually emitted into the
#: expanded argv across one top-level expansion call. The file-read cap
#: above bounds fan-out but not per-file size: a single response file near
#: the 1 MiB byte cap can itself pack ~95k short whitespace-separated
#: ``@loop.rsp`` tokens, so 64 reads of a file that dense could still emit
#: several million tokens before the read budget is exhausted (Codex
#: review, second round). A generous ceiling for any real build (which
#: rarely emits more than a few thousand flags total) while bounding
#: worst-case output size independent of how the file-read budget is spent.
_MAX_RESPONSE_FILE_OUTPUT_TOKENS = 20000

#: Aggregate cap on the number of tokens emitted across *every* entry in one
#: ``load_compile_db()`` call, on top of the per-entry cap above and
#: independent of the *_file_cache* read/parse dedup. Caching a response
#: file's own tokenization removes redundant I/O when many entries share
#: one ``@file``, but each entry still walks and re-materializes its own
#: fully expanded argv list -- an untrusted database with thousands of
#: entries each pointing at one dense (near-per-entry-cap) response file
#: can still turn a few MB of JSON into a large aggregate amount of list
#: allocation/CPU work even with zero extra disk reads (Codex review, fifth
#: round). Sized generously above any real single project's total expanded
#: flag volume (thousands of entries at a few hundred flags each) while
#: still bounding worst-case aggregate output independent of entry count.
_MAX_RESPONSE_FILE_DB_OUTPUT_TOKENS = 2_000_000

#: Aggregate cap on the number of tokens ever *retained* in the shared
#: *_file_cache* across one ``load_compile_db()`` call, independent of
#: *_MAX_RESPONSE_FILE_DB_OUTPUT_TOKENS* above. That budget only charges
#: tokens actually spliced into some entry's output; a distinct response
#: file whose own size is under the fixed per-entry cap (so not rejected
#: outright) but whose tokens never fit any entry's *actual remaining*
#: budget at the point it was read still gets its full tokens list cached
#: for possible reuse -- an untrusted database with many such distinct,
#: near-cap files can accumulate an unbounded amount of cached-but-never-
#: used memory without ever charging the output budget (Codex review,
#: seventh round). Charged at cache-population time, regardless of
#: whether the tokens are ever actually used.
_MAX_RESPONSE_FILE_CACHE_TOKENS = 2_000_000

#: Response files feeding a single compile action are typically a handful of
#: KB even for a very long include-dir list (oneDAL's own longest is well
#: under this); a much larger file is either not a real response file or not
#: one worth trusting blindly.
_MAX_RESPONSE_FILE_BYTES = 1024 * 1024


def _split_windows_command_line(text: str) -> list[str]:
    """Tokenize a command line / response-file body using Windows/MSVC argv
    rules (the same backslash-before-quote escaping ``CommandLineToArgvW``
    and ``cl.exe`` use), instead of ``shlex.split(text, posix=False)`` —
    which implements POSIX-shell quoting, not MSVC's, so a quoted path like
    ``-I"C:\\Program Files\\SDK"`` gets split mid-path on the embedded space
    (Codex review).

    Rules (Microsoft's documented C runtime argv-parsing convention):
    whitespace (space/tab/newline) delimits arguments unless inside a
    double-quoted span; a run of N backslashes immediately followed by a
    double-quote emits ``N // 2`` literal backslashes, and if N is odd the
    final backslash escapes the quote (a literal ``"`` is emitted, the
    quoted-span state is untouched) while an even N instead toggles the
    quoted-span state; a run of backslashes NOT followed by a double-quote
    is emitted literally. Never raises — unlike ``shlex.split``, malformed
    quoting has no invalid state under these rules.
    """
    args: list[str] = []
    current: list[str] = []
    in_quotes = False
    seen_token = False
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if not in_quotes and c in " \t\r\n":
            if seen_token:
                args.append("".join(current))
                current = []
                seen_token = False
            i += 1
            continue
        seen_token = True
        if c == "\\":
            j = i
            while j < n and text[j] == "\\":
                j += 1
            num_backslashes = j - i
            if j < n and text[j] == '"':
                current.append("\\" * (num_backslashes // 2))
                if num_backslashes % 2 == 1:
                    current.append('"')
                    j += 1
                else:
                    in_quotes = not in_quotes
                    j += 1
            else:
                current.append("\\" * num_backslashes)
            i = j
            continue
        if c == '"':
            in_quotes = not in_quotes
            i += 1
            continue
        current.append(c)
        i += 1
    if seen_token:
        args.append("".join(current))
    return args


#: Compiler-driver stems that use MSVC/``CommandLineToArgvW`` response-file
#: quoting regardless of host OS -- matched after stripping a trailing
#: ``-N``/``-N.N`` version suffix (``clang-cl-20``, ``clang-cl-17.0``).
_CL_STYLE_DRIVER_STEMS = frozenset({"cl", "clang-cl", "dpcpp-cl"})
_DRIVER_VERSION_SUFFIX_RE = re.compile(r"-\d+(?:\.\d+)*$")


def _is_cl_style_driver(argv0: str) -> bool:
    """Return True if *argv0* names an MSVC-compatible driver (``cl.exe``,
    ``clang-cl``, ``dpcpp-cl``, including versioned spellings like
    ``clang-cl-20``), as opposed to a GNU-mode driver (``clang++``, ``g++``,
    a MinGW-prefixed GCC, ...). Used to pick response-file quoting by the
    *compiler actually invoked*, not the host OS: a GNU-mode driver
    (including MinGW GCC or GNU-mode clang++ running on Windows) uses GNU
    ``@file`` quoting, not MSVC's, so dispatching purely on ``os.name``
    corrupts a quoted/backslash-escaped argument for that combination
    (Codex review).

    Uses the shared cross-platform ``basename()`` (not ``Path(argv0).stem``):
    ``Path().stem`` only splits on ``/`` even on POSIX, so a Windows-style
    compiler path recorded in a cross-compiled/Windows-generated
    ``compile_commands.json`` (e.g. ``C:\\VS\\bin\\clang-cl.exe``) scanned on
    Linux CI kept the whole path as its "stem" and never matched
    ``_CL_STYLE_DRIVER_STEMS``, silently picking GNU quoting for a real
    CL-mode driver (found while consolidating this against the equivalent
    helpers in ``dumper_clang.py``/``source_extractors/_argv.py``)."""
    from .buildsource.source_extractors._argv import basename

    stem = basename(argv0).lower()
    if stem.endswith(".exe"):
        stem = stem[: -len(".exe")]
    stem = _DRIVER_VERSION_SUFFIX_RE.sub("", stem)
    return stem in _CL_STYLE_DRIVER_STEMS


def _split_command_line(text: str, *, cl_style: bool | None = None) -> list[str]:
    """Tokenize a compile-db ``command``/``arguments``-string or a
    response-file body.

    *cl_style* selects the argv-quoting convention explicitly (True for
    MSVC/``CommandLineToArgvW`` rules via :func:`_split_windows_command_line`,
    False for POSIX shell quoting via ``shlex``) — used for a
    ``@response-file`` *body*, since that is genuinely interpreted by
    whichever compiler reads it (driver-dependent): a GNU-mode driver on
    Windows still uses GNU response-file quoting.

    The *top-level* ``command``/``arguments`` string is a different case and
    deliberately stays ``os.name``-guessed (``cl_style=None``, the default):
    it reflects whatever convention the build system/shell that *wrote* the
    compile database entry used, which tracks the host that generated the
    JSON, not the compiler that happens to be invoked -- a MinGW GNU-mode
    driver invoked on Windows still has its own ``directory``/``command``
    written with native (unquoted, backslash-containing) Windows paths by a
    Windows-hosted build system. Re-tokenizing the top-level string by
    driver instead was tried and reverted: POSIX ``shlex`` treats an
    unquoted backslash as an escape character, so it silently corrupts an
    unquoted Windows path like ``C:\\mingw64\\bin\\g++.exe`` into
    ``C:mingw64bing++.exe`` for exactly the GNU-driver-on-Windows case this
    would have tried to "fix" (Codex review).
    """
    if cl_style is None:
        cl_style = os.name == "nt"
    if cl_style:
        return _split_windows_command_line(text)
    return shlex.split(text, posix=True)


def _safe_resolve(path: Path) -> Path | None:
    try:
        return path.resolve()
    except OSError:
        return None
    except RuntimeError:
        # Path.resolve() raises RuntimeError (not OSError) for a symlink
        # loop on Python < 3.13 -- OSError only became the documented
        # behavior in 3.13, and this project supports 3.10+ (Codex review).
        # An uncaught RuntimeError here would abort the whole compile
        # database load instead of degrading this one @file to its literal
        # token, same as any other unreadable response file.
        return None


def _read_response_file(path: Path, root: Path) -> str | None:
    """Best-effort, bounded read of a GNU ``@response-file``.

    Returns ``None`` (never raises) for anything that isn't a plausible,
    in-tree response file: missing, not a regular file, oversized, or
    outside *root* (the trusted compile-database directory) — an
    ``@/etc/passwd`` or ``@../../secret`` token in an untrusted
    ``compile_commands.json`` (e.g. a PR checkout scanned in CI) must not
    read arbitrary filesystem content into the parsed argv, which
    ``adapters/compile_db.py`` persists into ``CompileUnit.argv`` and
    ultimately the ``.abi.json`` artifact — mirrors
    ``adapters.make._read_response_file``'s identical build-tree jail. The
    caller falls back to keeping the original ``@file`` token untouched
    instead of losing the rest of the argument list.
    """
    resolved = _safe_resolve(path)
    if resolved is None or not resolved.is_relative_to(root):
        return None
    try:
        st = resolved.stat()
    except OSError:
        return None
    if not resolved.is_file() or st.st_size > _MAX_RESPONSE_FILE_BYTES:
        return None
    try:
        data = resolved.read_bytes()
    except OSError:
        return None
    if len(data) > _MAX_RESPONSE_FILE_BYTES:
        return None
    return data.decode("utf-8", errors="replace")


#: Cache of a single response file's own (non-recursive) tokenization,
#: keyed by ``(str(resolved_path), cl_style)`` -- see the *_file_cache*
#: parameter of :func:`_expand_response_files`. ``None`` records "this file
#: is unreadable/oversized/out-of-tree/unparsable" so a repeated reference
#: doesn't re-run those checks either.
_ResponseFileCache = dict[tuple[str, bool], "list[str] | None"]


def _read_and_tokenize_response_file(
    path: Path,
    root: Path,
    *,
    cl_style: bool,
    read_budget: list[int],
    cache_token_budget: list[int] | None,
) -> list[str] | None:
    """One response file's tokens, or ``None`` when it must not be expanded.

    ``None`` is a *cacheable* rejection — unreadable/oversized/out-of-tree file,
    unparsable contents, or a token count no entry could ever afford — so the
    caller memoizes it and no later entry re-reads the same file to reach the
    same answer.

    Two of those rejections are size caps rather than errors:

    * Over ``_MAX_RESPONSE_FILE_OUTPUT_TOKENS``: no entry's own output budget
      ever starts above this constant (a fresh top-level call always defaults to
      it), so a file this dense can never fit any entry regardless of how much
      of that budget is left -- cache the rejection instead of the full token
      list, or many distinct oversized files in one untrusted database would
      each retain a huge, never-usable tokens list indefinitely (Codex review,
      sixth round).
    * Over the remaining *cache_token_budget*: a distinct file under the fixed
      per-entry cap (so not caught above) can still be over THIS entry's own
      remaining budget and be rejected for it -- but its full tokens list would
      still be retained for possible reuse by a later entry with a fresh budget.
      Many such distinct, never-actually-used files can accumulate unbounded
      cached memory without ever charging the database-wide output budget (which
      is only charged when tokens are actually spliced into some entry's
      output). This separate, cache-population-time budget is charged instead,
      regardless of whether the tokens end up used (Codex review, seventh round).
    """
    text = _read_response_file(path, root)
    if text is None:
        return None
    read_budget[0] -= 1
    try:
        tokens = _split_command_line(text, cl_style=cl_style)
    except ValueError:
        return None
    if len(tokens) > _MAX_RESPONSE_FILE_OUTPUT_TOKENS:
        return None
    if cache_token_budget is not None:
        if len(tokens) > cache_token_budget[0]:
            return None
        cache_token_budget[0] -= len(tokens)
    return tokens


def _exceeds_output_budget(
    tokens: list[str], output_budget: list[int], db_output_budget: list[int] | None
) -> bool:
    """True when splicing *tokens* would overrun a remaining output budget.

    The pre-loop budget check only rejects a *subsequent* ``@file`` once the
    budget has already gone negative, so a single response file at or near the
    byte cap (tokenizable into hundreds of thousands of short tokens) could
    otherwise still be expanded whole in one shot before that check ever fires
    (Codex review, third round). The caller rejects the whole file rather than
    partially truncating it -- a partial flag list is worse than none.
    """
    return len(tokens) > output_budget[0] or (
        db_output_budget is not None and len(tokens) > db_output_budget[0]
    )


def _response_file_tokens_for_arg(
    arg: str,
    directory: Path,
    root: Path,
    *,
    cl_style: bool,
    read_budget: list[int],
    output_budget: list[int],
    db_output_budget: list[int] | None,
    cache_token_budget: list[int] | None,
    file_cache: _ResponseFileCache,
) -> list[str] | None:
    """Tokens for one ``@file`` argument, or ``None`` to keep the token as-is.

    Resolves the path against *directory* (the compile action's own working
    directory) unless already absolute, consults *file_cache*, and applies every
    budget gate. ``None`` covers all four "do not expand" outcomes: an exhausted
    output budget, an exhausted read budget, a cached-or-fresh rejection from
    :func:`_read_and_tokenize_response_file`, and a token count that would
    overrun the remaining output budget.
    """
    if output_budget[0] <= 0 or (
        db_output_budget is not None and db_output_budget[0] <= 0
    ):
        return None
    raw_path = Path(arg[1:])
    path = raw_path if raw_path.is_absolute() else directory / raw_path
    resolved = _safe_resolve(path)
    cache_key = (str(resolved), cl_style) if resolved is not None else None
    if cache_key is not None and cache_key in file_cache:
        tokens = file_cache[cache_key]
    elif read_budget[0] <= 0:
        # No read budget left. Deliberately *not* cached: nothing was read, so
        # there is no result to memoize -- a later call with its own budget must
        # still get a real chance to read this file.
        return None
    else:
        tokens = _read_and_tokenize_response_file(
            path,
            root,
            cl_style=cl_style,
            read_budget=read_budget,
            cache_token_budget=cache_token_budget,
        )
        if cache_key is not None:
            file_cache[cache_key] = tokens
    if tokens is None or _exceeds_output_budget(
        tokens, output_budget, db_output_budget
    ):
        return None
    return tokens


def _expand_response_files(
    arguments: list[str],
    directory: Path,
    root: Path,
    _depth: int = 0,
    _budget: list[int] | None = None,
    _output_budget: list[int] | None = None,
    *,
    cl_style: bool = False,
    _file_cache: _ResponseFileCache | None = None,
    _db_output_budget: list[int] | None = None,
    _cache_token_budget: list[int] | None = None,
) -> list[str]:
    """Inline GNU-style ``@response-file`` arguments in a compiler argv.

    Make-generated ``compile_commands.json`` entries commonly spell a long
    include-dir list as ``clang++ @build/inc_folders.txt -c foo.cpp`` instead
    of literal ``-I`` tokens, to stay under the platform argv length limit —
    this is standard practice for make-based build systems, not a malformed
    entry. Left unexpanded, every flag the response file carries (almost
    always ``-I``/``-D``) is invisible to :func:`_extract_flags` and to the
    L3 ``CompileUnit`` this same argv projects into
    (``adapters/compile_db.py`` reuses ``entry.arguments`` verbatim) — every
    ``-I`` is silently dropped long before either parser runs, producing a
    ``file not found`` on every translation unit regardless of which
    compiler ends up invoked. A response file's own contents may themselves
    ``@include`` another one, so expansion recurses, bounded by
    *_MAX_RESPONSE_FILE_DEPTH*, resolving every nested ``@file`` against the
    *original* *directory* (the compile action's own working directory) at
    every recursion level, never the including response file's own
    directory. This is deliberate, not an oversight: it's GCC/binutils'
    (and Clang's own command-line ``@file``, as opposed to its separate
    ``--config`` configuration-file mechanism) documented behavior for a
    *command-line* response file — only Clang configuration files use
    includer-relative nesting (via the ``<CFGDIR>`` token), which is a
    different, unrelated feature this parser never encounters (a
    ``compile_commands.json`` entry is a verbatim compiler invocation, never
    a ``--config`` file). *root* is the trusted directory a resolved
    ``@file`` must stay under (see :func:`_read_response_file`).

    Unreadable/oversized/out-of-tree files, and unparsable file contents,
    degrade to keeping the original ``@file`` token rather than raising —
    matching ``_extract_flags``'s existing "skip what we don't understand"
    contract for any other flag it doesn't recognize. *_budget*/
    *_output_budget* (internal, shared across the whole recursive call tree
    via single-element lists) are the remaining count of response files this
    call may still read (see *_MAX_RESPONSE_FILE_EXPANSIONS*) and the
    remaining number of tokens it may still emit (see
    *_MAX_RESPONSE_FILE_OUTPUT_TOKENS*) respectively — two independent caps,
    since bounding file reads alone still lets one large, token-dense file
    (near the byte cap, packed with short tokens) emit an outsized amount of
    output within that read budget (Codex review, second round). *cl_style*
    selects response-file body quoting per :func:`_split_command_line` —
    pass the caller's own :func:`_is_cl_style_driver` result for the
    compiler actually being invoked, not the host OS (Codex review).

    *_file_cache* (internal, optionally shared across many *separate*
    top-level calls by a caller like :func:`load_compile_db`) memoizes a
    single response file's own read + tokenization by resolved path. An
    untrusted ``compile_commands.json`` with thousands of entries that all
    reference the *same* response file would otherwise re-read and
    re-tokenize it from scratch on every entry's own fresh *_budget*/
    *_output_budget* — amplifying a few MB of attacker-controlled JSON into
    orders of magnitude more I/O and parsing work (Codex review, fourth
    round). A cache hit skips the read/tokenize entirely (and does not
    consume *_budget*, since no file is actually read) but still goes
    through the same recursive expansion, output-budget accounting, and
    depth limit as a miss, so nested ``@file`` handling is unaffected.

    *_db_output_budget* (internal, shared the same way as *_file_cache*)
    additionally bounds the aggregate number of tokens emitted across
    *every* entry in the whole database, on top of the existing per-entry
    *_output_budget* -- caching removes redundant I/O for a repeated file,
    but each entry still walks and re-materializes its own expanded argv
    list, so thousands of entries referencing one dense file can still
    amplify into a large aggregate amount of list allocation/CPU work with
    zero extra disk reads (Codex review, fifth round). *_cache_token_budget*
    is a separate shared budget charged at *cache-population* time (whether
    or not the tokens end up used by any entry): a distinct response file
    under the fixed per-entry cap but over a particular entry's own
    *remaining* budget still gets its full tokens list retained in
    *_file_cache* for possible reuse, so many such distinct files can
    accumulate unbounded cached-but-unused memory without ever charging
    *_db_output_budget* (Codex review, seventh round).
    """
    if _depth > _MAX_RESPONSE_FILE_DEPTH:
        return arguments
    if _budget is None:
        _budget = [_MAX_RESPONSE_FILE_EXPANSIONS]
    if _output_budget is None:
        _output_budget = [_MAX_RESPONSE_FILE_OUTPUT_TOKENS]
    if _file_cache is None:
        _file_cache = {}
    expanded: list[str] = []
    for arg in arguments:
        if not arg.startswith("@") or len(arg) == 1:
            expanded.append(arg)
            continue
        tokens = _response_file_tokens_for_arg(
            arg,
            directory,
            root,
            cl_style=cl_style,
            read_budget=_budget,
            output_budget=_output_budget,
            db_output_budget=_db_output_budget,
            cache_token_budget=_cache_token_budget,
            file_cache=_file_cache,
        )
        if tokens is None:
            expanded.append(arg)
            continue
        _output_budget[0] -= len(tokens)
        if _db_output_budget is not None:
            _db_output_budget[0] -= len(tokens)
        expanded.extend(
            _expand_response_files(
                tokens,
                directory,
                root,
                _depth + 1,
                _budget,
                _output_budget,
                cl_style=cl_style,
                _file_cache=_file_cache,
                _db_output_budget=_db_output_budget,
                _cache_token_budget=_cache_token_budget,
            )
        )
    return expanded


@dataclass
class CompileEntry:
    """One entry from compile_commands.json."""

    file: Path
    directory: Path
    arguments: list[str]

    @classmethod
    def from_dict(
        cls,
        raw: dict[str, object],
        db_dir: Path,
        _file_cache: _ResponseFileCache | None = None,
        _db_output_budget: list[int] | None = None,
        _cache_token_budget: list[int] | None = None,
    ) -> CompileEntry:
        """Parse a single compile_commands.json entry.

        Handles both ``arguments`` (JSON array) and ``command`` (shell string)
        forms as specified by the Clang compilation database standard.
        ``@response-file`` arguments are expanded inline, constrained to
        *db_dir* (see :func:`_expand_response_files`) so every downstream
        consumer of ``arguments`` sees the real flags, not an opaque
        ``@file`` token — without reading outside the compilation
        database's own trusted directory. *_file_cache*/*_db_output_budget*/
        *_cache_token_budget*, when supplied by :func:`load_compile_db`, are
        shared across every entry in the same compile database so a
        response file referenced by many entries is only actually
        read/tokenized once, and both the aggregate expansion work and the
        aggregate cached memory across the whole database stay bounded
        (see :func:`_expand_response_files`).
        """
        directory = Path(str(raw.get("directory", db_dir)))
        file_str = str(raw.get("file", ""))
        file_path = Path(file_str)
        if not file_path.is_absolute():
            file_path = directory / file_path

        if "arguments" in raw:
            args_raw = raw["arguments"]
            if isinstance(args_raw, list):
                arguments = [str(a) for a in args_raw]
            else:
                arguments = _split_command_line(str(args_raw))
        elif "command" in raw:
            arguments = _split_command_line(str(raw["command"]))
        else:
            arguments = []

        # Unwrap a compiler-launcher prefix (ccache/sccache/…) before reading
        # the driver token -- otherwise a launcher-wrapped CL-style action
        # (e.g. "sccache clang-cl @args.rsp") tests the launcher name
        # instead of the real driver and picks the wrong quoting convention
        # (Codex review).
        from .buildsource.source_extractors._argv import strip_launchers

        unwrapped = strip_launchers(arguments)
        cl_style = bool(unwrapped) and _is_cl_style_driver(unwrapped[0])
        arguments = _expand_response_files(
            arguments,
            directory,
            _safe_resolve(db_dir) or db_dir,
            cl_style=cl_style,
            _file_cache=_file_cache,
            _db_output_budget=_db_output_budget,
            _cache_token_budget=_cache_token_budget,
        )

        return cls(file=file_path.resolve(), directory=directory, arguments=arguments)


@dataclass
class BuildContext:
    """Compilation context derived from compile_commands.json (ADR-020a).

    Captures the exact flags that were used to compile one or more TUs,
    enabling deterministic header parsing via CastXML.
    """

    defines: dict[str, str | None] = field(default_factory=dict)
    undefines: set[str] = field(default_factory=set)
    include_paths: list[Path] = field(default_factory=list)
    system_includes: list[Path] = field(default_factory=list)
    #: Parallel to ``include_paths``/``system_includes`` (same index),
    #: recording whether each entry's ORIGINAL, as-written ``-I``/``-isystem``
    #: operand was already absolute -- as opposed to a relative operand this
    #: module resolved by joining it onto *directory* (round 30 Finding 2,
    #: Codex review, fresh evidence). ``_resolve_path`` below deliberately
    #: never rewrites a genuinely-absolute operand (an absolute path is
    #: absolute regardless of *directory*), but by the time that operand
    #: reaches ``include_paths``/``system_includes`` as a plain ``Path`` it
    #: is indistinguishable from a relative operand that HAPPENED to resolve
    #: to the exact same absolute location once joined onto *directory* --
    #: a downstream consumer that needs to rebase a *derived* path onto a
    #: different effective directory (e.g. once a leading ``env -C DIR``
    #: prefix is accounted for) but must NEVER rebase a path that was
    #: genuinely absolute in the source command needs this provenance
    #: captured at the one point it is still available: before the join.
    #: ``kw_only=True`` (CodeRabbit review, fresh evidence) -- same
    #: mid-list-insertion reasoning as ``CompileUnit.include_paths_explicit``
    #: in ``build_evidence.py``.
    include_paths_explicit: list[bool] = field(default_factory=list, kw_only=True)
    system_includes_explicit: list[bool] = field(default_factory=list, kw_only=True)
    language_standard: str | None = None
    target_triple: str | None = None
    sysroot: Path | None = None
    extra_flags: list[str] = field(default_factory=list)
    compile_db_path: Path | None = None

    # Conflict tracking (populated by union fallback)
    define_conflicts: dict[str, list[str]] = field(default_factory=dict)
    standard_variants: list[str] = field(default_factory=list)

    def to_castxml_flags(self) -> list[str]:
        """Convert this build context to CastXML-compatible flags.

        Returns a list of command-line arguments suitable for passing to
        CastXML (or any Clang-compatible frontend).
        """
        flags: list[str] = []

        if self.language_standard and "++" in self.language_standard:
            flags.append(f"-std={self.language_standard}")

        if self.target_triple:
            flags.append(f"--target={self.target_triple}")

        if self.sysroot:
            flags.append(f"--sysroot={self.sysroot}")

        for macro, value in sorted(self.defines.items()):
            if value is not None:
                flags.append(f"-D{macro}={value}")
            else:
                flags.append(f"-D{macro}")

        for macro in sorted(self.undefines):
            flags.append(f"-U{macro}")

        for inc in self.include_paths:
            flags.extend(["-I", str(inc)])

        for inc in self.system_includes:
            flags.extend(["-isystem", str(inc)])

        flags.extend(self.extra_flags)
        return flags

    @property
    def has_conflicts(self) -> bool:
        """Return True if define or standard conflicts were detected."""
        return bool(self.define_conflicts) or len(self.standard_variants) > 1


def source_matches_filter(
    file: str | Path, directory: str | Path | None, pattern: str
) -> bool:
    """Whether a translation unit's *file* matches a ``source_filter`` glob.

    The one definition of what ``--compile-db-filter`` means, shared by every
    layer that narrows a compile database by it — this module's own
    :class:`CompileEntry` scan, ``header_conditionals``' raw-dict scan for the
    ADR-039 collector, and ``buildsource.header_compile_context``'s
    :class:`~abicheck.buildsource.adapters.base.CompileUnit` scan for the P0.3
    L3→L2 fold. Three layers had (or would have had) their own copy of this
    predicate; the same shape has already drifted silently once in this
    codebase (the MSVC-driver vocabulary, third finding on the root
    ``AGENTS.md``'s forced-include entry), and a filter that selects different
    translation units in the L2 fold than in the collector is exactly the kind
    of disagreement that produces a snapshot describing two builds at once.

    A **relative** *file* is resolved against *directory* first (matching how
    :class:`CompileEntry` stores ``directory / file``), then the pattern is
    tested against the absolute path, the directory-relative path, and the
    CWD-relative path — so an absolute filter matches a relative-``file``
    entry and a relative ``src/libfoo/**`` filter matches an absolute-``file``
    entry.

    A redacted ``CompileUnit`` (``buildsource.redaction.RedactionPolicy``,
    ADR-032 D7) is a real, common caller shape this must not double-join:
    both ``source`` and ``directory`` have their shared home prefix replaced
    with the same placeholder (``~`` by default), so a redacted *file* like
    ``~/proj/a.cpp`` is *not* :meth:`Path.is_absolute` — the placeholder
    isn't a root — but it is already anchored under the identically-redacted
    *directory* (``~/proj``), not a bare relative name that needs joining.
    Joining it anyway produced ``~/proj/~/proj/a.cpp`` (Codex review, P1),
    silently matching nothing and falling back to "every compile unit
    matches". Checked with :meth:`Path.is_relative_to`, which compares path
    *segments* rather than the filesystem, so it works identically whether
    *file* is a real relative name (``a.cpp`` is never relative to an
    unrelated ``directory``, so the ordinary join path is unaffected) or an
    already-directory-anchored redacted spelling.
    """
    path = Path(file)
    if not path.is_absolute() and directory is not None:
        directory_path = Path(directory)
        if not path.is_relative_to(directory_path):
            path = directory_path / path
    if fnmatch(str(path), pattern):
        return True
    if directory is not None:
        try:
            return fnmatch(str(path.relative_to(directory)), pattern)
        except ValueError:
            pass  # file is not under directory — fall through to CWD-relative
    try:
        return fnmatch(str(path.relative_to(Path.cwd())), pattern)
    except ValueError:
        return False


def _entry_matches_filter(entry: CompileEntry, pattern: str) -> bool:
    """Test if a compile entry matches a source_filter glob pattern.

    A thin caller of :func:`source_matches_filter` — see that function for the
    matching rules and for why they live in one place.
    """
    return source_matches_filter(entry.file, entry.directory, pattern)


def load_compile_db(path: Path) -> list[CompileEntry]:
    """Load and parse a compile_commands.json file.

    Args:
        path: Path to compile_commands.json (file) or a build directory
              containing compile_commands.json.

    Returns:
        List of parsed compile entries.

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If the file is not valid JSON or has wrong structure.
    """
    if path.is_dir():
        path = path / "compile_commands.json"

    if not path.exists():
        raise ValidationError(
            f"Compilation database not found: {path}. "
            "Ensure -p points to a directory containing compile_commands.json "
            "or to the file itself."
        )

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValidationError(
            f"Invalid JSON in compilation database {path}: {exc}"
        ) from exc

    if not isinstance(raw, list):
        raise ValidationError(
            f"compile_commands.json must be a JSON array, got {type(raw).__name__}"
        )

    # Resolve *path* itself (not just db_dir below) before taking its parent:
    # compile_commands.json is commonly symlinked from the source tree into an
    # out-of-tree build directory, and the response-file trust jail must be
    # the real (target) directory the entries' relative paths/@files actually
    # live beside -- otherwise every @file next to the symlink's *target* is
    # wrongly treated as outside the (symlink-parent) root and never expanded
    # (Codex review).
    resolved_path = _safe_resolve(path)
    db_dir = (resolved_path or path).parent
    entries: list[CompileEntry] = []
    # Shared across every entry in this database so a response file
    # referenced by many entries (a common pattern, and an amplification
    # vector for an untrusted database) is only actually read/tokenized
    # once, and so the aggregate expansion output across the whole database
    # stays bounded even once I/O is deduped (Codex review) -- see
    # _expand_response_files's _file_cache/_db_output_budget.
    file_cache: _ResponseFileCache = {}
    db_output_budget = [_MAX_RESPONSE_FILE_DB_OUTPUT_TOKENS]
    cache_token_budget = [_MAX_RESPONSE_FILE_CACHE_TOKENS]
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            _logger.warning("Skipping non-object entry at index %d", i)
            continue
        try:
            entries.append(
                CompileEntry.from_dict(
                    item, db_dir, file_cache, db_output_budget, cache_token_budget
                )
            )
        except (KeyError, TypeError, ValueError, OSError) as exc:
            _logger.warning("Skipping malformed entry at index %d: %s", i, exc)

    _logger.info("Loaded %d compile entries from %s", len(entries), path)
    return entries


#: ABI-relevant flags forwarded verbatim from a matched compile-DB entry into
#: the real castxml/clang header-parse command (via ``to_castxml_flags``'s
#: ``extra_flags``). Kept in sync with the broader
#: ``buildsource.adapters.base.ABI_RELEVANT_FLAG_PREFIXES`` list (that one
#: feeds the separate L3 build-evidence-drift diff, not the L2 header parse
#: itself) — data-model/calling-convention/target flags a real build used are
#: exactly the kind of thing that must reach the actual header parse, not
#: just be recorded as advisory build-evidence (P0/P1 toolchain-profile
#: audit): omitting them here silently re-introduces "header parse drift"
#: for -m32/-m64/-march=/-stdlib=/enum-packing/char-signedness builds even
#: though a matched compile_commands.json entry had the real flags.
_ABI_EXTRA_PREFIXES = (
    "-fabi-version=",
    "-fpack-struct=",
    "-fms-extensions",
    "-fno-exceptions",
    "-fno-rtti",
    "-fexceptions",
    "-frtti",
    "-stdlib=",
    "-m32",
    "-m64",
    "-march=",
    "-mtune=",
    "-mabi=",
    "-mfloat-abi=",
    "-mfpmath=",
    "-fshort-enums",
    "-fno-short-enums",
    "-fshort-wchar",
    "-fsigned-char",
    "-funsigned-char",
)


def _resolve_path(raw: str, directory: Path) -> Path:
    """Resolve a path string relative to *directory* if not absolute."""
    p = Path(raw)
    if not p.is_absolute():
        p = directory / p
    return p


def _try_consume_include(
    arg: str, arguments: list[str], i: int, directory: Path, ctx: BuildContext
) -> int:
    """Handle -I (combined and separate forms). Returns new index."""
    m = _INCLUDE_RE.match(arg)
    if m:
        ctx.include_paths.append(_resolve_path(m.group(1), directory))
        ctx.include_paths_explicit.append(Path(m.group(1)).is_absolute())
        return i + 1
    if arg == "-I" and i + 1 < len(arguments):
        ctx.include_paths.append(_resolve_path(arguments[i + 1], directory))
        ctx.include_paths_explicit.append(Path(arguments[i + 1]).is_absolute())
        return i + 2
    return i  # no match — caller must advance


def _try_consume_isystem(
    arg: str, arguments: list[str], i: int, directory: Path, ctx: BuildContext
) -> int:
    """Handle -isystem (combined and separate forms). Returns new index."""
    m = _ISYSTEM_RE.match(arg)
    if m:
        ctx.system_includes.append(_resolve_path(m.group(1), directory))
        ctx.system_includes_explicit.append(Path(m.group(1)).is_absolute())
        return i + 1
    if arg == "-isystem" and i + 1 < len(arguments):
        ctx.system_includes.append(_resolve_path(arguments[i + 1], directory))
        ctx.system_includes_explicit.append(Path(arguments[i + 1]).is_absolute())
        return i + 2
    return i  # no match — caller must advance


def _try_consume_target(
    arg: str, arguments: list[str], i: int, ctx: BuildContext
) -> int:
    """Handle --target= and -target (combined and separate forms). Returns new index."""
    m = _TARGET_RE.match(arg)
    if m:
        ctx.target_triple = m.group(1)
        return i + 1
    if arg in ("-target", "--target") and i + 1 < len(arguments):
        ctx.target_triple = arguments[i + 1]
        return i + 2
    return i  # no match — caller must advance


def _try_consume_sysroot(
    arg: str, arguments: list[str], i: int, directory: Path, ctx: BuildContext
) -> int:
    """Handle --sysroot=, --sysroot, and -isysroot forms. Returns new index.

    Relative sysroot paths are resolved against *directory* (the compile
    entry's working directory), matching the behaviour of -I and -isystem.
    Absolute paths are stored as-is.
    """
    m = _SYSROOT_RE.match(arg)
    if m:
        ctx.sysroot = _resolve_path(m.group(1), directory)
        return i + 1
    if arg in ("--sysroot", "-isysroot") and i + 1 < len(arguments):
        ctx.sysroot = _resolve_path(arguments[i + 1], directory)
        return i + 2
    return i  # no match — caller must advance


def _is_abi_extra_flag(arg: str) -> bool:
    """Return True for ABI-relevant flags that should be forwarded as extra_flags."""
    return bool(_VISIBILITY_RE.match(arg)) or arg.startswith(_ABI_EXTRA_PREFIXES)


def _try_consume_define_undef(
    arg: str, arguments: list[str], i: int, ctx: BuildContext
) -> int:
    """Handle -Dmacro[=value], -D macro[=value], -Umacro, -U macro.

    Handles both combined forms (``-DNAME=V``, ``-UNAME``) and split forms
    (``-D NAME=V``, ``-U NAME``).  Returns the new argument index after
    consuming this flag (and its value token if applicable), or *i* unchanged
    if the argument was not a define/undef flag.
    """
    m = _DEFINE_RE.match(arg)
    if m:
        ctx.defines[m.group(1)] = m.group(2)  # None if no =value
        return i + 1
    if arg == "-D" and i + 1 < len(arguments):
        # Split form: -D NAME[=VALUE]
        name, _, value = arguments[i + 1].partition("=")
        ctx.defines[name] = value if value else None
        return i + 2
    m = _UNDEF_RE.match(arg)
    if m:
        ctx.undefines.add(m.group(1))
        return i + 1
    if arg == "-U" and i + 1 < len(arguments):
        # Split form: -U NAME
        ctx.undefines.add(arguments[i + 1])
        return i + 2
    return i  # no match — caller must advance


def _consume_std_extra(arg: str, ctx: BuildContext) -> bool:
    """Handle -std=xxx and ABI-relevant extra flags. Returns True if consumed."""
    m = _STD_RE.match(arg)
    if m:
        ctx.language_standard = m.group(1)
        return True
    if _is_abi_extra_flag(arg):
        ctx.extra_flags.append(arg)
        return True
    return False


def _extract_flags(arguments: list[str], directory: Path) -> BuildContext:
    """Extract ABI-relevant flags from a compiler argument list.

    Parses -D, -U, -I, -isystem, -std=, --target=, --sysroot=, and
    other ABI-affecting flags.  Paths are resolved relative to the
    entry's working directory.
    """
    ctx = BuildContext()
    i = 0
    while i < len(arguments):
        arg = arguments[i]

        new_i = _try_consume_define_undef(arg, arguments, i, ctx)
        if new_i != i:
            i = new_i
            continue

        # -I and -isystem (combined and separate)
        new_i = _try_consume_include(arg, arguments, i, directory, ctx)
        if new_i != i:
            i = new_i
            continue

        new_i = _try_consume_isystem(arg, arguments, i, directory, ctx)
        if new_i != i:
            i = new_i
            continue

        if _consume_std_extra(arg, ctx):
            i += 1
            continue

        # --target=xxx or -target xxx (combined and separate)
        new_i = _try_consume_target(arg, arguments, i, ctx)
        if new_i != i:
            i = new_i
            continue

        # --sysroot=, --sysroot, -isysroot (combined and separate)
        new_i = _try_consume_sysroot(arg, arguments, i, directory, ctx)
        if new_i != i:
            i = new_i
            continue

        # Skip flags we don't care about (those that take a value token)
        i += 2 if (arg in _FLAGS_WITH_ARG and i + 1 < len(arguments)) else 1

    return ctx


def _header_included_by_tu(
    header_path: Path,
    entry: CompileEntry,
) -> bool:
    """Check if a TU's source file likely includes the given header.

    Uses a lightweight scan of the source file for #include directives
    that match the header path suffix (not just filename) to reduce
    false positives from unrelated headers with the same name.
    """
    try:
        source_content = entry.file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False

    header_name = header_path.name
    # First pass: quick check for the filename in any #include
    if header_name not in source_content:
        return False
    # Match #include "..." or #include <...> containing the header filename.
    # We check the matched path suffix against the actual header path to
    # reduce false positives from unrelated headers with the same name.
    pattern = re.compile(rf'#\s*include\s*[<"]([^>"]*{re.escape(header_name)})[>"]')
    for m in pattern.finditer(source_content):
        include_arg = m.group(1)
        # Check if the include argument is a suffix of the header path
        if str(header_path).endswith(include_arg):
            return True
        # Also accept bare filename match as fallback
        if include_arg == header_name:
            return True
    return False


def build_context_for_header(
    entries: list[CompileEntry],
    header_path: Path,
    source_filter: str | None = None,
) -> BuildContext:
    """Find the best TU for a header and derive its build context (ADR-020a).

    Strategy:
    1. Filter entries by source_filter glob if specified
    2. Find TUs that include the header (by scanning source files)
    3. If found, use the first matching TU's flags
    4. If not found, fall back to union strategy

    Args:
        entries: Parsed compile database entries.
        header_path: The public header to match.
        source_filter: Optional glob pattern to filter source files
                       (e.g., "src/libfoo/**").

    Returns:
        BuildContext with flags appropriate for parsing the header.
    """
    header_resolved = header_path.resolve()

    # Filter entries
    filtered = entries
    if source_filter:
        filtered = [e for e in entries if _entry_matches_filter(e, source_filter)]
        if not filtered:
            _logger.warning(
                "No compile entries match filter %r; using all entries",
                source_filter,
            )
            filtered = entries

    # Phase 1: Find TUs that directly include this header
    matching_entries: list[CompileEntry] = []
    for entry in filtered:
        if _header_included_by_tu(header_resolved, entry):
            matching_entries.append(entry)

    if matching_entries:
        if len(matching_entries) > 1:
            _logger.info(
                "Header %s included by %d TUs; using first match: %s",
                header_path.name,
                len(matching_entries),
                matching_entries[0].file.name,
            )
        entry = matching_entries[0]
        ctx = _extract_flags(entry.arguments, entry.directory)
        ctx.compile_db_path = entry.directory / "compile_commands.json"
        return ctx

    # Phase 2: Union fallback
    _logger.debug(
        "Header %s not matched to any TU; using union fallback",
        header_path.name,
    )
    return build_context_union_fallback(filtered)


def _std_sort_key(std: str) -> tuple[int, int]:
    """Numeric sort key for C/C++ standard strings.

    Maps standard names to (language, version) tuples for correct ordering.
    Handles draft names like c++2a, c++2b, c++2c (→ 20, 23, 26).
    """
    # Extract the numeric/draft suffix after the last occurrence of c/c++/gnu/gnu++
    m = re.search(r"(\d+[a-z]?)$", std)
    if not m:
        return (0, 0)
    suffix = m.group(1)
    is_cpp = "c++" in std or "gnu++" in std

    # Map draft names to release numbers
    draft_map = {"2a": 20, "2b": 23, "2c": 26}
    if suffix in draft_map:
        version = draft_map[suffix]
    elif suffix.isdigit():
        version = int(suffix)
    else:
        version = 0

    return (1 if is_cpp else 0, version)


def _filter_entries_by_glob(
    entries: list[CompileEntry], source_filter: str | None
) -> list[CompileEntry]:
    """Return entries matching *source_filter* glob, or all entries if no match."""
    if not source_filter:
        return entries
    filtered = [e for e in entries if _entry_matches_filter(e, source_filter)]
    return filtered if filtered else entries


def _merge_defines_from_contexts(
    contexts: list[BuildContext],
) -> tuple[dict[str, str | None], dict[str, list[str]]]:
    """Merge define maps from multiple contexts; track per-macro conflicts.

    Returns:
        (merged_defines, define_conflicts) — conflicts maps macro name to list of
        distinct seen values (including the first).
    """
    merged: dict[str, str | None] = {}
    conflicts: dict[str, list[str]] = {}
    for ctx in contexts:
        for macro, value in ctx.defines.items():
            val_str = value if value is not None else "(defined)"
            if macro in merged:
                existing = merged[macro]
                existing_str = existing if existing is not None else "(defined)"
                if existing_str != val_str:
                    if macro not in conflicts:
                        conflicts[macro] = [existing_str]
                    conflicts[macro].append(val_str)
            else:
                merged[macro] = value
    if conflicts:
        for macro, values in conflicts.items():
            _logger.warning(
                "Macro %s has conflicting values across TUs: %s; using first value",
                macro,
                ", ".join(sorted(set(values))),
            )
    return merged, conflicts


def _merge_path_list(contexts: list[BuildContext], attr: str) -> list[Path]:
    """Merge a list[Path] attribute from multiple contexts, deduplicating by resolved path."""
    seen: set[str] = set()
    merged: list[Path] = []
    for ctx in contexts:
        for p in getattr(ctx, attr):
            key = str(p.resolve())
            if key not in seen:
                seen.add(key)
                merged.append(p)
    return merged


def _pick_best_standard(contexts: list[BuildContext]) -> tuple[str | None, list[str]]:
    """Choose the highest language standard from all contexts.

    Returns:
        (lang_std, sorted_unique_standards) — lang_std may be None if none found.
    """
    raw = [ctx.language_standard for ctx in contexts if ctx.language_standard]
    standards = sorted(set(raw))
    if not standards:
        return None, standards
    cpp_stds = [s for s in standards if "c++" in s or "gnu++" in s]
    c_stds = [s for s in standards if s not in cpp_stds]
    if cpp_stds:
        return max(cpp_stds, key=_std_sort_key), standards
    return max(c_stds, key=_std_sort_key), standards


def _pick_target_sysroot(
    contexts: list[BuildContext],
) -> tuple[str | None, Path | None]:
    """Return the single consistent target triple and sysroot, warning on conflicts."""
    targets = {ctx.target_triple for ctx in contexts if ctx.target_triple}
    sysroots = {str(ctx.sysroot) for ctx in contexts if ctx.sysroot}

    target: str | None = None
    if len(targets) > 1:
        _logger.warning(
            "Conflicting target triples: %s; use --compiler-option to override",
            ", ".join(sorted(targets)),
        )
    elif targets:
        target = next(iter(targets))

    sysroot: Path | None = None
    if len(sysroots) > 1:
        _logger.warning(
            "Conflicting sysroots: %s; use --sysroot to override",
            ", ".join(sorted(sysroots)),
        )
    elif sysroots:
        sysroot = Path(next(iter(sysroots)))

    return target, sysroot


def _merge_extra_flags(contexts: list[BuildContext]) -> list[str]:
    """Merge extra_flags from all contexts, preserving order and deduplicating."""
    seen: set[str] = set()
    merged: list[str] = []
    for ctx in contexts:
        for f in ctx.extra_flags:
            if f not in seen:
                seen.add(f)
                merged.append(f)
    return merged


def build_context_union_fallback(
    entries: list[CompileEntry],
    source_filter: str | None = None,
) -> BuildContext:
    """Union strategy: merge flags from all TUs (ADR-020a fallback).

    Used when a header cannot be matched to a specific TU.  Unions
    defines and include paths, warns on conflicts.

    Args:
        entries: Parsed compile database entries.
        source_filter: Optional glob pattern to filter source files.

    Returns:
        BuildContext with merged flags.
    """
    filtered = _filter_entries_by_glob(entries, source_filter)
    if not filtered:
        return BuildContext()

    contexts = [_extract_flags(e.arguments, e.directory) for e in filtered]

    merged_defines, define_conflicts = _merge_defines_from_contexts(contexts)
    merged_undefines: set[str] = set()
    for ctx in contexts:
        merged_undefines |= ctx.undefines

    merged_includes = _merge_path_list(contexts, "include_paths")
    merged_sys_includes = _merge_path_list(contexts, "system_includes")
    lang_std, standards = _pick_best_standard(contexts)
    target, sysroot = _pick_target_sysroot(contexts)
    merged_extra = _merge_extra_flags(contexts)

    return BuildContext(
        defines=merged_defines,
        undefines=merged_undefines,
        include_paths=merged_includes,
        system_includes=merged_sys_includes,
        language_standard=lang_std,
        target_triple=target,
        sysroot=sysroot,
        extra_flags=merged_extra,
        compile_db_path=filtered[0].directory / "compile_commands.json",
        define_conflicts=define_conflicts,
        standard_variants=standards,
    )
