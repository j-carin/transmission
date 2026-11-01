# Transmission 4.1.4 with background completion moves

This branch is stock Transmission **4.1.4** plus a backport of upstream PR
[#8875](https://github.com/transmission/transmission/pull/8875) ("relocate completed
torrents on a background thread", by Daniel Altamirano), plus one fix of ours that
makes a manual move of a torrent that is still downloading safe (see below). Nothing
else is changed.

## The problem it fixes

When `incomplete-dir` and `download-dir` are on different filesystems, Transmission
moves a finished torrent by copying it, and it does the copy **synchronously on its
session thread**. For the whole copy the daemon is frozen: RPC and the web UI stop
answering, peers stall, and other torrents make no progress. With a slow destination
(NAS, SMB, an rclone/FUSE mount, a USB disk) and a large torrent, that is many minutes.
Sonarr and Radarr see the client as down and back off.

It has been reported upstream for years (#694, #6420, #6961, #7611, and the umbrella
#2462 "nonblocking disk IO"). PR #8875 fixes it by running the move on a dedicated
worker thread, modelled on Transmission's existing background verify worker. As of
this writing it is unreviewed and targets `main`, so it does not apply to the 4.1.x
releases people actually run. This branch is that backport.

## Measured result

A 3 GiB torrent moved from local disk onto an rclone mount of a remote storage box
(about 27 MB/s), with an RPC call fired every second during the move. The stock
comparison was run once, on 4.1.3; the later images were re-run patched only.

|                                     | patched 4.1.4-2 | patched 4.1.4 | patched 4.1.3 | stock 4.1.3 |
|-------------------------------------|-----------------|---------------|---------------|-------------|
| copy duration                       | 119.2 s         | 121.2 s       | 120.1 s       | 119.1 s     |
| `torrent-set-location` returned in  | 0.003 s         | 0.002 s       | 0.001 s       | 118.9 s     |
| RPC polls failed (10 s timeout)     | **0 / 119**     | **0 / 121**   | **0 / 120**   | **108 / 119** |
| longest stretch with no RPC answer  | 1.0 s           | 1.0 s         | 1.0 s         | 118.9 s     |
| data verified at destination        | 100%            | 100%          | 100%          | 100%        |

Full method and per-poll data: [`TEST_RESULTS.md`](TEST_RESULTS.md), [`results/`](results/),
[`asyncmove_test.py`](asyncmove_test.py).

## What changed versus upstream's PR

The two backport commits are cherry-picked with their original authorship. Their changes in
`torrent.cc`, `torrent.h`, `session.cc` and `session.h` are identical to the PR, and
`libtransmission/move.cc` is byte-identical. Adapting to 4.1.x (done on 4.1.3; the
same two commits cherry-picked onto 4.1.4 without conflicts) meant:

- `torrent.cc`: 4.1.3 uses `doomed_.emit(tor)` where `main` has `doomed_(tor)`; the PR's
  `move_remove(tor)` goes before it.
- `move.h` includes `tr-macros.h` (4.1.3 has no `types.h`).
- The new tests in `tests/libtransmission/move-test.cc` were rewritten against 4.1.3's
  test APIs and C++17.
- 4.1.3 still has the write cache that `main` removed; it is flushed on the session
  thread by the existing `close_torrent_files()` before the move is queued.

The client still identifies as stock 4.1.4 everywhere: peer-id `-TR4140-`, user agent
`Transmission/4.1.4`, and `--version` prints `transmission-daemon 4.1.4 (a6d907d9ea)`.
Private trackers see nothing new.

## Moving a torrent that is still downloading

The PR as written (and `main` with it) has a gap that upstream issue #8806 worries
about: on a *manual* set-location of a torrent that is still downloading, blocks that
were requested before the move keep arriving during the copy and are written anyway,
into the old directory or into files that were already copied, and are then lost while
the torrent counts them as done. The automatic move on completion is not exposed,
since that torrent has nothing left to download. Measured with a live peer
([`active_move_test.py`](active_move_test.py)): the previous image moved a 512 MiB,
64-file torrent at 30% and then reported it complete, with no hash failures, but a
verify found 98.68% and 17 of the 64 files differed from the original.

The fix (commit `b45a79d82`) keeps one rule: a torrent in the Moving state never
writes or reads piece data.

- Blocks received before the move are flushed from the write cache by the existing
  `close_torrent_files()` on the session thread, before the state becomes Moving, so
  they travel with the copy.
- A peer block that arrives while Moving is dropped before the cache and reported as a
  reject, so it is re-requested after the move. New requests were already held back.
- Webseeds get the same treatment, and send no new requests while Moving (they never
  checked whether downloading was allowed).
- Queued upload requests are not read from disk while Moving. This also closes a few
  seconds after a completion move starts in which a peer could be sent bytes read from
  the half-copied destination file.

All of this runs on the session thread, so nothing can slip in between the flush and
the state change. Pausing the torrent for the move was the other option, but the queue
or a user could start it again mid-move, and webseeds would then write anyway. It
would also announce a stop to trackers for every move. With the fix the same test
reaches 100% on verify, 64 of 64 files match by SHA-256, and nothing is left in the
old directory. The cost is re-downloading what was in flight (about 12 MB at 4 MB/s).

Tests on 4.1.4 with the fix: the full ctest suite passes (635 run, 0 failed; 22 of
them skip for missing locales or IPv6), including the 25 move and incomplete-dir tests,
which also pass repeated 15 times. Two of those are new for the downloading case, and
one of them fails if `close_torrent_files()` stops flushing the cache.

## Docker image (linuxserver base)

`Dockerfile` builds the daemon on Alpine with the same flags as Alpine's own package
and drops it into the exact `lscr.io/linuxserver/transmission` image (pinned by digest).
Only `/usr/bin/transmission-daemon` differs, so it is a drop-in replacement for an
existing linuxserver container: same s6 setup, PUID/PGID, volumes and config.

```sh
cd asyncmove
./prepare-source.sh                 # exports this branch + submodules to transmission-src.tar
docker build -t local/transmission-asyncmove:4.1.4-2 .
```

The pinned base is arm64 (`linux/arm64`); for amd64, set `--build-arg BASE=` to the
amd64 digest of the same linuxserver tag. Then point your compose service's `image:` at
the new tag and recreate the container.

## Keeping it current

When upstream tags a new release, a new `async-move-<version>` branch is cut from the
tag, the backport is cherry-picked onto it, and the image is rebuilt on that release's
linuxserver image. If #8875 (or an equivalent) is merged into a release, the fork is
retired in favour of stock. The full diff against stock: `git diff 4.1.4 async-move-4.1.4`.

| branch | upstream tag | linuxserver base (arm64 digest) | image tag | status |
|--------|--------------|---------------------------------|-----------|--------|
| `async-move-4.1.4` | 4.1.4 (`a6d907d9ea`, 2026-10-24) | 4.1.4-r0-ls367, `sha256:48ef9dd6e3d7…` | `local/transmission-asyncmove:4.1.4-2` | current: with the downloading-move fix; tested 2026-11-01 |
| `async-move-4.1.4` at `5e572ea0f` | 4.1.4 | 4.1.4-r0-ls367, same digest | `local/transmission-asyncmove:4.1.4` | previous: PR backport only; kept for rollback |
| `async-move-4.1.3` | 4.1.3 (`838877323f`) | 4.1.3-r0-ls364, `sha256:50e09ccd5cd7…` (first built on ls363, `sha256:4813745e2c72…`) | `local/transmission-asyncmove:4.1.3` | previous; kept for rollback |

No warranty. It moves your files; test it on a throwaway torrent first.
