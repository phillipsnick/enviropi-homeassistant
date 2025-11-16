from __future__ import annotations

import logging
import signal
import sys
import time

from .config import Config
from .mqtt import (
    MqttClient,
    build_discovery_payloads,
    build_display_switch_discovery,
    build_display_binary_sensor_discovery,
)
from .sensors import Sensors
from .display import Display


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("enviropi")


def main() -> int:
    cfg = Config()
    try:
        cfg.validate()
    except Exception as e:
        logger.error("Configuration error: %s", e)
        return 2

    sensors = Sensors(enable_light=cfg.enable_light, enable_pms=cfg.enable_pms)
    display = Display() if cfg.enable_display else None
    if display and not display.available():
        logger.info("Display not available; continuing without LCD output")
        display = None

    client = MqttClient(
        host=cfg.mqtt_host,
        port=cfg.mqtt_port,
        username=cfg.mqtt_username,
        password=cfg.mqtt_password,
        tls=cfg.mqtt_tls,
        client_id=cfg.device_id,
    )

    state_topic = f"enviropi/{cfg.device_id}/state"
    availability_topic = f"enviropi/{cfg.device_id}/status"
    display_cmd_topic = f"enviropi/{cfg.device_id}/display/set"
    display_state_topic = f"enviropi/{cfg.device_id}/display/state"

    # Graceful shutdown handling
    stopping = {"flag": False}

    def _sigterm(signum, frame):  # type: ignore
        logger.info("Signal %s received, stopping...", signum)
        stopping["flag"] = True

    signal.signal(signal.SIGINT, _sigterm)
    signal.signal(signal.SIGTERM, _sigterm)

    client.start()
    client.wait_connected(10.0)

    # Publish discovery for available metrics after first read to know what exists
    first = True

    try:
        # Proximity-driven toggle: rising edge over threshold toggles display
        last_prox_high = False
        prox_ready = False  # ignore first sample to avoid an immediate unwanted toggle at startup
        # Ensure display initially ON so users see output without interaction
        if display:
            display.set_enabled(True)
        # Setup HA discovery for display switch and subscribe to command
        if display:
            switch_cfg = build_display_switch_discovery(
                cfg.discovery_prefix,
                cfg.device_id,
                name_prefix="Enviro+",
                command_topic=display_cmd_topic,
                state_topic=display_state_topic,
                availability_topic=availability_topic,
            )
            client.publish_json(
                f"{cfg.discovery_prefix}/switch/{cfg.device_id}/display/config",
                switch_cfg,
                retain=True,
            )
            # Publish binary_sensor discovery for display state
            bin_cfg = build_display_binary_sensor_discovery(
                cfg.discovery_prefix,
                cfg.device_id,
                name_prefix="Enviro+",
                state_topic=display_state_topic,
                availability_topic=availability_topic,
            )
            client.publish_json(
                f"{cfg.discovery_prefix}/binary_sensor/{cfg.device_id}/display_on/config",
                bin_cfg,
                retain=True,
            )

            def _handle_display_cmd(topic: str, payload: str) -> None:
                p = payload.strip().upper()
                if p in {"ON", "OFF"}:
                    display.set_enabled(p == "ON")
                    logger.info("Display command: %s", p)
                elif p == "TOGGLE":
                    display.set_enabled(not display.is_enabled())
                    logger.info("Display command: TOGGLE -> %s", "ON" if display.is_enabled() else "OFF")
                else:
                    logger.warning("Unknown display command payload: %r", payload)
                # publish state after any command
                client.publish_str(display_state_topic, "ON" if display.is_enabled() else "OFF", retain=True)

            client.subscribe(display_cmd_topic, _handle_display_cmd)
        while not stopping["flag"]:
            reading = sensors.read()

            if first:
                first = False
                discovery = build_discovery_payloads(
                    cfg.discovery_prefix,
                    cfg.device_id,
                    name_prefix="Enviro+",
                    state_topic=state_topic,
                    availability_topic=availability_topic,
                    metrics=reading.keys(),
                )
                for topic, payload in discovery.items():
                    client.publish_json(topic, payload, retain=True)
                client.publish_str(availability_topic, "online", retain=True)
                # publish initial display state if display exists
                if display:
                    client.publish_str(display_state_topic, "ON", retain=True)

            # Proximity toggle handling
            if display and "proximity" in reading:
                prox = int(reading["proximity"])
                is_high = prox >= cfg.proximity_threshold
                if prox_ready:
                    if is_high and not last_prox_high:
                        # Rising edge -> toggle
                        display.set_enabled(not display.is_enabled())
                        logger.info("Proximity toggle: %s (prox=%s)", "ON" if display.is_enabled() else "OFF", prox)
                        # Sync state to MQTT so HA reflects the change
                        client.publish_str(display_state_topic, "ON" if display.is_enabled() else "OFF", retain=True)
                last_prox_high = is_high
                prox_ready = True

            # If display is on, render current reading
            if display and display.is_enabled():
                display.render(reading)

            # Publish state JSON (only available metrics)
            client.publish_json(state_topic, reading)
            time.sleep(max(1, cfg.interval))
    except Exception as e:
        logger.exception("Fatal error in service loop: %s", e)
        return 1
    finally:
        # Advertise offline
        try:
            client.publish_str(availability_topic, "offline", retain=True)
        except Exception:
            pass
        # Ensure display is turned off on exit
        try:
            if display:
                display.set_enabled(False)
                # Update final state
                try:
                    client.publish_str(display_state_topic, "OFF", retain=True)
                except Exception:
                    pass
        except Exception:
            pass
        client.stop()

    return 0


if __name__ == "__main__":
    sys.exit(main())
