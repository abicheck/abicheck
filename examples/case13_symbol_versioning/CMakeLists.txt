abicheck_add_case(case13_symbol_versioning
    V1_SOURCES old/lib.c
    V2_SOURCES new/lib.c
    V2_LINK_OPTIONS "LINKER:--version-script=${CMAKE_CURRENT_SOURCE_DIR}/libfoo.map"
    APP_SOURCES app.c
    PLATFORMS linux
)
