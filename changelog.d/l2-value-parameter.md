### Fixed

- On SysV x86-64, comparing stripped C++ libraries with public headers can now identify the exported function whose record parameter switches between value passing and an invisible reference. The CastXML frontend uses the record definition from its Clang parse.
