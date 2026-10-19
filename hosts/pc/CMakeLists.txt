add_executable(khbbs main.c)
target_link_libraries(khbbs PRIVATE khbbs_recomp khbbs_runtime psprecomp)
target_compile_options(khbbs PRIVATE -Wall -Wextra)

# --snapshot deletes old outputs when armed, so it must refuse to name an
# input or another output (checked before anything is opened).
add_test(NAME snapshot_refuses_elf COMMAND khbbs --elf t_in.bin --snapshot t_in.bin --snapshot-at sceIoOpen:1)
add_test(NAME snapshot_refuses_sidecar COMMAND khbbs --elf t_in.bin.json --snapshot t_in.bin --snapshot-at sceIoOpen:1)
add_test(NAME snapshot_refuses_trace COMMAND khbbs --elf t_in.elf --trace t_out --snapshot t_out --snapshot-at sceIoOpen:1)
set_tests_properties(snapshot_refuses_elf snapshot_refuses_sidecar snapshot_refuses_trace
                     PROPERTIES PASS_REGULAR_EXPRESSION "refusing --snapshot")
