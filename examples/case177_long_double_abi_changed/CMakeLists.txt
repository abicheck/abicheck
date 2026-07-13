abicheck_add_case(case177_long_double_abi_changed
    LANG CXX
    V1_SOURCES v1.cpp
    V2_SOURCES v2.cpp
    V1_HEADERS v1.hpp
    V2_HEADERS v2.hpp
    V2_LINK_OPTIONS -lquadmath
    APP_SOURCES app.cpp
    PLATFORMS linux
)
