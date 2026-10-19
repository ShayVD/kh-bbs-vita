/* Guest RAM snapshot "just before the K-th call to a firmware function", for
 * tools/oracle/mem_diff.py (P2). PPSSPP's side is tools/oracle/snapshot.py.
 *
 * - The event is NAME:K or 0xNID:K (K >= 1, decimal or 0x hex). NAME matches
 *   the name psprecomp registered for the NID; an unimplemented NID has none,
 *   so it needs the 0xNID form.
 * - At the K-th matching call, before its handler runs, RAM
 *   [0x08000000, 0x0A000000) is written raw to `path`, then a sidecar
 *   `path`.json:
 *     {"base":"0x08000000","size":33554432,"event":"NAME:K","side":"khbbs",
 *      "nid":"0x...","ra":"0x..."}
 *   `event` is the string as given; `ra` is the caller's return address.
 *   The hook then removes itself.
 * - It uses psprecomp's pre-call hook, independent of the trace hook in
 *   trace.c; both can be on. */
#ifndef KHBBS_SNAPSHOT_H
#define KHBBS_SNAPSHOT_H

#include <stdint.h>

/* Parses `event` and installs the hook. Removes any old `path` and its
 * sidecar first, so a run whose event never comes leaves no stale snapshot.
 * 0, or -1 (message on stderr) for a bad event or an overlong path. */
int  khbbs_snapshot_arm(const char *event, const char *path);
/* 0 while the event has not come; 1 once the snapshot is written; -1 if the
 * event came but writing the dump or its sidecar failed (both are removed). */
int  khbbs_snapshot_fired(void);
void khbbs_snapshot_disarm(void);   /* removes the hook; fired() keeps its value */

#endif
