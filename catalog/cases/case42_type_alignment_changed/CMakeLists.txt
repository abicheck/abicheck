abicheck_add_case(case42_type_alignment_changed
    V1_SOURCES bad.c
    V2_SOURCES good.c
    V1_HEADERS bad.h
    V2_HEADERS good.h
    V1_FORCE_INCLUDE ${CMAKE_CURRENT_SOURCE_DIR}/bad.h
    V2_FORCE_INCLUDE ${CMAKE_CURRENT_SOURCE_DIR}/good.h
    APP_SOURCES app.c
    PLATFORMS linux macos
)
