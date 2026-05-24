#!/usr/bin/env python3
"""Native Messaging Host for Nexus Cookie Export extension.

Reads the Nexus port from ~/.nexus/port and responds to the
extension's ``get-port`` command via Chrome's native messaging protocol
(4-byte LE length-prefixed JSON over stdin/stdout).
"""

import json
import os
import struct
import sys
from pathlib import Path

PORT_FILE = Path.home() / ".nexus" / "port"
PORT_FILE_ALT = Path.home() / ".nexus" / ".port"


def _read_port() -> int | None:
    for pf in (PORT_FILE, PORT_FILE_ALT):
        if pf.is_file():
            try:
                return int(pf.read_text().strip())
            except (ValueError, OSError):
                pass
    return None


def read_message():
    raw_length = sys.stdin.buffer.read(4)
    if not raw_length or len(raw_length) < 4:
        return None
    length = struct.unpack("=I", raw_length)[0]
    if length == 0:
        return None
    data = sys.stdin.buffer.read(length)
    return json.loads(data)


def send_message(msg):
    encoded = json.dumps(msg).encode("utf-8")
    sys.stdout.buffer.write(struct.pack("=I", len(encoded)))
    sys.stdout.buffer.write(encoded)
    sys.stdout.buffer.flush()


def main():
    while True:
        msg = read_message()
        if msg is None:
            break
        command = msg.get("command", "")
        if command == "get-port":
            port = _read_port()
            if port is not None:
                send_message({"port": port})
            else:
                send_message({"port": None, "error": "port file not found"})
        else:
            send_message({"error": "unknown command"})


if __name__ == "__main__":
    main()
