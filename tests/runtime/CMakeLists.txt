# Runtime unit tests: synthetic inputs only, never game data.
add_executable(test_loader test_loader.c)
target_link_libraries(test_loader PRIVATE khbbs_runtime)
add_test(NAME loader COMMAND test_loader WORKING_DIRECTORY ${CMAKE_CURRENT_BINARY_DIR})
