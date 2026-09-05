abicheck_add_case(case29_ifunc_transition
    V1_SOURCES old/lib.c
    V2_SOURCES new/lib.c
    V1_INCLUDE_DIRS ${CMAKE_CURRENT_SOURCE_DIR}/old
    V2_INCLUDE_DIRS ${CMAKE_CURRENT_SOURCE_DIR}/new
    APP_SOURCES app.c
    PLATFORMS linux
)
