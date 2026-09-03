import time

# Supervisor: MicroPython runs this file automatically after boot.py returns.
# It imports the climate app and retries on transient failures (I2C/BME280/
# relay init, NTP, etc.) so the device never idles at the REPL.
#
# KeyboardInterrupt is re-raised (not swallowed) so a Ctrl-C over WebREPL
# still drops to the REPL for interactive debugging. It derives from
# BaseException, so it is NOT caught by `except Exception`.

while True:
    try:
        import climate
        climate.run()
    except KeyboardInterrupt:
        print("[MAIN] KeyboardInterrupt - stopping supervisor")
        raise
    except Exception as e:
        print("[MAIN] climate failed:", repr(e))
        print("[MAIN] retrying in 5s...")
        time.sleep(5)
