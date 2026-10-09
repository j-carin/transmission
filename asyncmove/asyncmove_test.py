#!/usr/bin/env python3
"""Cross-filesystem move test against a throwaway transmission container.

Adds the test torrent with its data already in /downloads/incomplete (as the
torrent's download dir, so no automatic completion move fires), waits for the
verify to reach 100%, then asks for `torrent-set-location --move` to
/downloads/complete (the rclone mount). One RPC poll (torrent-get, the same
call `transmission-remote -l` makes) is fired every second on its own thread,
each with a 10 s timeout, from 10 s before the move until 5 s after the data
has fully arrived. Then re-verifies at the destination.

usage: asyncmove_test.py LABEL PORT TORRENT SRC_FILE DST_FILE OUT_PREFIX
"""

import base64
import json
import os
import statistics
import sys
import threading
import time
import urllib.error
import urllib.request

LABEL, PORT, TORRENT, SRC, DST, OUT = sys.argv[1:7]
URL = f"http://127.0.0.1:{PORT}/transmission/rpc"
POLL_TIMEOUT = 10.0
FIELDS = ["id", "name", "status", "percentDone", "leftUntilDone", "downloadDir",
          "recheckProgress", "haveValid", "totalSize", "error", "errorString"]

sid_lock = threading.Lock()
sid = [""]


def rpc(method, args=None, timeout=POLL_TIMEOUT):
    body = json.dumps({"method": method, "arguments": args or {}}).encode()
    for _ in range(3):
        with sid_lock:
            hdr = {"X-Transmission-Session-Id": sid[0], "Content-Type": "application/json"}
        req = urllib.request.Request(URL, data=body, headers=hdr)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                out = json.loads(r.read())
                if out.get("result") != "success":
                    raise RuntimeError(f"{method}: {out.get('result')}")
                return out["arguments"]
        except urllib.error.HTTPError as e:
            if e.code == 409:
                with sid_lock:
                    sid[0] = e.headers.get("X-Transmission-Session-Id", "")
                continue
            raise
    raise RuntimeError("session id handshake failed")


def torrent():
    ts = rpc("torrent-get", {"fields": FIELDS})["torrents"]
    return ts[0] if ts else None


def log(msg):
    print(f"[{LABEL} {time.strftime('%H:%M:%S')}] {msg}", flush=True)


# --- wait for the daemon -------------------------------------------------
t0 = time.time()
while True:
    try:
        rpc("session-get", timeout=2)
        break
    except Exception:
        if time.time() - t0 > 120:
            sys.exit("daemon never answered")
        time.sleep(1)
log("rpc up")

# --- add and verify ------------------------------------------------------
if torrent() is None:
    meta = base64.b64encode(open(TORRENT, "rb").read()).decode()
    rpc("torrent-add", {"metainfo": meta, "download-dir": "/downloads/incomplete", "paused": False})
    log("added")
t_verify0 = time.time()
kicked = False
while True:
    t = torrent()
    if t["status"] not in (1, 2) and t["percentDone"] >= 1.0:
        break
    if not kicked and time.time() - t_verify0 > 10 and t["status"] not in (1, 2):
        rpc("torrent-verify", {"ids": [t["id"]]})
        kicked = True
        log("verify requested explicitly")
    if time.time() - t_verify0 > 900:
        sys.exit(f"pre-move verify never finished: {t}")
    time.sleep(2)
pre = torrent()
log(f"pre-move verify done in {time.time() - t_verify0:.0f}s: {pre['percentDone']*100:.1f}% "
    f"haveValid={pre['haveValid']} dir={pre['downloadDir']}")
TOTAL = pre["totalSize"]
TID = pre["id"]

# --- pollers -------------------------------------------------------------
polls = []          # dicts: sent, done, latency, ok, err, dir
plock = threading.Lock()
stop = threading.Event()


def one_poll():
    sent = time.time()
    rec = {"sent": sent}
    try:
        t = torrent()
        rec.update(ok=True, dir=t["downloadDir"], status=t["status"])
    except Exception as e:  # noqa: BLE001
        rec.update(ok=False, err=type(e).__name__ + ": " + str(e)[:80])
    rec["done"] = time.time()
    rec["latency"] = rec["done"] - sent
    with plock:
        polls.append(rec)


def poll_loop():
    nxt = time.time()
    while not stop.is_set():
        threading.Thread(target=one_poll, daemon=True).start()
        nxt += 1.0
        time.sleep(max(0.0, nxt - time.time()))


fs_samples = []


def fs_state():
    s = os.stat(SRC).st_size if os.path.exists(SRC) else None
    d = os.stat(DST).st_size if os.path.exists(DST) else None
    return s, d


poller = threading.Thread(target=poll_loop, daemon=True)
poller.start()
time.sleep(10)

# --- the move ------------------------------------------------------------
move_rec = {}


def do_move():
    move_rec["sent"] = time.time()
    try:
        rpc("torrent-set-location", {"ids": [TID], "location": "/downloads/complete", "move": True},
            timeout=1800)
        move_rec["ok"] = True
    except Exception as e:  # noqa: BLE001
        move_rec["ok"] = False
        move_rec["err"] = str(e)
    move_rec["done"] = time.time()


mover = threading.Thread(target=do_move)
mover.start()
time.sleep(0.05)
t_move = move_rec.get("sent", time.time())
log("set-location --move sent")

t_fs_done = None
t_dst_first = None
last_print = 0
while True:
    s, d = fs_state()
    now = time.time()
    fs_samples.append({"t": now - t_move, "src": s, "dst": d})
    if d and t_dst_first is None:
        t_dst_first = now
    if now - last_print >= 15:
        log(f"t+{now - t_move:5.0f}s src={s} dst={d}")
        last_print = now
    if t_fs_done is None and s is None and d == TOTAL:
        t_fs_done = now
        log(f"data fully at destination, source gone, t+{now - t_move:.1f}s")
    if t_fs_done is not None:
        with plock:
            confirmed = [p for p in polls if p["ok"] and p["sent"] > t_fs_done
                         and p.get("dir") == "/downloads/complete"]
        if confirmed and now - t_fs_done >= 5:
            break
    if now - t_move > 1800:
        log("TIMEOUT waiting for the move")
        break
    time.sleep(1)
stop.set()
poller.join()
mover.join(timeout=30)
time.sleep(POLL_TIMEOUT + 1)    # let in-flight polls finish or time out

with plock:
    allp = sorted(polls, key=lambda p: p["sent"])
t_end = t_fs_done or time.time()
during = [p for p in allp if t_move <= p["sent"] <= t_end]
before = [p for p in allp if p["sent"] < t_move]
ok_lat = [p["latency"] for p in during if p["ok"]]
fails = [p for p in during if not p["ok"]]
# longest stretch with no successful RPC answer, measured on completion times
answers = sorted([t_move] + [p["done"] for p in allp if p["ok"] and p["done"] >= t_move])
gaps = [b - a for a, b in zip(answers, answers[1:])]
first_answer_after = [p["done"] for p in allp if p["ok"] and p["sent"] > t_move + 0.5]

summary = {
    "label": LABEL,
    "total_bytes": TOTAL,
    "set_location_rpc_latency_s": round(move_rec.get("done", 0) - move_rec.get("sent", 0), 3),
    "set_location_ok": move_rec.get("ok"),
    "copy_duration_s": round(t_fs_done - t_move, 1) if t_fs_done else None,
    "first_dst_bytes_after_s": round(t_dst_first - t_move, 1) if t_dst_first else None,
    "throughput_MBps": round(TOTAL / 1e6 / (t_fs_done - t_move), 1) if t_fs_done else None,
    "baseline_polls": len(before),
    "baseline_median_ms": round(statistics.median([p["latency"] for p in before if p["ok"]]) * 1000, 1)
    if any(p["ok"] for p in before) else None,
    "polls_during_move": len(during),
    "ok_polls_during_move": len(ok_lat),
    "failed_polls_during_move": len(fails),
    "median_latency_ms_ok": round(statistics.median(ok_lat) * 1000, 1) if ok_lat else None,
    "max_latency_ms_ok": round(max(ok_lat) * 1000, 1) if ok_lat else None,
    "max_latency_s_any": round(max(p["latency"] for p in during), 2) if during else None,
    "longest_no_answer_gap_s": round(max(gaps), 1) if gaps else None,
    "failure_kinds": sorted({p["err"].split(":")[0] for p in fails}),
}
log("move summary: " + json.dumps(summary))

# --- re-verify at the destination ----------------------------------------
t_v = time.time()
rpc("torrent-verify", {"ids": [TID]}, timeout=600)
time.sleep(3)
while True:
    t = torrent()
    if t["status"] not in (1, 2):
        break
    if time.time() - t_v > 1200:
        log("TIMEOUT waiting for the post-move verify")
        break
    time.sleep(3)
post = torrent()
summary["post_verify"] = {
    "duration_s": round(time.time() - t_v, 1),
    "percentDone": post["percentDone"],
    "haveValid": post["haveValid"],
    "downloadDir": post["downloadDir"],
    "error": post["error"],
    "errorString": post["errorString"],
}
log("post-move verify: " + json.dumps(summary["post_verify"]))

for p in allp:
    p["sent_rel"] = round(p["sent"] - t_move, 3)
json.dump({"summary": summary, "polls": allp, "fs": fs_samples, "move": move_rec},
          open(OUT + ".json", "w"), indent=1)
print(json.dumps(summary, indent=1))
