abicheck_add_case(case26_union_field_added
    V1_SOURCES old/lib.c
    V2_SOURCES new/lib.c
    V1_INCLUDE_DIRS ${CMAKE_CURRENT_SOURCE_DIR}/old
    V2_INCLUDE_DIRS ${CMAKE_CURRENT_SOURCE_DIR}/new
    APP_SOURCES app.c
    PLATFORMS linux macos windows
)
