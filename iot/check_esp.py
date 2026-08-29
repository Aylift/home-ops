"""Listen to ESP32 REPL output to observe main loop + dashboard errors."""
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
