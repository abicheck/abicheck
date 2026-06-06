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

"""Dumper — headers + .so → AbiSnapshot via castxml."""
from __future__ import annotations

import hashlib
import logging
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import warnings
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from xml.etree.ElementTree import (
    Element,  # type annotation only; parsing uses defusedxml
)

if TYPE_CHECKING:
    from .dwarf_advanced import AdvancedDwarfMetadata
    from .dwarf_metadata import DwarfMetadata
    from .elf_metadata import ElfMetadata

from defusedxml import ElementTree as DefusedET

from .dumper_castxml import (
    _CastxmlParser as _CastxmlParser,
)
from .dumper_castxml import (
    _parse_vtable_index as _parse_vtable_index,
)
from .dumper_castxml import (
    _vt_sort_key as _vt_sort_key,
)
from .elf_symbol_filter import is_abi_relevant_elf_symbol
from .errors import SnapshotError, ValidationError
from .model import (
    AbiSnapshot,
    ElfVisibility,
    Function,
    RecordType,
    Variable,
    Visibility,
)

log = logging.getLogger(__name__)


def _castxml_available() -> bool:
    return shutil.which("castxml") is not None


_HIDDEN_VIS = frozenset({"STV_HIDDEN", "STV_INTERNAL"})


def _is_abi_relevant_symbol(name: str) -> bool:
    """Return False for symbols that are NOT part of the library's public ABI.

    Filters out (in ELF-only mode):
    1. GCC/compiler internal symbols (``ix86_*``, ``_ZGV*``, ``__svml_*`` …)
       that leak into ``.dynsym`` through a statically-linked runtime.
    2. Transitive C++ stdlib symbols (``_ZNSt*``, ``_ZTI*`` …) that appear
       in ``.dynsym`` via weak linkage from libstdc++ / libc++.
    3. Private C symbols that use ``__`` as a namespace separator
       (e.g. ``H5C__flush``, ``MPI__send``).  These follow an internal
       naming convention and are *not* part of the public API, even though
       they may have global ELF visibility.
    """
    return is_abi_relevant_elf_symbol(name)


def _pyelftools_exported_symbols(so_path: Path) -> tuple[set[str], set[str]]:
    """Return (exported_dynamic, exported_static) sets of mangled symbol names.

    Uses pyelftools (pure Python) instead of shelling out to readelf.
    - exported_dynamic: symbols from .dynsym, truly exported via ELF
    - exported_static: symbols from .symtab (all symbols including static)
    """
    from elftools.common.exceptions import ELFError
    from elftools.elf.elffile import ELFFile
    from elftools.elf.sections import SymbolTableSection

    def _extract_symbols(elf: Any, section_name: str) -> set[str]:
        syms: set[str] = set()
        section = elf.get_section_by_name(section_name)
        if section is None or not isinstance(section, SymbolTableSection):
            return syms
        for sym in section.iter_symbols():
            shndx = sym.entry.st_shndx
            if shndx in ("SHN_UNDEF", "SHN_ABS"):
                continue
            bind = sym.entry.st_info.bind
            vis = sym.entry.st_other.visibility
            if bind in ("STB_GLOBAL", "STB_WEAK") and vis not in _HIDDEN_VIS:
                name = sym.name
                if name and _is_abi_relevant_symbol(name):
                    syms.add(name)
        return syms

    try:
        with open(so_path, "rb") as f:
            elf: Any = ELFFile(f)  # type: ignore[no-untyped-call]
            exported_dynamic = _extract_symbols(elf, ".dynsym")
            try:
                exported_static = _extract_symbols(elf, ".symtab")
            except (ELFError, OSError):
                exported_static = set(exported_dynamic)
            return exported_dynamic, exported_static
    except (ELFError, OSError) as exc:
        raise SnapshotError(f"Failed to parse ELF file {so_path}: {exc}") from exc


def _cache_key(
    headers: list[Path],
    extra_includes: list[Path],
    compiler: str,
    *,
    gcc_path: str | None = None,
    gcc_prefix: str | None = None,
    gcc_options: str | None = None,
    sysroot: Path | None = None,
    nostdinc: bool = False,
    lang: str | None = None,
) -> str:
    h = hashlib.sha256()
    for p in sorted(str(x.resolve()) for x in headers):
        h.update(p.encode())
        try:
            h.update(str(os.path.getmtime(p)).encode())
        except OSError:
            pass
    # Also hash mtimes of files in extra_include dirs (catches most transitive changes)
    for inc_dir in sorted(str(x) for x in extra_includes):
        inc_path = Path(inc_dir)
        h.update(inc_dir.encode())
        if inc_path.is_dir():
            for f in sorted(inc_path.rglob("*.h")) + sorted(inc_path.rglob("*.hpp")):
                try:
                    h.update(str(f).encode())
                    h.update(str(f.stat().st_mtime).encode())
                except OSError:
                    pass
    h.update(compiler.encode())
    # Include toolchain parameters so different cross-compilation configs
    # produce distinct cache entries
    h.update(f"gcc_path={gcc_path or ''}".encode())
    h.update(f"gcc_prefix={gcc_prefix or ''}".encode())
    h.update(f"gcc_options={gcc_options or ''}".encode())
    h.update(f"sysroot={sysroot or ''}".encode())
    h.update(f"nostdinc={nostdinc}".encode())
    h.update(f"lang={lang or ''}".encode())
    return h.hexdigest()


def _cache_path(key: str) -> Path:
    if sys.platform == "win32":
        # Use %LOCALAPPDATA%/abi_check/castxml on Windows
        local = os.environ.get("LOCALAPPDATA")
        if local:
            cache_dir = Path(local) / "abi_check" / "castxml"
        else:
            cache_dir = Path.home() / "AppData" / "Local" / "abi_check" / "castxml"
    else:
        cache_dir = Path.home() / ".cache" / "abi_check" / "castxml"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / f"{key}.xml"


# C++ file extensions that unambiguously indicate C++ content.
_CPP_EXTENSIONS = frozenset({".hpp", ".hxx", ".hh", ".h++", ".tpp"})

# Structural C++ patterns — match actual declarations, not keywords in comments.
# These are compiled regexes applied line-by-line to non-comment lines.
_CPP_PATTERNS = [
    re.compile(rb"^\s*class\s+\w+\s*[:{]"),          # class Foo { / class Foo :
    re.compile(rb"^\s*namespace\s+\w+"),               # namespace ns
    re.compile(rb"^\s*template\s*<"),                  # template<...>
    re.compile(rb"^\s*using\s+\w+\s*="),               # using alias = ...
    re.compile(rb'^\s*extern\s+"C"'),                  # extern "C" — castxml always uses C++ mode
    re.compile(rb"^\s*public\s*:"),                     # public:
    re.compile(rb"^\s*private\s*:"),                    # private:
    re.compile(rb"^\s*protected\s*:"),                  # protected:
    # C++ keywords that can appear anywhere in a line (not just at start)
    re.compile(rb"\bvirtual\s+"),                       # virtual member functions
    re.compile(rb"(?<!\w)~\w+\s*\("),                     # destructor ~Foo()
    re.compile(rb":\s*public\s+\w+"),                   # struct Derived : public Base
    re.compile(rb":\s*private\s+\w+"),                  # : private Base
    re.compile(rb":\s*protected\s+\w+"),                # : protected Base
    re.compile(rb"\bclass\s+\w+\s*[{;]"),              # class anywhere (forward decl or def)
    re.compile(rb"\bconst\s+\w[\w:]*\s*&"),               # const Type& reference (C++ idiom)
    re.compile(rb"\bstatic_cast\b"),                    # C++ cast
    re.compile(rb"\bconstexpr\b"),                      # C++ constexpr
    re.compile(rb"\bnullptr\b"),                        # C++ nullptr
    re.compile(rb"\bnoexcept\b"),                       # C++ noexcept
    re.compile(rb"\boverride\b"),                           # C++ override specifier
]


# Structural C++20 patterns — concepts and requires-expressions. When any
# of these appears in a header, castxml must be invoked with a C++20-aware
# `-std=` flag or it will fail to parse the file. The patterns target the
# definition site (`concept X = ...`, `requires(...) {`, `template <Foo T>`-
# style constrained template parameters) rather than uses, so we don't
# over-trigger.
_CPP20_PATTERNS = [
    re.compile(rb"^\s*concept\s+\w+\s*="),          # concept Addable = ...
    re.compile(rb"\brequires\s*\("),                # requires(T a, T b) { ... }
    re.compile(rb"\brequires\s+\w"),                # template<T> requires Foo<T>
]


def _detect_cpp20_headers(header_paths: list[Path]) -> bool:
    """Return True if any header contains C++20-only syntax (concept/requires).

    Used to decide whether to pass ``-std=gnu++20`` to castxml. castxml's
    default standard is whatever the underlying compiler defaults to
    (usually C++17 on modern gcc), which does not accept ``concept``
    declarations. This detection is conservative: only definition-site
    syntax counts, not the keyword in arbitrary text.
    """
    for p in header_paths:
        try:
            content = p.read_bytes()
        except OSError:
            continue
        content = re.sub(rb"/\*.*?\*/", b"", content, flags=re.DOTALL)
        for line in content.split(b"\n"):
            stripped = line.split(b"//")[0]
            if any(pat.search(stripped) for pat in _CPP20_PATTERNS):
                return True
    return False


def _detect_cpp_headers(header_paths: list[Path]) -> bool:
    """Auto-detect whether headers require C++ compilation mode (FIX-A).

    Returns True if any header has a C++ extension or contains structural
    C++ syntax (class/namespace/template declarations on non-comment lines).

    Note: ``extern "C"`` (even inside ``#ifdef __cplusplus`` guards) is treated
    as a C++ indicator because castxml always parses in C++ mode — passing
    ``-x c`` would conflict with ``__cplusplus`` being defined internally.
    """
    for p in header_paths:
        if p.suffix.lower() in _CPP_EXTENSIONS:
            return True
        try:
            content = p.read_bytes()
        except OSError:
            continue
        # Strip C-style block comments to reduce false positives
        content = re.sub(rb"/\*.*?\*/", b"", content, flags=re.DOTALL)
        for line in content.split(b"\n"):
            # Skip C++ line comments
            stripped = line.split(b"//")[0]
            if any(pat.search(stripped) for pat in _CPP_PATTERNS):
                return True
    return False


def _resolve_compiler_binary(
    compiler: str,
    gcc_path: str | None,
    gcc_prefix: str | None,
) -> tuple[str, str]:
    """Resolve the compiler binary and dialect (gnu/msvc) for castxml.

    Returns (cc_bin, cc_id) where cc_id is "gnu" or "msvc".
    """
    _cc_map = {"c++": "g++", "cc": "gcc", "g++": "g++", "gcc": "gcc",
               "clang++": "clang++", "clang": "clang"}

    if gcc_path:
        cc_bin = gcc_path
    elif gcc_prefix:
        suffix = "g++" if compiler in ("c++", "g++", "clang++") else "gcc"
        cc_bin = f"{gcc_prefix}{suffix}"
    else:
        cc_bin = _cc_map.get(compiler, compiler)

    exe_name = Path(cc_bin).name.lower()
    cc_id = "msvc" if exe_name in ("cl", "cl.exe") else "gnu"
    return cc_bin, cc_id


def _build_castxml_command(
    cc_bin: str, cc_id: str,
    extra_includes: list[Path],
    out_xml: Path, agg_path: Path,
    *,
    sysroot: Path | None = None,
    nostdinc: bool = False,
    gcc_options: str | None = None,
    force_cpp: bool = False,
    force_cpp20: bool = False,
) -> list[str]:
    """Build the castxml command line."""
    cmd = ["castxml", "--castxml-output=1",
           f"--castxml-cc-{cc_id}", cc_bin]
    for inc in extra_includes:
        cmd += ["-I", str(inc)]

    if sysroot:
        cmd += [f"--sysroot={sysroot.as_posix()}"]
    if nostdinc:
        cmd += ["-nostdinc"]
    if gcc_options:
        cmd += shlex.split(gcc_options, posix=os.name != "nt")

    # Workaround: castxml with --castxml-cc-gnu gcc auto-injects -std=gnu++17
    # which is rejected when parsing a .h file in C mode.
    if not force_cpp and cc_id == "gnu":
        cmd += ["-x", "c", "-std=gnu11"]
    elif force_cpp20 and not (
        gcc_options
        and ("-std=" in gcc_options or "/std:" in gcc_options)
    ):
        # Headers contain C++20-only syntax (concept / requires-expression).
        # Castxml's default standard is whatever the host compiler picks
        # (usually C++17 on modern gcc / MSVC), which rejects concepts.
        # Force C++20 unless the caller already supplied an explicit -std=.
        # MSVC uses /std:c++20; gcc/clang use -std=gnu++20.
        if cc_id == "msvc":
            cmd += ["/std:c++20"]
        else:
            cmd += ["-x", "c++", "-std=gnu++20"]

    cmd += ["-o", str(out_xml), str(agg_path)]
    return cmd


def _validate_castxml_output(
    result: subprocess.CompletedProcess[str],
    out_xml: Path,
    headers: list[Path],
    force_cpp: bool,
) -> Element:
    """Validate castxml output and return parsed XML root."""
    if result.returncode != 0:
        hint = ""
        if not force_cpp and _detect_cpp_headers(headers):
            hint = (
                "\n\nHint: The header files appear to contain C++ syntax "
                "(class, namespace, template) but --lang c was specified. "
                "Try removing --lang or using --lang c++."
            )
        raise SnapshotError(
            f"castxml failed (exit {result.returncode}):\n{result.stderr[:2000]}{hint}"
        )
    if not out_xml.exists() or out_xml.stat().st_size == 0:
        stderr_snippet = result.stderr[:1000].strip()
        detail = f"\ncastxml stderr: {stderr_snippet}" if stderr_snippet else ""
        raise SnapshotError(
            f"castxml exited 0 but produced no output file (or empty file).{detail}"
        )
    try:
        root = cast(Element, DefusedET.parse(str(out_xml)).getroot())
    except Exception as xml_exc:
        stderr_snippet = result.stderr[:1000].strip()
        detail = f"\ncastxml stderr: {stderr_snippet}" if stderr_snippet else ""
        raise SnapshotError(
            f"castxml produced invalid XML: {xml_exc}{detail}"
        ) from xml_exc
    if len(root) == 0:
        stderr_snippet = result.stderr[:1000].strip()
        detail = f"\ncastxml stderr: {stderr_snippet}" if stderr_snippet else ""
        raise SnapshotError(
            f"castxml produced an empty XML document (no declarations found). "
            f"Check that the header paths are correct and the compiler can "
            f"parse them.{detail}"
        )
    return root


def _castxml_dump(
    headers: list[Path],
    extra_includes: list[Path],
    compiler: str = "c++",
    *,
    gcc_path: str | None = None,
    gcc_prefix: str | None = None,
    gcc_options: str | None = None,
    sysroot: Path | None = None,
    nostdinc: bool = False,
    lang: str | None = None,
) -> Element:
    """Run castxml on headers and return parsed XML root.

    Args:
        compiler: "c++" (maps to g++) or "cc" (maps to gcc).
        gcc_path: Explicit path to a GCC/G++ cross-compiler binary.
        gcc_prefix: Cross-toolchain prefix (e.g. "aarch64-linux-gnu-").
        gcc_options: Extra compiler flags passed through to castxml.
        sysroot: Alternative system root directory.
        nostdinc: If True, do not search standard system include paths.
        lang: Force language ("C" or "C++").  If "C", aggregated header uses .h extension.
    """
    if not _castxml_available():
        raise SnapshotError(
            "castxml not found in PATH. Install with: apt install castxml, "
            "brew install castxml, conda install -c conda-forge castxml, "
            "or choco install castxml (Windows); then ensure castxml is in PATH."
        )

    # Check disk cache
    key = _cache_key(
        headers, extra_includes, compiler,
        gcc_path=gcc_path, gcc_prefix=gcc_prefix, gcc_options=gcc_options,
        sysroot=sysroot, nostdinc=nostdinc, lang=lang,
    )
    cached = _cache_path(key)
    if cached.exists():
        try:
            _cached_root = DefusedET.parse(str(cached)).getroot()
        except Exception:
            _cached_root = None
        if _cached_root is None:
            cached.unlink(missing_ok=True)
        else:
            return cast(Element, _cached_root)

    cc_bin, cc_id = _resolve_compiler_binary(compiler, gcc_path, gcc_prefix)

    with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as tmp:
        out_xml = Path(tmp.name)

    # Determine aggregate header extension: .h for C-only, .hpp for C++ (FIX-A).
    force_cpp = lang and lang.upper() in ("C++", "CPP")
    if not lang:
        force_cpp = _detect_cpp_headers(headers)
    agg_ext = ".hpp" if force_cpp else ".h"

    # Detect C++20 concept / requires syntax separately — castxml's default
    # standard (typically C++17) rejects these, so we need to override
    # the standard explicitly. Only meaningful when we're already in C++ mode.
    force_cpp20 = bool(force_cpp) and _detect_cpp20_headers(headers)

    with tempfile.NamedTemporaryFile(suffix=agg_ext, mode="w", delete=False) as agg:
        for h in headers:
            agg.write(f'#include "{h.resolve()}"\n')
        agg_path = Path(agg.name)

    cmd = _build_castxml_command(
        cc_bin, cc_id, extra_includes, out_xml, agg_path,
        sysroot=sysroot, nostdinc=nostdinc, gcc_options=gcc_options,
        force_cpp=bool(force_cpp),
        force_cpp20=force_cpp20,
    )

    try:
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=False)
        except subprocess.TimeoutExpired as exc:
            stderr_snippet = ""
            if exc.stderr:
                text = exc.stderr if isinstance(exc.stderr, str) else exc.stderr.decode("utf-8", errors="replace")
                stderr_snippet = f"\nPartial stderr: {text[:1000].strip()}"
            raise SnapshotError(
                f"castxml timed out after 120 seconds. The header file may contain "
                f"syntax that causes the compiler to hang. Check that the header "
                f"is valid and can be compiled with gcc/g++.{stderr_snippet}"
            ) from exc
        root = _validate_castxml_output(result, out_xml, headers, bool(force_cpp))
        shutil.copy2(str(out_xml), str(cached))
        return root
    finally:
        agg_path.unlink(missing_ok=True)
        out_xml.unlink(missing_ok=True)



# castxml parser + helpers moved to dumper_castxml (see top-of-file imports)


def _detect_format(path: Path) -> str:
    """Detect binary format from magic bytes. Returns 'elf', 'macho', 'pe', or 'unknown'."""
    try:
        with open(path, "rb") as f:
            magic = f.read(4)
    except OSError:
        return "unknown"
    if magic == b"\x7fELF":
        return "elf"
    _macho_magics = {
        b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe",
        b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe",
        b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca",
        b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca",
    }
    if magic in _macho_magics:
        return "macho"
    if magic[:2] == b"MZ":
        return "pe"
    return "unknown"


def dump(
    so_path: Path,
    headers: list[Path],
    extra_includes: list[Path] | None = None,
    version: str = "unknown",
    compiler: str = "c++",
    *,
    gcc_path: str | None = None,
    gcc_prefix: str | None = None,
    gcc_options: str | None = None,
    sysroot: Path | None = None,
    nostdinc: bool = False,
    lang: str | None = None,
    dwarf_only: bool = False,
    debug_format: str | None = None,
    public_headers: list[Path] | None = None,
    public_header_dirs: list[Path] | None = None,
) -> AbiSnapshot:
    """Create an AbiSnapshot from a shared library + headers.

    Supports ELF (.so), Mach-O (.dylib), and PE (.dll) binaries.
    Binary format is auto-detected from magic bytes.  For all formats,
    castxml header analysis is performed when *headers* are provided.

    Args:
        so_path: Path to the shared library (.so / .dylib / .dll).
        headers: List of public header files to parse.
        extra_includes: Additional -I include directories for castxml.
        version: Version string for the snapshot (e.g. "1.2.3").
        compiler: Compiler frontend for castxml ("c++" or "cc").
        gcc_path: Explicit path to a GCC/G++ cross-compiler binary.
        gcc_prefix: Cross-toolchain prefix (e.g. "aarch64-linux-gnu-").
        gcc_options: Extra compiler flags passed through to castxml.
        sysroot: Alternative system root directory.
        nostdinc: If True, do not search standard system include paths.
        lang: Force language ("C" or "C++").
        dwarf_only: If True, force DWARF-only mode even when headers
            are available (ADR-003).
        debug_format: Force debug format for ELF inputs: "dwarf", "btf", or "ctf".
            None = auto-detect (DWARF preferred for userspace, BTF for kernel).
            Ignored for Mach-O and PE binaries.
        public_headers: Explicit public-header files used only to classify
            declaration provenance (ADR-015). When empty, every declaration's
            origin stays UNKNOWN and behaviour is unchanged.
        public_header_dirs: Directories whose headers are treated as public
            for provenance classification.

    Returns:
        AbiSnapshot with functions, variables, and types populated.
    """
    fmt = _detect_format(so_path)

    if fmt == "macho":
        snapshot = _dump_macho(
            so_path, headers, extra_includes or [], version, compiler,
            gcc_path=gcc_path, gcc_prefix=gcc_prefix, gcc_options=gcc_options,
            sysroot=sysroot, nostdinc=nostdinc, lang=lang,
            dwarf_only=dwarf_only,
            public_headers=public_headers, public_header_dirs=public_header_dirs,
        )
    elif fmt == "pe":
        snapshot = _dump_pe(
            so_path, headers, extra_includes or [], version, compiler,
            gcc_path=gcc_path, gcc_prefix=gcc_prefix, gcc_options=gcc_options,
            sysroot=sysroot, nostdinc=nostdinc, lang=lang,
            public_headers=public_headers, public_header_dirs=public_header_dirs,
        )
    elif fmt == "elf":
        snapshot = _dump_elf(
            so_path, headers, extra_includes or [], version, compiler,
            gcc_path=gcc_path, gcc_prefix=gcc_prefix, gcc_options=gcc_options,
            sysroot=sysroot, nostdinc=nostdinc, lang=lang,
            dwarf_only=dwarf_only,
            debug_format=debug_format,
            public_headers=public_headers, public_header_dirs=public_header_dirs,
        )
    else:
        from .binary_utils import detect_archive
        if detect_archive(so_path):
            raise ValidationError(
                f"'{so_path}' is a static/import library archive (.a/.lib), which "
                "abicheck does not analyse — it compares single linkable images "
                "(shared libraries and objects). Extract the members (e.g. "
                "`ar x lib.a`) and compare the resulting object files or the shared "
                "library built from them instead."
            )
        raise ValidationError(
            f"Unrecognised binary format for {so_path}: "
            f"expected ELF, Mach-O, or PE but detected {fmt!r}. "
            f"Ensure the file is a valid shared library."
        )

    # Note: from_headers (the HEADER_AWARE evidence-tier signal) is set by the
    # format-specific builders (_dump_elf / _dump_pe / _dump_macho) at the point
    # castxml actually parses headers, so every entry point — including the CLI
    # and service native-binary paths that call those builders directly (e.g.
    # service._try_header_scoped_dump), bypassing this function — records it
    # correctly. DWARF-only and symbols-only builds leave it False.

    # Tag declaration provenance (source_header + origin). Always derives
    # source_header from the parsed source location; origin is only
    # classified when a public-header set is supplied (ADR-015, D4).
    from .provenance import apply_provenance
    return apply_provenance(snapshot, public_headers, public_header_dirs)


def _is_kernel_binary(path: Path) -> bool:
    """Heuristic: is this a kernel binary (vmlinux, *.ko, *.ko.xz, *.ko.zst)?"""
    name = path.name
    if name == "vmlinux":
        return True
    suffixes = path.suffixes  # e.g. ['.ko', '.xz']
    suffix_str = "".join(suffixes)
    if suffix_str in (".ko", ".ko.xz", ".ko.zst", ".ko.gz"):
        return True
    # Check for .modinfo section (kernel module indicator)
    try:
        from elftools.elf.elffile import ELFFile
        with open(path, "rb") as f:
            elf = ELFFile(f)  # type: ignore[no-untyped-call]
            return elf.get_section_by_name(".modinfo") is not None  # type: ignore[no-untyped-call]
    except Exception:  # noqa: BLE001
        return False


def _resolve_debug_metadata(
    so_path: Path,
    debug_format: str | None,
) -> tuple[DwarfMetadata, AdvancedDwarfMetadata]:
    """Resolve debug metadata using the specified or auto-detected format.

    Returns (dwarf_meta, dwarf_adv) — the same types as parse_dwarf().
    BTF/CTF data is converted to DwarfMetadata for checker compatibility.
    """
    from .dwarf_advanced import AdvancedDwarfMetadata

    if debug_format == "btf":
        from .btf_metadata import parse_btf_metadata
        btf = parse_btf_metadata(so_path)
        if not btf.has_btf:
            log.warning("BTF requested but no .BTF section in %s", so_path)
        return btf.to_dwarf_metadata(), AdvancedDwarfMetadata()

    if debug_format == "ctf":
        from .ctf_metadata import parse_ctf_metadata
        ctf = parse_ctf_metadata(so_path)
        if not ctf.has_ctf:
            log.warning("CTF requested but no .ctf section in %s", so_path)
        return ctf.to_dwarf_metadata(), AdvancedDwarfMetadata()

    if debug_format == "dwarf":
        from .dwarf_unified import parse_dwarf
        return parse_dwarf(so_path)

    if debug_format is not None:
        raise ValueError(
            f"Invalid debug_format {debug_format!r}; expected 'dwarf', 'btf', or 'ctf'."
        )

    # Auto-detect: kernel binaries prefer BTF, userspace prefers DWARF
    from .btf_metadata import has_btf_section, parse_btf_metadata
    from .ctf_metadata import has_ctf_section, parse_ctf_metadata
    from .dwarf_unified import parse_dwarf

    is_kernel = _is_kernel_binary(so_path)

    if is_kernel:
        # BTF > DWARF > CTF for kernel binaries
        if has_btf_section(so_path):
            btf = parse_btf_metadata(so_path)
            if btf.has_btf:
                log.info("Using BTF debug info from %s (kernel binary)", so_path)
                return btf.to_dwarf_metadata(), AdvancedDwarfMetadata()

    # DWARF > BTF > CTF for userspace (or kernel fallback)
    dwarf_meta, dwarf_adv = parse_dwarf(so_path)
    if dwarf_meta.has_dwarf:
        return dwarf_meta, dwarf_adv

    # Fallback to BTF if DWARF not available
    if has_btf_section(so_path):
        btf = parse_btf_metadata(so_path)
        if btf.has_btf:
            log.info("No DWARF, falling back to BTF in %s", so_path)
            return btf.to_dwarf_metadata(), AdvancedDwarfMetadata()

    # Fallback to CTF
    if has_ctf_section(so_path):
        ctf = parse_ctf_metadata(so_path)
        if ctf.has_ctf:
            log.info("No DWARF/BTF, falling back to CTF in %s", so_path)
            return ctf.to_dwarf_metadata(), AdvancedDwarfMetadata()

    # No debug info at all — return empty DWARF metadata
    return dwarf_meta, dwarf_adv


_ELF_VIS_MAP: dict[str, ElfVisibility] = {
    "default": ElfVisibility.DEFAULT,
    "protected": ElfVisibility.PROTECTED,
    "hidden": ElfVisibility.HIDDEN,
    "internal": ElfVisibility.INTERNAL,
}


def _populate_elf_visibility(snap: AbiSnapshot) -> None:
    """Populate elf_visibility on Function/Variable from ELF metadata symbols."""
    if snap.elf is None:
        return
    sym_map = snap.elf.symbol_map
    for func in snap.functions:
        elf_sym = sym_map.get(func.mangled)
        if elf_sym is not None:
            func.elf_visibility = _ELF_VIS_MAP.get(elf_sym.visibility)
    for var in snap.variables:
        elf_sym = sym_map.get(var.mangled)
        if elf_sym is not None:
            var.elf_visibility = _ELF_VIS_MAP.get(elf_sym.visibility)


def _elf_classify_symbols(
    elf_meta: ElfMetadata,
    exported_dynamic: set[str],
) -> tuple[set[str], set[str], set[str], set[str]]:
    """Split ELF metadata symbols into typed subsets for the no-header path.

    Returns ``(exported_dynamic, funcs, objects, tls)`` where *exported_dynamic*
    may be the original fallback set when *elf_meta* has no symbols.
    """
    from .elf_metadata import SymbolType

    exported_dynamic_funcs: set[str] = exported_dynamic  # fallback
    exported_dynamic_objects: set[str] = set()
    exported_dynamic_tls: set[str] = set()
    if elf_meta.symbols:
        # Apply the shared ABI-relevance filter here too: this no-header path
        # rebuilds the exported sets directly from ``elf_meta.symbols`` rather
        # than the already-filtered ``_pyelftools_exported_symbols`` result, so
        # lifecycle stubs (``_init``/``_fini``) and transitive runtime symbols
        # would otherwise re-enter the symbol-only ABI surface as ELF_ONLY
        # functions. Keeping it consistent with the DWARF-backed path.
        exported_dynamic_funcs = {
            sym.name for sym in elf_meta.symbols
            if sym.sym_type in (SymbolType.FUNC, SymbolType.IFUNC, SymbolType.NOTYPE)
            and is_abi_relevant_elf_symbol(sym.name)
        }
        exported_dynamic_objects = {
            sym.name for sym in elf_meta.symbols
            if sym.sym_type == SymbolType.OBJECT
            and is_abi_relevant_elf_symbol(sym.name)
        }
        exported_dynamic_tls = {
            sym.name for sym in elf_meta.symbols
            if sym.sym_type == SymbolType.TLS
            and is_abi_relevant_elf_symbol(sym.name)
        }
        # Full set for CastxmlParser: determines PUBLIC vs ELF_ONLY visibility
        exported_dynamic = exported_dynamic_funcs | exported_dynamic_objects | exported_dynamic_tls
    return exported_dynamic, exported_dynamic_funcs, exported_dynamic_objects, exported_dynamic_tls


def _elf_lang_to_profile(lang: str | None) -> str | None:
    """Convert a ``--lang`` flag value to an internal language-profile string."""
    if lang is None:
        return None
    lu = lang.upper()
    if lu == "C":
        return "c"
    if lu in ("C++", "CPP"):
        return "cpp"
    return None


def _try_dwarf_snapshot(
    so_path: Path,
    elf_meta: ElfMetadata,
    dwarf_meta: DwarfMetadata,
    dwarf_adv: AdvancedDwarfMetadata,
    version: str,
    profile_hint: str | None,
    headers: list[Path],
    dwarf_only: bool,
) -> tuple[AbiSnapshot | None, list[RecordType]]:
    """Attempt to build a snapshot from DWARF debug info.

    Returns ``(snapshot, dwarf_only_types)``.  When the snapshot should be
    used directly, *snapshot* is non-None.  When DWARF produced no symbols
    (and *dwarf_only* is False), *snapshot* is None and *dwarf_only_types*
    carries the partial type list for the symbol-only fallback path.
    """
    from .dwarf_snapshot import build_snapshot_from_dwarf

    if dwarf_only and headers:
        warnings.warn(
            "--dwarf-only: ignoring provided headers; using DWARF as primary data source.",
            UserWarning,
            stacklevel=3,
        )

    snap = build_snapshot_from_dwarf(
        so_path,
        elf_meta,
        dwarf_meta,
        dwarf_adv,
        version=version,
        language_profile=profile_hint,
    )
    # If DWARF produced functions (or was explicitly forced), use it.
    if snap.functions or snap.variables or dwarf_only:
        if not headers and not dwarf_only:
            warnings.warn(
                "No headers provided — using DWARF debug info as primary data source. "
                "#define constants and default parameter values will be unavailable.",
                UserWarning,
                stacklevel=3,
            )
        _populate_elf_visibility(snap)
        return snap, []
    # DWARF snapshot had no symbols of its own (often the case when
    # the binary exports only constructors / extern "C" wrappers that
    # the DWARF subprogram filter rejected). Keep the *types* it
    # extracted — they include bases / vtable info that pure-DWARF
    # metadata (DwarfMetadata.structs) does not retain.
    return None, list(snap.types)


def _build_symbol_only_snapshot(
    so_path: Path,
    version: str,
    elf_meta: ElfMetadata,
    dwarf_meta: DwarfMetadata,
    dwarf_adv: AdvancedDwarfMetadata,
    exported_dynamic_funcs: set[str],
    exported_dynamic_objects: set[str],
    exported_dynamic_tls: set[str],
    dwarf_only_types: list[RecordType],
    profile_hint: str | None,
) -> AbiSnapshot:
    """Build a symbol-only :class:`AbiSnapshot` when no headers are available.

    Issues the appropriate ``UserWarning`` based on whether DWARF-derived
    types are present, then assembles the snapshot from ELF-exported symbols.
    """
    # No headers → symbol-only fallback. When the DWARF snapshot
    # builder produced types but no functions, we still preserve
    # those types (see *dwarf_only_types*), so the warning is
    # narrowed to reflect what's actually missing.
    if dwarf_only_types:
        warnings.warn(
            "No headers provided — using ELF-exported symbols for "
            "functions/variables; DWARF-derived type information "
            "preserved.",
            UserWarning,
            stacklevel=3,
        )
    else:
        warnings.warn(
            "No headers provided and no DWARF debug info — only ELF-exported "
            "symbols will be captured; type information will be missing.",
            UserWarning,
            stacklevel=3,
        )
    snapshot = AbiSnapshot(
        library=so_path.name,
        version=version,
        source_path=str(so_path),
        functions=[
            Function(
                name=sym,
                mangled=sym,
                return_type="?",
                visibility=Visibility.ELF_ONLY,
                # Absence of Itanium _Z prefix is strong evidence of C linkage
                is_extern_c=not sym.startswith("_Z"),
            )
            for sym in sorted(exported_dynamic_funcs)
        ],
        variables=[
            Variable(
                name=sym,
                mangled=sym,
                type="?",
                visibility=Visibility.ELF_ONLY,
            )
            for sym in sorted(exported_dynamic_objects | exported_dynamic_tls)
        ],
        # Preserve DWARF-derived types (with bases / vtable) when the
        # symbol-only fallback is taken. Pure DwarfMetadata loses
        # inheritance info; retaining the partially-populated DWARF
        # snapshot's types lets downstream detectors (e.g. internal
        # leak detection) still see the relationships.
        types=dwarf_only_types,
        elf=elf_meta,
        dwarf=dwarf_meta,
        dwarf_advanced=dwarf_adv,
        elf_only_mode=True,
        platform="elf",
        language_profile=profile_hint,
    )
    _populate_elf_visibility(snapshot)
    return snapshot


def _dump_elf(
    so_path: Path,
    headers: list[Path],
    extra_includes: list[Path],
    version: str,
    compiler: str,
    *,
    gcc_path: str | None = None,
    gcc_prefix: str | None = None,
    gcc_options: str | None = None,
    sysroot: Path | None = None,
    nostdinc: bool = False,
    lang: str | None = None,
    dwarf_only: bool = False,
    debug_format: str | None = None,
    public_headers: list[Path] | None = None,
    public_header_dirs: list[Path] | None = None,
) -> AbiSnapshot:
    """ELF-specific dump: pyelftools + debug info (DWARF/BTF/CTF) + castxml."""
    exported_dynamic, exported_static = _pyelftools_exported_symbols(so_path)

    from .elf_metadata import parse_elf_metadata

    elf_meta = parse_elf_metadata(so_path)
    exported_dynamic, exported_dynamic_funcs, exported_dynamic_objects, exported_dynamic_tls = (
        _elf_classify_symbols(elf_meta, exported_dynamic)
    )
    dwarf_meta, dwarf_adv = _resolve_debug_metadata(so_path, debug_format)
    profile_hint = _elf_lang_to_profile(lang)

    # ADR-003: Updated fallback chain
    # --dwarf-only → force DWARF mode regardless of headers
    # no headers + DWARF available → DWARF-only mode (24/30 detectors)
    # no headers + no DWARF → symbols-only mode (6/30 detectors)
    dwarf_only_types: list[RecordType] = []
    if dwarf_only or (not headers and dwarf_meta.has_dwarf):
        snap, dwarf_only_types = _try_dwarf_snapshot(
            so_path, elf_meta, dwarf_meta, dwarf_adv,
            version, profile_hint, headers, dwarf_only,
        )
        if snap is not None:
            return snap

    if not headers:
        return _build_symbol_only_snapshot(
            so_path, version, elf_meta, dwarf_meta, dwarf_adv,
            exported_dynamic_funcs, exported_dynamic_objects, exported_dynamic_tls,
            dwarf_only_types, profile_hint,
        )

    xml_root = _castxml_dump(
        headers, extra_includes, compiler=compiler,
        gcc_path=gcc_path, gcc_prefix=gcc_prefix, gcc_options=gcc_options,
        sysroot=sysroot, nostdinc=nostdinc, lang=lang,
    )
    parser = _CastxmlParser(
        xml_root, exported_dynamic, exported_static,
        public_header_paths=[str(h) for h in headers] + [str(h) for h in (public_headers or [])],
        public_dir_paths=[str(d) for d in (public_header_dirs or [])],
    )

    snapshot = AbiSnapshot(
        library=so_path.name,
        version=version,
        source_path=str(so_path),
        functions=parser.parse_functions(),
        variables=parser.parse_variables(),
        types=parser.parse_types(),
        enums=parser.parse_enums(),
        typedefs=parser.parse_typedefs(),
        constants=parser.parse_constants(),
        elf=elf_meta,
        dwarf=dwarf_meta,
        dwarf_advanced=dwarf_adv,
        # Reached only when headers were supplied and castxml ran (the no-header
        # and DWARF-only branches return earlier): this surface is header-parsed.
        from_headers=True,
        platform="elf",
        language_profile=profile_hint,
    )
    _populate_elf_visibility(snapshot)
    return snapshot


def _dump_macho(
    dylib_path: Path,
    headers: list[Path],
    extra_includes: list[Path],
    version: str,
    compiler: str,
    *,
    gcc_path: str | None = None,
    gcc_prefix: str | None = None,
    gcc_options: str | None = None,
    sysroot: Path | None = None,
    nostdinc: bool = False,
    lang: str | None = None,
    dwarf_only: bool = False,
    public_headers: list[Path] | None = None,
    public_header_dirs: list[Path] | None = None,
) -> AbiSnapshot:
    """Mach-O dump: export table from macholib + castxml header analysis."""
    if dwarf_only:
        warnings.warn(
            "dwarf_only=True is not supported for Mach-O; "
            "falling back to normal extraction.",
            UserWarning,
            stacklevel=2,
        )
    from .macho_metadata import parse_macho_metadata

    macho_meta = parse_macho_metadata(dylib_path)
    # Build exported symbol set from Mach-O export table
    exported_dynamic: set[str] = {
        exp.name for exp in macho_meta.exports
        if exp.name and _is_abi_relevant_symbol(exp.name)
    }

    profile_hint: str | None = None
    if lang is not None:
        lu = lang.upper()
        if lu == "C":
            profile_hint = "c"
        elif lu in ("C++", "CPP"):
            profile_hint = "cpp"

    if not headers:
        warnings.warn(
            "No headers provided — only Mach-O exported symbols will be captured; "
            "type information will be missing.",
            UserWarning,
            stacklevel=2,
        )
        # Normalize Mach-O leading underscore: _foo → foo, __Z... → _Z...
        def _normalize_macho_sym(s: str) -> str:
            if s.startswith("_"):
                return s[1:]
            return s

        # Split exports into functions (__TEXT) and variables (__DATA)
        # using section classification from Mach-O nlist entries.
        _relevant = [
            exp for exp in macho_meta.exports
            if exp.name and _is_abi_relevant_symbol(exp.name)
        ]
        macho_funcs = [exp for exp in _relevant if not exp.is_data]
        macho_vars = [exp for exp in _relevant if exp.is_data]

        return AbiSnapshot(
            library=dylib_path.name,
            version=version,
            source_path=str(dylib_path),
            functions=[
                Function(
                    name=_normalize_macho_sym(exp.name),
                    mangled=_normalize_macho_sym(exp.name),
                    return_type="?",
                    # ELF_ONLY: marks symbols as export-table-only (no header
                    # confirmation). This lets the checker distinguish
                    # binary-only removals as FUNC_REMOVED_ELF_ONLY.
                    visibility=Visibility.ELF_ONLY,
                    is_extern_c=not _normalize_macho_sym(exp.name).startswith("_Z"),
                )
                for exp in sorted(macho_funcs, key=lambda e: e.name)
            ],
            variables=[
                Variable(
                    name=_normalize_macho_sym(exp.name),
                    mangled=_normalize_macho_sym(exp.name),
                    type="?",
                    visibility=Visibility.ELF_ONLY,
                )
                for exp in sorted(macho_vars, key=lambda e: e.name)
            ],
            macho=macho_meta,
            elf_only_mode=True,
            platform="macho",
            language_profile=profile_hint,
        )

    xml_root = _castxml_dump(
        headers, extra_includes, compiler=compiler,
        gcc_path=gcc_path, gcc_prefix=gcc_prefix, gcc_options=gcc_options,
        sysroot=sysroot, nostdinc=nostdinc, lang=lang,
    )
    # On macOS, C symbols have a leading underscore in the export table
    # (Mach-O convention). Strip it for matching against castxml names.
    exported_no_underscore: set[str] = set()
    for sym in exported_dynamic:
        if sym.startswith("_"):
            exported_no_underscore.add(sym[1:])
        else:
            exported_no_underscore.add(sym)
    parser = _CastxmlParser(
        xml_root, exported_no_underscore, exported_no_underscore,
        public_header_paths=[str(h) for h in headers] + [str(h) for h in (public_headers or [])],
        public_dir_paths=[str(d) for d in (public_header_dirs or [])],
    )

    return AbiSnapshot(
        library=dylib_path.name,
        version=version,
        source_path=str(dylib_path),
        functions=parser.parse_functions(),
        variables=parser.parse_variables(),
        types=parser.parse_types(),
        enums=parser.parse_enums(),
        typedefs=parser.parse_typedefs(),
        constants=parser.parse_constants(),
        macho=macho_meta,
        # Reached only when headers were supplied and castxml ran (the no-header
        # branch returns earlier): this surface is header-parsed.
        from_headers=True,
        platform="macho",
        language_profile=profile_hint,
    )


def _dump_pe(
    dll_path: Path,
    headers: list[Path],
    extra_includes: list[Path],
    version: str,
    compiler: str,
    *,
    gcc_path: str | None = None,
    gcc_prefix: str | None = None,
    gcc_options: str | None = None,
    sysroot: Path | None = None,
    nostdinc: bool = False,
    lang: str | None = None,
    public_headers: list[Path] | None = None,
    public_header_dirs: list[Path] | None = None,
) -> AbiSnapshot:
    """PE dump: export table from pefile + castxml header analysis."""
    from .pe_metadata import parse_pe_metadata

    pe_meta = parse_pe_metadata(dll_path)
    exported_dynamic: set[str] = {
        (exp.name or f"ordinal:{exp.ordinal}")
        for exp in pe_meta.exports
    }
    exported_static: set[str] = set(exported_dynamic)

    profile_hint: str | None = None
    if lang is not None:
        lu = lang.upper()
        if lu == "C":
            profile_hint = "c"
        elif lu in ("C++", "CPP"):
            profile_hint = "cpp"

    if not headers:
        warnings.warn(
            "No headers provided — only PE exported symbols will be captured; "
            "type information will be missing.",
            UserWarning,
            stacklevel=2,
        )
        return AbiSnapshot(
            library=dll_path.name,
            version=version,
            source_path=str(dll_path),
            functions=[
                Function(
                    name=sym, mangled=sym, return_type="?",
                    visibility=Visibility.ELF_ONLY,
                    is_extern_c=not sym.startswith("?"),
                )
                for sym in sorted(exported_dynamic)
            ],
            pe=pe_meta,
            elf_only_mode=True,
            platform="pe",
            language_profile=profile_hint,
        )

    xml_root = _castxml_dump(
        headers, extra_includes, compiler=compiler,
        gcc_path=gcc_path, gcc_prefix=gcc_prefix, gcc_options=gcc_options,
        sysroot=sysroot, nostdinc=nostdinc, lang=lang,
    )
    parser = _CastxmlParser(
        xml_root, exported_dynamic, exported_static,
        public_header_paths=[str(h) for h in headers] + [str(h) for h in (public_headers or [])],
        public_dir_paths=[str(d) for d in (public_header_dirs or [])],
    )

    return AbiSnapshot(
        library=dll_path.name,
        version=version,
        source_path=str(dll_path),
        functions=parser.parse_functions(),
        variables=parser.parse_variables(),
        types=parser.parse_types(),
        enums=parser.parse_enums(),
        typedefs=parser.parse_typedefs(),
        constants=parser.parse_constants(),
        pe=pe_meta,
        # Reached only when headers were supplied and castxml ran (the no-header
        # branch returns earlier): this surface is header-parsed.
        from_headers=True,
        platform="pe",
        language_profile=profile_hint,
    )
