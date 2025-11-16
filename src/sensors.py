from __future__ import annotations

import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


class Sensors:
    """
    Wrapper around Enviro+ sensors. All imports are optional; when a sensor
    isn't available, readings are omitted.
    """

    def __init__(self, enable_light: bool = True, enable_pms: bool = True) -> None:
        self.enable_light = enable_light
        self.enable_pms = enable_pms

        # Try import enviroplus components lazily
        self._bme280 = None
        self._gas = None
        self._ltr559 = None
        self._pms5003 = None
        # No sound level support (disabled)

        # BME280 and Gas
        try:
            from enviroplus import gas
            from bme280 import BME280  # type: ignore
            self._gas = gas
            self._bme280 = BME280()
        except Exception as e:
            logger.warning("BME280/Gas unavailable: %s", e)

        # Light / proximity
        if enable_light:
            try:
                from ltr559 import LTR559  # type: ignore
                self._ltr559 = LTR559()
            except Exception as e:
                logger.warning("LTR559 unavailable: %s", e)

        # PMS5003
        if enable_pms:
            try:
                from pms5003 import PMS5003  # type: ignore
                self._pms5003 = PMS5003()
            except Exception as e:
                logger.warning("PMS5003 unavailable: %s", e)

        # Sound level (MEMS mic) intentionally not supported; library support is unreliable.

    def read(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {}

        # BME280
        if self._bme280 is not None:
            try:
                temp = self._bme280.get_temperature()
                hum = self._bme280.get_humidity()
                pres = self._bme280.get_pressure()
                if temp is not None:
                    data["temperature_c"] = round(float(temp), 2)
                if hum is not None:
                    data["humidity_pct"] = round(float(hum), 2)
                if pres is not None:
                    data["pressure_hpa"] = round(float(pres), 2)
            except Exception as e:
                logger.warning("Error reading BME280: %s", e)

        # Gas sensor
        if self._gas is not None:
            try:
                readings = self._gas.read_all()
                # Values in Ohms; convert to kOhm for readability
                data["gas_oxidising_kohm"] = round(readings.oxidising / 1000.0, 3)
                data["gas_reducing_kohm"] = round(readings.reducing / 1000.0, 3)
                data["gas_nh3_kohm"] = round(readings.nh3 / 1000.0, 3)
            except Exception as e:
                logger.warning("Error reading Gas: %s", e)

        # Light
        if self._ltr559 is not None:
            try:
                data["light_lux"] = round(float(self._ltr559.get_lux()), 2)
                data["proximity"] = int(self._ltr559.get_proximity())
            except Exception as e:
                logger.warning("Error reading LTR559: %s", e)

        # PMS5003
        if self._pms5003 is not None:
            try:
                pm = self._pms5003.read()
                data["pm1_0"] = int(pm.pm_ug_per_m3(1.0))
                data["pm2_5"] = int(pm.pm_ug_per_m3(2.5))
                data["pm10"] = int(pm.pm_ug_per_m3(10))
                # Compute AQI based on US EPA from PM2.5 and PM10, choose the worst (highest)
                aqi_values = []
                if "pm2_5" in data:
                    aqi_values.append(_aqi_from_pm25(float(data["pm2_5"])) )
                if "pm10" in data:
                    aqi_values.append(_aqi_from_pm10(float(data["pm10"])) )
                if aqi_values:
                    # Round to nearest integer per typical presentation
                    data["aqi"] = int(round(max(aqi_values)))
            except Exception as e:
                logger.warning("Error reading PMS5003: %s", e)

        # No sound level reading.

        return data


# --- AQI helper functions (US EPA breakpoints) ---

def _linear(aqi_hi: float, aqi_lo: float, conc_hi: float, conc_lo: float, conc: float) -> float:
    if conc_hi == conc_lo:
        return aqi_hi
    return (aqi_hi - aqi_lo) / (conc_hi - conc_lo) * (conc - conc_lo) + aqi_lo


def _aqi_from_pm25(pm25: float) -> float:
    # Concentration to 1 decimal place
    c = max(0.0, round(pm25 * 10) / 10.0)
    # Breakpoints (µg/m³): (Clow, Chigh, Ilow, Ihigh)
    bp = [
        (0.0, 12.0, 0, 50),
        (12.1, 35.4, 51, 100),
        (35.5, 55.4, 101, 150),
        (55.5, 150.4, 151, 200),
        (150.5, 250.4, 201, 300),
        (250.5, 350.4, 301, 400),
        (350.5, 500.4, 401, 500),
    ]
    for cl, ch, il, ih in bp:
        if c <= ch:
            return _linear(ih, il, ch, cl, c)
    return 500.0


def _aqi_from_pm10(pm10: float) -> float:
    # Truncate to integer per EPA for PM10
    c = int(max(0.0, pm10))
    bp = [
        (0, 54, 0, 50),
        (55, 154, 51, 100),
        (155, 254, 101, 150),
        (255, 354, 151, 200),
        (355, 424, 201, 300),
        (425, 504, 301, 400),
        (505, 604, 401, 500),
    ]
    for cl, ch, il, ih in bp:
        if c <= ch:
            return _linear(ih, il, ch, cl, c)
    return 500.0
