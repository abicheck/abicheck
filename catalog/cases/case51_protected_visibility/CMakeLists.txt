# -fsemantic-interposition is explicit on both sides: it is GCC's default,
# but Clang (since 13) assumes no interposition at -O1+ and may inline or
# directly call a default-visibility function from inside the library -- the
# v1 baseline would then never call an interposed hook at all, so the case's
# promise ("v1 is interposable") would not hold under Clang Release builds.
abicheck_add_case(case51_protected_visibility
    V1_SOURCES old/lib.c
    V2_SOURCES new/lib.c
    V1_INCLUDE_DIRS ${CMAKE_CURRENT_SOURCE_DIR}/old
    V2_INCLUDE_DIRS ${CMAKE_CURRENT_SOURCE_DIR}/new
    V1_COMPILE_OPTIONS -fsemantic-interposition
    V2_COMPILE_OPTIONS -fsemantic-interposition
    APP_SOURCES app.c
    PLATFORMS linux
)
