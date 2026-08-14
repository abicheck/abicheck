# Reference build for the abicheck Clang facts plugin (ADR-035 D5, ADR-038 Plugin injection).
# Optional optimization — not part of the abicheck CI build. The plugin is
# ABI-locked to the loading clang's LLVM major (ADR-038 C.5): build it once
# against your pinned toolchain image and inject it.
# C is enabled alongside CXX because LLVM's FindFFI/FindTerminfo modules (pulled
# in by find_package(LLVM)) run check_c_source_compiles, which requires the C
# language to be enabled in the project.
cmake_minimum_required(VERSION 3.20)
project(abicheck_facts_plugin CXX C)

find_package(LLVM REQUIRED CONFIG)
find_package(Clang REQUIRED CONFIG)

add_library(abicheck-facts MODULE AbicheckFactsPlugin.cpp)
target_include_directories(abicheck-facts SYSTEM PRIVATE ${LLVM_INCLUDE_DIRS} ${CLANG_INCLUDE_DIRS})
target_compile_definitions(abicheck-facts PRIVATE ${LLVM_DEFINITIONS})
target_compile_features(abicheck-facts PRIVATE cxx_std_17)

# Explicit overrides for the two ABI knobs a downstream LLVM fork can get
# wrong relative to what find_package(LLVM) reports -- see the Intel
# oneAPI/icx experimental section in README.md. Both default to "auto"
# (today's pre-existing, unmodified behavior for every vanilla apt.llvm.org/
# Apple/Debian Clang toolchain): RTTI follows the discovered
# LLVM_ENABLE_RTTI value below, and the standard library follows whatever
# -stdlib the toolchain defaults to.
set(ABICHECK_PLUGIN_RTTI "auto" CACHE STRING
  "RTTI mode for the plugin: auto (honor the discovered LLVM_ENABLE_RTTI), on, or off.")
set_property(CACHE ABICHECK_PLUGIN_RTTI PROPERTY STRINGS auto on off)

set(ABICHECK_PLUGIN_STDLIB "auto" CACHE STRING
  "C++ standard library ABI to build the plugin against: auto (toolchain default), libc++, or libstdc++.")
set_property(CACHE ABICHECK_PLUGIN_STDLIB PROPERTY STRINGS auto libc++ libstdc++)

# The cache STRINGS property above is GUI-only (a CMake ComboBox hint) --
# CMake does not enforce it on a value supplied via -D, so a case mismatch
# or typo (e.g. -DABICHECK_PLUGIN_RTTI=OFF, the CMake ON/OFF convention)
# would otherwise silently fall through both case-sensitive STREQUAL
# branches below, matching neither "off" nor "auto" and producing a plugin
# missing the ABI fix the caller explicitly asked for -- exactly the
# Intel-oneAPI load/crash failure this option exists to prevent (Codex
# review). Normalize case, then fail loudly on anything else.
string(TOLOWER "${ABICHECK_PLUGIN_RTTI}" _abicheck_plugin_rtti)
if(NOT _abicheck_plugin_rtti MATCHES "^(auto|on|off)$")
  message(FATAL_ERROR "ABICHECK_PLUGIN_RTTI must be one of auto, on, off (got: '${ABICHECK_PLUGIN_RTTI}')")
endif()

string(TOLOWER "${ABICHECK_PLUGIN_STDLIB}" _abicheck_plugin_stdlib)
if(NOT _abicheck_plugin_stdlib MATCHES "^(auto|libc\\+\\+|libstdc\\+\\+)$")
  message(FATAL_ERROR "ABICHECK_PLUGIN_STDLIB must be one of auto, libc++, libstdc++ (got: '${ABICHECK_PLUGIN_STDLIB}')")
endif()

# LLVM/Clang are conventionally built without RTTI; a plugin that links their
# symbols must match, or it fails to load (undefined typeinfo). Honor the
# loading toolchain's setting when CMake exposes it, unless the caller
# overrides it explicitly (a vendor-bundled LLVM/Clang CMake package does not
# always populate LLVM_ENABLE_RTTI, so "auto" can mean "unknown, assume RTTI
# is on" rather than "confirmed on").
if(_abicheck_plugin_rtti STREQUAL "off")
  target_compile_options(abicheck-facts PRIVATE -fno-rtti)
elseif(_abicheck_plugin_rtti STREQUAL "auto" AND NOT LLVM_ENABLE_RTTI)
  target_compile_options(abicheck-facts PRIVATE -fno-rtti)
endif()

# Some toolchains (observed for Intel's icpx/icx oneAPI compiler) run their
# Clang frontend against libc++ rather than the host's libstdc++, even on
# Linux -- a plugin built against the wrong one crosses the Clang plugin
# ABI boundary (ParseArgs's std::vector<std::string> argument, among others)
# with mismatched container layouts and crashes rather than merely failing
# to load. "auto" (the default) changes nothing, matching every existing
# vanilla-Clang build.
if(_abicheck_plugin_stdlib STREQUAL "libc++")
  target_compile_options(abicheck-facts PRIVATE -stdlib=libc++)
  target_link_options(abicheck-facts PRIVATE -stdlib=libc++)
elseif(_abicheck_plugin_stdlib STREQUAL "libstdc++")
  target_compile_options(abicheck-facts PRIVATE -stdlib=libstdc++)
  target_link_options(abicheck-facts PRIVATE -stdlib=libstdc++)
endif()

# Clang plugins link against the loading clang's symbols; do not bundle LLVM.
set_target_properties(abicheck-facts PROPERTIES PREFIX "lib")
