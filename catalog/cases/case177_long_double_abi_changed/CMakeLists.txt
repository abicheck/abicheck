# __float128 / libquadmath is x86-64 (and select other) specific; guard the
# build itself so a non-x86-64 host never attempts it, matching
# case64_calling_convention_changed's pattern.
if(NOT (CMAKE_SYSTEM_PROCESSOR MATCHES "^(x86_64|AMD64)$"
        AND CMAKE_CXX_COMPILER_ID MATCHES "^(GNU|Clang|AppleClang)$"))
  message(STATUS "Skipping case177_long_double_abi_changed (requires x86-64 + GCC/Clang)")
  return()
endif()

# libquadmath ships with GCC's runtime, in a directory a non-GCC driver (e.g.
# a standalone Clang) does not always search. Resolve it to a path: first the
# default library search, then the location GCC itself reports; fall back to
# the plain -lquadmath spelling when neither finds it.
find_library(CASE177_QUADMATH NAMES quadmath libquadmath.so.0)
if(NOT CASE177_QUADMATH)
  find_program(CASE177_GCC NAMES gcc)
  if(CASE177_GCC)
    execute_process(
      COMMAND ${CASE177_GCC} -print-file-name=libquadmath.so
      OUTPUT_VARIABLE _case177_qm OUTPUT_STRIP_TRAILING_WHITESPACE
      RESULT_VARIABLE _case177_qm_rc)
    if(_case177_qm_rc EQUAL 0 AND IS_ABSOLUTE "${_case177_qm}" AND EXISTS "${_case177_qm}")
      set(CASE177_QUADMATH "${_case177_qm}")
    endif()
  endif()
endif()
if(CASE177_QUADMATH)
  set(_case177_quadmath_link "${CASE177_QUADMATH}")
else()
  set(_case177_quadmath_link -lquadmath)
endif()

abicheck_add_case(case177_long_double_abi_changed
    LANG CXX
    V1_SOURCES v1.cpp
    V2_SOURCES v2.cpp
    V1_HEADERS v1.hpp
    V2_HEADERS v2.hpp
    V2_LINK_OPTIONS ${_case177_quadmath_link}
    APP_SOURCES app.cpp
    PLATFORMS linux
)
