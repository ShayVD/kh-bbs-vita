# Platform-free runtime: no OS, window or GPU headers here (docs/architecture.md).
add_library(khbbs_runtime STATIC
    args.c
    loader.c
    trace.c
    snapshot.c
    misslog.c
    boot.c
    unimplemented.c
)
target_include_directories(khbbs_runtime PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/include)
target_link_libraries(khbbs_runtime PUBLIC psprecomp)
target_compile_options(khbbs_runtime PRIVATE -Wall -Wextra)
