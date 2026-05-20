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
from typing import Any, cast
from xml.etree.ElementTree import (
    Element,  # type annotation only; parsing uses defusedxml
)

from defusedxml import ElementTree as DefusedET

from .errors import SnapshotError, ValidationError
from .model import (
    AbiSnapshot,
    AccessLevel,
    ElfVisibility,
    EnumMember,
    EnumType,
    Function,
    Param,
    RecordType,
    TypeField,
    Variable,
    Visibility,
)

log = logging.getLogger(__name__)


def _castxml_available() -> bool:
    return shutil.which("castxml") is not None


_HIDDEN_VIS = frozenset({"STV_HIDDEN", "STV_INTERNAL"})

# ---------------------------------------------------------------------------
# ELF-only mode: ABI-relevance filter
# ---------------------------------------------------------------------------
# Prefixes that identify GCC/compiler-internal symbols which may leak into
# .dynsym through statically-linked runtime (e.g. libgcc_s, SVML).
_GCC_INTERNAL_PREFIXES = (
    "ix86_",
    "x86_64_",
    "__cpu_model",
    "__cpu_features",
    "_ZGV",          # GCC SIMD vector variants (e.g. _ZGVbN2v_sin)
    "__svml_",       # Intel Short Vector Math Library
    "__libm_sse2_",
    "__libm_avx_",
)

# Prefixes that identify transitive C++ standard-library symbols which may
# appear in .dynsym via weak linkage (libstdc++ / libc++).
_STDLIB_PREFIXES = (
    "_ZNSt",              # std:: namespace members (libstdc++)
    "_ZNKSt",             # const std:: methods
    "_ZNSt3__1",          # libc++ inline-namespace __1
    "_ZdlPv",             # operator delete(void*)
    "_ZnwSt",             # operator new(std::size_t)
    "_ZnaSt",             # operator new[](std::size_t)
    "_ZdaPv",             # operator delete[](void*)
    "_ZTVN10__cxxabiv",   # vtables for RTTI (typeinfo infrastructure)
    "_ZTI",               # typeinfo objects
    "_ZTS",               # typeinfo strings
    "_ZSt",               # std:: global symbols (e.g. _ZSt4cout)
)


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
    if not name:
        return False

    # GCC/compiler internals
    for prefix in _GCC_INTERNAL_PREFIXES:
        if name.startswith(prefix):
            return False

    # Transitive libstdc++/libc++ symbols
    for prefix in _STDLIB_PREFIXES:
        if name.startswith(prefix):
            return False

    # Private C symbols with __ as a namespace separator
    # (e.g. H5C__flush_marked_entries, MPI__send).
    # Exclusions:
    #   • C++ mangled names start with _Z — handled separately by demangler.
    #   • System symbols start with __ or _ followed by an uppercase letter
    #     (POSIX/C reserved) — they are already excluded because they start
    #     with __ which would be caught if we checked name[0:2], but we want
    #     to be precise: we only filter names that have __ *after* the first
    #     two characters, meaning the library itself added the separator.
    if not name.startswith("_Z") and "__" in name[2:]:
        return False

    return True


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

    with tempfile.NamedTemporaryFile(suffix=agg_ext, mode="w", delete=False) as agg:
        for h in headers:
            agg.write(f'#include "{h.resolve()}"\n')
        agg_path = Path(agg.name)

    cmd = _build_castxml_command(
        cc_bin, cc_id, extra_includes, out_xml, agg_path,
        sysroot=sysroot, nostdinc=nostdinc, gcc_options=gcc_options,
        force_cpp=bool(force_cpp),
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


def _parse_vtable_index(vi_str: str | None) -> int | None:
    """Parse vtable_index attribute, returning None for missing/invalid values."""
    if vi_str is None:
        return None
    stripped = vi_str.lstrip("-")
    return int(vi_str) if stripped.isdigit() else None


def _vt_sort_key(item: tuple[int | None, str]) -> tuple[int, int]:
    vi, _ = item
    return (0, vi) if vi is not None else (1, 0)


class _CastxmlParser:
    """Parse castxml XML into ABI model objects."""

    def __init__(self, root: Element, exported_dynamic: set[str],
                 exported_static: set[str]):
        self._root = root
        self._exported_dynamic = exported_dynamic
        self._exported_static = exported_static
        self._id_map: dict[str, Element] = {}
        self._virtual_methods_by_class: dict[str, list[Element]] = {}
        self._build_id_map()

    def _build_id_map(self) -> None:
        for el in self._root:
            eid = el.get("id")
            if eid:
                self._id_map[eid] = el
        # Build class_id → list of virtual Method/Destructor elements
        # In castxml output, methods are top-level elements with a "context" attribute
        for el in self._root:
            if el.tag in ("Method", "Destructor") and el.get("virtual") == "1":
                ctx = el.get("context")
                if ctx:
                    self._virtual_methods_by_class.setdefault(ctx, []).append(el)

    def _resolve(self, id_: str) -> Element | None:
        return self._id_map.get(id_)

    def _type_name(self, id_: str, depth: int = 0) -> str:
        if depth > 10:
            return "?"
        el = self._resolve(id_)
        if el is None:
            return "?"
        tag = el.tag
        if tag in ("FundamentalType", "Enumeration"):
            return el.get("name", "?")
        if tag == "PointerType":
            return self._type_name(el.get("type", ""), depth + 1) + "*"
        if tag == "ReferenceType":
            return self._type_name(el.get("type", ""), depth + 1) + "&"
        if tag == "RValueReferenceType":
            return self._type_name(el.get("type", ""), depth + 1) + "&&"
        if tag == "CvQualifiedType":
            base = self._type_name(el.get("type", ""), depth + 1)
            const = "const " if el.get("const") == "1" else ""
            return f"{const}{base}"
        if tag in ("Struct", "Class", "Union"):
            return el.get("name", "?")
        if tag == "Typedef":
            return el.get("name", "?")
        if tag == "ArrayType":
            max_ = el.get("max", "")
            base = self._type_name(el.get("type", ""), depth + 1)
            return f"{base}[{max_}]" if max_ else f"{base}[]"
        return el.get("name", tag)

    def _pointer_depth(self, id_: str, depth: int = 0) -> int:
        """Count pointer nesting depth: T=0, T*=1, T**=2, etc."""
        if depth > 10:
            return 0
        el = self._resolve(id_)
        if el is None:
            return 0
        if el.tag == "PointerType":
            return 1 + self._pointer_depth(el.get("type", ""), depth + 1)
        if el.tag in ("CvQualifiedType", "Typedef"):
            return self._pointer_depth(el.get("type", ""), depth + 1)
        return 0

    @staticmethod
    def _access_level(el: Element) -> AccessLevel:
        """Map castxml 'access' attribute to AccessLevel enum."""
        raw = el.get("access", "public")
        if raw == "protected":
            return AccessLevel.PROTECTED
        if raw == "private":
            return AccessLevel.PRIVATE
        return AccessLevel.PUBLIC

    def _visibility(self, mangled: str, name: str = "") -> Visibility:
        """Determine visibility based on ELF symbol tables."""
        # Check dynamic symbols (.dynsym) — truly exported
        if mangled and mangled in self._exported_dynamic:
            return Visibility.PUBLIC
        if name and name in self._exported_dynamic:
            return Visibility.PUBLIC
        # Check all symbols (.symtab) — present in ELF but not exported
        if mangled and mangled in self._exported_static:
            return Visibility.ELF_ONLY
        if name and name in self._exported_static:
            return Visibility.ELF_ONLY
        return Visibility.HIDDEN

    def _is_builtin_element(self, el: Element) -> bool:
        """Return True if element originates from a compiler built-in pseudo-file.

        Real castxml output: elements carry a ``file`` attribute (e.g. ``file="f0"``)
        pointing directly to a ``File`` element in the id-map — NOT via a separate
        ``Location`` element.  The compound ``location`` attribute (``"f0:0"``) is
        informational only and is NOT a map key.

        Known built-in file names emitted by castxml:
        - ``<builtin>``       (clang/castxml built-in declarations)
        - ``<built-in>``      (older castxml / GCC)
        - ``<command-line>``  (preprocessor command-line defines)
        """
        file_id = el.get("file", "")
        if not file_id:
            return False
        file_el = self._id_map.get(file_id)
        if file_el is None:
            return False
        fname = file_el.get("name", "")
        return fname in ("<builtin>", "<built-in>", "<command-line>")

    def parse_functions(self) -> list[Function]:
        funcs = []
        for el in self._root:
            if el.tag not in ("Function", "Method", "Constructor", "Destructor"):
                continue
            name = el.get("name", "")
            if not name:
                continue
            # Skip compiler built-ins and command-line synthetic declarations
            if self._is_builtin_element(el):
                continue
            mangled = el.get("mangled", "") or name  # C functions: use plain name
            ret_id = el.get("returns", "")
            ret_type = self._type_name(ret_id) if ret_id else "void"
            ret_ptr_depth = self._pointer_depth(ret_id) if ret_id else 0

            params = []
            for arg in el:
                if arg.tag == "Argument":
                    p_name = arg.get("name", "")
                    p_type_id = arg.get("type", "")
                    p_type = self._type_name(p_type_id)
                    p_depth = self._pointer_depth(p_type_id)
                    params.append(Param(name=p_name, type=p_type, pointer_depth=p_depth))

            vis = self._visibility(el.get("mangled", ""), name)
            is_virtual = el.get("virtual") == "1"
            noexcept_re = re.search(r"noexcept", el.get("attributes", ""))
            vtable_index = _parse_vtable_index(el.get("vtable_index")) if is_virtual else None

            # Detect extern "C": explicit extern attribute OR no mangled name (C linkage)
            raw_mangled = el.get("mangled", "")
            is_extern_c = (
                el.get("extern") == "1"
                or not raw_mangled  # C functions have no mangled name
            )

            loc_id = el.get("location", "")
            loc_el = self._id_map.get(loc_id)
            source_loc = None
            if loc_el is not None:
                file_id = loc_el.get("file", "")
                file_el = self._id_map.get(file_id)
                fname = file_el.get("name", "") if file_el is not None else ""
                line = loc_el.get("line", "")
                source_loc = f"{fname}:{line}" if fname else None

            is_static = el.get("static") == "1"
            is_const = el.get("const") == "1"
            is_volatile = el.get("volatile") == "1"
            is_pure_virtual = el.get("pure_virtual") == "1"
            is_deleted = el.get("deleted") == "1"
            # castxml emits inline="1" for inline functions/methods
            is_inline = el.get("inline") == "1"

            # C++ ref-qualifier: castxml emits refqual="lvalue" or "rvalue"
            refqual_raw = el.get("refqual", "")
            ref_qualifier = {"lvalue": "&", "rvalue": "&&"}.get(refqual_raw, "")

            funcs.append(Function(
                name=name,
                mangled=mangled,
                return_type=ret_type,
                params=params,
                visibility=vis,
                is_virtual=is_virtual,
                is_noexcept=bool(noexcept_re),
                is_extern_c=is_extern_c,
                vtable_index=vtable_index,
                source_location=source_loc,
                is_static=is_static,
                is_const=is_const,
                is_volatile=is_volatile,
                is_pure_virtual=is_pure_virtual,
                is_deleted=is_deleted,
                is_inline=is_inline,
                access=self._access_level(el),
                return_pointer_depth=ret_ptr_depth,
                ref_qualifier=ref_qualifier,
            ))
        return funcs

    def parse_variables(self) -> list[Variable]:
        variables = []
        for el in self._root:
            if el.tag != "Variable":
                continue
            name = el.get("name", "")
            # C-mode castxml does not emit a mangled attribute for C-linkage variables
            # (C has no name mangling); fall back to plain name as the symbol key,
            # mirroring the same pattern in parse_functions().
            mangled = el.get("mangled", "") or name
            if not mangled:
                continue
            # Skip compiler built-ins and command-line synthetic declarations
            if self._is_builtin_element(el):
                continue
            type_name = self._type_name(el.get("type", ""))
            # Use castxml structured attribute first; fall back to word-boundary
            # regex on type_name to avoid false positives on names like
            # "constructor_t", "const_iterator", "myconstant".
            is_const = (
                el.get("const") == "1"
                or bool(re.search(r"\bconst\b", type_name))
            )
            vis = self._visibility(mangled, name)
            variables.append(Variable(
                name=name, mangled=mangled, type=type_name, visibility=vis,
                is_const=is_const,
            ))
        return variables

    def parse_types(self) -> list[RecordType]:
        # Build reverse mapping: struct/union ID → typedef name for anonymous types.
        # This allows us to include `typedef struct { ... } Foo;` where the struct
        # itself is anonymous (name="") but reachable via the typedef.
        typedef_name_for: dict[str, str] = {}
        for el in self._root:
            if el.tag != "Typedef":
                continue
            td_name = el.get("name", "")
            if not td_name:
                continue
            target_id = el.get("type", "")
            target_el = self._resolve(target_id)
            # Follow through ElaboratedType / CvQualifiedType wrappers
            # that castxml may insert between Typedef and the actual Struct.
            while target_el is not None and target_el.tag in (
                "ElaboratedType", "CvQualifiedType",
            ):
                target_id = target_el.get("type", "")
                target_el = self._resolve(target_id)
            if target_el is not None and target_el.tag in ("Struct", "Class", "Union"):
                target_name = target_el.get("name", "")
                if not target_name:
                    # Anonymous struct/union with a typedef alias — record it.
                    # Use the struct's own id as key (may differ from the
                    # Typedef's type attr when ElaboratedType is involved).
                    struct_id = target_el.get("id", "")
                    if struct_id:
                        typedef_name_for[struct_id] = td_name

        types = []
        for el in self._root:
            if self._is_public_record_type(el):
                types.append(self._build_record_type(el))
            elif el.tag in ("Struct", "Class", "Union"):
                # Check if this is an anonymous struct reachable via typedef
                eid = el.get("id", "")
                override_name = typedef_name_for.get(eid)
                if override_name and not self._is_builtin_element(el):
                    types.append(self._build_record_type(el, override_name=override_name))
        return types

    def _is_public_record_type(self, el: Any) -> bool:
        if el.tag not in ("Struct", "Class", "Union"):
            return False
        name = el.get("name", "")
        if not name or el.get("artificial") == "1":
            return False
        if name.startswith("__"):
            return False
        # Skip compiler built-ins and command-line synthetic types
        if self._is_builtin_element(el):
            return False
        return True

    def _build_record_type(self, el: Any, override_name: str | None = None) -> RecordType:
        name = override_name or el.get("name", "")
        is_opaque = el.get("incomplete") == "1"
        return RecordType(
            name=name,
            kind=el.tag.lower(),
            size_bits=self._optional_int_attr(el, "size"),
            alignment_bits=self._optional_int_attr(el, "align"),
            fields=[] if is_opaque else self._parse_record_fields(el),
            bases=[] if is_opaque else [
                self._type_name(b.get("type", ""))
                for b in el if b.tag == "Base" and b.get("virtual") != "1"
            ],
            virtual_bases=[] if is_opaque else [
                self._type_name(b.get("type", ""))
                for b in el if b.tag == "Base" and b.get("virtual") == "1"
            ],
            vtable=[] if is_opaque else self._build_vtable(el.get("id", "")),
            is_union=el.tag == "Union",
            is_opaque=is_opaque,
        )

    def _optional_int_attr(self, el: Any, attr: str) -> int | None:
        raw = el.get(attr)
        return int(raw) if raw and raw.isdigit() else None

    def _parse_record_fields(self, el: Any) -> list[TypeField]:
        """Parse struct/class/union fields.

        castxml uses two layouts depending on version / output mode:
        - Inline children: ``<Struct><Field .../></Struct>``
        - Members attribute: ``<Struct members="_14 _15 _16 ..."/>`` (IDs resolved via id_map)

        We support both: first scan inline children, then fall back to the
        ``members`` attribute so we never miss fields in either format.
        """
        fields: list[TypeField] = []

        # Collect Field elements: inline children first
        field_elements: list[Any] = [c for c in el if c.tag == "Field"]

        # Fallback: resolve via space-separated "members" attribute
        if not field_elements:
            for mid in el.get("members", "").split():
                member_el = self._id_map.get(mid)
                if member_el is not None and member_el.tag == "Field":
                    field_elements.append(member_el)

        for child in field_elements:
            child_name = child.get("name", "")
            if not child_name:
                # Anonymous struct/union member — flatten its fields into parent
                fields.extend(self._expand_anonymous_field(child))
                continue
            bitfield_bits, is_bitfield = self._parse_bitfield_bits(child.get("bits"))
            fields.append(TypeField(
                name=child_name,
                type=self._type_name(child.get("type", "")),
                offset_bits=self._optional_int_attr(child, "offset"),
                is_bitfield=is_bitfield,
                bitfield_bits=bitfield_bits,
                access=self._access_level(child),
            ))
        return fields

    def _expand_anonymous_field(
        self, field_el: Any, _depth: int = 0, _outer_offset: int = 0
    ) -> list[TypeField]:
        """Flatten anonymous struct/union field into the parent's field list.

        In castxml output, anonymous unions/structs inside a struct appear as
        ``Field`` elements with ``name=""`` pointing to a ``Union`` or ``Struct``
        element.  We inline their named fields at the correct offset to prevent
        false ``TYPE_FIELD_REMOVED`` reports when a named field moves into an
        anonymous union (issue #58).

        ``_depth`` guards against malformed/cyclic XML (max nesting: 16).
        ``_outer_offset`` carries the accumulated offset from outer anonymous
        members so doubly-nested fields get correct absolute ``offset_bits``.
        """
        if _depth > 16:
            return []
        type_id = field_el.get("type", "")
        type_el = self._resolve(type_id)
        if type_el is None or type_el.tag not in ("Union", "Struct"):
            return []

        this_offset = _outer_offset + (self._optional_int_attr(field_el, "offset") or 0)
        result: list[TypeField] = []

        # Collect inner Field elements (inline children or members attribute)
        inner_fields: list[Any] = [c for c in type_el if c.tag == "Field"]
        if not inner_fields:
            for mid in type_el.get("members", "").split():
                member_el = self._id_map.get(mid)
                if member_el is not None and member_el.tag == "Field":
                    inner_fields.append(member_el)

        for inner in inner_fields:
            inner_name = inner.get("name", "")
            if not inner_name:
                # Doubly-nested anonymous member — recurse, passing accumulated offset
                result.extend(self._expand_anonymous_field(
                    inner, _depth + 1, _outer_offset=this_offset,
                ))
                continue
            inner_offset = self._optional_int_attr(inner, "offset") or 0
            bitfield_bits, is_bitfield = self._parse_bitfield_bits(inner.get("bits"))
            result.append(TypeField(
                name=inner_name,
                type=self._type_name(inner.get("type", "")),
                offset_bits=this_offset + inner_offset,
                is_bitfield=is_bitfield,
                bitfield_bits=bitfield_bits,
                access=self._access_level(inner),
            ))
        return result

    @staticmethod
    def _parse_bitfield_bits(bits_raw: str | None) -> tuple[int | None, bool]:
        try:
            bitfield_bits = int(bits_raw) if bits_raw is not None else None
        except ValueError:
            return (None, False)
        return (bitfield_bits, bitfield_bits is not None)

    def _build_vtable(self, class_id: str) -> list[str]:
        virtual_methods = self._collect_virtual_methods(class_id)
        virtual_methods.sort(key=_vt_sort_key)
        return [m for _, m in virtual_methods]

    def _collect_virtual_methods(
        self, cid: str, seen: set[str] | None = None,
    ) -> list[tuple[int | None, str]]:
        if seen is None:
            seen = set()
        if cid in seen:
            return []
        seen.add(cid)
        class_el = self._id_map.get(cid)
        if class_el is None:
            return []

        # Use a dict keyed by vtable_index so derived methods overwrite base entries,
        # preventing duplicate slots when a derived class overrides a virtual method.
        slots: dict[int | None, str] = {}
        for base in class_el:
            if base.tag != "Base":
                continue
            base_type_el = self._resolve(base.get("type", ""))
            if base_type_el is not None:
                for idx, name in self._collect_virtual_methods(base_type_el.get("id", ""), seen):
                    slots[idx] = name

        for method_el in self._virtual_methods_by_class.get(cid, []):
            mangled_name = method_el.get("mangled", "")
            if not mangled_name:
                continue
            idx = _parse_vtable_index(method_el.get("vtable_index"))
            slots[idx] = mangled_name

        return list(slots.items())


    def parse_enums(self) -> list[EnumType]:
        enums = []
        for el in self._root:
            if el.tag != "Enumeration":
                continue
            name = el.get("name", "")
            if not name or name.startswith("__"):
                continue
            if self._is_builtin_element(el):
                continue
            members = []
            for child in el:
                if child.tag == "EnumValue":
                    m_name = child.get("name", "")
                    m_val_str = child.get("init", "0")
                    try:
                        m_val = int(m_val_str)
                    except ValueError:
                        m_val = 0
                    members.append(EnumMember(name=m_name, value=m_val))
            enums.append(EnumType(name=name, members=members))
        return enums

    def _underlying_type_name(self, id_: str, depth: int = 0) -> str:
        """Follow typedef chains to the concrete base type name."""
        if depth > 20:
            return "?"
        el = self._resolve(id_)
        if el is None:
            return "?"
        if el.tag == "Typedef":
            return self._underlying_type_name(el.get("type", ""), depth + 1)
        return self._type_name(id_)

    def parse_typedefs(self) -> dict[str, str]:
        typedefs: dict[str, str] = {}
        for el in self._root:
            if el.tag != "Typedef":
                continue
            name = el.get("name", "")
            if not name:
                continue
            if self._is_builtin_element(el):
                continue
            type_id = el.get("type", "")
            # Flatten typedef chains: alias → alias2 → int  stored as  alias → int
            underlying = self._underlying_type_name(type_id) if type_id else "?"
            typedefs[name] = underlying
        return typedefs


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

    Returns:
        AbiSnapshot with functions, variables, and types populated.
    """
    fmt = _detect_format(so_path)

    if fmt == "macho":
        return _dump_macho(
            so_path, headers, extra_includes or [], version, compiler,
            gcc_path=gcc_path, gcc_prefix=gcc_prefix, gcc_options=gcc_options,
            sysroot=sysroot, nostdinc=nostdinc, lang=lang,
            dwarf_only=dwarf_only,
        )
    if fmt == "pe":
        return _dump_pe(
            so_path, headers, extra_includes or [], version, compiler,
            gcc_path=gcc_path, gcc_prefix=gcc_prefix, gcc_options=gcc_options,
            sysroot=sysroot, nostdinc=nostdinc, lang=lang,
        )

    if fmt != "elf":
        raise ValidationError(
            f"Unrecognised binary format for {so_path}: "
            f"expected ELF, Mach-O, or PE but detected {fmt!r}. "
            f"Ensure the file is a valid shared library."
        )

    return _dump_elf(
        so_path, headers, extra_includes or [], version, compiler,
        gcc_path=gcc_path, gcc_prefix=gcc_prefix, gcc_options=gcc_options,
        sysroot=sysroot, nostdinc=nostdinc, lang=lang,
        dwarf_only=dwarf_only,
        debug_format=debug_format,
    )


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
) -> tuple[Any, Any]:
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
) -> AbiSnapshot:
    """ELF-specific dump: pyelftools + debug info (DWARF/BTF/CTF) + castxml."""
    exported_dynamic, exported_static = _pyelftools_exported_symbols(so_path)

    from .elf_metadata import SymbolType, parse_elf_metadata

    elf_meta = parse_elf_metadata(so_path)
    # Use filtered ELF metadata symbols as authoritative surface for no-header mode.
    # This excludes version-definition aux symbols like LIBFOO_1.0.
    # Split into two sets: function-like symbols (for Function builder) and
    # object symbols (globals) — merged for CastxmlParser visibility check.
    # Split into func-like (for Function builder) and object (globals) sets.
    # Fall back to pyelftools set when elf_meta is unavailable.
    exported_dynamic_funcs: set[str] = exported_dynamic  # fallback
    exported_dynamic_objects: set[str] = set()
    exported_dynamic_tls: set[str] = set()
    if elf_meta is not None and elf_meta.symbols:
        exported_dynamic_funcs = {
            sym.name for sym in elf_meta.symbols
            if sym.sym_type in (SymbolType.FUNC, SymbolType.IFUNC, SymbolType.NOTYPE)
        }
        exported_dynamic_objects = {
            sym.name for sym in elf_meta.symbols
            if sym.sym_type == SymbolType.OBJECT
        }
        exported_dynamic_tls = {
            sym.name for sym in elf_meta.symbols
            if sym.sym_type == SymbolType.TLS
        }
        # Full set for CastxmlParser: determines PUBLIC vs ELF_ONLY visibility
        exported_dynamic = exported_dynamic_funcs | exported_dynamic_objects | exported_dynamic_tls
    dwarf_meta, dwarf_adv = _resolve_debug_metadata(so_path, debug_format)

    profile_hint: str | None = None
    if lang is not None:
        lu = lang.upper()
        if lu == "C":
            profile_hint = "c"
        elif lu in ("C++", "CPP"):
            profile_hint = "cpp"

    # ADR-003: Updated fallback chain
    # --dwarf-only → force DWARF mode regardless of headers
    # no headers + DWARF available → DWARF-only mode (24/30 detectors)
    # no headers + no DWARF → symbols-only mode (6/30 detectors)
    #
    # Strategy: if DWARF is present, attempt the DWARF build. If it
    # produces at least one function, use it. Otherwise fall through
    # to symbol-only mode (the DWARF may contain only CRT stubs) — but
    # still carry the type information across so downstream detectors
    # (notably internal_leak) can use it.
    dwarf_only_types: list[RecordType] = []
    if dwarf_only or (not headers and dwarf_meta.has_dwarf):
        if dwarf_only and headers:
            warnings.warn(
                "--dwarf-only: ignoring provided headers; using DWARF as primary data source.",
                UserWarning,
                stacklevel=2,
            )

        from .dwarf_snapshot import build_snapshot_from_dwarf
        snap = build_snapshot_from_dwarf(
            so_path,
            elf_meta,
            dwarf_meta,
            dwarf_adv,
            version=version,
            language_profile=profile_hint,
        )
        # If DWARF produced functions (or was explicitly forced), use it.
        # Otherwise fall through to symbol-only mode.
        if snap.functions or snap.variables or dwarf_only:
            if not headers and not dwarf_only:
                warnings.warn(
                    "No headers provided — using DWARF debug info as primary data source. "
                    "#define constants and default parameter values will be unavailable.",
                    UserWarning,
                    stacklevel=2,
                )
            _populate_elf_visibility(snap)
            return snap
        # DWARF snapshot had no symbols of its own (often the case when
        # the binary exports only constructors / extern "C" wrappers that
        # the DWARF subprogram filter rejected). Keep the *types* it
        # extracted — they include bases / vtable info that pure-DWARF
        # metadata (DwarfMetadata.structs) does not retain.
        dwarf_only_types = list(snap.types)

    if not headers:
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
                stacklevel=2,
            )
        else:
            warnings.warn(
                "No headers provided and no DWARF debug info — only ELF-exported "
                "symbols will be captured; type information will be missing.",
                UserWarning,
                stacklevel=2,
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

    xml_root = _castxml_dump(
        headers, extra_includes, compiler=compiler,
        gcc_path=gcc_path, gcc_prefix=gcc_prefix, gcc_options=gcc_options,
        sysroot=sysroot, nostdinc=nostdinc, lang=lang,
    )
    parser = _CastxmlParser(xml_root, exported_dynamic, exported_static)

    snapshot = AbiSnapshot(
        library=so_path.name,
        version=version,
        source_path=str(so_path),
        functions=parser.parse_functions(),
        variables=parser.parse_variables(),
        types=parser.parse_types(),
        enums=parser.parse_enums(),
        typedefs=parser.parse_typedefs(),
        elf=elf_meta,
        dwarf=dwarf_meta,
        dwarf_advanced=dwarf_adv,
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
                    # confirmation).  This ensures the checker uses
                    # FUNC_REMOVED_ELF_ONLY (compatible) rather than
                    # FUNC_REMOVED (breaking) for visibility-cleanup removals.
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
        macho=macho_meta,
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
    parser = _CastxmlParser(xml_root, exported_dynamic, exported_static)

    return AbiSnapshot(
        library=dll_path.name,
        version=version,
        source_path=str(dll_path),
        functions=parser.parse_functions(),
        variables=parser.parse_variables(),
        types=parser.parse_types(),
        enums=parser.parse_enums(),
        typedefs=parser.parse_typedefs(),
        pe=pe_meta,
        platform="pe",
        language_profile=profile_hint,
    )
