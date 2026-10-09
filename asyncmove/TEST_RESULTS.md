# Async-move transmission image: build and live-mount test (2026-10-09)

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
