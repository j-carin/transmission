# Transmission 4.1.3 with background completion moves

This branch is stock Transmission **4.1.3** plus a backport of upstream PR
[#8875](https://github.com/transmission/transmission/pull/8875) ("relocate completed
torrents on a background thread", by Daniel Altamirano). Nothing else is changed.

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
(about 27 MB/s), with an RPC call fired every second during the move:

|                                     | this branch | stock 4.1.3 |
|-------------------------------------|-------------|-------------|
| copy duration                       | 120.1 s     | 119.1 s     |
| `torrent-set-location` returned in  | 0.001 s     | 118.9 s     |
| RPC polls failed (10 s timeout)     | **0 / 120** | **108 / 119** |
| longest stretch with no RPC answer  | 1.0 s       | 118.9 s     |
| data verified at destination        | 100%        | 100%        |

Full method and per-poll data: [`TEST_RESULTS.md`](TEST_RESULTS.md), [`results/`](results/),
[`asyncmove_test.py`](asyncmove_test.py).

## What changed versus upstream's PR

The two commits are cherry-picked with their original authorship. The lines changed in
`torrent.cc`, `torrent.h`, `session.cc` and `session.h` are identical to the PR, and
`libtransmission/move.cc` is byte-identical. Adapting to 4.1.3 meant:

- `torrent.cc`: 4.1.3 uses `doomed_.emit(tor)` where `main` has `doomed_(tor)`; the PR's
  `move_remove(tor)` goes before it.
- `move.h` includes `tr-macros.h` (4.1.3 has no `types.h`).
- The new tests in `tests/libtransmission/move-test.cc` were rewritten against 4.1.3's
  test APIs and C++17.
- 4.1.3 still has the write cache that `main` removed; it is flushed on the session
  thread by the existing `close_torrent_files()` before the move is queued.

The client still identifies as stock 4.1.3 everywhere: peer-id `-TR4130-`, user agent
`Transmission/4.1.3`, and `--version` prints `transmission-daemon 4.1.3 (838877323f)`.
Private trackers see nothing new.

Tests: the full libtransmission suite passes (549 run, 549 passed), including the move
and incomplete-dir tests repeated 15 times.

Known limitation, inherited from the PR and equally present on `main`: a *manual*
set-location while a torrent is still downloading can accept blocks already in flight
during the copy. The automatic move on completion is not affected.

## Docker image (linuxserver base)

`Dockerfile` builds the daemon on Alpine with the same flags as Alpine's own package
and drops it into the exact `lscr.io/linuxserver/transmission` image (pinned by digest).
Only `/usr/bin/transmission-daemon` differs, so it is a drop-in replacement for an
existing linuxserver container: same s6 setup, PUID/PGID, volumes and config.

```sh
cd asyncmove
./prepare-source.sh                 # exports this branch + submodules to transmission-src.tar
docker build -t local/transmission-asyncmove:4.1.3 .
```

The pinned base is arm64 (`linux/arm64`); for amd64, set `--build-arg BASE=` to the
amd64 digest of the same linuxserver tag. Then point your compose service's `image:` at
the new tag and recreate the container.

## Keeping it current

When upstream tags a new release, this branch is rebased onto it and rebuilt; if #8875
(or an equivalent) is merged into a release, the fork is retired in favour of stock.
The PR on this fork (base: `upstream-4.1.3`) shows the full diff against stock.

No warranty. It moves your files; test it on a throwaway torrent first.
