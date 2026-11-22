"""Read Itanium constructor/destructor ABI names using the libclang C API."""
import ctypes as ct
from clang import cindex as ci


class RawString(ct.Structure):
    _fields_ = [('data', ct.c_void_p), ('flags', ct.c_uint)]


class StringSet(ct.Structure):
    _fields_ = [('strings', ct.POINTER(RawString)), ('count', ct.c_uint)]


def variant_reader(library):
    lib = ct.CDLL(library)
    lib.clang_Cursor_getCXXManglings.argtypes = [ci.Cursor]
    lib.clang_Cursor_getCXXManglings.restype = ct.POINTER(StringSet)
    lib.clang_getCString.argtypes = [RawString]
    lib.clang_getCString.restype = ct.c_char_p
    lib.clang_disposeStringSet.argtypes = [ct.POINTER(StringSet)]

    def read(cursor):
        strings = lib.clang_Cursor_getCXXManglings(cursor)
        if not strings:
            return []
        try:
            s = strings.contents
            return [lib.clang_getCString(s.strings[i]).decode() for i in range(s.count)]
        finally:
            lib.clang_disposeStringSet(strings)
    return read
