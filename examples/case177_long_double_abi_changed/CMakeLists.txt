# __float128 / libquadmath is x86-64 (and select other) specific; guard the
# build itself so a non-x86-64 host never attempts it, matching
# case64_calling_convention_changed's pattern.
if(NOT (CMAKE_SYSTEM_PROCESSOR MATCHES "^(x86_64|AMD64)$"
        AND CMAKE_CXX_COMPILER_ID MATCHES "^(GNU|Clang|AppleClang)$"))
  message(STATUS "Skipping case177_long_double_abi_changed (requires x86-64 + GCC/Clang)")
  return()
endif()

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
