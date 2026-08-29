from machine import Pin, I2C
import bme280
import time
import math
import urequests
import ntptime
import config

# --- KONFIGURACJA POGODY ---
API_KEY = config.API_KEY
CITY = config.CITY
WEATHER_URL = f"http://api.openweathermap.org/data/2.5/weather?q={CITY}&appid={API_KEY}&units=metric"

# --- KONFIGURACJA KLIMATU ---
THRESHOLD_ON = 60.0      # Zwykły próg włączenia
THRESHOLD_OFF = 50.0     # Próg wyłączenia (histereza)
EMERGENCY_RH = 75.0      # Twardy próg zalania/awarii

# --- KONFIGURACJA DASHBOARDU (API BACKEND) ---
ENABLE_DASHBOARD = False # Zmień na True, gdy postawisz serwer FastAPI/Django
DASHBOARD_URL = config.DASHBOARD_URL

# --- INICJALIZACJA SPRZĘTU ---
i2c = I2C(0, scl=Pin(22), sda=Pin(21))
bme = bme280.BME280(i2c=i2c)
relay = Pin(19, Pin.OUT)
relay.value(0)

# Synchronizacja czasu
try:
    print("Synchronizacja czasu z internetem...")
    ntptime.settime()
except Exception as e:
    print(f"Błąd synchronizacji czasu NTP: {e}")

# Zmienne sterujące
last_api_check = 0
api_interval = 900  # 900 sekund = 15 minut
ext_ah = None       

def calculate_ah(temp, rh):
    """Oblicza wilgotność bezwzględną (g/m3) z równania Magnusa"""
    es = 6.112 * math.exp((17.67 * temp) / (243.5 + temp))
    e = es * (rh / 100.0)
    # Poprawiony mnożnik: 216.74 zamiast 2.1674 * 1000 dla uniknięcia błędów skali
    return (e * 216.74) / (273.15 + temp)

def fetch_external_ah():
    """Pobiera pogodę z API. Zwraca wartość lub None przy błędzie."""
    try:
        response = urequests.get(WEATHER_URL)
        data = response.json()
        response.close()
        
        if 'main' not in data:
            print(f"\n[API ODRZUCONE] Brak klucza 'main' (Prawdopodobnie 401).")
            return None
            
        ext_temp = data['main']['temp']
        ext_rh = data['main']['humidity']
        ah = calculate_ah(ext_temp, ext_rh)
        print(f"\n[API ZAKTUALIZOWANE] Zewnątrz: {ext_temp:.1f}°C, {ext_rh}% RH -> {ah:.2f} g/m3")
        return ah
    except Exception as e:
        print(f"\n[API BŁĄD] Wyjątek połączenia: {e}")
        return None

def should_ventilate(int_temp, int_rh, ext_ah_value):
    """Zwraca krotkę: (Czy_włączyć_wiatrak, "Powód / Tryb")"""
    if int_rh >= EMERGENCY_RH:
        return True, "AWARYJNY (Zalanie)"
        
    if int_rh <= THRESHOLD_ON and relay.value() == 0:
        return False, "CZUWANIE (W normie)"
        
    int_ah = calculate_ah(int_temp, int_rh)
    
    if ext_ah_value is not None:
        if ext_ah_value < int_ah:
            return True, "API (Z zewnątrz suche)"
        else:
            return False, "API (Z zewnątrz mokre)"
            
    # GUARD: Awaria API, przechodzimy na kalendarz
    current_month = time.localtime()[1]
    
    if time.localtime()[0] <= 2000:
        return False, "GUARD (Brak czasu NTP)"

    if current_month in [11, 12, 1, 2, 3, 4]:
        return True, "GUARD (Zima)"
    else:
        return False, "GUARD (Lato)"

def send_to_dashboard(payload):
    """Wysyła payload do Twojego serwera i zamyka gniazdo"""
    try:
        response = urequests.post(DASHBOARD_URL, json=payload)
        # Opcjonalnie: print(f"Dashboard status: {response.status_code}")
        response.close() # Krytyczne: urequests łatwo zapycha pamięć RAM bez .close()
    except Exception as e:
        print(f"[DASHBOARD BŁĄD] Nie można wysłać danych: {e}")

print("\n>>> System Klimatyczny v3.0 (IoT Edition) gotowy <<<")

while True:
    try:
        current_time = time.time()
        
        # Odśwież dane z zewnątrz co 15 minut
        if last_api_check == 0 or (current_time - last_api_check) > api_interval:
            ext_ah = fetch_external_ah()
            last_api_check = current_time

        # Odczyt z piwnicy
        temp = bme.temperature()
        press = bme.pressure()
        hum = bme.humidity()
        
        # Logika decyzyjna
        vent_decision, mode_reason = should_ventilate(temp, hum, ext_ah)
        
        # Obsługa wiatraka z histerezą
        if vent_decision and hum > THRESHOLD_ON and relay.value() == 0:
            relay.value(1)
        elif (not vent_decision or hum < THRESHOLD_OFF) and relay.value() == 1:
            relay.value(0)
            
        stan_wentylatora = "ON" if relay.value() == 1 else "OFF"
        print(f"[PIWNICA] T: {temp:.2f}°C | RH: {hum:.2f}% | Ciś: {press:.1f}hPa | Wiatrak: {stan_wentylatora} | Tryb: {mode_reason}")
        
        # --- SEKCJA DASHBOARDU ---
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
            
        # Pętla wykonuje się co 5 sekund (jeśli wysyłasz na backend, rozważ wydłużenie np. do 15 lub 30s)
        time.sleep(5) 
        
    except Exception as e:
        print(f"Błąd głównej pętli: {e}")
        time.sleep(5)