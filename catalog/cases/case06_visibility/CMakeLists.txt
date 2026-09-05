abicheck_add_case(case06_visibility
    V1_SOURCES bad.c
    V2_SOURCES good.c
    V2_COMPILE_OPTIONS -fvisibility=hidden
    APP_SOURCES app.c
    APP_LINK_LIBRARIES ${CMAKE_DL_LIBS}
    PLATFORMS linux macos
)
