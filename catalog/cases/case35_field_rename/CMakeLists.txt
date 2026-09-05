abicheck_add_case(case35_field_rename
    V1_SOURCES v1.c
    V2_SOURCES v2.c
    V1_FORCE_INCLUDE ${CMAKE_CURRENT_SOURCE_DIR}/v1.h
    V2_FORCE_INCLUDE ${CMAKE_CURRENT_SOURCE_DIR}/v2.h
    APP_SOURCES app.c
    PLATFORMS linux macos windows
)
