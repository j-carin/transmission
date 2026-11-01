# Async-move transmission image: 4.1.4 port, build and live-mount test (2026-11-01)

## Source

- Branch `async-move-4.1.4`, cut from upstream tag 4.1.4 (`a6d907d9ea`, released 2026-10-24).
- The two backport commits (`5eec1cce1`, `d152de0b3` on the 4.1.3 branch) cherry-picked with no conflicts. `git range-diff` shows the second commit identical and the first differing only in diff context: 4.1.4 changed the line above one hunk in `save_resume_file()` (the new config-dir lock check). The added and removed lines are the same as on 4.1.3.
- Upstream 4.1.4 touches the same files (config-dir lock in `session.cc`/`session.h`, `set_file_priorities()` now taking the torrent lock, `is_piece_checked()` made public). None of it overlaps the move path; the move worker never calls into those.
- The `asyncmove/` commits cherry-picked cleanly, then the Dockerfile was moved to the 4.1.4 base.

## Unit tests (host build, gcc 13, RelWithDebInfo)

- `libtransmission-test --gtest_filter='*Move*:*IncompleteDir*'`: 23 tests from 4 suites, 23 passed.
- The same filter with `--gtest_repeat=15`: 15 of 15 iterations passed.
- Full `ctest`: 633 run, 0 failed. 22 of the 633 are skipped by the tests themselves (`da_DK`/`fr_FR`/`ru_RU` locales not installed, no global IPv6 source); 11 more are disabled upstream and not counted.

## Image

- Tags: `local/transmission-asyncmove:4.1.4` and `local/transmission-asyncmove:4.1.4-326f15f3ed`
- Image ID: `sha256:baf0e25a788cf3eaeab8483fa3ddf51f8ecbe1ba322c4d594963997e4e934348` (local only)
- Source: branch `async-move-4.1.4` at `326f15f3ed`, exported by `prepare-source.sh`
- Base for both stages: `lscr.io/linuxserver/transmission@sha256:48ef9dd6e3d7e48de55d43cc1e16fd6e9ea269a2f06c0593a3ecbd74755fd387`, the linux/arm64 manifest of `4.1.4-r0-ls367` (index `sha256:5b3c27a96c8a37c878a525ebf377a32032c3a3acfe845ecaa7c3eb10b09c2caa`, built 2026-10-30, image ID `4412c552e2b1`)
- The final image is the base's 10 layers unchanged plus one layer holding only `/usr/bin/transmission-daemon` (root:root, 0755). Env, entrypoint, cmd, volumes and ports are identical to the base; two labels are added.
- The aports APKBUILD for 4.1.4-r0 uses the same flags as 4.1.3-r0, so the build recipe is unchanged.
- This time the builder's `apk add` upgraded nothing: musl 1.2.6-r6, libstdc++ 15.2.0-r9, libcrypto3/libssl3 3.5.9-r0 and zlib 1.3.2-r1 are the base's own versions.

| | stock 4.1.4 image | patched image |
|---|---|---|
| `transmission-daemon --version` | `transmission-daemon 4.1.4 (a6d907d9ea)` | `transmission-daemon 4.1.4 (a6d907d9ea)` |
| `version.h` | | `PEERID_PREFIX "-TR4140-"`, `USERAGENT_PREFIX "4.1.4"`, `LONG_VERSION_STRING "4.1.4 (a6d907d9ea)"` |
| ldd | 19 libs, all resolved | 19 libs, all resolved |
| size | 1,823,864 B | 1,836,696 B |

## Live test on the rclone mount (patched image only)

Same method and driver as the 4.1.3 run below: one throwaway container `transmission-asyncmove-test` on the default bridge, RPC on `127.0.0.1:19091`, PUID/PGID 1010, its own `/config`; a local-disk directory as `/downloads/incomplete` and `complete/_asyncmove-test` on the `bigbox-media` rclone mount as `/downloads/complete`. DHT, PEX, LPD, port forwarding and watch-dir off, an unpublished peer port, message-level 4, `incomplete-dir-enabled=false`. One fresh 3 GiB file from `/dev/urandom`, one tracker-less torrent of 768 pieces of 4 MiB, hard-linked into the incomplete dir from a pristine copy.

| | patched 4.1.4 | patched 4.1.3 (2026-10-09) |
|---|---|---|
| copy duration (move sent until source gone and destination full) | 121.2 s (26.6 MB/s) | 120.1 s (26.8 MB/s) |
| `torrent-set-location` RPC returned after | 0.002 s | 0.001 s |
| baseline RPC median (11 polls before the move) | 1.3 ms | 1.3 ms |
| polls sent during the move | 121 | 120 |
| failed polls (no answer within 10 s) | **0** | **0** |
| median / max latency during the move | 1.4 ms / 8.1 ms | 1.2 ms / 4.6 ms |
| longest stretch with no RPC answer | 1.0 s (the poll interval) | 1.0 s |
| post-move verify | 100%, haveValid 3,221,225,472, no error, 81 s | 100%, no error, 26 s |

The verify took longer than in October because it read the file back through the mount while rclone was uploading it.

Daemon log:

```
03:31:53.177 inf move Background move worker thread started (move.cc:32)
03:31:53.177 inf move Starting a move job; 0 more still queued (move.cc:61)
03:33:53.889 inf move Move job completed successfully (move.cc:78)
03:33:53.889 inf asyncmove-3g.bin Finished moving files from '/downloads/incomplete' to '/downloads/complete' (torrent.cc:1791)
```

**Remote integrity.** This time the file was left on the mount until rclone had uploaded it ("Copied (new)", "upload succeeded try #1" at 03:36:57). Its SHA-256 computed on the storage box over SSH, `c8efecc8e2887e83d371c4b48f2934ac5dde920c1420cbc9d5894c58e22c6eb4`, matches the local pristine copy.

### Shutdown check

The first `docker stop` of the test container (default 10 s grace) ended in SIGKILL, exit 137, about a minute after the verify finished. It did not happen again in six more stops of the same image, each 4 to 7 s with exit 0 and `queue.json` written:
- the same config restarted, no move
- a fresh 64 MiB torrent moved onto the mount, then stopped
- the 3 GiB torrent re-verified on the mount, then stopped
- move then verify then stop, three times

A seventh run with the live service's `cap_drop: ALL`, `cap_add` list and `no-new-privileges` also started, answered RPC and stopped cleanly in 4.5 s. The daemon log of the killed run was not kept, so the cause is unknown. Clean stops take 4 to 7 s against a 10 s default, so a slow one can reach the limit; a longer `stop_grace_period` on the service would cover it.

Results: `results/run-patched-4.1.4.json` (per-poll data), `results/run-patched-4.1.4.log`, `results/daemon-patched-4.1.4.log`.

### Cleanup

The test container (and the shutdown-check containers) were removed, the local test directory deleted, and `complete/_asyncmove-test` deleted from the mount; a listing on the storage box confirms it is gone. The live `transmission` container and the compose file were not touched.

---

# Async-move transmission image: 4.1.3 build and live-mount test (2026-10-09)

## Image

- Tags: `local/transmission-asyncmove:4.1.3` and `local/transmission-asyncmove:4.1.3-d152de0b31`
- Image ID: `sha256:599e979ea7203c652d1094b30baccab1010d7052f40ada8d61bcb997f47a6e07` (local only, never pushed, so there is no repo digest)
- Source: branch `async-move-4.1.3` at `d152de0b31` (two commits on tag 4.1.3), exported with every submodule (recursively) by `prepare-source.sh` into `transmission-src.tar`
- Base for both stages: `lscr.io/linuxserver/transmission@sha256:4813745e2c721997d8849f5718786fd4406b3c656fbef479f87a161e51383cbd` (image ID `a22ab22238f5`, label `4.1.3-r0-ls363`), the linuxserver 4.1.3 image this was tested against

The final image has the base's 10 layers unchanged plus one layer that holds only `/usr/bin/transmission-daemon` (root:root, 0755). Its config (env, entrypoint, cmd, volumes) is identical to the base. The only metadata change is two added labels.

### Version and libraries

| | stock image | patched image |
|---|---|---|
| `transmission-daemon --version` | `transmission-daemon 4.1.3 (838877323f)` | `transmission-daemon 4.1.3 (838877323f)` (byte-identical) |
| `version.h` | | `PEERID_PREFIX "-TR4130-"`, `USERAGENT_PREFIX "4.1.3"`, `LONG_VERSION_STRING "4.1.3 (838877323f)"` |
| ldd | 20 libs, all resolved | the same 20 libs, all resolved, no missing |
| size | 1,812,088 B | 1,826,584 B |

**Revision string.** 4.1.3's CMakeLists.txt reads `$GIT_COMMIT` only when `$JENKINS_URL` is also set. Otherwise it uses git, or a `REVISION` file, or `0`, and it keeps only the first 10 characters. The Dockerfile sets both, with the full 4.1.3 tag hash `838877323facc4cc2b677fe817e203779c437bb1`. Stock prints `838877323f`.

**Build flags.** These follow the aports `community/transmission` APKBUILD (4.1.3-r0):
- clang/clang++ with `-flto -O2 -DNDEBUG` and `-fuse-ld=lld`, on top of abuild's defaults from `/usr/share/abuild/default.conf`
- `CMAKE_BUILD_TYPE=None`, `ENABLE_DEPRECATED=ON`, `ENABLE_NLS=ON`, `INSTALL_LIB=OFF`, `WITH_CRYPTO=openssl`, `WITH_SYSTEMD=OFF`
- system deflate, event2, miniupnpc and psl
- only the daemon target is built (CLI, GTK, Qt, utils and tests are off)

**Builder libraries.** The builder is the live image plus `apk add` of the build deps. Alpine edge has moved on since the base was built, so that install upgraded four runtime packages inside the builder stage only:
- musl 1.2.6-r3 to r5
- libcrypto3/libssl3 3.5.8-r1 to 3.5.9-r0
- zlib 1.3.2-r0 to r1

The headers the binary compiled against are therefore one patch release newer for OpenSSL and zlib. The sonames are the same, and the final image runs against the base's own r3/3.5.8/r0 libraries, which is what both test runs exercised. curl, libevent, libdeflate, libpsl, miniupnpc and libstdc++ matched the live versions exactly.

## Live test on the rclone mount

### Setup

Each image ran as a throwaway container on the default bridge network:
- the patched one as `transmission-asyncmove-test` with RPC on `127.0.0.1:19091`
- the stock one as `transmission-stock-test` with RPC on `127.0.0.1:19092`

Both used the linuxserver PUID/PGID convention and their own `/config`. Mounts:
- a directory on local disk as `/downloads/incomplete`
- a directory on an rclone mount of a remote storage box as `/downloads/complete`


Settings were the image defaults with these changes:
- DHT, PEX, LPD, port forwarding and watch-dir off
- unique peer ports, which were not published
- message-level 4
- `incomplete-dir-enabled=false`

**Data.** One file, 3 GiB (3,221,225,472 B) from `/dev/urandom`, as one torrent with no trackers (768 pieces of 4 MiB). Each run got the same file, hard-linked into the incomplete dir from a pristine copy.

**Procedure** (`asyncmove_test.py`, identical for both images):
1. Add the torrent with `download-dir=/downloads/incomplete`. Incomplete-dir is disabled so that reaching 100% does not start the automatic completion move. That move uses the same `set_location()` path, so the result applies to it.
2. Wait for 100%.
3. Fire one `torrent-get` RPC per second, each on its own thread with a 10 s timeout. A poll counts as failed if it gets no answer within 10 s. Baseline polling runs for 10 s first.
4. Send `torrent-set-location {location:/downloads/complete, move:true}`.
5. Watch the source and destination files every second until the destination holds all 3 GiB and the source is gone.
6. Keep polling for 5 s more.
7. Run `torrent-verify` and wait for it to finish.

### Results

| | patched (async move) | stock 4.1.3 |
|---|---|---|
| copy duration (move sent until source gone and destination full) | 120.1 s (26.8 MB/s) | 119.1 s (27.0 MB/s) |
| `torrent-set-location` RPC returned after | 0.001 s | 118.9 s |
| baseline RPC median (before the move) | 1.3 ms | 1.3 ms |
| polls sent during the move | 120 | 119 |
| failed polls (no answer within 10 s) | **0** | **108** |
| median latency, all polls during the move | 1 ms | 10.0 s (timeout) |
| median / max latency of answered polls | 1.2 ms / 4.6 ms | 4.9 s / 9.9 s (these are the last ~10 s of polls, answered when the freeze lifted) |
| longest stretch with no RPC answer | 1.0 s (the poll interval) | **118.9 s**, the whole copy |
| post-move verify | 100%, haveValid 3,221,225,472, no error, 26 s | 100%, haveValid 3,221,225,472, no error, 36 s |

Daemon log for the patched move:

```
18:11:30.345 inf move Background move worker thread started (move.cc:32)
18:11:30.345 inf move Starting a move job; 0 more still queued (move.cc:61)
18:13:29.789 inf move Move job completed successfully (move.cc:78)
18:13:29.790 inf asyncmove-3g.bin Finished moving files from '/downloads/incomplete' to '/downloads/complete' (torrent.cc:1787)
```

**Reading.** Both images copy at the same speed, because the mount (the rclone VFS cache write path) is the bottleneck. The stock daemon was completely unreachable for the whole copy, and even the `set-location` call that started the move took 119 s to return. Scaled to the mount's rate, that is the 27-minute freeze seen on the 32 GB pack. The patched daemon answered every poll in a few milliseconds during the same copy.

### Remote integrity

The stock run's destination file was uploaded by rclone at 18:20:28 ("Copied (new)", "upload succeeded try #1"). Its SHA-256 on the storage box, computed on the remote side, is `4348ac8b51b1020aca96f4ae09d3af80def4c95de8156f9755a1faedcb29886c`, identical to the local source.

The patched run's destination file was deleted to reset for the stock run (18:14:34) before rclone had finished uploading it. That delete produced the rclone line `Failed to copy: Update ReadFrom failed: context canceled` followed by `upload canceled` and `removed cache file as file deleted`. It was the test's own `rm`, not a Transmission move: that move had finished at 18:13:29 and been verified by 18:14:13. As a result, the patched run's data was verified through the mount (hash check of all 768 pieces) but not checksummed on the box itself. The data path is the same `tr_sys_path_copy` in both images; the patch changes only which thread runs it.

## Notes and surprises

- **The "verify to 100%" before the move was the fast path.** 4.1.3 treats a newly added torrent whose files exist at the right size as a seed without hashing (`is_new_torrent_a_seed()`, unless `torrent-added-verify-mode` is `full`). It reported 100% in under a second. The full hash verify ran only after the move, at the destination, and passed for both images.
- rclone queued the destination for upload twice in each run: once when the copy closed the file, and again when the post-move verify closed it.
- The test containers were on plain bridge networking, as asked. With DHT, PEX and LPD off and no trackers, the only outbound traffic is Transmission's own global-IP lookup at start (it logged `Cached IPv4 global address`, the box's real IP). The data was random bytes, so there was nothing to announce.
- Each test daemon logged `ERR Couldn't read '/config/queue.json'` on first start. This is harmless (a fresh config), but it reached the journal through the container log driver.
- The first build attempt failed at cmake configure, because clang needed gcc's `libgcc_s.so` (from `build-base`, which abuild always installs) and the flags were read from the wrong file. Both are fixed in the Dockerfile. That failed build may have raised a logerror alert.

## Cleanup

- Both test containers were stopped (exit 0) and removed.
- The local test directory was deleted.
- The test directory was deleted from the mount, and a listing on the remote confirms it is gone.
- The builder-stage intermediate image was removed.

The live `transmission` container was never touched. It was started 2026-10-07T01:37:33Z, still runs image `a22ab22238f5`, and `docker-compose.yaml` was last modified 2026-09-28.

## Files

- `Dockerfile`
- `prepare-source.sh` (writes `transmission-src.tar`, the build context)
- `asyncmove_test.py` (the test driver)
- `results/` (per-poll JSON for both runs, the driver logs, the stock daemon log and the build log)
