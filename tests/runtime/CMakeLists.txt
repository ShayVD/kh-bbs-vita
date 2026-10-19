# Runtime unit tests: synthetic inputs only, never game data.
add_executable(test_loader test_loader.c)
target_link_libraries(test_loader PRIVATE khbbs_runtime)
add_test(NAME loader COMMAND test_loader WORKING_DIRECTORY ${CMAKE_CURRENT_BINARY_DIR})
target_compile_options(test_loader PRIVATE -Wall -Wextra)

add_executable(test_trace test_trace.c)
target_link_libraries(test_trace PRIVATE khbbs_runtime)
add_test(NAME trace COMMAND test_trace WORKING_DIRECTORY ${CMAKE_CURRENT_BINARY_DIR})
target_compile_options(test_trace PRIVATE -Wall -Wextra)

add_executable(test_snapshot test_snapshot.c)
target_link_libraries(test_snapshot PRIVATE khbbs_runtime)
add_test(NAME snapshot COMMAND test_snapshot WORKING_DIRECTORY ${CMAKE_CURRENT_BINARY_DIR})
target_compile_options(test_snapshot PRIVATE -Wall -Wextra)
# Empty directories where the snapshot or its sidecar would go: arming and a
# failed write must leave them alone (stdio cannot make directories). Whatever
# an earlier run left there is cleared first.
set(SNAPSHOT_DIRS t_snap_dir.bin t_snap_dir.bin.json t_snap_sd.bin t_snap_sd.bin.json)
add_test(NAME snapshot_dirs_clear COMMAND ${CMAKE_COMMAND} -E rm -rf ${SNAPSHOT_DIRS}
         WORKING_DIRECTORY ${CMAKE_CURRENT_BINARY_DIR})
add_test(NAME snapshot_dirs COMMAND ${CMAKE_COMMAND} -E make_directory
         t_snap_dir.bin t_snap_dir.bin.json t_snap_sd.bin.json WORKING_DIRECTORY ${CMAKE_CURRENT_BINARY_DIR})
set_tests_properties(snapshot_dirs_clear PROPERTIES FIXTURES_SETUP snapshot_dirs)
set_tests_properties(snapshot_dirs PROPERTIES FIXTURES_SETUP snapshot_dirs DEPENDS snapshot_dirs_clear)
set_tests_properties(snapshot PROPERTIES FIXTURES_REQUIRED snapshot_dirs)

add_executable(test_args test_args.c)
target_link_libraries(test_args PRIVATE khbbs_runtime)
add_test(NAME args COMMAND test_args WORKING_DIRECTORY ${CMAKE_CURRENT_BINARY_DIR})
target_compile_options(test_args PRIVATE -Wall -Wextra)

add_executable(test_misslog test_misslog.c)
target_link_libraries(test_misslog PRIVATE khbbs_runtime)
add_test(NAME misslog COMMAND test_misslog WORKING_DIRECTORY ${CMAKE_CURRENT_BINARY_DIR})
target_compile_options(test_misslog PRIVATE -Wall -Wextra)

# test_boot forks to check the post-boot miss handler.
if(UNIX)
    add_executable(test_boot test_boot.c)
    target_link_libraries(test_boot PRIVATE khbbs_runtime)
    add_test(NAME boot COMMAND test_boot WORKING_DIRECTORY ${CMAKE_CURRENT_BINARY_DIR})
    target_compile_options(test_boot PRIVATE -Wall -Wextra)
endif()
