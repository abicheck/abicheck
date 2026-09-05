# case139: Symbol version node removed — v1 exports beta under version node
# LIBX_2.0; v2 deletes that node and folds beta into LIBX_1.0. A consumer
# linked against beta@LIBX_2.0 can no longer resolve its versioned symbol.
abicheck_add_case(case139_symbol_version_node_removed
    V1_SOURCES v1.c
    V1_LINK_OPTIONS -Wl,--version-script=${CMAKE_CURRENT_SOURCE_DIR}/v1.map
    V2_SOURCES v2.c
    V2_LINK_OPTIONS -Wl,--version-script=${CMAKE_CURRENT_SOURCE_DIR}/v2.map
    APP_SOURCES app.c
    PLATFORMS linux
)
