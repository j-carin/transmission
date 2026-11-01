#!/usr/bin/env python3
"""Minimal HTTP BitTorrent tracker for the active-download move test.

Remembers every peer that announces (address from the connection, port from the
query) and answers each announce with the other peers of the same info hash, in
compact form. stdlib only, so it runs inside the linuxserver transmission image:

    docker run --rm --network amtest --name amtracker --entrypoint python3 \
        -v $PWD/mini_tracker.py:/t.py:ro local/transmission-asyncmove:4.1.4-2 /t.py
"""

import socket
import struct
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PEERS: dict[bytes, dict[tuple[str, int], None]] = {}


def bencode(obj) -> bytes:
    if isinstance(obj, int):
        return b"i%de" % obj
    if isinstance(obj, bytes):
        return b"%d:%s" % (len(obj), obj)
    if isinstance(obj, str):
        return bencode(obj.encode())
    if isinstance(obj, dict):
        return b"d" + b"".join(bencode(k) + bencode(v) for k, v in sorted(obj.items())) + b"e"
    raise TypeError(type(obj))


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        if url.path != "/announce":
            self.send_error(404)
            return
        query = urllib.parse.parse_qs(url.query, encoding="latin-1")
        info_hash = query["info_hash"][0].encode("latin-1")
        peer = (self.client_address[0], int(query["port"][0]))
        swarm = PEERS.setdefault(info_hash, {})
        if query.get("event", [""])[0] == "stopped":
            swarm.pop(peer, None)
        else:
            swarm[peer] = None
        others = b"".join(socket.inet_aton(ip) + struct.pack("!H", port) for ip, port in swarm if (ip, port) != peer)
        body = bencode({"interval": 30, "min interval": 5, "complete": 0, "incomplete": 0, "peers": others})
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        print(f"announce {peer} event={query.get('event', [''])[0]!r} -> {len(others) // 6} peer(s)", flush=True)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 6969
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
