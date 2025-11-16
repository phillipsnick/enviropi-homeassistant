# Enviropi: Enviro+ to MQTT for Home Assistant

## Overview

This project reads data from the Pimoroni Enviro+ (and optional PMS5003 particulate sensor) on Linux and publishes it to an MQTT broker using Home Assistant MQTT Discovery. Each metric is exposed as a sensor entity in Home Assistant.

## Raspberry Pi setup (Enviro+ and optional PMS5003)

Follow Pimoroni’s official documentation to prepare your Raspberry Pi and Enviro+ hardware. The links below are the source of truth and should be consulted if anything differs here:

- Enviro+ docs and installer: https://github.com/pimoroni/enviroplus-python/tree/main
- PMS5003 product page (wiring info): https://shop.pimoroni.com/products/pms5003-particulate-matter-sensor-with-cable?variant=29075640352851

Summary of the key steps on Raspberry Pi OS (Lite or Full):

1) Fit the Enviro+ HAT to the Raspberry Pi GPIO header. If you have the Enviro+ Mini it also fits the standard 40-pin header.

2) Update the OS and enable interfaces required by Enviro+:

```
sudo apt update && sudo apt -y full-upgrade
sudo raspi-config
```

In raspi-config:
- Interface Options → Enable I2C
- Interface Options → Enable SPI
- Interface Options → Enable Serial Port, then:
  - Login shell over serial? → No
  - Enable serial hardware? → Yes

Reboot when prompted or run: `sudo reboot`

3) Install Pimoroni Enviro+ dependencies (recommended one-line installer):

```
curl -sS https://get.pimoroni.com/enviroplus | bash
```

This installs the required Python libraries, SPI/I2C dependencies, and example scripts. Reboot after installation if asked.

If readings appear and the LCD shows output, your hardware is set up correctly. You can then proceed to the Quick Start below to run this project.

## Features

- Periodic sensor readings from Enviro+: temperature, humidity, pressure, gas (oxidising, reducing, NH3), light, proximity, and optional PM1/PM2.5/PM10.
- Derived Air Quality Index (AQI) calculated from PM2.5/PM10 when particulate sensor is enabled.
- MQTT publishing with Home Assistant Discovery auto-config.
- Single compact JSON state topic with value_template per entity.
- Graceful shutdown and robust handling when some hardware/modules are not available.
- Optional on-device LCD output: proximity tap toggles LCD on/off; shows key metrics when on.
- Home Assistant switch to enable/disable the LCD display via MQTT.
- Home Assistant binary sensor that reflects whether the display is currently ON.

Note: Windows is not supported. This project targets Linux (e.g., Raspberry Pi OS).

## Quick Start (Linux)

1) Install system dependencies for Enviro+ (per Pimoroni docs) and Python 3.9+.
2) Create and activate a Python virtual environment, then install requirements:

```
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

3) Set environment variables (examples):

```
export MQTT_HOST=192.168.1.10
export MQTT_PORT=1883
export MQTT_USERNAME=homeassistant
export MQTT_PASSWORD=yourpass
# Optional settings
# Sensor read interval in seconds (default 30)
export ENVIROPI_UPDATE_INTERVAL=30
export ENVIROPI_DEVICE_ID=enviroplus-pi
export ENVIROPI_DISCOVERY_PREFIX=homeassistant
export ENVIROPI_ENABLE_PMS=true
export ENVIROPI_ENABLE_LIGHT=true
# LCD display (Enviro+) control
export ENVIROPI_ENABLE_DISPLAY=true
# Proximity threshold to toggle display (depends on your unit/ambient)
export ENVIROPI_PROXIMITY_THRESHOLD=1000
```

4) Run the service:

```
python -m src.service
```

### Run directly (no system service)

- Foreground with logs (recommended while testing):

```
python -m src.service
```

- Run in background without systemd (basic nohup example):

```
nohup python -m src.service > enviropi.out 2>&1 &
echo $!  # prints PID so you can stop it later

# To stop later:
kill <PID>
# or
pkill -f "python -m src.service"
```

### Notes when running directly

- Make sure your environment variables (MQTT_HOST, etc.) are exported in the same shell/session you launch the process from, e.g.:

```
export MQTT_HOST=192.168.1.10
nohup python -m src.service &
```

- For long‑running sessions with interactive access, consider using tmux or screen instead of nohup.

## Virtual environment (venv) setup (Linux)

If you haven't used venv before, here are the recommended steps.

- Ensure Python 3.9+ is installed.
- On Debian/Ubuntu you may need to install the venv module first:

```
sudo apt update && sudo apt install -y python3-venv
```

- Create the environment and install dependencies:

```
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

- Deactivate when you're done:

```
deactivate
```

## Home Assistant

With MQTT integration enabled, entities should appear automatically within ~1 minute after startup. Entities are named "Enviro+ <Metric>" by default and grouped under a device matching ENVIROPI_DEVICE_ID.

- A switch entity "Enviro+ Display" will be created when the Enviro+ LCD is available and enabled. Toggling this switch turns the device display ON/OFF via MQTT.
- A binary sensor entity "Enviro+ Display On" will be created to reflect the actual display state (ON/OFF). It stays in sync with proximity toggles and the switch.
- When PM readings are available, a sensor entity "Enviro+ AQI" is created, representing the US EPA AQI derived from PM2.5 and PM10 (worst case).

## Configuration

Environment variables:

- MQTT_HOST: required, MQTT broker hostname/IP.
- MQTT_PORT: optional, default 1883.
- MQTT_USERNAME / MQTT_PASSWORD: optional, for authenticated brokers.
- MQTT_TLS: optional (true/false), default false.
- ENVIROPI_UPDATE_INTERVAL: optional, seconds between sensor reads, default 30.
- ENVIROPI_DEVICE_ID: optional, default "enviroplus-<hostname>".
- ENVIROPI_DISCOVERY_PREFIX: optional, default "homeassistant".
- ENVIROPI_ENABLE_PMS: optional (true/false), enable PMS5003 particulate readings, default true if module present.
- ENVIROPI_ENABLE_LIGHT: optional, enable/disable LTR559 light/proximity, default true if module present.
- ENVIROPI_ENABLE_DISPLAY: optional (true/false), enable LCD output on Enviro+, default true if module present.
- ENVIROPI_PROXIMITY_THRESHOLD: optional, integer threshold (default 1000). A rising edge over this value toggles LCD on/off.
- ENVIROPI_LCD_BACKLIGHT_PIN: optional, override LCD backlight BCM pin if needed. Defaults to 19; Enviro+ Mini often uses 12. The service auto‑tries common pins (12, 19) if unset.

## Display control via MQTT

When LCD hardware is available, the service exposes a Home Assistant MQTT switch and topics:

- Discovery: <prefix>/switch/<device_id>/display/config
- Command: enviropi/<device_id>/display/set (payloads: ON, OFF, TOGGLE)
- State:   enviropi/<device_id>/display/state (payloads: ON, OFF)

The display state is kept in sync when you toggle via proximity or the HA switch.

## Display state sensor via MQTT

A Home Assistant binary_sensor is published for the display state:

- Discovery: <prefix>/binary_sensor/<device_id>/display_on/config
- State topic: enviropi/<device_id>/display/state (payloads: ON, OFF)
- Device class: power

## Notes

- If any sensor module is missing or errors at runtime, the service will log a warning and continue with available sensors. Corresponding metrics will be omitted from the JSON state when unavailable.
- You can safely run this service on systems without Enviro+ hardware to verify MQTT and discovery behavior; metrics will simply be missing.
- Display behavior: When enabled and available, a proximity event crossing the threshold turns the display on; the next proximity event turns it off. When off, the backlight is disabled and the screen is cleared.
- Display compatibility: The service prefers the enviroplus.lcd wrapper when available and falls back to the ST7735 driver with conservative SPI speed. It also tries common CS (0/1), rotations (270/90), and backlight pins (12/19) to match Pimoroni examples. You can set ENVIROPI_LCD_BACKLIGHT_PIN to force a specific backlight pin.
- AQI details: The value is calculated using US EPA breakpoints from PM2.5 (rounded to 0.1 µg/m³) and PM10 (truncated to integer) and the higher of the two AQI sub-indices is reported.

Backlight note: When turning the display OFF, the service explicitly switches the LCD backlight off (including a GPIO fallback) to avoid leaving the backlight illuminated.

## License

MIT
