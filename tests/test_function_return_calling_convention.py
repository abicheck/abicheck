"""Return-site calling conventions of function pointers, joined from clang (default lane).

A function that returns a function pointer keeps the pointee's convention in
clang's ``qualType``, as the trailing attribute of the declaration. castxml
drops it from the XML, so the return's function type needs the same join the
parameter and field sites get. These pin the key and the convention for that
case without a toolchain.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from abicheck.extract.headers.castxml.function_type_cc import (
    castxml_function_type_keys,
    clang_function_type_cc,
)


def test_castxml_return_key_reaches_the_returned_function_type() -> None:
    root = ET.fromstring(
        """<CastXML>
          <Function id="_1" name="rfp" mangled="_Z3rfpv" returns="_3" context="_0"/>
          <PointerType id="_3" type="_4"/>
          <FunctionType id="_4" returns="_2"><Argument type="_5"/></FunctionType>
          <FundamentalType id="_2" name="void"/>
          <FundamentalType id="_5" name="int"/>
          <Namespace id="_0" name="::"/>
        </CastXML>"""
    )
    assert castxml_function_type_keys(root) == {"_4": "return:_Z3rfpv"}


def test_clang_return_convention_is_the_pointee_of_a_pointer_return() -> None:
    # clang prints the returned function pointer's convention as the trailing
    # attribute of the declaration, after the function's own parameter list.
    ast = {
        "kind": "TranslationUnitDecl",
        "inner": [
            {
                "kind": "FunctionDecl",
                "name": "rfp",
                "mangledName": "_Z3rfpv",
                "type": {"qualType": "void (*(void))(int) __attribute__((ms_abi))"},
                "inner": [],
            }
        ],
    }
    assert clang_function_type_cc(ast) == {"return:_Z3rfpv": "ms_abi"}


def test_own_convention_of_a_plain_function_is_not_a_return_convention() -> None:
    ast = {
        "kind": "TranslationUnitDecl",
        "inner": [
            {
                "kind": "FunctionDecl",
                "name": "own",
                "mangledName": "_Z3ownv",
                "type": {"qualType": "void (int) __attribute__((ms_abi))"},
                "inner": [],
            }
        ],
    }
    assert clang_function_type_cc(ast) == {}
