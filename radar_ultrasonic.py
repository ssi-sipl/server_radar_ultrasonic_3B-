import requests
import json
import RPi.GPIO as GPIO
import time
import serial
import threading
import logging
import os

# Logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

#lock for send_alert 
alert_lock = threading.Lock()

# GPIO Mode
# Using BCM numbering because power pins use GPIO numbers.
# If you prefer BOARD numbering, change the pin numbers
# accordingly.

GPIO.setmode(GPIO.BCM)

# AI Box URL
#change the ip according to the AI Box ip address

SERVER_URL = "http://192.168.1.100:5000/api/alerts/from-nx"

# Configuration File

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

CONFIG_FILE = os.path.join(BASE_DIR, "sensors.json")

# Sensor Definitions

SENSORS = {

    "RD001": {
        "type": "radar",
        "uart": "/dev/ttyS0",
        "baudrate": 115200,
        "power_pin": 17
    },

    "US001": {
        "type": "ultrasonic",
        "trig": 23,
        "echo": 24,
        "power_pin": 27
    }

}

# GPIO Setup

for sensor in SENSORS.values():

    GPIO.setup(sensor["power_pin"], GPIO.OUT)

    GPIO.output(sensor["power_pin"], GPIO.HIGH)

    if sensor["type"] == "ultrasonic":

        GPIO.setup(sensor["trig"], GPIO.OUT)
        GPIO.setup(sensor["echo"], GPIO.IN)

# Power Control

def sensor_on(power_pin):

    GPIO.output(power_pin, GPIO.LOW)


def sensor_off(power_pin):

    GPIO.output(power_pin, GPIO.HIGH)


# Configuration Loader

def load_config():

    if not os.path.exists(CONFIG_FILE):

        default = {

            "sensorBoxId":"sensor1",

            "RD001": {
                "enabled": True,
                "min_range": 120,
                "max_range": 400
            },

            "US001": {
                "enabled": True,
                "min_range": 120,
                "max_range": 400
            }

        }

        with open(CONFIG_FILE, "w") as f:
             json.dump(default, f, indent=4)

        return default

    with open(CONFIG_FILE, "r") as f:

        return json.load(f)
    
# HTTP Communication

def send_http_command(payload):

    try:

        response = requests.post(
            SERVER_URL,
            json=payload,
            timeout=5
        )

        response.raise_for_status()

        logging.info(
            f"Alert sent successfully ({payload['sensorId']})"
        )

    except Exception as e:

        logging.error(f"HTTP Error : {e}")

# Alert Generator

def send_alert(sensor_id):

    with alert_lock:
        timestamp_us = int(time.time() * 1000000)
        config = load_config()
        sensor_box_id = config["sensorBoxId"]
        
        payload = {

        "sensorId": "sensorBoxId",

        "data":"Type:nx.base.Detection;Confidence:0.72;TimestampUs:{timestamp_us};"
        }
        send_http_command(payload)
        time.sleep(3)


# Ultrasonic Distance Function

def measure_distance(trig, echo):

    GPIO.output(trig, GPIO.HIGH)
    time.sleep(0.00001)
    GPIO.output(trig, GPIO.LOW)

    pulse_start = time.time()
    timeout = pulse_start

    while GPIO.input(echo) == 0:

        pulse_start = time.time()

        if pulse_start - timeout > 0.02:

            return -1

    while GPIO.input(echo) == 1:

        pulse_end = time.time()

        if pulse_end - pulse_start > 0.02:
            
            return -1

    duration = pulse_end - pulse_start

    distance = duration * 17150

    if distance < 2 or distance > 800:

        return -1

    return distance

# Radar Worker

def radar_worker(sensor_id):

    logging.info(f"radar working is started {"sensor_id"}")
    sensor = SENSORS[sensor_id]

    while True:

        config = load_config()

        sensor_config = config["sensors"].get(sensor_id, {})

        if not sensor_config.get("enabled", False):

            sensor_off(sensor["power_pin"])

            time.sleep(2)

            continue

        sensor_on(sensor["power_pin"])

        try:

            with serial.Serial(
                sensor["uart"],
                baudrate=sensor["baudrate"],
                timeout=1
            ) as ser:

                logging.info(
                    f"{sensor_id} connected on "
                    f"{sensor['uart']}"
                )

                while True:
                     config = load_config()

                     sensor_config = config["sensors"].get(
                        sensor_id,
                        {}
                     )

                     if not sensor_config.get("enabled",False):
                        break

                     min_range = sensor_config.get(
                        "min_range",
                        120
                     )

                     max_range = sensor_config.get(
                        "max_range",
                        400
                     )

                     logging.info(f"Bytes waiting: {ser.in_waiting}")
                     if ser.in_waiting:
                        data = ser.read(ser.in_waiting)

                        try:
                            text = data.decode("utf-8", errors="ignore")

                            for line in text.splitlines():

                                line = line.strip()

                                if not line:
                                    continue

                                if line.startswith("Range"):

                                     parts = line.split()

                                     if len(parts) >= 2:

                                         distance = float(parts[1])

                                         logging.info(f"{sensor_id} Distance={distance}")

                                         if min_range <= distance <= max_range:
                                             
                                             logging.info(
                                                 f"{sensor_id} DETECTED {distance}"
                                             )

                                             send_alert(...)
                                             time.sleep(3)
                        except Exception as e:
                            logging.error(e)
        except Exception as e:

            logging.error(
                f"{sensor_id} Error : {e}"
            )

            time.sleep(0.5)

# Ultrasonic Worker

def ultrasonic_worker(sensor_id):

    logging.info(f"ultrasonic worker started with {"sensor_id"}")
    sensor = SENSORS[sensor_id]

    while True:

        config = load_config()

        sensor_config = config["sensors"].get(
            sensor_id,
            {}
        )

        if not sensor_config.get(
            "enabled",
            False
        ):

            sensor_off(
                sensor["power_pin"]
            )
            
            time.sleep(2)

            continue

        sensor_on(
            sensor["power_pin"]
        )

        min_range = sensor_config.get(
            "min_range",
            120
        )

        max_range = sensor_config.get(
            "max_range",
            400
        )

        distance = measure_distance(
            sensor["trig"],
            sensor["echo"]
        )

        if distance != -1:
            logging.info(
                f"{sensor_id}"
                f" Distance="
                f"{distance:.2f}"
            )

            if (min_range<= distance<= max_range):

                logging.info(
                    f"{sensor_id}"f" DETECTED"f"{distance}"
                )

                send_alert(sensor_id)
                time.sleep(3)

# Thread Manager

def start_sensor_threads():

    threads = []

    for sensor_id, sensor in SENSORS.items():

        if sensor["type"] == "ultrasonic":

            thread = threading.Thread(
                target=ultrasonic_worker,
                args=(sensor_id,)
            )

        elif sensor["type"] == "radar":

            thread = threading.Thread(
                target=radar_worker,
                args=(sensor_id,)
            )

        else:

            continue
        thread.daemon = True

        thread.start()

        threads.append(thread)

        logging.info(
            f"Started thread "
            f"{sensor_id}"
        )

    return threads
                
# Main

def main():

    logging.info(
        "Sensor Manager Started"
    )

    start_sensor_threads()

    while True:

        time.sleep(10)

# Entry Point

if __name__ == "__main__":

    try:

        main()

    except KeyboardInterrupt:
        
        logging.info(
            "Stopping Sensor Manager"
        )

    finally:

        for sensor in SENSORS.values():

            sensor_off(
                sensor["power_pin"]
            )

        GPIO.cleanup()

        logging.info(
            "GPIO Cleaned"
        )
