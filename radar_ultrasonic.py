import json
import logging
import os
import queue
import re
import statistics
import threading
import time
from collections import deque

import RPi.GPIO as GPIO
import requests
import serial

try:
    import lgpio
except ImportError:
    lgpio = None

# AI Box URL - change the IP according to the AI Box IP address
SERVER_URL = "http://192.168.1.100:5000/api/alerts/from-nx"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "sensors.json")
LOG_FILE = os.path.join(BASE_DIR, "sensor_manager.log")

COOLDOWN_SEC = 3      
CONFIRM_READINGS = 2      
RADAR_HOLD_SEC = 1.0    
POWER_ACTIVE_LOW = True  
DISPLAY_HZ = 10           

SENSORS = {
    "RD001": {
        "type": "radar",
        "uart": "/dev/serial0",
        "baudrate": 115200,
        "power_pin": 17,
    },
    "US001": {
        "type": "ultrasonic",
        "trig": 23,
        "echo": 24,
        "power_pin": 27,
    },
}

DEFAULT_CONFIG = {
    "sensorBoxId": "sensor1",
    "sensors": {
        "RD001": {
            "enabled": True, "min_range": 120, "max_range": 400,
            "offset_cm": 0.0, "scale": 1.0,
        },
        "US001": {
            "enabled": True, "min_range": 120, "max_range": 400,
            "offset_cm": 0.0, "scale": 1.0, "temperature_c": 25.0,
        },
    },
}

LG = None  


class ConsoleHandler(logging.StreamHandler):
    def emit(self, record):
        try:
            self.stream.write("\r\033[K")
        except Exception:
            pass
        super().emit(record)


def setup_logging():
    fmt = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    ch = ConsoleHandler()
    ch.setFormatter(fmt)
    root.addHandler(ch)

    fh = logging.FileHandler(LOG_FILE)
    fh.setFormatter(fmt)
    root.addHandler(fh)


class ConfigStore:
    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        self.mtime = None
        self.data = json.loads(json.dumps(DEFAULT_CONFIG))

    @staticmethod
    def _normalize(raw):
        """Accepts both {"sensors": {...}} and the old flat layout."""
        cfg = {"sensorBoxId": raw.get("sensorBoxId", "sensor1"), "sensors": {}}
        src = raw.get("sensors")
        if not isinstance(src, dict):
            src = {k: v for k, v in raw.items() if isinstance(v, dict)}
        for sid, default in DEFAULT_CONFIG["sensors"].items():
            merged = dict(default)
            merged.update(src.get(sid, {}))
            cfg["sensors"][sid] = merged
        return cfg

    def get(self):
        with self.lock:
            try:
                if not os.path.exists(self.path):
                    with open(self.path, "w") as f:
                        json.dump(DEFAULT_CONFIG, f, indent=4)
                    logging.warning(f"{self.path} not found - created default")
                mtime = os.path.getmtime(self.path)
                if mtime != self.mtime:
                    with open(self.path) as f:
                        raw = json.load(f)
                    self.data = self._normalize(raw)
                    self.mtime = mtime
                    logging.info("Configuration loaded")
            except Exception as e:
                logging.error(f"Config error, keeping previous settings: {e}")
            return self.data


def sensor_on(pin):
    GPIO.output(pin, GPIO.LOW if POWER_ACTIVE_LOW else GPIO.HIGH)


def sensor_off(pin):
    GPIO.output(pin, GPIO.HIGH if POWER_ACTIVE_LOW else GPIO.LOW)


def open_lgpio():
    if lgpio is None:
        logging.warning(
            "lgpio not installed - ultrasonic uses software timing "
            "(readings will jitter). Run: pip install lgpio"
        )
        return None
    try:
        handle = lgpio.gpiochip_open(0)
    except Exception as e:
        logging.warning(f"lgpio could not open gpiochip0 ({e}) - using software timing")
        return None
    logging.info("lgpio ready - ultrasonic uses kernel edge timestamps")
    return handle


def setup_gpio():
    GPIO.setwarnings(False)
    GPIO.setmode(GPIO.BCM)
    for s in SENSORS.values():
        GPIO.setup(s["power_pin"], GPIO.OUT)
        sensor_off(s["power_pin"])
        # trig/echo are handled by lgpio when available, else by RPi.GPIO
        if s["type"] == "ultrasonic" and LG is None:
            GPIO.setup(s["trig"], GPIO.OUT, initial=GPIO.LOW)
            GPIO.setup(s["echo"], GPIO.IN)

# HTTP communication (background thread, never blocks the sensors)

class HttpSender:
    def __init__(self, url):
        self.url = url
        self.session = requests.Session()
        self.q = queue.Queue(maxsize=100)
        self.running = True
        self.thread = threading.Thread(target=self._worker, daemon=True, name="http")

    def start(self):
        self.thread.start()

    def stop(self):
        self.running = False

    def enqueue(self, payload):
        try:
            self.q.put_nowait(payload)
        except queue.Full:
            logging.error("Alert queue full - dropping alert")

    def _worker(self):
        while self.running:
            try:
                payload = self.q.get(timeout=0.5)
            except queue.Empty:
                continue
            self.send_http_command(payload)

    def send_http_command(self, payload):
        try:
            response = self.session.post(self.url, json=payload, timeout=5)
            response.raise_for_status()
            logging.info(f"Alert sent successfully ({payload['sensorId']})")
        except Exception as e:
            logging.error(f"HTTP Error : {e}")

# Alerts: debounce + cooldown, no blocking sleeps

class AlertManager:
    def __init__(self, cfgstore, sender):
        self.cfgstore = cfgstore
        self.sender = sender
        self.lock = threading.Lock()
        self.hits = {}
        self.last_alert = {}

    def update(self, sensor_id, in_range):
        with self.lock:
            if not in_range:
                self.hits[sensor_id] = 0
                return
            self.hits[sensor_id] = self.hits.get(sensor_id, 0) + 1
            if self.hits[sensor_id] < CONFIRM_READINGS:
                return
            now = time.monotonic()
            if now - self.last_alert.get(sensor_id, -1e9) < COOLDOWN_SEC:
                return
            self.last_alert[sensor_id] = now
        self.send_alert(sensor_id)

    def send_alert(self, sensor_id):
        timestamp_us = int(time.time() * 1_000_000)
        sensor_box_id = self.cfgstore.get()["sensorBoxId"]
        payload = {
            "sensorId": sensor_box_id,
            "data": (
                f"Type:nx.base.Detection;Confidence:0.72;"
                f"TimestampUs:{timestamp_us};"
            ),
        }
        logging.info(f"{sensor_id} DETECTED -> sending alert")
        self.sender.enqueue(payload)


# Shared state, filter, calibration

class SensorState:
    def __init__(self, sensor_id, kind):
        self.sensor_id = sensor_id
        self.kind = kind
        self.distance = None   
        self.raw = None          
        self.in_range = False
        self.status = "starting"
        self.stamps = deque(maxlen=20)

    def rate(self):
        if len(self.stamps) < 2:
            return 0.0
        dt = self.stamps[-1] - self.stamps[0]
        return (len(self.stamps) - 1) / dt if dt > 0 else 0.0


class RangeFilter:

    def __init__(self, size=5, steady_cm=2.0):
        self.window = deque(maxlen=size)
        self.steady_cm = steady_cm

    def clear(self):
        self.window.clear()

    def update(self, value):
        self.window.append(value)
        w = list(self.window)
        last3 = w[-3:]
        if len(last3) == 3 and max(last3) - min(last3) <= self.steady_cm:
            return sum(last3) / 3.0
        return statistics.median(w)


def calibrate(raw, sensor_cfg):
    return raw * sensor_cfg.get("scale", 1.0) + sensor_cfg.get("offset_cm", 0.0)


def publish(sensor_id, state, value, sensor_cfg, alerts, raw=None):
    state.distance = value
    if raw is not None:
        state.raw = raw
        state.stamps.append(time.monotonic())
    in_range = (
        value is not None
        and sensor_cfg["min_range"] <= value <= sensor_cfg["max_range"]
    )
    state.in_range = in_range
    alerts.update(sensor_id, in_range)


def guarded(fn, sensor_id, *args):
    """A crashing worker must be loud, never silent."""
    try:
        fn(sensor_id, *args)
    except Exception:
        logging.exception(f"Worker {sensor_id} crashed")

# Ultrasonic

def echo_to_cm(width_us, temp_c):
    """Echo pulse width (us) -> distance (cm), corrected for temperature."""
    speed_m_s = 331.3 + 0.606 * temp_c
    return width_us * 1e-6 * speed_m_s * 100.0 / 2.0


class LgpioRanger:
    """Echo timed with kernel edge timestamps (nanoseconds) via lgpio."""

    def __init__(self, handle, trig, echo):
        self.h, self.trig, self.echo = handle, trig, echo
        self.rise = None
        self.width_us = None
        self.evt = threading.Event()
        lgpio.gpio_claim_output(handle, trig, 0)
        lgpio.gpio_claim_alert(handle, echo, lgpio.BOTH_EDGES)
        self.cb = lgpio.callback(handle, echo, lgpio.BOTH_EDGES, self._edge)

    def _edge(self, chip, gpio, level, timestamp):
        if level == 1:
            self.rise = timestamp
        elif level == 0 and self.rise is not None:
            self.width_us = (timestamp - self.rise) / 1000.0
            self.rise = None
            self.evt.set()

    def measure_us(self, timeout=0.03):
        self.evt.clear()
        self.width_us = None
        self.rise = None
        lgpio.gpio_write(self.h, self.trig, 1)
        time.sleep(0.00002)               # >= 10 us trigger pulse
        lgpio.gpio_write(self.h, self.trig, 0)
        if not self.evt.wait(timeout):
            return None
        return self.width_us

    def close(self):
        try:
            self.cb.cancel()
            lgpio.gpio_free(self.h, self.trig)
            lgpio.gpio_free(self.h, self.echo)
        except Exception:
            pass


class GpioRanger:
    """Software-timed fallback (less accurate: Python polling jitter)."""

    def __init__(self, trig, echo):
        self.trig, self.echo = trig, echo

    def measure_us(self, timeout=0.03):
        GPIO.output(self.trig, GPIO.HIGH)
        time.sleep(0.00001)
        GPIO.output(self.trig, GPIO.LOW)

        deadline = time.perf_counter() + timeout
        start = time.perf_counter()
        while GPIO.input(self.echo) == 0:
            start = time.perf_counter()
            if start > deadline:
                return None

        end = start
        while GPIO.input(self.echo) == 1:
            end = time.perf_counter()
            if end - start > timeout:
                return None
        return (end - start) * 1e6

    def close(self):
        pass


def ultrasonic_worker(sensor_id, cfgstore, alerts, state, stop):
    sensor = SENSORS[sensor_id]
    logging.info(f"Ultrasonic worker started ({sensor_id})")
    if LG is not None:
        ranger = LgpioRanger(LG, sensor["trig"], sensor["echo"])
    else:
        ranger = GpioRanger(sensor["trig"], sensor["echo"])

    flt = RangeFilter(size=5)
    misses = 0
    powered = False

    try:
        while not stop.is_set():
            sc = cfgstore.get()["sensors"][sensor_id]

            if not sc.get("enabled", False):
                if powered:
                    sensor_off(sensor["power_pin"])
                    powered = False
                    flt.clear()
                    publish(sensor_id, state, None, sc, alerts)
                state.status = "disabled"
                stop.wait(1)
                continue

            if not powered:
                sensor_on(sensor["power_pin"])
                powered = True
                state.status = "warming up"
                stop.wait(1.0)
                state.status = "ok"
                continue

            width = ranger.measure_us()
            raw = None
            if width is not None:
                cm = echo_to_cm(width, sc.get("temperature_c", 25.0))
                if 2.0 <= cm <= 450.0:
                    raw = cm

            if raw is None:
                misses += 1
                if misses >= 3:
                    flt.clear()
                    publish(sensor_id, state, None, sc, alerts)
            else:
                misses = 0
                value = flt.update(calibrate(raw, sc))
                publish(sensor_id, state, value, sc, alerts, raw=raw)

            stop.wait(0.06)   # HC-SR04 needs >= 60 ms between pings
    finally:
        ranger.close()
        sensor_off(sensor["power_pin"])


# --------------------------------------------------------------------------
# Radar (text lines such as "Range 123")
# --------------------------------------------------------------------------
RANGE_RE = re.compile(r"Range\D*(-?\d+(?:\.\d+)?)")


def radar_worker(sensor_id, cfgstore, alerts, state, stop):
    sensor = SENSORS[sensor_id]
    logging.info(f"Radar worker started ({sensor_id})")
    flt = RangeFilter(size=3)
    powered = False
    ser = None
    last_rx = time.monotonic()
    last_range = 0.0
    last_warn = 0.0

    while not stop.is_set():
        sc = cfgstore.get()["sensors"][sensor_id]

        if not sc.get("enabled", False):
            if ser:
                ser.close()
                ser = None
            if powered:
                sensor_off(sensor["power_pin"])
                powered = False
                flt.clear()
                publish(sensor_id, state, None, sc, alerts)
            state.status = "disabled"
            stop.wait(1)
            continue

        if not powered:
            sensor_on(sensor["power_pin"])
            powered = True
            state.status = "warming up"
            stop.wait(2.0)

        if ser is None:
            try:
                ser = serial.Serial(sensor["uart"], sensor["baudrate"], timeout=0.5)
                ser.reset_input_buffer()
                last_rx = time.monotonic()
                state.status = "ok"
                logging.info(f"{sensor_id} connected on {sensor['uart']}")
            except Exception as e:
                state.status = "port error"
                logging.error(f"{sensor_id} cannot open {sensor['uart']}: {e}")
                stop.wait(3)
                continue

        try:
            # If we ever fall behind, drop stale data so values stay real-time
            if ser.in_waiting > 1024:
                ser.reset_input_buffer()
            line = ser.readline().decode("utf-8", errors="ignore").strip()
        except Exception as e:
            logging.error(f"{sensor_id} read error: {e}")
            try:
                ser.close()
            except Exception:
                pass
            ser = None
            state.status = "port error"
            stop.wait(1)
            continue

        now = time.monotonic()

        # Target gone: no Range line for a while -> treat as clear
        if state.distance is not None and now - last_range > RADAR_HOLD_SEC:
            flt.clear()
            publish(sensor_id, state, None, sc, alerts)

        if not line:
            if now - last_rx > 5 and now - last_warn > 5:
                state.status = "no data"
                last_warn = now
                logging.warning(
                    f"{sensor_id}: no data for 5s - check power pin, TX/RX wiring, baud rate"
                )
            continue

        last_rx = now
        match = RANGE_RE.search(line)
        if match:
            state.status = "ok"
            last_range = now
            raw = float(match.group(1))
            value = flt.update(calibrate(raw, sc))
            publish(sensor_id, state, value, sc, alerts, raw=raw)

    if ser:
        ser.close()
    sensor_off(sensor["power_pin"])


# Terminal display
def format_state(s):
    if s.status != "ok":
        text = f"({s.status})"
    elif s.distance is None:
        text = "-- clear"
    else:
        raw = f"{s.raw:6.1f}" if s.raw is not None else "   -- "
        text = f"{s.distance:6.1f}cm raw{raw} {s.rate():4.1f}Hz"
        if s.in_range:
            text += " ALERT"
    return f"{s.sensor_id} {text}".ljust(40)


def display_loop(states, stop):
    while not stop.is_set():
        line = "| ".join(format_state(s) for s in states)
        print(f"\r\033[K{line}", end="", flush=True)
        stop.wait(1.0 / DISPLAY_HZ)


# Main

def main():
    global LG
    setup_logging()
    logging.info("Sensor Manager Started")

    cfgstore = ConfigStore(CONFIG_FILE)
    cfgstore.get()
    LG = open_lgpio()
    setup_gpio()

    sender = HttpSender(SERVER_URL)
    sender.start()
    alerts = AlertManager(cfgstore, sender)

    stop = threading.Event()
    states, threads = [], []

    for sensor_id, sensor in SENSORS.items():
        state = SensorState(sensor_id, sensor["type"])
        worker = ultrasonic_worker if sensor["type"] == "ultrasonic" else radar_worker
        t = threading.Thread(
            target=guarded,
            args=(worker, sensor_id, cfgstore, alerts, state, stop),
            daemon=True,
            name=sensor_id,
        )
        t.start()
        states.append(state)
        threads.append(t)
        logging.info(f"Started thread {sensor_id}")

    try:
        display_loop(states, stop)
    except KeyboardInterrupt:
        print()
        logging.info("Stopping Sensor Manager")
    finally:
        stop.set()
        for t in threads:
            t.join(timeout=2)
        sender.stop()
        for s in SENSORS.values():
            sensor_off(s["power_pin"])
        GPIO.cleanup()
        if LG is not None:
            lgpio.gpiochip_close(LG)
        logging.info("GPIO Cleaned")


if __name__ == "__main__":
    main()