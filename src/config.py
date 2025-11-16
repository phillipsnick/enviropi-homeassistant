import os


def _get_bool(env_name: str, default: bool) -> bool:
    val = os.getenv(env_name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "y", "on"}


class Config:
    def __init__(self) -> None:
        self.mqtt_host: str = os.getenv("MQTT_HOST", "192.168.1.10")
        self.mqtt_port: int = int(os.getenv("MQTT_PORT", "1883"))
        self.mqtt_username: str | None = os.getenv("MQTT_USERNAME")
        self.mqtt_password: str | None = os.getenv("MQTT_PASSWORD")
        self.mqtt_tls: bool = _get_bool("MQTT_TLS", False)

        self.device_id: str = os.getenv("ENVIROPI_DEVICE_ID", "enviropi-1")
        # Update interval (seconds)
        self.update_interval: int = int(os.getenv("ENVIROPI_UPDATE_INTERVAL", "30"))
        # Keep existing attribute name for callers
        self.interval: int = self.update_interval
        self.discovery_prefix: str = os.getenv("ENVIROPI_DISCOVERY_PREFIX", "homeassistant")

        self.enable_pms: bool = _get_bool("ENVIROPI_ENABLE_PMS", True)
        self.enable_light: bool = _get_bool("ENVIROPI_ENABLE_LIGHT", True)
        # LCD display control (Enviro+)
        self.enable_display: bool = _get_bool("ENVIROPI_ENABLE_DISPLAY", True)
        # Proximity threshold used to toggle display on/off
        self.proximity_threshold: int = int(os.getenv("ENVIROPI_PROXIMITY_THRESHOLD", "1000"))

    def validate(self) -> None:
        if not self.mqtt_host:
            raise ValueError("MQTT_HOST is required")
