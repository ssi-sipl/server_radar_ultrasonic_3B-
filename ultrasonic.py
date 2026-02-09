import requests
import json
import RPi.GPIO as GPIO
import time
import logging

# Configure logging
logging.basicConfig(
    filename='ultrasonic.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# GPIO Configuration
GPIO.setmode(GPIO.BOARD)

ULTRASONIC_SENSORS = [                      # Hardcoded GPIO pin for trigger and echo for each ultrasonic sensor
    {"id": "US1", "trig": 23, "echo": 24},
    {"id": "US2", "trig": 15, "echo": 13},
]

for sensor in ULTRASONIC_SENSORS:
    GPIO.setup(sensor["trig"], GPIO.OUT)
    GPIO.setup(sensor["echo"], GPIO.IN)

# Server configuration
SERVER_URL = 'http://192.168.1.2:80'  # URL for testing on local server

# Valid range for triggering HTTP requests
VALID_RANGE_MIN = 120
VALID_RANGE_MAX = 780

# Function to send HTTP command
def send_http_command(url, method='POST', params=None, data=None, headers=None):
    try:
        response = requests.request(method, url, params=params, data=data, headers=headers, timeout=0.5)
        response.raise_for_status()  # Raise an exception for 4xx or 5xx status codes
        return response.text
    except requests.exceptions.RequestException as e:
        logging.error(f"Error: {e}")
        return None

def measure_distance_ultrasonic(trig, echo):
    GPIO.output(trig, GPIO.HIGH)
    time.sleep(0.00001)  # 10 microseconds
    GPIO.output(trig, GPIO.LOW)

    pulse_start = time.time()
    timeout_start = pulse_start

    while GPIO.input(echo) == 0:
        pulse_start = time.time()
        if pulse_start - timeout_start > 0.02:  # Timeout for no echo received
            return -1

    while GPIO.input(echo) == 1:
        pulse_end = time.time()
        if pulse_end - pulse_start > 0.02:  # Timeout for long echo
            return -1

    pulse_duration = pulse_end - pulse_start
    distance = pulse_duration * 17150  # Convert to cm

    if distance < 2 or distance > 800:
        return -1

    return distance

def check_and_send_request(distance, sensor_id, sensor_type):
    if VALID_RANGE_MIN <= distance <= VALID_RANGE_MAX:
        data = {
            "cameraId": "RD001",
            "eventTime": int(time.time()),
            "timeStampStr": time.strftime("%Y-%m-%d %H:%M:%S"),
            "eventType": "Sensor_Event",
            "eventTag": "distance",
            "sensorId": sensor_id,
            "sensorType": sensor_type
        }
        
        headers = {'Content-Type': 'application/json'}
        response = send_http_command(SERVER_URL, method='POST', data=json.dumps(data), headers=headers)
        if response:
            logging.info(f"{sensor_id} ({sensor_type}) | HTTP Response: {response}")
        else:
            logging.error(f"{sensor_id} ({sensor_type}) | Failed to send HTTP request.")
    else:
        logging.info(f"{sensor_id} ({sensor_type}) | Distance {distance:.2f} cm is out of the valid range ({VALID_RANGE_MIN} - {VALID_RANGE_MAX} cm).")


def main():
    try: # Start the ultrasonic sensor reading loop in the main thread
        while True:
            for sensor in ULTRASONIC_SENSORS:
                distance = measure_distance_ultrasonic(sensor["trig"], sensor["echo"])
                if distance != -1:
                    logging.info(f"{sensor['id']} Ultrasonic Distance: {distance:.2f} cm")
                    check_and_send_request(distance, sensor["id"], "Ultrasonic")
            time.sleep(3)

    except KeyboardInterrupt:
        logging.info("Program interrupted by user.")
    finally:
        GPIO.cleanup()
        logging.info("GPIO cleaned up. Exiting program.")

if __name__ == "__main__":
    main()