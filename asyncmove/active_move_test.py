#!/usr/bin/env python3
"""Move a torrent that is still downloading, with a live peer sending it blocks.

A seeder and a leecher (throwaway containers) find each other through
mini_tracker.py. The leecher downloads into /downloads/incomplete with a speed
limit; once it reaches MOVE_AT of the data, `torrent-set-location --move` sends
it to /downloads/complete while blocks are still in flight. The download then
has to finish at the new location. Afterwards: nothing may be left in (or
written back to) the old directory, a full verify must reach 100%, and every
file must hash the same as the pristine copy.

usage: active_move_test.py LABEL LEECH_PORT SEED_PORT TORRENT \
           HOST_OLD_DIR HOST_NEW_DIR PRISTINE_DIR OUT_PREFIX [MOVE_AT]

HOST_OLD_DIR / HOST_NEW_DIR are the host paths of the leecher's
/downloads/incomplete and /downloads/complete; PRISTINE_DIR holds the
torrent's top-level folder as created.
"""

import base64
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request

LABEL, LEECH_PORT, SEED_PORT, TORRENT, OLD_DIR, NEW_DIR, PRISTINE, OUT = sys.argv[1:9]
MOVE_AT = float(sys.argv[9]) if len(sys.argv) > 9 else 0.3
FIELDS = ["id", "name", "status", "percentDone", "leftUntilDone", "downloadDir", "haveValid",
          "haveUnchecked", "sizeWhenDone", "rateDownload", "peersConnected", "corruptEver",
          "downloadedEver", "error", "errorString"]
sids: dict[str, str] = {}


def rpc(port, method, args=None, timeout=30):
    url = f"http://127.0.0.1:{port}/transmission/rpc"
    body = json.dumps({"method": method, "arguments": args or {}}).encode()
    for _ in range(3):
        req = urllib.request.Request(url, data=body, headers={
            "X-Transmission-Session-Id": sids.get(port, ""), "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                out = json.loads(r.read())
                if out.get("result") != "success":
                    raise RuntimeError(f"{method}: {out.get('result')}")
                return out["arguments"]
        except urllib.error.HTTPError as e:
            if e.code == 409:
                sids[port] = e.headers.get("X-Transmission-Session-Id", "")
                continue
            raise
    raise RuntimeError("session id handshake failed")


def torrent(port):
    ts = rpc(port, "torrent-get", {"fields": FIELDS})["torrents"]
    return ts[0] if ts else None


def log(msg):
    print(f"[{LABEL} {time.strftime('%H:%M:%S')}] {msg}", flush=True)


def wait_for(pred, what, limit):
    t0 = time.time()
    while True:
        v = pred()
        if v:
            return v
        if time.time() - t0 > limit:
            sys.exit(f"timeout waiting for {what}")
        time.sleep(0.5)


def files_under(root):
    out = []
    for dirpath, _, names in os.walk(root):
        out += [os.path.relpath(os.path.join(dirpath, n), root) for n in names]
    return sorted(out)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def _up(port):
    try:
        rpc(port, "session-get", timeout=2)
        return True
    except Exception:  # noqa: BLE001
        return False


for port in (LEECH_PORT, SEED_PORT):
    wait_for(lambda p=port: _up(p), f"rpc on {port}", 120)
log("both daemons up")

meta = base64.b64encode(open(TORRENT, "rb").read()).decode()

# --- seeder ----------------------------------------------------------------
rpc(SEED_PORT, "torrent-add", {"metainfo": meta, "download-dir": "/downloads/complete", "paused": False})
seed = wait_for(lambda: (t := torrent(SEED_PORT)) and t["percentDone"] >= 1.0 and t["status"] == 6 and t,
                "seeder to seed", 600)
TOTAL = seed["sizeWhenDone"]
log(f"seeder seeding {TOTAL} B")

# --- leecher ---------------------------------------------------------------
rpc(LEECH_PORT, "torrent-add", {"metainfo": meta, "download-dir": "/downloads/incomplete", "paused": False})
samples = []
t_start = time.time()


def sample(phase):
    t = torrent(LEECH_PORT)
    t["t"] = round(time.time() - t_start, 2)
    t["phase"] = phase
    samples.append(t)
    return t


last = 0
while True:
    t = sample("before")
    if t["percentDone"] >= MOVE_AT:
        break
    if time.time() - last > 10:
        log(f"downloading {t['percentDone']*100:.1f}% at {t['rateDownload']/1e6:.1f} MB/s, peers {t['peersConnected']}")
        last = time.time()
    if time.time() - t_start > 900:
        sys.exit("leecher never reached the move point")
    time.sleep(0.5)

pre = t
log(f"moving at {pre['percentDone']*100:.1f}% (haveValid {pre['haveValid']}, {pre['rateDownload']/1e6:.1f} MB/s)")
t_move = time.time()
rpc(LEECH_PORT, "torrent-set-location", {"ids": [pre["id"]], "location": "/downloads/complete", "move": True})
rpc_latency = time.time() - t_move

while True:
    t = sample("moving")
    if t["downloadDir"] == "/downloads/complete":
        break
    if time.time() - t_move > 1800:
        sys.exit("move never finished")
    time.sleep(0.5)
t_moved = time.time()
moving = [s for s in samples if s["phase"] == "moving"]
log(f"move done in {t_moved - t_move:.1f}s; haveValid during the move "
    f"{min(s['haveValid'] for s in moving)}..{max(s['haveValid'] for s in moving)}, "
    f"max rate {max(s['rateDownload'] for s in moving)/1e6:.2f} MB/s")

last = 0
while True:
    t = sample("after")
    if t["percentDone"] >= 1.0:
        break
    if time.time() - last > 10:
        log(f"downloading {t['percentDone']*100:.1f}% at {t['rateDownload']/1e6:.1f} MB/s in {t['downloadDir']}")
        last = time.time()
    if time.time() - t_moved > 1800:
        sys.exit("download never finished after the move")
    time.sleep(0.5)
t_done = time.time()
log(f"download complete {t_done - t_moved:.0f}s after the move; corruptEver={t['corruptEver']}")

leftovers = files_under(OLD_DIR)
log(f"files left in the old directory: {leftovers}")

rpc(LEECH_PORT, "torrent-verify", {"ids": [t["id"]]})
time.sleep(3)
post = wait_for(lambda: (x := torrent(LEECH_PORT)) and x["status"] not in (1, 2) and x, "verify", 1800)
log(f"verify: {post['percentDone']*100:.2f}% haveValid {post['haveValid']} of {TOTAL}, error {post['error']} {post['errorString']!r}")

name = post["name"]
mismatches = []
for rel in files_under(os.path.join(PRISTINE, name)):
    a = sha256(os.path.join(PRISTINE, name, rel))
    dst = os.path.join(NEW_DIR, name, rel)
    b = sha256(dst) if os.path.exists(dst) else None
    if a != b:
        mismatches.append(rel)
n_files = len(files_under(os.path.join(PRISTINE, name)))
log(f"sha256: {n_files - len(mismatches)} of {n_files} files match the pristine copy")

summary = {
    "label": LABEL,
    "total_bytes": TOTAL,
    "move_at_percent": round(pre["percentDone"] * 100, 1),
    "rate_before_move_MBps": round(pre["rateDownload"] / 1e6, 2),
    "set_location_rpc_s": round(rpc_latency, 3),
    "move_duration_s": round(t_moved - t_move, 1),
    "have_valid_at_move": pre["haveValid"],
    "have_valid_during_move": [min(s["haveValid"] for s in moving), max(s["haveValid"] for s in moving)],
    "max_rate_during_move_MBps": round(max(s["rateDownload"] for s in moving) / 1e6, 2),
    "finish_after_move_s": round(t_done - t_moved, 1),
    "corrupt_ever": t["corruptEver"],
    "old_dir_leftovers": leftovers,
    "post_verify": {k: post[k] for k in ("percentDone", "haveValid", "downloadDir", "error", "errorString")},
    "files": n_files,
    "sha256_mismatches": mismatches,
}
json.dump({"summary": summary, "samples": samples}, open(OUT + ".json", "w"), indent=1)
print(json.dumps(summary, indent=1))
