import requests
import json
import RPi.GPIO as GPIO
import time
import serial
import threading
import logging
import pigpio

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# GPIO Configuration
GPIO.setmode(GPIO.BOARD)

ULTRASONIC_SENSORS = [                      # Hardcoded GPIO pin for trigger and echo for each ultrasonic sensor
    {"id": "US1", "trig": 23, "echo": 24},
    {"id": "US2", "trig": 15, "echo": 13},
]

for sensor in ULTRASONIC_SENSORS:
    GPIO.setup(sensor["trig"], GPIO.OUT)
    GPIO.setup(sensor["echo"], GPIO.IN)

# Radar sensor serial communication setup
RADAR_PORT = '/dev/ttyS0'  # Hardcoded serial port for radar sensor
RADAR_BAUDRATE = 115200        # Hardcoded baud rate for radar sensor

# Server configuration
SERVER_URL = 'http://192.168.0.5:3300/analyticEvent'  # URL for testing on local server

# Valid range for triggering HTTP requests
VALID_RANGE_MIN = 120
VALID_RANGE_MAX = 780

# Function to send HTTP command
def send_http_command(url, method='POST', params=None, data=None, headers=None):
    try:
        response = requests.request(method, url, params=params, data=data, headers=headers)
        response.raise_for_status()  # Raise an exception for 4xx or 5xx status codes
        return response.text
    except requests.exceptions.RequestException as e:
        print(f"Error: {e}")
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

# Function to read from the radar sensor (UART)
def read_from_port(ser):
    buffer = ""
    try:
        while True:
            if ser.in_waiting > 0:
                data = ser.read(ser.in_waiting)
                # Decode incoming bytes
                decoded = data.decode("utf-8", errors="ignore")
                buffer += decoded                
                # Process complete lines only
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    # Strict numeric check
                    if line.isdigit():
                        distance = int(line)
                        if 10 <= distance <= 780:
                            logging.info(f"RADAR_1 |Radar Sensor Value: {distance} cm")
                            check_and_send_request(distance, "RADAR_1", "Hardware_UART")
                        else:
                            logging.info(f"Distance {distance:.2f} cm is out of the valid range (10 - 780 cm).")

            time.sleep(0.1)  # Sleep briefly to avoid busy waiting
    except KeyboardInterrupt:
        logging.info("Exiting...")
    finally:
        ser.close()  # Ensure the serial port is closed on exit

def read_from_soft_uart():

    RX_GPIO = 17      # BCM GPIO17 → physical pin 11
    BAUD = 9600

    pi = pigpio.pi()
    if not pi.connected:
        logging.error("pigpio daemon not running")
        return

    pi.set_mode(RX_GPIO, pigpio.INPUT)
    pi.bb_serial_read_open(RX_GPIO, BAUD)

    logging.info("Software UART radar started")

    try:
        while True:
            count, data = pi.bb_serial_read(RX_GPIO)
            if count > 0:
                decoded = data.decode("utf-8", errors="ignore")
                numeric = ''.join(filter(str.isdigit, decoded))
                if numeric:
                    distance = float(numeric)
                    logging.info(f"RADAR_2 | Soft UART Radar: {distance} cm")
                    check_and_send_request(distance, "RADAR_2", "Software_UART")
            time.sleep(0.05)

    except KeyboardInterrupt:
        pass
    finally:
        pi.bb_serial_read_close(RX_GPIO)
        pi.stop()

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
    try: # Hardware UART radar
        hw_ser = serial.Serial("/dev/serial0", baudrate=RADAR_BAUDRATE, timeout=1)
        logging.info("Hardware UART radar connected on /dev/serial0")

        hw_thread = threading.Thread(target=read_from_port, args=(hw_ser,), daemon=True)
        hw_thread.start()

        # Software UART radar
        sw_thread = threading.Thread(target=read_from_soft_uart, daemon=True)
        sw_thread.start()

        # Start the ultrasonic sensor reading loop in the main thread
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
        try:
            hw_ser.close()
        except Exception:
            pass
        GPIO.cleanup()
        logging.info("GPIO cleaned up. Exiting program.")

if __name__ == "__main__":
    main()