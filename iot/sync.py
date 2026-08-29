import os
import subprocess
import sys

def load_env(path):
    """Minimal .env parser with no external dependencies."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())

# .env lives at repo root, one level above this script's directory
load_env(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"))

# --- Node config ---
IP = os.getenv("ESP_IP", "")
PASS = os.getenv("WEBREPL_PASS", "")
# webrepl_cli.py lives in the same directory as this script (iot/)
CLI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "webrepl_cli.py")

def push_to_esp(folder):
    """Upload all .py/.json files from the given folder to the device."""
    if not os.path.exists(folder):
        print(f"Folder '{folder}' does not exist!")
        return

    files = [f for f in os.listdir(folder) if f.endswith(('.py', '.json'))]
    if not files:
        print(f"No files to upload in {folder}")
        return

    for f in files:
        local_path = os.path.join(folder, f)
        print(f"\n[UPLOAD] Uploading {f} to ESP32...")
        # python webrepl_cli.py -p <pass> ./basement/main.py <ip>:/main.py
        subprocess.run([sys.executable, CLI, "-p", PASS, local_path, f"{IP}:/{f}"])

def pull_from_esp(folder):
    """Download the defined file set from the device to disk."""
    files = ["boot.py", "main.py", "bme280.py", "webrepl_cfg.py"]
    os.makedirs(folder, exist_ok=True)

    for f in files:
        print(f"\n[DOWNLOAD] Downloading {f} from ESP32...")
        # python webrepl_cli.py -p <pass> <ip>:/main.py ./basement
        subprocess.run([sys.executable, CLI, "-p", PASS, f"{IP}:/{f}", folder])

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage:")
        print("  Upload:   python sync.py push basement")
        print("  Download: python sync.py pull basement")
        sys.exit(1)

    action = sys.argv[1]
    folder = sys.argv[2]

    if action == "push":
        push_to_esp(folder)
    elif action == "pull":
        pull_from_esp(folder)
    else:
        print("Unknown command. Use 'push' or 'pull'.")
