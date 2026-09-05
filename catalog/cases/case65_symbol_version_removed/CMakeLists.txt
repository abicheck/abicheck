abicheck_add_case(case65_symbol_version_removed
    V1_SOURCES v1.c
    V2_SOURCES v2.c
    V1_HEADERS v1.h
    V2_HEADERS v2.h
    V1_LINK_OPTIONS -Wl,--version-script=${CMAKE_CURRENT_SOURCE_DIR}/v1.map
    V2_LINK_OPTIONS -Wl,--version-script=${CMAKE_CURRENT_SOURCE_DIR}/v2.map
    APP_SOURCES app.c
    PLATFORMS linux
)
