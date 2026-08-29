import network
import time
import webrepl
import config

# Network config
SSID = config.SSID
PASSWORD = config.PASSWORD

station = network.WLAN(network.STA_IF)
station.active(True)

if not station.isconnected():
    print("Connecting to WiFi...")
    station.connect(SSID, PASSWORD)

    # Wait for connection
    while not station.isconnected():
        time.sleep(1)

print("Connected to WiFi!")
print("Device IP:", station.ifconfig()[0])

# Start OTA server
webrepl.start()
