import os
import subprocess
import sys
import time

# CONFIGURATION
USER = "rudra"
WORKING_DIR = "/home/rudra/server_radar_ultrasonic_3B-"
VENV_NAME = "rudrarakshak"
PYTHON_SCRIPT = os.path.join(WORKING_DIR, "radar_ultrasonic.py")
VENV_PATH = os.path.join(WORKING_DIR, VENV_NAME)
SERVICE_NAME = "radar.service"
SERVICE_PATH = f"/etc/systemd/system/{SERVICE_NAME}"
REQUIREMENTS_FILE = os.path.join(WORKING_DIR, "requirements.txt")
LOG_FILE = os.path.join(WORKING_DIR, "radar.log")

# Derived paths
VENV_PYTHON = os.path.join(VENV_PATH, "bin", "python")
VENV_PIP = os.path.join(VENV_PATH, "bin", "pip")

def run_command(command, description):
    try:
        subprocess.run(command, check=True)
        print(f"{description} succeeded.")
    except subprocess.CalledProcessError as e:
        print(f"{description} failed with error: {e}")
        sys.exit(1)

def wait_for_apt_lock(max_wait=300):
    """Wait for the apt lock to be released before continuing."""
    lock_file = "/var/lib/dpkg/lock-frontend"
    waited = 0
    print("Checking for apt/dpkg lock...")

    def lsof_installed():
        return subprocess.run(["which", "lsof"], stdout=subprocess.DEVNULL).returncode == 0

    while os.path.exists(lock_file):
        if lsof_installed():
            try:
                pid_output = subprocess.check_output(["lsof", lock_file]).decode()
                print(f"🔒 Lock held by:\n{pid_output}")
            except subprocess.CalledProcessError:
                break  # Lock file exists but not in use
        else:
            print("🔍 'lsof' not installed. Skipping PID display.")

        if waited >= max_wait:
            print(f"❌ Timeout: Lock still held after {max_wait} seconds.")
            sys.exit(1)

        print(f"⏳ Lock present. Waiting... {waited}s")
        time.sleep(5)
        waited += 5

    print("✅ Lock released. Continuing...")

def ensure_lsof_installed():
    print("Checking for 'lsof' utility...")
    result = subprocess.run(["which", "lsof"], stdout=subprocess.PIPE)
    if result.returncode != 0:
        print("'lsof' not found. Installing...")
        wait_for_apt_lock()
        run_command(["sudo", "apt-get", "install", "lsof", "-y",], "Installing lsof")

def update_and_upgrade_os():
    wait_for_apt_lock()
    run_command(["sudo", "apt-get", "update"], "Update package lists")
    try:
        run_command(["sudo", "apt-get", "upgrade", "-y"], "Upgrade packages")
    except SystemExit:
        print("⚠️ Warning: Package upgrade failed. Continuing setup anyway.")

def install_basics():
    wait_for_apt_lock()
    run_command(["sudo", "apt-get", "install", "-y", "python3", "python3-pip", "python3-venv", "git"],
                "Installing Python3, pip, venv, and Git")

def create_virtualenv():
    if not os.path.exists(VENV_PATH):
        print(f"Creating virtual environment at {VENV_PATH}...")
        run_command(["python3", "-m", "venv", VENV_PATH], "Virtual environment creation")
    else:
        print("✅ Virtual environment already exists.")

def install_requirements():
    if os.path.exists(REQUIREMENTS_FILE):
        print("Installing packages from requirements.txt...")
        run_command([VENV_PIP, "install", "-r", REQUIREMENTS_FILE], "Installing Python packages")
    else:
        print("⚠️ No requirements.txt found. Skipping package installation.Recommended to install the packages manually")

def create_service():
    service_content = f"""[Unit]
Description=Radar-Ultrasonic Server Auto Start
After=network.target

[Service]
User={USER}
WorkingDirectory={WORKING_DIR}
ExecStart={VENV_PYTHON} {PYTHON_SCRIPT}
Restart=always
Environment=PYTHONUNBUFFERED=1
StandardOutput=append:{LOG_FILE}
StandardError=append:{LOG_FILE}

[Install]
WantedBy=multi-user.target
"""
    print("Creating systemd service...")
    with open("temp_service.service", "w") as f:
        f.write(service_content)

    run_command(["sudo", "mv", "temp_service.service", SERVICE_PATH], "Moving service file to system directory")
    run_command(["sudo", "chmod", "644", SERVICE_PATH], "Setting permissions on service file")
    run_command(["sudo", "systemctl", "daemon-reexec"], "Reloading systemd")
    run_command(["sudo", "systemctl", "daemon-reload"], "Reloading services")
    run_command(["sudo", "systemctl", "enable", SERVICE_NAME], "Enabling service to run at boot")
    run_command(["sudo", "systemctl", "restart", SERVICE_NAME], "Starting service now")

def main():
    ensure_lsof_installed()
    update_and_upgrade_os()
    install_basics()
    create_virtualenv()
    install_requirements()
    create_service()

    print("\n✅ Setup complete. Your script will now auto-start on reboot.")
    print(f"📂 Virtual environment: {VENV_PATH}")
    print(f"📜 Service file: {SERVICE_PATH}")
    print(f"🪵 Log file: {LOG_FILE}")

if __name__ == "__main__":
    main()
