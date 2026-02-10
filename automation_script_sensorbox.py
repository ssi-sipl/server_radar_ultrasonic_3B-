import os
import subprocess
import sys

# CONFIGURATION
USER = "rudra"
WORKING_DIR = "/home/rudra/server_radar_ultrasonic_3B-"
VENV_NAME = "rudrarakshak"
PYTHON_SCRIPT = os.path.join(WORKING_DIR, "radar_ultrasonic.py")
VENV_PATH = os.path.join(WORKING_DIR, VENV_NAME)
SERVICE_NAME = "radar.service"
SERVICE_PATH = f"/etc/systemd/system/{SERVICE_NAME}"
LOG_FILE = os.path.join(WORKING_DIR, "radar.log")

VENV_PYTHON = os.path.join(VENV_PATH, "bin", "python")


def run_command(command, description):
    try:
        subprocess.run(command, check=True)
        print(f"{description} succeeded.")
    except subprocess.CalledProcessError as e:
        print(f"{description} failed with error: {e}")
        sys.exit(1)


def create_service():
    service_content = f"""[Unit]
Description=Radar-Ultrasonic Server Auto Start
After=network.target

[Service]
User={USER}
WorkingDirectory={WORKING_DIR}
ExecStart={VENV_PYTHON} {PYTHON_SCRIPT} >> {LOG_FILE} 2>&1
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
"""
    print("Creating systemd service...")
    with open("temp_service.service", "w") as f:
        f.write(service_content)

    run_command(["sudo", "mv", "temp_service.service", SERVICE_PATH], "Moving service file to system directory")
    run_command(["sudo", "chmod", "644", SERVICE_PATH], "Setting permissions on service file")
    run_command(["sudo", "systemctl", "daemon-reload"], "Reloading systemd")
    run_command(["sudo", "systemctl", "enable", SERVICE_NAME], "Enabling service to run at boot")
    run_command(["sudo", "systemctl", "restart", SERVICE_NAME], "Starting service now")


def main():
    create_service()

    print("\n✅ Setup complete. Your script will now auto-start on reboot.")
    print(f"📂 Virtual environment: {VENV_PATH}")
    print(f"📜 Service file: {SERVICE_PATH}")
    print(f"🪵 Log file: {LOG_FILE}")


if __name__ == "__main__":
    main()
