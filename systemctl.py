import json
import sys
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

CONFIG_FILE = os.path.join(BASE_DIR, "sensors.json")

with open(CONFIG_FILE, "r") as f:
    config = json.load(f)

sensors = config["sensors"]

if len(sys.argv) < 2:
    print("Usage:")
    print("sensorctl status")
    print("sensorctl on SENSOR_ID")
    print("sensorctl off SENSOR_ID")
    print("sensorctl range SENSOR_ID MIN MAX")
    sys.exit(1)

command = sys.argv[1]

if command == "status":
     print(f"Sensor Box : {config['sensorBoxId']}")
     print("-" * 40)

     for sensor_id, sensor in sensors.items():

        status = "ON" if sensor["enabled"] else "OFF"

        print(
            f"{sensor_id:8}"
            f" Status={status}"
            f" Range={sensor['min_range']}-{sensor['max_range']} cm"
        )

elif command in ["on", "off"]:

    if len(sys.argv) != 3:
        print("Usage: sensorctl on SENSOR_ID")
        sys.exit(1)

    sensor_id = sys.argv[2]

    if sensor_id not in sensors:
        print("Sensor not found")
        sys.exit(1)

    sensors[sensor_id]["enabled"] = command == "on"

    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=4)

    print(f"{sensor_id} -> {command.upper()}")

elif command == "range":

    if len(sys.argv) != 5:
        print("Usage: sensorctl range SENSOR_ID MIN MAX")
        sys.exit(1)

    sensor_id = sys.argv[2]

    if sensor_id not in sensors:
        print("Sensor not found")
        sys.exit(1)

    sensors[sensor_id]["min_range"] = int(sys.argv[3])
    sensors[sensor_id]["max_range"] = int(sys.argv[4])

    with open(CONFIG_FILE, "w") as f:
          json.dump(config, f, indent=4)

    print(
        f"{sensor_id} Range Updated "
        f"({sys.argv[3]}-{sys.argv[4]} cm)"
    )

else:

    print("Invalid Command")
