from machine import Pin, I2C
import bme280
import time
import math
import urequests
import ntptime
import config

# Hard cap on every HTTP call so a dead backend (PC powered off, no RST to fail
# fast) can never stall the control loop. Without this the socket connect can
# block for minutes and the relay stays frozen in its last state.
# NOTE: MicroPython's socket module has no setdefaulttimeout(), so the timeout
# is passed per-request to urequests instead (see HTTP_TIMEOUT below).
HTTP_TIMEOUT = 10

# --- WEATHER CONFIG ---
# The backend is the single weather authority: it fetches OpenWeatherMap once,
# caches it, and serves the normalized result to every ESP32 + the dashboard.
# This device just reads the backend's /api/weather/current projection, so no
# OWM API key or coordinates live on the device anymore.

# --- CLIMATE CONFIG ---
THRESHOLD_ON = 55.0      # Fan turn-on threshold (aim ~55% RH)
THRESHOLD_OFF = 50.0     # Fan turn-off threshold (hysteresis)
EMERGENCY_RH = 75.0      # Hard flood/failure threshold
AH_HYSTERESIS = 0.5      # Dead-band (g/m3): fan flips only when AH differs by > this

# GUARD fallback (backend unreachable): decide on inside RH alone, with a
# temperature gate. Ventilating a warm, humid basement is safe; a cold one is
# not (condensation risk), so the fan only runs when it is humid AND warm.
GUARD_TEMP_MIN = 18.0    # Only ventilate in GUARD when temp is above this (C)
GUARD_RH_ON = 60.0       # GUARD: turn fan ON above this RH
GUARD_RH_OFF = 55.0      # GUARD: turn fan OFF below this RH (hysteresis)

# How long a cached outside-AH reading stays usable when the backend is down.
# 6h covers a PC being powered off overnight without dropping to GUARD.
EXT_AH_MAX_AGE = 6 * 3600

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
NODE_ID = config.NODE_ID
# Weather endpoint on the same backend. DASHBOARD_URL points at the telemetry
# POST (…/api/telemetry); swap that suffix for the weather projection endpoint.
WEATHER_URL = DASHBOARD_URL.replace("/api/telemetry", "/api/weather/current")
# Manual fan override set from the dashboard. Polled every cycle; while active
# the fan is forced ON regardless of the climate decision.
OVERRIDE_URL = DASHBOARD_URL.replace("/api/telemetry", "/api/fan/override")


def calculate_ah(temp, rh):
    """Calculate absolute humidity (g/m3) from Magnus equation"""
    es = 6.112 * math.exp((17.67 * temp) / (243.5 + temp))
    e = es * (rh / 100.0)
    # Corrected multiplier: 216.74 instead of 2.1674 * 1000 to avoid scale errors
    return (e * 216.74) / (273.15 + temp)


def fetch_override():
    """Return the remaining override seconds from the backend, or 0.

    Any failure (backend down, 404, bad JSON) returns 0 so the device simply
    falls back to its own climate logic — the override is a convenience, never
    a dependency.
    """
    response = None
    try:
        response = urequests.get(
            f"{OVERRIDE_URL}?node_id={NODE_ID}", timeout=HTTP_TIMEOUT
        )
        if response.status_code != 200:
            return 0
        data = response.json()
        if not data.get("active"):
            return 0
        return int(data.get("remaining_seconds", 0))
    except Exception as e:
        print(f"\n[OVERRIDE ERROR] {e}")
        return 0
    finally:
        if response is not None:
            try:
                response.close()
            except Exception:
                pass


def fetch_external_ah():
    """Fetch outside absolute humidity from the backend weather endpoint.

    Returns the AH value (g/m3) or None on error. The backend computes AH from
    OWM temp/RH, so this device only reads the ready-made
    `absolute_humidity_g_m3` field. A 404 (weather disabled) or 503
    (unavailable) returns None; the caller keeps the last cached value.
    """
    response = None
    try:
        response = urequests.get(WEATHER_URL, timeout=HTTP_TIMEOUT)
        status = response.status_code
        data = response.json()

        if status != 200 or 'absolute_humidity_g_m3' not in data:
            print(f"\n[API REJECTED] Weather endpoint status {status}.")
            return None

        ah = data['absolute_humidity_g_m3']
        print(
            f"\n[API UPDATED] Outside: {data.get('temperature_c'):.1f}C, "
            f"{data.get('relative_humidity_pct'):.0f}% RH -> {ah:.2f} g/m3"
        )
        return ah
    except Exception as e:
        print(f"\n[API ERROR] Connection exception: {e}")
        return None
    finally:
        # Always close: urequests leaks RAM/sockets otherwise, and a raise in
        # .json() would otherwise skip the close entirely.
        if response is not None:
            try:
                response.close()
            except Exception:
                pass


def _guard_decision(int_temp, int_rh, fan_on):
    """Fallback when outside AH is unknown (backend down, no fresh cache).

    Decides on inside readings alone: ventilate only when the basement is
    humid AND warm enough that outside air is unlikely to be colder/wetter
    (condensation risk). Below GUARD_TEMP_MIN the fan stays OFF.
    """
    if int_temp < GUARD_TEMP_MIN:
        return False, "GUARD (Cold)"
    if int_rh >= GUARD_RH_ON:
        return True, "GUARD (Humid)"
    if int_rh <= GUARD_RH_OFF:
        return False, "GUARD (Dry)"
    # Dead-band: hold the current state.
    return fan_on, "GUARD (Humid)" if fan_on else "GUARD (Dry)"


def should_ventilate(int_temp, int_rh, ext_ah_value, fan_on, now, last_state_change):
    """Return tuple: (should_turn_on_fan, "Reason / Mode").

    Hysteresis: the fan flips only when the AH difference exceeds
    AH_HYSTERESIS, and only after MIN_RUN_TIME / MIN_OFF_TIME have elapsed
    since the last flip. This stops rapid on/off cycling when inside and
    outside AH are nearly equal (sensor/API noise).

    Emergency is AH-aware: high RH alone (e.g. heavy rain) is NOT a flood.
    The fan is only forced ON when outside air is actually drier than inside;
    if outside is wetter, ventilating would pull MORE moisture in, so the fan
    stays OFF. When outside AH is unknown (backend down, no cache) we fall back
    to the GUARD rule (humid + warm) rather than a hard OFF, so a genuine flood
    in a warm basement is still ventilated. The emergency branch decides
    immediately (no MIN_OFF_TIME) so a genuine flood is never delayed.
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
            # Unknown outside AH: use the GUARD rule instead of forcing OFF.
            return _guard_decision(int_temp, int_rh, fan_on)

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

    # GUARD: backend unreachable and no usable cached outside AH.
    return _guard_decision(int_temp, int_rh, fan_on)


def send_to_dashboard(payload):
    """Send payload to your server and close the socket"""
    response = None
    try:
        import ujson
        # Encode to UTF-8 bytes - urequests computes Content-Length from len(str),
        # and multibyte characters would undercount the length and truncate the JSON.
        body = ujson.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        response = urequests.post(
            DASHBOARD_URL, data=body, headers=headers, timeout=HTTP_TIMEOUT
        )
        print("[DASHBOARD] status:", response.status_code)
    except Exception as e:
        print(f"[DASHBOARD ERROR] Could not send data: {e}")
    finally:
        # Critical: urequests easily exhausts RAM without .close().
        if response is not None:
            try:
                response.close()
            except Exception:
                pass


def run():
    """Initialize hardware and run the climate control loop forever.

    Hardware init is inside this function (not at module level) so the
    supervisor in main.py can retry on a transient I2C/BME280/relay failure.
    """
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
    prev_fan = None          # Last known fan state, to detect transitions
    prev_emergency = False   # Last emergency state, to detect entry into emergency
    last_state_change = 0    # time.time() of last fan state flip
    last_api_check = 0
    last_heartbeat = 0
    ext_ah = None            # Last known-good outside AH (cached across failures)
    ext_ah_time = 0          # time.time() when ext_ah was last refreshed

    print("\n>>> Climate System v3.0 (IoT Edition) ready <<<")

    while True:
        try:
            current_time = time.time()

            # Refresh external weather data less frequently than the control loop.
            if last_api_check == 0 or (current_time - last_api_check) >= API_INTERVAL:
                fresh = fetch_external_ah()
                last_api_check = current_time
                if fresh is not None:
                    ext_ah = fresh
                    ext_ah_time = current_time

            # Use the cached reading only while it is fresh enough; otherwise
            # pass None so should_ventilate() falls back to GUARD.
            usable_ah = (
                ext_ah
                if ext_ah is not None and (current_time - ext_ah_time) <= EXT_AH_MAX_AGE
                else None
            )

            # Manual override from the dashboard: force the fan ON while it has
            # time left. Polled every cycle so a button press takes effect on
            # the next wake-up (≤ LOOP_INTERVAL).
            override_secs = fetch_override()

            # Read from basement
            temp = bme.temperature()
            press = bme.pressure()
            hum = bme.humidity()

            # Current states
            fan_on = relay.value() == 1
            emergency = hum >= EMERGENCY_RH

            # Decision logic
            vent_decision, mode_reason = should_ventilate(
                temp, hum, usable_ah, fan_on, current_time, last_state_change
            )

            # Override wins over the climate decision.
            if override_secs > 0:
                vent_decision = True
                mode_reason = f"OVERRIDE ({override_secs // 60}m left)"

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
                        "node_id": NODE_ID,
                        "timestamp": current_time,
                        "temperature": round(temp, 2),
                        "humidity": round(hum, 2),
                        "pressure": round(press, 1),
                        "ah_inside": round(calculate_ah(temp, hum), 2),
                        "ah_outside": round(ext_ah, 2)
                            if ext_ah is not None else None,
                        "fan_active": fan_now,
                        "mode": mode_reason,
                        "override_seconds": override_secs
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
