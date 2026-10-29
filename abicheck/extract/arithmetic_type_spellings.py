"""Which declared types' initializers are numbers a consumer observes.

A non-const header variable carries its initializer as ``Variable.value`` only
when the type is arithmetic (an integer, bool, character, or floating type,
spelled as the language or ``<stdint.h>``/``<stddef.h>`` names it). For any
other type the initializer is a spelling, not a number: an address
(``const int *p = &a;``), a class construction, a function call. Such a
spelling changes whenever the expression is renamed, so comparing it would
report a change nobody made. Both frontends share this rule.
"""

from __future__ import annotations

import re

__all__ = ["ARITHMETIC_SPELLINGS", "is_arithmetic_spelling"]

_CV_RE = re.compile(r"\b(?:const|volatile)\b")

ARITHMETIC_SPELLINGS: frozenset[str] = frozenset(
    {
        "bool",
        "char",
        "signed char",
        "unsigned char",
        "wchar_t",
        "char8_t",
        "char16_t",
        "char32_t",
        "short",
        "short int",
        "signed short",
        "signed short int",
        "unsigned short",
        "unsigned short int",
        "int",
        "signed",
        "signed int",
        "unsigned",
        "unsigned int",
        "long",
        "long int",
        "signed long",
        "signed long int",
        "unsigned long",
        "unsigned long int",
        "long long",
        "long long int",
        "signed long long",
        "signed long long int",
        "unsigned long long",
        "unsigned long long int",
        "float",
        "double",
        "long double",
        "int8_t",
        "uint8_t",
        "int16_t",
        "uint16_t",
        "int32_t",
        "uint32_t",
        "int64_t",
        "uint64_t",
        "size_t",
        "ptrdiff_t",
    }
)


def is_arithmetic_spelling(type_name: str) -> bool:
    """Whether *type_name* (cv-qualifiers ignored) is an arithmetic type spelling.

    A pointer, reference, array, class, enum, or unrecognized typedef is not.
    """
    return " ".join(_CV_RE.sub(" ", type_name).split()) in ARITHMETIC_SPELLINGS
