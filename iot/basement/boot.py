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

# Launch the climate control loop with a watchdog.
# If main.py raises during module-level init (e.g. a transient NTP or
# sensor failure), retry instead of leaving the device idle at the REPL.
while True:
    try:
        import main
        break  # main.py runs its own infinite loop; only reached on import error
    except Exception as e:
        print("[BOOT] main.py failed to start:", repr(e))
        print("[BOOT] retrying in 5s...")
        time.sleep(5)
