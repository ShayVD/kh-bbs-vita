# khbbs: Kingdom Hearts Birth by Sleep (ULUS10505 v1.00) static recompilation.
# See docs/architecture.md. Game files come from KHBBS_GAME_DIR (default ./game)
# and generated code goes to the build tree; neither is ever committed.
cmake_minimum_required(VERSION 3.20)  # ctest fixtures: cmake -E rm, create_hardlink
project(khbbs C)

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)

set(KHBBS_HOST "pc" CACHE STRING "Host to build: pc or vita")
set(KHBBS_GAME_DIR "${CMAKE_SOURCE_DIR}/game" CACHE PATH "Directory with EBOOT.dec.ELF (tools/extract_game.sh)")
set(PSPRECOMP_DIR "${CMAKE_SOURCE_DIR}/third_party/psprecomp" CACHE PATH "psprecomp fork checkout")
option(KHBBS_WITH_GAME "Recompile the game and build the host (needs KHBBS_GAME_DIR)" ON)

if(NOT EXISTS "${PSPRECOMP_DIR}/CMakeLists.txt")
    message(FATAL_ERROR "psprecomp not found at ${PSPRECOMP_DIR}; run tools/setup_toolchain.sh clones")
endif()
add_subdirectory("${PSPRECOMP_DIR}" psprecomp EXCLUDE_FROM_ALL)

add_subdirectory(runtime)

enable_testing()
add_subdirectory(tests/runtime)

if(KHBBS_WITH_GAME)
    include(cmake/gen.cmake)
    add_subdirectory(hosts/${KHBBS_HOST})
endif()
