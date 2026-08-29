"""Soft-reset ESP32 via WebREPL: send machine.reset() to reboot cleanly."""
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

# Interrupt any running main.py loop (Ctrl-C), then soft reset so boot.py + main.py run
ws.write(b"\x03", wc.WEBREPL_FRAME_TXT)
time.sleep(1)
ws.write(b"import machine\r\nmachine.reset()\r\n", wc.WEBREPL_FRAME_TXT)
print("Soft reset command sent.")
# Keep the socket open a few seconds so the device fully processes the reset.
# Closing immediately can drop the reset command before it is executed.
time.sleep(3)
s.close()
