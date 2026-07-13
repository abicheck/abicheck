abicheck_add_case(case178_unnamed_type_in_public_abi
    LANG CXX
    V1_SOURCES v1.cpp
    V2_SOURCES v2.cpp
    V1_HEADERS v1.h
    V2_HEADERS v2.h
    V2_COMPILE_OPTIONS -std=c++17
    APP_SOURCES app.cpp
    APP_COMPILE_OPTIONS -std=c++17
    APP_LINK_LIBRARIES dl
    PLATFORMS linux
)
