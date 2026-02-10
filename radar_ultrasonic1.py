import requests
import json
import RPi.GPIO as GPIO
import time
import serial
import threading
import logging
import pigpio

pi = pigpio.pi()
if not pi.connected:
    raise RuntimeError("pigpio daemon not working")

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# GPIO Configuration
GPIO.setmode(GPIO.BOARD)

#Enable / Logic-high pin
ENABLE_PIN = 15
GPIO.setup(ENABLE_PIN, GPIO.OUT)
GPIO.output(ENABLE_PIN, GPIO.HIGH)

RPiGPIO_PINS = [23, 24, 13, 15]
for pin in RPiGPIO_PINS:
    GPIO.setup(pin, GPIO.OUT, initial=GPIO.LOW)

pgio_pins = [17,23]
for pin in pgio_pins:
    pi.set_mode(pin, pigpio.INPUT)

pi.set_mode(5, pigpio.OUTPUT)
pi.set_mode(5,0)

ULTRASONIC_SENSORS = [                      # Hardcoded GPIO pin for trigger and echo for each ultrasonic sensor
    {"id": "US1", "trig": 23, "echo": 24},
    {"id": "US2", "trig": 15, "echo": 13},
]

for sensor in ULTRASONIC_SENSORS:
    GPIO.setup(sensor["trig"], GPIO.OUT)
    GPIO.setup(sensor["echo"], GPIO.IN)

# Radar sensor serial communication setup
RADAR_PORT = '/dev/ttyS0'  # Hardcoded serial port for radar sensor
RADAR_BAUDRATE = 9600        # Hardcoded baud rate for radar sensor

# Server configuration
SERVER_URL = 'http://192.168.0.88:5000/api/alerts/from-nx'  # URL for testing on local server

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
        print(f"Error: {e}")
        return None

def measure_distance_ultrasonic(trig, echo):
    GPIO.output(trig, GPIO.HIGH)
    time.sleep(0.00001)  # 10 microseconds
    GPIO.output(trig, GPIO.LOW)

    start_time = time.time()
    timeout = 0.02
    while GPIO.input(echo) == GPIO.LOW:
        pulse_start = time.time()
        if pulse_start - start_time > timeout:  # Timeout for no echo received
            return -1
    pulse_start = time.time()

    while GPIO.input(echo) == GPIO.HIGH:
        pulse_end = time.time()
        if pulse_end - pulse_start > timeout:  # Timeout for long echo
            return -1

    pulse_duration = pulse_end - pulse_start
    distance = pulse_duration * 17150  # Convert to cm

    if distance < 2 or distance > 800:
        return -1

    return distance

# Function to read from the radar sensor (UART)
def read_from_port(ser):
    try:
        while True:
            try:
                if ser.in_waiting > 0:
                    data = ser.readline(ser.in_waiting)
                    # Decode incoming bytes
                    decoded_data = data.decode("utf-8", errors="ignore").strip()

                    # Process complete lines only
                    numeric_values = "".join(filter(str.isdigit, decoded_data))
                    if numeric_values:
                        distance = float(numeric_values)
                        if VALID_RANGE_MIN <= distance <= VALID_RANGE_MAX:
                            logging.info(f"RADAR_1 |Radar Sensor Value: {distance} cm")
                            check_and_send_request(distance, "RADAR_1", "Hardware_UART")
                        else:
                            logging.info(f"RADAR1 | Distance {distance:.2f} cm is out of the valid range (10 - 780 cm).")
                time.sleep(0.1)  # Sleep briefly to avoid busy waiting
            except serial.SerialException as e:
                logging.error(f"Serial error: {e}")
                time.sleep(1)
    except KeyboardInterrupt:
        logging.info("Exiting...")
    finally:
        ser.close()  # Ensure the serial port is closed on exit

def read_from_soft_uart(rx_gpio, tx_gpio, sensor_id):
    BAUD = 9600
    buffer = ""

    pi.set_mode(rx_gpio, pigpio.INPUT)
    pi.set_mode(tx_gpio, pigpio.OUTPUT)
    pi.write(tx_gpio, 1)

    # Ensure GPIO is not already in use

    try:
        pi.bb_serial_read_open(rx_gpio, BAUD)
        logging.info(f"Software UART radar started for sensor {sensor_id} on GPIO {rx_gpio}"
        f"(RX={rx_gpio}, TX={tx_gpio})")

        while True:
            count, data = pi.bb_serial_read(rx_gpio)
            if count > 0:
                buffer += data.decode("utf-8", errors="ignore")

                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()

                    if line.isdigit():
                        distance = int(line)
                        if VALID_RANGE_MIN <= distance <= VALID_RANGE_MAX:
                            logging.info(f"{sensor_id} | Radar Distance: {distance} cm")
                            check_and_send_request(distance, sensor_id, "Software_UART")
                        else:
                            logging.info(
                                f"{sensor_id} | Distance {distance} cm out of range"
                            )

            time.sleep(0.05)
    except Exception as e:
        logging.error(f"{sensor} | Software UART error: {e}")
    finally:
        pi.bb_serial_read_close(rx_gpio)
        logging.info(f"{sensor_id} | Software UART stopped")


def check_and_send_request(distance_cm, sensor_id, sensor_type):

    if VALID_RANGE_MIN <= distance_cm <= VALID_RANGE_MAX:

        # Convert cm → meters
        distance_m = distance_cm / 100.0

        # Create formatted timestamp string
        time_stamp_str = time.strftime("%Y-%m-%d %H:%M:%S")

        # Convert to microseconds
        timestamp_seconds = int(
            time.mktime(
                time.strptime(time_stamp_str, "%Y-%m-%d %H:%M:%S")
            )
        )
        timestamp_us = timestamp_seconds * 1_000_000

        data_string = (
            f"Type:nx.base.Sensor;"
            f"distance:{distance_m};"
            f"TimestampUs:{timestamp_us};"
        )

        payload = {
            "sensorId": "ipcam1",
            "data": data_string
        }

        headers = {'Content-Type': 'application/json'}

        try:
            requests.post(SERVER_URL, json=payload, headers=headers)

            logging.info(
                f"{sensor_id} ({sensor_type}) → Sent NX event | {time_stamp_str}"
            )

        except Exception as e:
            logging.error(f"HTTP Error: {e}")

    else:
        logging.info(
            f"{sensor_id} ({sensor_type}) | Distance {distance_cm:.2f} cm out of range"
        )

def main():
    try: # Hardware UART radar
        hw_ser = serial.Serial("/dev/serial0", baudrate=RADAR_BAUDRATE, timeout=1)
        logging.info("Hardware UART radar connected on /dev/serial0")

        hw_thread = threading.Thread(target=read_from_port, args=(hw_ser,), daemon=True)
        hw_thread.start()


        # Software UART radar (RADAR_2); GPIO17 → Physical pin 11
        sw_thread_1 = threading.Thread(target=read_from_soft_uart,args=(17, 5, "RADAR_2"),daemon=True)
        sw_thread_1.start()

        # Software UART radar (RADAR_3); GPIO27 → Physical pin 13
        #sw_thread_2 = threading.Thread(dtarget=read_from_soft_uart,args=(17, "RADAR_3"),daemon=True)
        #sw_thread_2.start()

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
        pi.stop()
        logging.info("GPIO cleaned up. Exiting program.")

if __name__ == "__main__":
    main()
