import os
import subprocess
import sys

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# --- Konfiguracja węzła (Node) ---
IP = os.getenv("ESP_IP", "")
PASS = os.getenv("WEBREPL_PASS", "")
CLI = "webrepl_cli.py"

def push_to_esp(folder):
    """Wysyła wszystkie pliki .py z wybranego folderu na urządzenie."""
    if not os.path.exists(folder):
        print(f"Folder '{folder}' nie istnieje!")
        return

    files = [f for f in os.listdir(folder) if f.endswith(('.py', '.json'))]
    if not files:
        print(f"Brak plików do wysłania w {folder}")
        return
        
    for f in files:
        local_path = os.path.join(folder, f)
        print(f"\n🚀 [UPLOAD] Wrzucam {f} na ESP32...")
        # Konstrukcja: python webrepl_cli.py -p <pass> ./basement/main.py <ip>:/main.py
        subprocess.run([sys.executable, CLI, "-p", PASS, local_path, f"{IP}:/{f}"])

def pull_from_esp(folder):
    """Pobiera zdefiniowaną strukturę z ESP32 do folderu na dysku."""
    # Lista plików do zgrania (dodaj tu swoje, np. logi)
    files = ["boot.py", "main.py", "bme280.py", "webrepl_cfg.py"]
    os.makedirs(folder, exist_ok=True)
    
    for f in files:
        print(f"\n📥 [DOWNLOAD] Pobieram {f} z ESP32...")
        # Konstrukcja: python webrepl_cli.py -p <pass> <ip>:/main.py ./basement
        subprocess.run([sys.executable, CLI, "-p", PASS, f"{IP}:/{f}", folder])

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Użycie narzędzia:")
        print("  Wysyłanie: python sync.py push basement")
        print("  Pobieranie: python sync.py pull basement")
        sys.exit(1)

    action = sys.argv[1]
    folder = sys.argv[2]

    if action == "push":
        push_to_esp(folder)
    elif action == "pull":
        pull_from_esp(folder)
    else:
        print("Nieznana komenda. Użyj 'push' lub 'pull'.")