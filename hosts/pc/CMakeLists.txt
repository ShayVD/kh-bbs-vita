add_executable(khbbs main.c)
target_link_libraries(khbbs PRIVATE khbbs_recomp khbbs_runtime psprecomp)
target_compile_options(khbbs PRIVATE -Wall -Wextra)
