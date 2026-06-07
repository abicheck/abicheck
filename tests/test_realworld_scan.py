"""Real-world library ABI scan integration tests.

Compiles two versions of a small real-world C library (zlib-style API surface)
from source, runs abicheck, and validates against expected results. This tests
the full pipeline: compile → dump → compare → report.

For true external library testing, a CI job would download release tarballs.
This test uses locally-compiled synthetic libraries that mirror real-world API
patterns (struct layouts, versioned symbols, typedefs, enums).

Requires: gcc, castxml.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import textwrap
import warnings
from pathlib import Path

import pytest

from abicheck.checker import ChangeKind, Verdict


def _require_tool(name: str) -> None:
    if shutil.which(name) is None:
        pytest.skip(f"{name} not found in PATH")


# ═══════════════════════════════════════════════════════════════════════════
# Realistic C library API surface (zlib-style)
# ═══════════════════════════════════════════════════════════════════════════

V1_HEADER = """\
#ifndef LIBCOMPRESS_H
#define LIBCOMPRESS_H

/* Avoid <stddef.h> — castxml on macOS pulls in libc++ headers it cannot parse */
typedef __SIZE_TYPE__ size_t;

/* Version */
#define LIBCOMPRESS_VERSION "1.0.0"
#define LIBCOMPRESS_VERNUM  0x100

/* Error codes */
typedef enum {
    COMPRESS_OK        = 0,
    COMPRESS_ESTREAM   = -1,
    COMPRESS_EDATA     = -2,
    COMPRESS_EMEM      = -3,
    COMPRESS_EBUF      = -4
} compress_status;

/* Opaque stream handle */
typedef struct compress_stream_s compress_stream;

/* Configuration */
typedef struct {
    int   level;       /* 0-9 */
    int   window_bits; /* 8-15 */
    int   mem_level;   /* 1-9 */
} compress_options;

/* API */
const char *compress_version(void);
compress_stream *compress_init(const compress_options *opts);
compress_status  compress_feed(compress_stream *s, const void *in, size_t in_len);
compress_status  compress_flush(compress_stream *s, void *out, size_t *out_len);
void             compress_free(compress_stream *s);
size_t           compress_bound(size_t src_len);

#endif
"""

V1_SOURCE = """\
#include <stdlib.h>
#include <string.h>

typedef struct compress_stream_s {
    int   level;
    int   window_bits;
    int   mem_level;
    void *buf;
    size_t buf_len;
} compress_stream;

typedef enum {
    COMPRESS_OK        = 0,
    COMPRESS_ESTREAM   = -1,
    COMPRESS_EDATA     = -2,
    COMPRESS_EMEM      = -3,
    COMPRESS_EBUF      = -4
} compress_status;

typedef struct {
    int   level;
    int   window_bits;
    int   mem_level;
} compress_options;

const char *compress_version(void) { return "1.0.0"; }

compress_stream *compress_init(const compress_options *opts) {
    compress_stream *s = calloc(1, sizeof(*s));
    if (!s) return NULL;
    s->level = opts ? opts->level : 6;
    s->window_bits = opts ? opts->window_bits : 15;
    s->mem_level = opts ? opts->mem_level : 8;
    return s;
}

compress_status compress_feed(compress_stream *s, const void *in, size_t in_len) {
    if (!s) return COMPRESS_ESTREAM;
    (void)in; (void)in_len;
    return COMPRESS_OK;
}

compress_status compress_flush(compress_stream *s, void *out, size_t *out_len) {
    if (!s) return COMPRESS_ESTREAM;
    (void)out; (void)out_len;
    return COMPRESS_OK;
}

void compress_free(compress_stream *s) { free(s); }

size_t compress_bound(size_t src_len) { return src_len + src_len / 1000 + 12; }
"""

# ── V2: Compatible addition (new function, new enum member) ──────────────

V2_COMPAT_HEADER = """\
#ifndef LIBCOMPRESS_H
#define LIBCOMPRESS_H

/* Avoid <stddef.h> — castxml on macOS pulls in libc++ headers it cannot parse */
typedef __SIZE_TYPE__ size_t;

#define LIBCOMPRESS_VERSION "1.1.0"
#define LIBCOMPRESS_VERNUM  0x110

typedef enum {
    COMPRESS_OK        = 0,
    COMPRESS_ESTREAM   = -1,
    COMPRESS_EDATA     = -2,
    COMPRESS_EMEM      = -3,
    COMPRESS_EBUF      = -4,
    COMPRESS_EPARAM    = -5   /* NEW in 1.1 */
} compress_status;

typedef struct compress_stream_s compress_stream;

typedef struct {
    int   level;
    int   window_bits;
    int   mem_level;
} compress_options;

const char *compress_version(void);
compress_stream *compress_init(const compress_options *opts);
compress_status  compress_feed(compress_stream *s, const void *in, size_t in_len);
compress_status  compress_flush(compress_stream *s, void *out, size_t *out_len);
void             compress_free(compress_stream *s);
size_t           compress_bound(size_t src_len);

/* NEW in 1.1 */
compress_status  compress_reset(compress_stream *s);

#endif
"""

V2_COMPAT_IMPL = """\
#include <stdlib.h>
#include <string.h>

struct compress_stream_s {
    int   level;
    int   window_bits;
    int   mem_level;
    void *buf;
    size_t buf_len;
};

const char *compress_version(void) { return "1.1.0"; }

compress_stream *compress_init(const compress_options *opts) {
    compress_stream *s = calloc(1, sizeof(*s));
    if (!s) return NULL;
    s->level = opts ? opts->level : 6;
    s->window_bits = opts ? opts->window_bits : 15;
    s->mem_level = opts ? opts->mem_level : 8;
    return s;
}

compress_status compress_feed(compress_stream *s, const void *in, size_t in_len) {
    if (!s) return COMPRESS_ESTREAM;
    (void)in; (void)in_len;
    return COMPRESS_OK;
}

compress_status compress_flush(compress_stream *s, void *out, size_t *out_len) {
    if (!s) return COMPRESS_ESTREAM;
    (void)out; (void)out_len;
    return COMPRESS_OK;
}

void compress_free(compress_stream *s) { free(s); }

size_t compress_bound(size_t src_len) { return src_len + src_len / 1000 + 12; }

compress_status compress_reset(compress_stream *s) {
    if (!s) return COMPRESS_ESTREAM;
    s->buf_len = 0;
    return COMPRESS_OK;
}
"""

# Build V2 source by concatenating the header (for public API consistency)
# with the implementation. This ensures compress_version() returns "1.1.0"
# matching the header's LIBCOMPRESS_VERSION.
V2_COMPAT_SOURCE = V2_COMPAT_HEADER + "\n" + V2_COMPAT_IMPL

# ── V2: Breaking change (struct layout changed) ─────────────────────────

V2_BREAKING_HEADER = """\
#ifndef LIBCOMPRESS_H
#define LIBCOMPRESS_H

/* Avoid <stddef.h> — castxml on macOS pulls in libc++ headers it cannot parse */
typedef __SIZE_TYPE__ size_t;

#define LIBCOMPRESS_VERSION "2.0.0"
#define LIBCOMPRESS_VERNUM  0x200

typedef enum {
    COMPRESS_OK        = 0,
    COMPRESS_ESTREAM   = -1,
    COMPRESS_EDATA     = -2,
    COMPRESS_EMEM      = -3,
    COMPRESS_EBUF      = -4
} compress_status;

typedef struct compress_stream_s compress_stream;

/* BREAKING: field reordered and type widened */
typedef struct {
    int   window_bits;
    long  level;        /* was int → long (size change) */
    int   mem_level;
} compress_options;

const char *compress_version(void);
compress_stream *compress_init(const compress_options *opts);
compress_status  compress_feed(compress_stream *s, const void *in, size_t in_len);
compress_status  compress_flush(compress_stream *s, void *out, size_t *out_len);
void             compress_free(compress_stream *s);
/* BREAKING: compress_bound removed */

#endif
"""

V2_BREAKING_SOURCE = """\
#include <stdlib.h>
#include <string.h>

typedef struct compress_stream_s {
    long  level;
    int   window_bits;
    int   mem_level;
    void *buf;
    size_t buf_len;
} compress_stream;

typedef enum {
    COMPRESS_OK        = 0,
    COMPRESS_ESTREAM   = -1,
    COMPRESS_EDATA     = -2,
    COMPRESS_EMEM      = -3,
    COMPRESS_EBUF      = -4
} compress_status;

typedef struct {
    int   window_bits;
    long  level;
    int   mem_level;
} compress_options;

const char *compress_version(void) { return "2.0.0"; }

compress_stream *compress_init(const compress_options *opts) {
    compress_stream *s = calloc(1, sizeof(*s));
    if (!s) return NULL;
    s->level = opts ? opts->level : 6;
    s->window_bits = opts ? opts->window_bits : 15;
    s->mem_level = opts ? opts->mem_level : 8;
    return s;
}

compress_status compress_feed(compress_stream *s, const void *in, size_t in_len) {
    if (!s) return COMPRESS_ESTREAM;
    (void)in; (void)in_len;
    return COMPRESS_OK;
}

compress_status compress_flush(compress_stream *s, void *out, size_t *out_len) {
    if (!s) return COMPRESS_ESTREAM;
    (void)out; (void)out_len;
    return COMPRESS_OK;
}

void compress_free(compress_stream *s) { free(s); }
"""


def _build_lib(src: str, hdr: str, name: str, tmp_path: Path) -> tuple[Path, Path]:
    """Compile source into a .so and write the header file."""
    so_path = tmp_path / f"lib{name}.so"
    hdr_path = tmp_path / f"{name}.h"
    src_path = tmp_path / f"{name}.c"

    hdr_path.write_text(textwrap.dedent(hdr).strip(), encoding="utf-8")
    src_path.write_text(textwrap.dedent(src).strip(), encoding="utf-8")

    cmd = ["gcc", "-shared", "-fPIC", "-g", "-fvisibility=default",
           f"-I{tmp_path}", "-o", str(so_path), str(src_path)]
    if sys.platform == "darwin":
        # Set a consistent install_name so builds in different directories
        # don't produce different LC_ID_DYLIB values.
        cmd.insert(-1, f"-Wl,-install_name,lib{name}.so")
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        pytest.skip(f"Compilation failed: {r.stderr[:300]}")

    return so_path, hdr_path


def _scan(old_so, old_hdr, new_so, new_hdr, tmp_path):
    """Run the full abicheck pipeline: dump → compare."""
    from abicheck.checker import compare
    from abicheck.dumper import dump

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        old_snap = dump(old_so, headers=[old_hdr], version="old", compiler="cc")
        new_snap = dump(new_so, headers=[new_hdr], version="new", compiler="cc")

    return compare(old_snap, new_snap)


@pytest.mark.integration
class TestRealWorldCompatibleRelease:
    """v1.0 → v1.1: additive API, with macOS install-name drift."""

    def test_compatible_release(self, tmp_path):
        _require_tool("gcc")
        _require_tool("castxml")

        v1_so, v1_hdr = _build_lib(V1_SOURCE, V1_HEADER, "compress_v1", tmp_path)
        v2_so, v2_hdr = _build_lib(V2_COMPAT_SOURCE, V2_COMPAT_HEADER,
                                    "compress_v2", tmp_path)

        r = _scan(v1_so, v1_hdr, v2_so, v2_hdr, tmp_path)

        expected = Verdict.COMPATIBLE_WITH_RISK if sys.platform == "darwin" else Verdict.COMPATIBLE
        assert r.verdict == expected, (
            f"Expected {expected.value} for additive release; got {r.verdict}. "
            f"Changes: {[(c.kind.value, c.symbol) for c in r.changes]}"
        )
        assert not r.breaking

        kinds = {c.kind for c in r.changes}
        assert ChangeKind.ENUM_MEMBER_ADDED in kinds, "Expected COMPRESS_EPARAM to be ENUM_MEMBER_ADDED"
        if sys.platform == "darwin":
            assert ChangeKind.SONAME_CHANGED in kinds, "Expected platform install-name drift to be detected"

    def test_compatible_release_confidence(self, tmp_path):
        """Compatible release with full data → high confidence."""
        _require_tool("gcc")
        _require_tool("castxml")

        v1_so, v1_hdr = _build_lib(V1_SOURCE, V1_HEADER, "compress_v1", tmp_path)
        v2_so, v2_hdr = _build_lib(V2_COMPAT_SOURCE, V2_COMPAT_HEADER,
                                    "compress_v2", tmp_path)

        r = _scan(v1_so, v1_hdr, v2_so, v2_hdr, tmp_path)
        # With headers + ELF + DWARF, should have good confidence
        from abicheck.checker_policy import Confidence
        assert r.confidence in (Confidence.HIGH, Confidence.MEDIUM)


@pytest.mark.integration
class TestRealWorldBreakingRelease:
    """v1.0 → v2.0: struct layout change + function removed = BREAKING."""

    def test_breaking_release(self, tmp_path):
        _require_tool("gcc")
        _require_tool("castxml")

        v1_so, v1_hdr = _build_lib(V1_SOURCE, V1_HEADER, "compress_v1", tmp_path)
        v2_so, v2_hdr = _build_lib(V2_BREAKING_SOURCE, V2_BREAKING_HEADER,
                                    "compress_v2", tmp_path)

        r = _scan(v1_so, v1_hdr, v2_so, v2_hdr, tmp_path)

        assert r.verdict == Verdict.BREAKING, (
            f"Expected BREAKING for major release; got {r.verdict}"
        )
        assert r.breaking

        kinds = {c.kind for c in r.changes}
        # compress_bound was removed
        assert ChangeKind.FUNC_REMOVED in kinds, "Expected compress_bound removal"

    def test_breaking_release_has_multiple_changes(self, tmp_path):
        """Breaking release should detect multiple types of changes."""
        _require_tool("gcc")
        _require_tool("castxml")

        v1_so, v1_hdr = _build_lib(V1_SOURCE, V1_HEADER, "compress_v1", tmp_path)
        v2_so, v2_hdr = _build_lib(V2_BREAKING_SOURCE, V2_BREAKING_HEADER,
                                    "compress_v2", tmp_path)

        r = _scan(v1_so, v1_hdr, v2_so, v2_hdr, tmp_path)
        # Should detect at least 2 different kinds of changes
        kinds = {c.kind for c in r.changes}
        assert len(kinds) >= 2, (
            f"Expected multiple change types; got {kinds}"
        )


@pytest.mark.integration
class TestRealWorldNoChange:
    """v1.0 → v1.0: identical → NO_CHANGE."""

    def test_same_version_no_change(self, tmp_path):
        _require_tool("gcc")
        _require_tool("castxml")

        # Build in separate subdirectories with the same library name so the
        # embedded SONAMEs match — different filenames would produce different
        # SONAMEs and cause a spurious SONAME_CHANGED finding.
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        dir_a.mkdir()
        dir_b.mkdir()
        v1_so, v1_hdr = _build_lib(V1_SOURCE, V1_HEADER, "compress_v1", dir_a)
        v1b_so, v1b_hdr = _build_lib(V1_SOURCE, V1_HEADER, "compress_v1", dir_b)

        r = _scan(v1_so, v1_hdr, v1b_so, v1b_hdr, tmp_path)
        assert r.verdict == Verdict.NO_CHANGE, (
            f"Expected NO_CHANGE for identical source; got {r.verdict}. "
            f"Changes: {[(c.kind.value, c.symbol) for c in r.changes]}"
        )


@pytest.mark.integration
class TestRealWorldAbidiffParity:
    """Cross-validate compatible release against abidiff."""

    def test_compatible_release_abidiff_agrees(self, tmp_path):
        _require_tool("gcc")
        _require_tool("castxml")
        _require_tool("abidiff")

        v1_so, v1_hdr = _build_lib(V1_SOURCE, V1_HEADER, "compress_v1", tmp_path)
        v2_so, v2_hdr = _build_lib(V2_COMPAT_SOURCE, V2_COMPAT_HEADER,
                                    "compress_v2", tmp_path)

        # abicheck result
        r = _scan(v1_so, v1_hdr, v2_so, v2_hdr, tmp_path)

        # abidiff result
        ab_result = subprocess.run(
            ["abidiff", "--no-show-locs", str(v1_so), str(v2_so)],
            capture_output=True, text=True, timeout=30,
        )
        code = ab_result.returncode
        # Check error bit first — abidiff sets bit 0 on tool errors
        if code & 1:
            pytest.fail(
                f"abidiff returned error (code {code}): "
                f"{ab_result.stderr[:300]}"
            )
        if code == 0:
            ab_verdict = "NO_CHANGE"
        elif code & 8:
            ab_verdict = "BREAKING"
        elif code & 4:
            ab_verdict = "COMPATIBLE"
        else:
            ab_verdict = "UNKNOWN"

        # Both should agree this is not BREAKING
        assert r.verdict != Verdict.BREAKING
        assert ab_verdict != "BREAKING"
        # Both tools should classify this in the "safe" category
        ac_safe = r.verdict in (Verdict.NO_CHANGE, Verdict.COMPATIBLE,
                                 Verdict.COMPATIBLE_WITH_RISK)
        ab_safe = ab_verdict in ("NO_CHANGE", "COMPATIBLE")
        assert ac_safe and ab_safe, (
            f"Parity: abicheck={r.verdict.value}, abidiff={ab_verdict}"
        )
