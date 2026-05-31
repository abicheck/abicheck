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

"""castxml XML → ABI model parser.

Split from ``dumper.py`` to keep that module under the AI-readiness file-size
soft cap. Re-exported from ``abicheck.dumper`` so existing imports of
``_CastxmlParser``, ``_parse_vtable_index``, and ``_vt_sort_key`` from
``abicheck.dumper`` keep working.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from xml.etree.ElementTree import (
    Element,  # type annotation only; parsing uses defusedxml
)

from .model import (
    AccessLevel,
    EnumMember,
    EnumType,
    Function,
    Param,
    RecordType,
    TypeField,
    Variable,
    Visibility,
)


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
        self._source_lines_cache: dict[str, list[str]] = {}
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

    def _source_line_has_explicit(
        self,
        loc_el: Element | None,
        declaration_el: Element | None = None,
    ) -> bool | None:
        """Fallback for castxml Converter nodes that omit explicit="1"."""
        if loc_el is not None:
            file_id = loc_el.get("file", "")
            line_raw = loc_el.get("line", "")
        elif declaration_el is not None:
            file_id = declaration_el.get("file", "")
            line_raw = declaration_el.get("line", "")
        else:
            return None
        file_el = self._id_map.get(file_id)
        if file_el is None:
            return None
        fname = file_el.get("name", "")
        if not fname or not line_raw:
            return None
        try:
            line_no = int(line_raw)
            lines = self._source_lines_cache.get(fname)
            if lines is None:
                lines = Path(fname).read_text(encoding="utf-8").splitlines()
                self._source_lines_cache[fname] = lines
        except (OSError, UnicodeDecodeError, ValueError, IndexError):
            return None
        # CastXML can point a split conversion operator at the ``operator``
        # line, while the ``explicit`` keyword is on the preceding line.
        start = max(0, line_no - 4)
        window_parts: list[str] = []
        for line in lines[start : min(len(lines), line_no + 5)]:
            window_parts.append(line.strip())
            if line_no - 1 <= start + len(window_parts) - 1 and (";" in line or "{" in line):
                break
        window = " ".join(window_parts)
        operator_match = re.search(r"\boperator\b", window)
        if operator_match is None:
            return False
        prefix = window[:operator_match.start()]
        declaration_start = max(prefix.rfind(";"), prefix.rfind("{"), prefix.rfind("}"))
        return bool(re.search(r"\bexplicit\b", prefix[declaration_start + 1 :]))

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

    def _build_hidden_friend_ids(self) -> set[str]:
        """Collect function ids referenced by class `befriending` attributes.

        castxml emits an in-class ``friend`` declaration as a separate
        ``Function`` / ``Method`` / ``OperatorFunction`` element at namespace
        scope, and records the link from the class via a ``befriending``
        attribute on the ``Class`` / ``Struct`` element — a whitespace-
        separated list of ids. We resolve those ids so we can mark the
        corresponding ``Function`` objects as hidden friends downstream.
        """
        ids: set[str] = set()
        for el in self._root:
            if el.tag not in ("Class", "Struct"):
                continue
            befriending = el.get("befriending", "")
            if not befriending:
                continue
            for fid in befriending.split():
                if fid:
                    ids.add(fid)
        return ids

    def parse_functions(self) -> list[Function]:
        funcs = []
        hidden_friend_ids = self._build_hidden_friend_ids()
        # castxml emits non-member operator overloads as <OperatorFunction>
        # (e.g. `bool operator==(const Foo&, const Foo&)` at namespace scope,
        # including hidden friends declared inside a class body).
        function_tags = (
            "Function", "Method", "Constructor", "Destructor",
            "Converter", "OperatorFunction", "OperatorMethod",
        )
        for el in self._root:
            # castxml emits user-defined conversion operators as <Converter>
            # rather than <Method>. They carry mangled names (unlike
            # constructors), `const`/`virtual`/`explicit` qualifiers, and an
            # implicit empty name (which we synthesize as `operator <T>`).
            if el.tag not in function_tags:
                continue
            name = el.get("name", "")
            if not name and el.tag == "Converter":
                # Synthesize a stable display name for conversion operators.
                ret_id = el.get("returns", "")
                ret_type_for_name = self._type_name(ret_id) if ret_id else "?"
                name = f"operator {ret_type_for_name}"
            # castxml emits operator name as the bare symbol (e.g. "==", "+").
            # Normalize to the canonical "operator==" form for readability and
            # to match how the rest of the pipeline (and human reports)
            # refer to operator overloads.
            if name and el.tag in ("OperatorFunction", "OperatorMethod") and not name.startswith("operator"):
                name = f"operator{name}"
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

            # CastXML may store source location two ways:
            #   1. Directly as ``file``/``line`` attributes on the declaration
            #      element (modern compound-attribute form).
            #   2. As ``location="loc1"`` referencing a separate ``Location``
            #      element in the id map (legacy form).
            # Try direct attrs first, then fall back to the id-map lookup so
            # both formats are supported without losing source_location info.
            file_id = el.get("file", "")
            line = el.get("line", "")
            loc_el: Element | None = None
            if not (file_id and line):
                loc_id = el.get("location", "")
                loc_el = self._id_map.get(loc_id) if loc_id else None
                if loc_el is not None:
                    file_id = loc_el.get("file", "")
                    line = loc_el.get("line", "")
            file_el = self._id_map.get(file_id) if file_id else None
            fname = file_el.get("name", "") if file_el is not None else ""
            source_loc = f"{fname}:{line}" if fname and line else None

            is_static = el.get("static") == "1"
            is_const = el.get("const") == "1"
            is_volatile = el.get("volatile") == "1"
            is_pure_virtual = el.get("pure_virtual") == "1"
            is_deleted = el.get("deleted") == "1"
            # castxml emits inline="1" for inline functions/methods
            is_inline = el.get("inline") == "1"
            # castxml emits explicit="1" on Constructor / Method elements that
            # carry the `explicit` specifier. Tri-state: only Constructor /
            # Method tags can be explicit; for plain Function / Destructor the
            # attribute is conceptually N/A and we leave is_explicit=None so
            # the diff does not produce spurious findings.
            is_explicit: bool | None
            if el.tag in ("Constructor", "Method"):
                is_explicit = el.get("explicit") == "1"
            elif el.tag == "Converter":
                is_explicit = (
                    el.get("explicit") == "1"
                    if el.get("explicit") is not None
                    else self._source_line_has_explicit(loc_el, el)
                )
            else:
                is_explicit = None

            # C++ ref-qualifier: castxml emits refqual="lvalue" or "rvalue"
            refqual_raw = el.get("refqual", "")
            ref_qualifier = {"lvalue": "&", "rvalue": "&&"}.get(refqual_raw, "")

            # Hidden-friend marker: castxml records the link via the
            # ``befriending`` attribute on the class element. We resolved
            # the referenced ids upfront and check membership here.
            is_hidden_friend = el.get("id", "") in hidden_friend_ids

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
                is_explicit=is_explicit,
                is_hidden_friend=is_hidden_friend,
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
                source_location=self._source_location(el),
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
            source_location=self._source_location(el),
        )

    def _source_location(self, el: Any) -> str | None:
        """Resolve a declaration's ``file:line`` source location.

        Mirrors the function-parsing path: castxml emits the location either
        directly as ``file``/``line`` attributes or as a ``location`` id
        referencing a ``Location`` element. Returns ``None`` when neither is
        present. Used to populate provenance (``source_header``/``origin``)
        on records, variables, and enums (ADR-015 v6).
        """
        file_id = el.get("file", "")
        line = el.get("line", "")
        if not (file_id and line):
            loc_id = el.get("location", "")
            loc_el = self._id_map.get(loc_id) if loc_id else None
            if loc_el is not None:
                file_id = loc_el.get("file", "")
                line = loc_el.get("line", "")
        file_el = self._id_map.get(file_id) if file_id else None
        fname = file_el.get("name", "") if file_el is not None else ""
        return f"{fname}:{line}" if fname and line else None

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
        # Unindexed entries (no vtable_index attribute) are kept separately so
        # multiple virtuals without an index don't collapse onto a single ``None``
        # key — that would silently drop all but the last one from the vtable.
        slots: dict[int, str] = {}
        unindexed: list[str] = []
        for base in class_el:
            if base.tag != "Base":
                continue
            base_type_el = self._resolve(base.get("type", ""))
            if base_type_el is not None:
                for idx, name in self._collect_virtual_methods(base_type_el.get("id", ""), seen):
                    if idx is None:
                        unindexed.append(name)
                    else:
                        slots[idx] = name

        for method_el in self._virtual_methods_by_class.get(cid, []):
            mangled_name = method_el.get("mangled", "")
            if not mangled_name:
                continue
            idx = _parse_vtable_index(method_el.get("vtable_index"))
            if idx is None:
                unindexed.append(mangled_name)
            else:
                slots[idx] = mangled_name

        return list(slots.items()) + [(None, name) for name in unindexed]


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
                        # base=0 auto-detects 0x.../0o.../0b... prefixes and signs
                        # so common C/C++ initializers like 0x10 don't silently
                        # collapse to 0.
                        m_val = int(m_val_str, 0)
                    except ValueError:
                        m_val = 0
                    members.append(EnumMember(name=m_name, value=m_val))
            enums.append(EnumType(
                name=name, members=members,
                source_location=self._source_location(el),
            ))
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


