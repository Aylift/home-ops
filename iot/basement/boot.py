import network
import time
import webrepl
import config

station = network.WLAN(network.STA_IF)

# Force a clean Wi-Fi state so an old DHCP lease/autoconnect
# cannot win before the static configuration is applied.
station.active(False)
time.sleep_ms(250)

station.active(True)
time.sleep_ms(100)

try:
    station.disconnect()
except Exception:
    pass

station.ifconfig((
    config.STATIC_IP,
    config.NETMASK,
    config.GATEWAY,
    config.DNS,
))

print("[WIFI] configured:", station.ifconfig())
print("[WIFI] connecting...")

station.connect(config.SSID, config.PASSWORD)

while not station.isconnected():
    time.sleep(1)

print("[WIFI] connected:", station.ifconfig())

# Start OTA server
webrepl.start()

# NOTE: boot.py must return normally here. MicroPython automatically
# executes main.py after boot.py completes. Do NOT add an import-main
# watchdog loop here: a Ctrl-C (KeyboardInterrupt) sent over WebREPL
# derives from BaseException, not Exception, so `except Exception` would
# NOT catch it -- it would propagate through boot.py, abort it, and leave
# the device idle at the REPL. Crash-retry supervision belongs in main.py.
