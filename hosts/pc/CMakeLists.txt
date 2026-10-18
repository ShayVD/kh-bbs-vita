add_executable(khbbs main.c)
target_link_libraries(khbbs PRIVATE khbbs_runtime khbbs_recomp psprecomp)
target_compile_options(khbbs PRIVATE -Wall -Wextra)
