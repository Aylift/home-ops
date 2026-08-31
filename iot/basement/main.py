from machine import Pin, I2C
import bme280
import time
import math
import urequests
import ntptime
import config

# --- WEATHER CONFIG ---
# Query by coordinates (lat/lon) for the closest weather to home instead of a
# city name ~30km away. OWM returns the nearest grid point to these coords.
API_KEY = config.API_KEY
LAT = config.LAT
LON = config.LON
WEATHER_URL = f"http://api.openweathermap.org/data/2.5/weather?lat={LAT}&lon={LON}&appid={API_KEY}&units=metric"

# --- CLIMATE CONFIG ---
THRESHOLD_ON = 60.0      # Fan turn-on threshold
THRESHOLD_OFF = 50.0     # Fan turn-off threshold (hysteresis)
EMERGENCY_RH = 75.0      # Hard flood/failure threshold
AH_HYSTERESIS = 0.5      # Dead-band (g/m3): fan flips only when AH differs by > this

# --- TIMING ---
# Authoritative control/sleep cadence. All device timing is derived from this.
LOOP_INTERVAL = 300

MIN_RUN_TIME = LOOP_INTERVAL
MIN_OFF_TIME = LOOP_INTERVAL
API_INTERVAL = 3 * LOOP_INTERVAL
HEARTBEAT_INTERVAL = LOOP_INTERVAL

# --- DASHBOARD (BACKEND API) CONFIG ---
ENABLE_DASHBOARD = True
DASHBOARD_URL = config.DASHBOARD_URL

# --- HARDWARE INIT ---
# Wrap in try/except so a transient sensor/relay failure cannot abort the
# whole boot (which would leave the device idle at the REPL prompt).
try:
    i2c = I2C(0, scl=Pin(22), sda=Pin(21))
    bme = bme280.BME280(i2c=i2c)
    relay = Pin(19, Pin.OUT)
    relay.value(0)
except Exception as e:
    print(f"[HARDWARE INIT ERROR] {e}")
    raise  # let boot.py watchdog retry

# Time sync
try:
    print("Syncing time from internet...")
    ntptime.settime()
except Exception as e:
    print(f"NTP time sync error: {e}")

# Control variables
prev_fan = None          # Last known fan state, to detect transitions
prev_emergency = False   # Last emergency state, to detect entry into emergency
last_state_change = 0    # time.time() of last fan state flip
last_api_check = 0
last_heartbeat = 0
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

def should_ventilate(int_temp, int_rh, ext_ah_value, fan_on, now):
    """Return tuple: (should_turn_on_fan, "Reason / Mode").

    Hysteresis: the fan flips only when the AH difference exceeds
    AH_HYSTERESIS, and only after MIN_RUN_TIME / MIN_OFF_TIME have elapsed
    since the last flip. This stops rapid on/off cycling when inside and
    outside AH are nearly equal (sensor/API noise).

    Emergency is AH-aware: high RH alone (e.g. heavy rain) is NOT a flood.
    The fan is only forced ON when outside air is actually drier than inside;
    if outside is wetter or unknown, ventilating would pull MORE moisture in,
    so the fan stays OFF. The emergency branch decides immediately (no
    MIN_OFF_TIME) so a genuine flood is never delayed by anti-cycling.
    """
    int_ah = calculate_ah(int_temp, int_rh)

    if int_rh >= EMERGENCY_RH:
        if ext_ah_value is not None:
            diff = int_ah - ext_ah_value
            if diff > AH_HYSTERESIS:
                return True, "EMERGENCY (Outside dry)"
            else:
                return False, "EMERGENCY (Outside wet)"
        else:
            return False, "EMERGENCY (Outside unknown)"

    if int_rh <= THRESHOLD_ON and not fan_on:
        return False, "STANDBY (Normal)"

    if ext_ah_value is not None:
        diff = int_ah - ext_ah_value  # >0 means outside is drier -> ventilate
        # Anti-cycling: don't flip until the min dwell time has passed.
        if fan_on and (now - last_state_change) < MIN_RUN_TIME:
            return True, "API (Outside dry)"
        if not fan_on and (now - last_state_change) < MIN_OFF_TIME:
            return False, "API (Outside wet)"
        # Hysteresis dead-band: ignore tiny differences.
        if diff > AH_HYSTERESIS:
            return True, "API (Outside dry)"
        elif diff < -AH_HYSTERESIS:
            return False, "API (Outside wet)"
        else:
            # Inside the dead-band: hold the current state.
            return fan_on, "API (Outside dry)" if fan_on else "API (Outside wet)"

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

        # Refresh external weather data less frequently than the control loop.
        if last_api_check == 0 or (current_time - last_api_check) >= API_INTERVAL:
            ext_ah = fetch_external_ah()
            last_api_check = current_time

        # Read from basement
        temp = bme.temperature()
        press = bme.pressure()
        hum = bme.humidity()

        # Current states
        fan_on = relay.value() == 1
        emergency = hum >= EMERGENCY_RH

        # Decision logic
        vent_decision, mode_reason = should_ventilate(
            temp, hum, ext_ah, fan_on, current_time
        )

        # Apply decision and track fan state changes.
        if vent_decision != fan_on:
            last_state_change = current_time

        relay.value(1 if vent_decision else 0)
        fan_now = relay.value() == 1

        fan_changed = prev_fan is not None and fan_now != prev_fan
        emergency_entered = emergency and not prev_emergency

        fan_state = "ON" if fan_now else "OFF"

        print(
            f"[BASEMENT] T: {temp:.2f}C | RH: {hum:.2f}% | "
            f"P: {press:.1f}hPa | Fan: {fan_state} | Mode: {mode_reason}"
        )

        # --- DASHBOARD SECTION ---
        # Send on fan transition, emergency entry, or regular heartbeat.
        if ENABLE_DASHBOARD:
            due_heartbeat = (
                last_heartbeat == 0
                or (current_time - last_heartbeat) >= HEARTBEAT_INTERVAL
            )

            if fan_changed or emergency_entered or due_heartbeat:
                payload = {
                    "timestamp": current_time,
                    "temperature": round(temp, 2),
                    "humidity": round(hum, 2),
                    "pressure": round(press, 1),
                    "ah_inside": round(calculate_ah(temp, hum), 2),
                    "ah_outside": round(ext_ah, 2)
                        if ext_ah is not None else None,
                    "fan_active": fan_now,
                    "mode": mode_reason
                }

                if fan_changed:
                    payload["action"] = (
                        "Fan turned ON" if fan_now else "Fan turned OFF"
                    )
                elif emergency_entered:
                    payload["action"] = "EMERGENCY: high humidity"

                send_to_dashboard(payload)
                last_heartbeat = current_time

            prev_fan = fan_now
            prev_emergency = emergency

        # Sleep until the next control cycle.
        time.sleep(LOOP_INTERVAL)

    except Exception as e:
        print(f"Main loop error: {e}")
        time.sleep(LOOP_INTERVAL)
