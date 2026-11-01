#!/bin/bash
# usage: active_run.sh LABEL IMAGE [MOVE_AT]
# Starts the tracker, a seeder and a leecher (throwaway containers on network amtest) and
# runs active_move_test.py. Test data under $T (pristine/am-active, am-active.torrent);
# the leecher's /downloads/complete is $MNT/LABEL on the rclone mount. uTP is off: over
# the docker bridge it stalled in bursts, TCP runs at the 4 MB/s limit.
set -euo pipefail
umask 007
LABEL=$1; IMAGE=$2; MOVE_AT=${3:-0.3}
T=/mnt/block/asyncmove-test2
SRC=$(cd "$(dirname "$0")" && pwd)
MNT=/mnt/block/media-storage/complete/_asyncmove-test
OUT=$T/out; mkdir -p $OUT

settings() {  # $1 = file, $2 = extra json
  cat > "$1" <<EOF
{
  "dht-enabled": false, "pex-enabled": false, "lpd-enabled": false,
  "port-forwarding-enabled": false, "watch-dir-enabled": false,
  "rpc-authentication-required": false, "rpc-whitelist-enabled": false,
  "rpc-host-whitelist-enabled": false, "message-level": 4, "peer-port": 51413,
  "download-dir": "/downloads/complete", "incomplete-dir": "/downloads/incomplete",
  "incomplete-dir-enabled": false, "utp-enabled": false $2
}
EOF
}

docker network inspect amtest >/dev/null 2>&1 || docker network create amtest >/dev/null

if ! docker ps --format '{{.Names}}' | grep -qx amtracker; then
  docker run -d --name amtracker --network amtest --user 1010:1010 --entrypoint python3 \
    -v $SRC/mini_tracker.py:/t.py:ro "$IMAGE" -u /t.py >/dev/null
fi

docker rm -f am-seeder >/dev/null 2>&1 || true
sudo rm -rf $T/seed-config $T/seed-data; mkdir -p $T/seed-config $T/seed-data
cp -al $T/pristine/am-active $T/seed-data/
settings $T/seed-config/settings.json ''
docker run -d --name am-seeder --network amtest -e PUID=1010 -e PGID=1010 -e TZ=Etc/UTC \
  -v $T/seed-config:/config -v $T/seed-data:/downloads/complete \
  -p 127.0.0.1:19093:9091 "$IMAGE" >/dev/null

L=am-leecher-$LABEL
docker rm -f $L >/dev/null 2>&1 || true
sudo rm -rf $T/$LABEL-config $T/$LABEL-old; mkdir -p $T/$LABEL-config $T/$LABEL-old
rm -rf $MNT/$LABEL; mkdir -p $MNT/$LABEL
settings $T/$LABEL-config/settings.json ', "speed-limit-down-enabled": true, "speed-limit-down": 4000'
docker run -d --name $L --network amtest -e PUID=1010 -e PGID=1010 -e TZ=Etc/UTC \
  -v $T/$LABEL-config:/config -v $T/$LABEL-old:/downloads/incomplete -v $MNT/$LABEL:/downloads/complete \
  -p 127.0.0.1:19091:9091 "$IMAGE" >/dev/null

python3 -I $SRC/active_move_test.py "$LABEL" 19091 19093 $T/am-active.torrent \
  $T/$LABEL-old $MNT/$LABEL $T/pristine $OUT/active-$LABEL "$MOVE_AT" 2>&1 | tee $OUT/active-$LABEL.log
docker logs $L > $OUT/daemon-active-$LABEL.log 2>&1
docker stop -t 60 $L am-seeder >/dev/null
echo "run $LABEL finished"
