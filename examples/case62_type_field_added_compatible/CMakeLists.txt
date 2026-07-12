abicheck_add_case(case62_type_field_added_compatible
    V1_SOURCES old/lib.c
    V2_SOURCES new/lib.c
    V1_HEADERS old/lib.h
    V2_HEADERS new/lib.h
    V1_FORCE_INCLUDE ${CMAKE_CURRENT_SOURCE_DIR}/old/lib.h
    V2_FORCE_INCLUDE ${CMAKE_CURRENT_SOURCE_DIR}/new/lib.h
    APP_SOURCES app.c
    PLATFORMS linux macos
)
