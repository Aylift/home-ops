from machine import Pin, I2C
import bme280
import time
import math
import urequests
import ntptime
import config

# --- WEATHER CONFIG ---
API_KEY = config.API_KEY
CITY = config.CITY
WEATHER_URL = f"http://api.openweathermap.org/data/2.5/weather?q={CITY}&appid={API_KEY}&units=metric"

# --- CLIMATE CONFIG ---
THRESHOLD_ON = 60.0      # Fan turn-on threshold
THRESHOLD_OFF = 50.0     # Fan turn-off threshold (hysteresis)
EMERGENCY_RH = 75.0      # Hard flood/failure threshold

# --- DASHBOARD (BACKEND API) CONFIG ---
ENABLE_DASHBOARD = True
DASHBOARD_URL = config.DASHBOARD_URL

# --- HARDWARE INIT ---
i2c = I2C(0, scl=Pin(22), sda=Pin(21))
bme = bme280.BME280(i2c=i2c)
relay = Pin(19, Pin.OUT)
relay.value(0)

# Time sync
try:
    print("Syncing time from internet...")
    ntptime.settime()
except Exception as e:
    print(f"NTP time sync error: {e}")

# Control variables
last_api_check = 0
api_interval = 900  # 900 seconds = 15 minutes
ext_ah = None

def calculate_ah(temp, rh):
    """Calculate absolute humidity (g/m3) from Magnus equation"""
    es = 6.112 * math.exp((17.67 * temp) / (243.5 + temp))
    e = es * (rh / 100.0)
    # Corrected multiplier: 216.74 instead of 2.1674 * 1000 to avoid scale errors
    return (e * 216.74) / (273.15 + temp)

def fetch_external_ah():
    """Fetch weather from API. Returns value or None on error."""
    try:
        response = urequests.get(WEATHER_URL)
        data = response.json()
        response.close()

        if 'main' not in data:
            print(f"\n[API REJECTED] Missing 'main' key (likely 401).")
            return None

        ext_temp = data['main']['temp']
        ext_rh = data['main']['humidity']
        ah = calculate_ah(ext_temp, ext_rh)
        print(f"\n[API UPDATED] Outside: {ext_temp:.1f}C, {ext_rh}% RH -> {ah:.2f} g/m3")
        return ah
    except Exception as e:
        print(f"\n[API ERROR] Connection exception: {e}")
        return None

def should_ventilate(int_temp, int_rh, ext_ah_value):
    """Return tuple: (should_turn_on_fan, "Reason / Mode")"""
    if int_rh >= EMERGENCY_RH:
        return True, "EMERGENCY (Flood)"

    if int_rh <= THRESHOLD_ON and relay.value() == 0:
        return False, "STANDBY (Normal)"

    int_ah = calculate_ah(int_temp, int_rh)

    if ext_ah_value is not None:
        if ext_ah_value < int_ah:
            return True, "API (Outside dry)"
        else:
            return False, "API (Outside wet)"

    # GUARD: API failure, fall back to calendar
    current_month = time.localtime()[1]

    if time.localtime()[0] <= 2000:
        return False, "GUARD (No NTP time)"

    if current_month in [11, 12, 1, 2, 3, 4]:
        return True, "GUARD (Winter)"
    else:
        return False, "GUARD (Summer)"

def send_to_dashboard(payload):
    """Send payload to your server and close the socket"""
    try:
        import ujson
        # Encode to UTF-8 bytes - urequests computes Content-Length from len(str),
        # and multibyte characters would undercount the length and truncate the JSON.
        body = ujson.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        response = urequests.post(DASHBOARD_URL, data=body, headers=headers)
        print("[DASHBOARD] status:", response.status_code)
        response.close() # Critical: urequests easily exhausts RAM without .close()
    except Exception as e:
        print(f"[DASHBOARD ERROR] Could not send data: {e}")

print("\n>>> Climate System v3.0 (IoT Edition) ready <<<")

while True:
    try:
        current_time = time.time()

        # Refresh external data every 15 minutes
        if last_api_check == 0 or (current_time - last_api_check) > api_interval:
            ext_ah = fetch_external_ah()
            last_api_check = current_time

        # Read from basement
        temp = bme.temperature()
        press = bme.pressure()
        hum = bme.humidity()

        # Decision logic
        vent_decision, mode_reason = should_ventilate(temp, hum, ext_ah)

        # Fan control with hysteresis
        if vent_decision and hum > THRESHOLD_ON and relay.value() == 0:
            relay.value(1)
        elif (not vent_decision or hum < THRESHOLD_OFF) and relay.value() == 1:
            relay.value(0)

        fan_state = "ON" if relay.value() == 1 else "OFF"
        print(f"[BASEMENT] T: {temp:.2f}C | RH: {hum:.2f}% | P: {press:.1f}hPa | Fan: {fan_state} | Mode: {mode_reason}")

        # --- DASHBOARD SECTION ---
        if ENABLE_DASHBOARD:
            payload = {
                "timestamp": current_time,
                "temperature": round(temp, 2),
                "humidity": round(hum, 2),
                "pressure": round(press, 1),
                "ah_inside": round(calculate_ah(temp, hum), 2),
                "ah_outside": round(ext_ah, 2) if ext_ah is not None else None,
                "fan_active": relay.value() == 1,
                "mode": mode_reason
            }
            send_to_dashboard(payload)

        # Loop runs every 5 seconds (if sending to backend, consider raising to 15 or 30s)
        time.sleep(5)

    except Exception as e:
        print(f"Main loop error: {e}")
        time.sleep(5)
