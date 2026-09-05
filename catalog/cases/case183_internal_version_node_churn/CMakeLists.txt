# Symbol versioning (linker version scripts) is a GNU ld / ELF concept.
abicheck_add_case(case183_internal_version_node_churn
    V1_SOURCES old/lib.c
    V2_SOURCES new/lib.c
    V1_HEADERS old/lib.h
    V2_HEADERS new/lib.h
    V1_LINK_OPTIONS "-Wl,--version-script=${CMAKE_CURRENT_SOURCE_DIR}/old/version.map"
    V2_LINK_OPTIONS "-Wl,--version-script=${CMAKE_CURRENT_SOURCE_DIR}/new/version.map"
    APP_SOURCES app.c
    PLATFORMS linux
)
