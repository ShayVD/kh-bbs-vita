add_executable(khbbs main.c)
target_link_libraries(khbbs PRIVATE khbbs_recomp khbbs_runtime psprecomp)
target_compile_options(khbbs PRIVATE -Wall -Wextra)

# --snapshot deletes old outputs when armed, so it must refuse to name an
# input or another output (checked before anything is opened).
add_test(NAME snapshot_refuses_elf COMMAND khbbs --elf t_in.bin --snapshot t_in.bin --snapshot-at sceIoOpen:1)
add_test(NAME snapshot_refuses_sidecar COMMAND khbbs --elf t_in.bin.json --snapshot t_in.bin --snapshot-at sceIoOpen:1)
add_test(NAME snapshot_refuses_trace COMMAND khbbs --elf t_in.elf --trace t_out --snapshot t_out --snapshot-at sceIoOpen:1)
# The same file spelled differently, before it exists, and a hard link.
add_test(NAME snapshot_refuses_respelled COMMAND khbbs --elf t_in.elf --trace ./t_new --snapshot t_new --snapshot-at sceIoOpen:1)
add_test(NAME snapshot_refuses_hard_link COMMAND khbbs --elf t_hl_in --snapshot t_hl_link --snapshot-at sceIoOpen:1)
add_test(NAME snapshot_hard_link_clear COMMAND ${CMAKE_COMMAND} -E rm -f t_new t_hl_in t_hl_link)
add_test(NAME snapshot_hard_link_make COMMAND ${CMAKE_COMMAND} -E touch t_hl_in)
add_test(NAME snapshot_hard_link COMMAND ${CMAKE_COMMAND} -E create_hardlink t_hl_in t_hl_link)
set_tests_properties(snapshot_hard_link_clear snapshot_hard_link_make snapshot_hard_link
                     PROPERTIES FIXTURES_SETUP snapshot_hard_link)
set_tests_properties(snapshot_hard_link_make PROPERTIES DEPENDS snapshot_hard_link_clear)
set_tests_properties(snapshot_hard_link PROPERTIES DEPENDS snapshot_hard_link_make)
set_tests_properties(snapshot_refuses_respelled snapshot_refuses_hard_link
                     PROPERTIES FIXTURES_REQUIRED snapshot_hard_link)
set_tests_properties(snapshot_refuses_elf snapshot_refuses_sidecar snapshot_refuses_trace
                     snapshot_refuses_respelled snapshot_refuses_hard_link
                     PROPERTIES PASS_REGULAR_EXPRESSION "refusing --snapshot")
