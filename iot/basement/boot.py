import network
import time
import webrepl
import config

# Konfiguracja sieci
SSID = config.SSID
PASSWORD = config.PASSWORD

station = network.WLAN(network.STA_IF)
station.active(True)

if not station.isconnected():
    print("Łączenie z siecią WiFi...")
    station.connect(SSID, PASSWORD)
    
    # Czekamy na połączenie
    while not station.isconnected():
        time.sleep(1)

print("Połączono z WiFi!")
print("Adres IP urządzenia:", station.ifconfig()[0])

# Uruchomienie serwera OTA
webrepl.start()