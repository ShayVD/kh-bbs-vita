# Recompile the user's EBOOT into ${KHBBS_GEN_DIR}. The output is derived from
# the game's code, so it only ever lives in the build tree.
set(KHBBS_ELF "${KHBBS_GAME_DIR}/EBOOT.dec.ELF")
if(NOT EXISTS "${KHBBS_ELF}")
    message(FATAL_ERROR "${KHBBS_ELF} not found. Run tools/extract_game.sh <iso> ${KHBBS_GAME_DIR}, "
                        "or configure with -DKHBBS_WITH_GAME=OFF to build only the runtime and tests.")
endif()

set(KHBBS_SPLIT 64)
set(KHBBS_GEN_DIR "${CMAKE_BINARY_DIR}/gen")
set(KHBBS_GEN_SRCS "${KHBBS_GEN_DIR}/main_register.c" "${KHBBS_GEN_DIR}/main_imports.c")
math(EXPR _last "${KHBBS_SPLIT} - 1")
foreach(i RANGE 0 ${_last})
    if(i LESS 10)
        set(_n "00${i}")
    elseif(i LESS 100)
        set(_n "0${i}")
    else()
        set(_n "${i}")
    endif()
    list(APPEND KHBBS_GEN_SRCS "${KHBBS_GEN_DIR}/main_funcs_${_n}.c")
endforeach()

add_custom_command(
    OUTPUT ${KHBBS_GEN_SRCS} "${KHBBS_GEN_DIR}/main_funcs.h"
    COMMAND ${CMAKE_COMMAND} -E make_directory "${KHBBS_GEN_DIR}"
    COMMAND $<TARGET_FILE:allegrexrecomp> emit "${KHBBS_ELF}" "${KHBBS_GEN_DIR}" main --split ${KHBBS_SPLIT}
    DEPENDS allegrexrecomp "${KHBBS_ELF}"
    COMMENT "Recompiling ${KHBBS_ELF}"
    VERBATIM)

add_library(khbbs_recomp STATIC ${KHBBS_GEN_SRCS})
target_include_directories(khbbs_recomp PRIVATE "${KHBBS_GEN_DIR}")
# khbbs_runtime provides psp_unimplemented; listing it here keeps single-pass
# linkers (GNU ld on Vita) resolving it after the generated objects.
target_link_libraries(khbbs_recomp PUBLIC khbbs_runtime psprecomp)
# -ffp-contract=off keeps PSP float semantics (no fused multiply-add); -w
# because generated code trips every unused-label/variable warning by design.
target_compile_options(khbbs_recomp PRIVATE -O1 -ffp-contract=off -w)
