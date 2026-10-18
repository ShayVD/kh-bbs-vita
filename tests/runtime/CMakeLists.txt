# Runtime unit tests: synthetic inputs only, never game data.
add_executable(test_loader test_loader.c)
target_link_libraries(test_loader PRIVATE khbbs_runtime)
add_test(NAME loader COMMAND test_loader WORKING_DIRECTORY ${CMAKE_CURRENT_BINARY_DIR})
target_compile_options(test_loader PRIVATE -Wall -Wextra)

add_executable(test_trace test_trace.c)
target_link_libraries(test_trace PRIVATE khbbs_runtime)
add_test(NAME trace COMMAND test_trace WORKING_DIRECTORY ${CMAKE_CURRENT_BINARY_DIR})
target_compile_options(test_trace PRIVATE -Wall -Wextra)

add_executable(test_boot test_boot.c)
target_link_libraries(test_boot PRIVATE khbbs_runtime)
add_test(NAME boot COMMAND test_boot WORKING_DIRECTORY ${CMAKE_CURRENT_BINARY_DIR})
target_compile_options(test_boot PRIVATE -Wall -Wextra)
