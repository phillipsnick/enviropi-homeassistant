from __future__ import annotations

import json
import logging
import socket
import threading
from typing import Any, Dict, Iterable

import paho.mqtt.client as mqtt

logger = logging.getLogger(__name__)


class MqttClient:
    def __init__(
        self,
        host: str,
        port: int = 1883,
        username: str | None = None,
        password: str | None = None,
        tls: bool = False,
        client_id: str | None = None,
        keepalive: int = 60,
    ) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.tls = tls
        self.keepalive = keepalive
        self.client_id = client_id or f"enviropi-{socket.gethostname()}"

        self._client = mqtt.Client(client_id=self.client_id, clean_session=True)
        if username:
            self._client.username_pw_set(username, password)
        if tls:
            self._client.tls_set()

        self._connected = threading.Event()
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message

        # Simple per-topic callback registry
        self._callbacks: dict[str, callable[[str, str], None]] = {}

    def _on_connect(self, client: mqtt.Client, userdata: Any, flags: Dict[str, Any], rc: int):
        if rc == 0:
            logger.info("Connected to MQTT broker %s:%s", self.host, self.port)
            self._connected.set()
        else:
            logger.error("Failed to connect to MQTT broker (rc=%s)", rc)

    def _on_disconnect(self, client: mqtt.Client, userdata: Any, rc: int):
        self._connected.clear()
        if rc != 0:
            logger.warning("Unexpected MQTT disconnect (rc=%s), will auto-reconnect", rc)

    def start(self) -> None:
        self._client.connect(self.host, self.port, self.keepalive)
        self._client.loop_start()

    def stop(self) -> None:
        try:
            self._client.loop_stop()
        finally:
            try:
                self._client.disconnect()
            except Exception:
                pass

    def publish_json(self, topic: str, payload_obj: Dict[str, Any], retain: bool = False, qos: int = 0) -> None:
        payload = json.dumps(payload_obj, separators=(",", ":"))
        self._client.publish(topic, payload=payload, qos=qos, retain=retain)

    def publish_str(self, topic: str, payload: str, retain: bool = False, qos: int = 0) -> None:
        self._client.publish(topic, payload=payload, qos=qos, retain=retain)

    def wait_connected(self, timeout: float | None = 10.0) -> bool:
        return self._connected.wait(timeout)

    # Subscriptions
    def subscribe(self, topic: str, callback) -> None:
        """
        Subscribe to a topic and register a callback.
        Callback signature: func(topic: str, payload: str) -> None
        """
        self._callbacks[topic] = callback
        self._client.subscribe(topic)

    # Internal dispatcher
    def _on_message(self, client: mqtt.Client, userdata, msg: mqtt.MQTTMessage):  # type: ignore
        try:
            payload = msg.payload.decode("utf-8") if isinstance(msg.payload, (bytes, bytearray)) else str(msg.payload)
        except Exception:
            payload = str(msg.payload)
        cb = self._callbacks.get(msg.topic)
        if cb:
            try:
                cb(msg.topic, payload)
            except Exception as e:
                logger.warning("Error in MQTT callback for %s: %s", msg.topic, e)


def build_discovery_payloads(
    discovery_prefix: str,
    device_id: str,
    name_prefix: str,
    state_topic: str,
    availability_topic: str,
    metrics: Iterable[str],
) -> Dict[str, Dict[str, Any]]:
    """
    Create HA discovery configs keyed by discovery topic.
    """
    device = {
        "identifiers": [device_id],
        "manufacturer": "Pimoroni",
        "model": "Enviro+",
        "name": name_prefix,
    }

    def ent(metric: str, name: str, unit: str | None, device_class: str | None, state_class: str | None):
        cfg: Dict[str, Any] = {
            "name": f"{name_prefix} {name}",
            "unique_id": f"{device_id}_{metric}",
            "state_topic": state_topic,
            "value_template": f"{{{{ value_json.{metric} }}}}",
            "availability_topic": availability_topic,
            "device": device,
        }
        if unit:
            cfg["unit_of_measurement"] = unit
        if device_class:
            cfg["device_class"] = device_class
        if state_class:
            cfg["state_class"] = state_class
        return cfg

    catalog: Dict[str, Dict[str, Any]] = {}

    meta: Dict[str, Dict[str, Any]] = {
        "temperature_c": {"name": "Temperature", "unit": "°C", "device_class": "temperature", "state_class": "measurement"},
        "humidity_pct": {"name": "Humidity", "unit": "%", "device_class": "humidity", "state_class": "measurement"},
        "pressure_hpa": {"name": "Pressure", "unit": "hPa", "device_class": "pressure", "state_class": "measurement"},
        "gas_oxidising_kohm": {"name": "Gas Oxidising", "unit": "kΩ", "device_class": None, "state_class": "measurement"},
        "gas_reducing_kohm": {"name": "Gas Reducing", "unit": "kΩ", "device_class": None, "state_class": "measurement"},
        "gas_nh3_kohm": {"name": "Gas NH3", "unit": "kΩ", "device_class": None, "state_class": "measurement"},
        "light_lux": {"name": "Illuminance", "unit": "lx", "device_class": "illuminance", "state_class": "measurement"},
        "proximity": {"name": "Proximity", "unit": None, "device_class": None, "state_class": "measurement"},
        "pm1_0": {"name": "PM1.0", "unit": "µg/m³", "device_class": None, "state_class": "measurement"},
        "pm2_5": {"name": "PM2.5", "unit": "µg/m³", "device_class": "pm25", "state_class": "measurement"},
        "pm10": {"name": "PM10", "unit": "µg/m³", "device_class": "pm10", "state_class": "measurement"},
        # Air Quality Index derived from PM2.5/PM10; no standard device_class in HA
        "aqi": {"name": "AQI", "unit": None, "device_class": None, "state_class": "measurement"},
    }

    for m in metrics:
        if m not in meta:
            continue
        info = meta[m]
        comp = "sensor"
        topic = f"{discovery_prefix}/{comp}/{device_id}/{m}/config"
        catalog[topic] = ent(m, info["name"], info["unit"], info["device_class"], info["state_class"])

    return catalog


def build_display_switch_discovery(
    discovery_prefix: str,
    device_id: str,
    name_prefix: str,
    command_topic: str,
    state_topic: str,
    availability_topic: str,
) -> Dict[str, Any]:
    """Build Home Assistant discovery payload for a display ON/OFF switch."""
    device = {
        "identifiers": [device_id],
        "manufacturer": "Pimoroni",
        "model": "Enviro+",
        "name": name_prefix,
    }
    return {
        "name": f"{name_prefix} Display",
        "unique_id": f"{device_id}_display",
        "command_topic": command_topic,
        "state_topic": state_topic,
        "payload_on": "ON",
        "payload_off": "OFF",
        "availability_topic": availability_topic,
        "device": device,
    }


def build_display_binary_sensor_discovery(
    discovery_prefix: str,
    device_id: str,
    name_prefix: str,
    state_topic: str,
    availability_topic: str,
) -> Dict[str, Any]:
    """Build Home Assistant discovery payload for a binary_sensor reflecting display ON/OFF state."""
    device = {
        "identifiers": [device_id],
        "manufacturer": "Pimoroni",
        "model": "Enviro+",
        "name": name_prefix,
    }
    return {
        "name": f"{name_prefix} Display On",
        "unique_id": f"{device_id}_display_on",
        "state_topic": state_topic,
        "payload_on": "ON",
        "payload_off": "OFF",
        "availability_topic": availability_topic,
        "device_class": "power",
        "device": device,
    }
