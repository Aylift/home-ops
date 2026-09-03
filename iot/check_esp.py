"""Listen to ESP32 REPL output to observe main loop + dashboard errors.

WARNING: Connecting to the WebREPL REPL sends a Ctrl-C, which interrupts the
running climate loop and drops the device to `>>>`. This script can NEVER
observe a live loop -- it always kills it first. To verify the device is
running, query the backend instead (GET /api/telemetry/latest?node_id=basement)
and check `received_at` is fresh. If you run this script, follow up with
soft_reset.py to restart the loop.
"""
import os
import socket
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import webrepl_cli as wc

IP = os.getenv("ESP_IP", "192.168.1.49")
PASS = os.getenv("WEBREPL_PASS", "admin")

host, port, _ = wc.parse_remote(IP + ":")
s = socket.socket()
s.connect((host, port))
wc.client_handshake(s)
ws = wc.websocket(s)
wc.login(ws, PASS)
ws.ioctl(9, 2)

# Just listen to the REPL output stream for 15s
s.settimeout(1)
end = time.time() + 15
buf = b""
while time.time() < end:
    try:
        data = s.recv(4096)
        if not data:
            break
        buf += data
    except socket.timeout:
        continue
    except OSError:
        break

print(buf.decode("utf-8", "replace"))
s.close()
