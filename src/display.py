from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class Display:
    """
    Minimal wrapper for Enviro+ LCD (ST7735) using Pillow for rendering.
    All imports are optional; class becomes a no-op if hardware/libs are missing.
    """

    def __init__(self) -> None:
        self._st7735 = None
        self._Image = None
        self._ImageDraw = None
        self._ImageFont = None
        self._enabled: bool = False
        # Hardware pins used by Pimoroni Enviro+ (BCM numbering)
        # Backlight pin differs between Enviro+ (19) and Enviro+ Mini (12).
        # Allow override via ENVIROPI_LCD_BACKLIGHT_PIN.
        bl_env = os.getenv("ENVIROPI_LCD_BACKLIGHT_PIN")
        default_bl = 19
        try:
            default_bl = int(bl_env) if bl_env is not None else 19
        except Exception:
            default_bl = 19
        # Try both likely pins when probing drivers/backlight
        self._bl_pins: list[int] = list(dict.fromkeys([default_bl, 12, 19]))
        self._bl_pin: int = self._bl_pins[0]  # primary used for logs and GPIO fallback preference
        self._rst_pin: int = 25  # LCD reset
        self._gpio = None  # type: ignore

        try:
            # Prefer Pimoroni's enviroplus.lcd wrapper when available since it matches hardware defaults
            try:
                from enviroplus import lcd as pimoroni_lcd  # type: ignore
                self._st7735 = pimoroni_lcd
                logger.info("Using enviroplus.lcd wrapper for display initialisation")
            except Exception:
                # Fallback to direct ST7735 driver
                try:
                    from ST7735 import ST7735  # type: ignore
                except Exception:
                    from st7735 import ST7735  # type: ignore

                # Typical Enviro+ LCD setup (SPI0, CS1, DC=9, BL=19, RST=25, rotation 270)
                # Use a conservative SPI speed for compatibility; too-high speeds can result in a blank screen.
                # Provide rst pin to ensure proper panel reset.
                def _make_lcd(cs_val: int, rotation: int, with_rst: bool, backlight_pin: int):
                    if with_rst:
                        return ST7735(
                            port=0,
                            cs=cs_val,
                            dc=9,
                            backlight=backlight_pin,
                            rst=self._rst_pin,
                            rotation=rotation,
                            spi_speed_hz=4_000_000,
                        )
                    else:
                        return ST7735(
                            port=0,
                            cs=cs_val,
                            dc=9,
                            backlight=backlight_pin,
                            rotation=rotation,
                            spi_speed_hz=4_000_000,
                        )

                lcd = None
                # Try preferred combinations in order of likelihood
                attempts = []
                for bl in self._bl_pins:
                    attempts.extend([
                        (bl, 1, 270, True),
                        (bl, 1, 270, False),
                        (bl, 0, 270, True),
                        (bl, 0, 270, False),
                        (bl, 1, 90, True),
                        (bl, 1, 90, False),
                    ])
                last_err: Optional[Exception] = None
                for bl, cs_val, rot, use_rst in attempts:
                    try:
                        lcd = _make_lcd(cs_val, rot, use_rst, bl)
                        # Many library versions require an explicit begin()
                        begin = getattr(lcd, "begin", None)
                        if callable(begin):
                            begin()
                        self._bl_pin = bl
                        logger.info("LCD driver created (cs=%s, rotation=%s, rst=%s, bl_pin=%s)", cs_val, rot, use_rst, bl)
                        break
                    except TypeError:
                        # Likely due to rst parameter on older libs; retry without rst within loop
                        last_err = None
                        continue
                    except Exception as e:
                        last_err = e
                        continue

                if lcd is None:
                    # Surface the most recent error if available
                    raise last_err or RuntimeError("Failed to initialise ST7735 display")

                self._st7735 = lcd

            from PIL import Image, ImageDraw, ImageFont  # type: ignore
            self._Image = Image
            self._ImageDraw = ImageDraw
            self._ImageFont = ImageFont
            logger.info("LCD display initialised")
        except Exception as e:
            logger.warning("LCD/Display unavailable: %s", e)

    def available(self) -> bool:
        return self._st7735 is not None

    def set_enabled(self, on: bool) -> None:
        on = bool(on)
        if on == self._enabled and self.available():
            # No change
            return
        self._enabled = on
        # Even if the LCD driver isn't available, ensure we best-effort toggle the backlight
        # via GPIO fallback when turning off.
        if not self.available():
            if self._enabled:
                # Try to turn the backlight ON via GPIO as best-effort
                try:
                    self._set_backlight(True)
                    logger.info("LCD: ON (driver unavailable, GPIO fallback)")
                except Exception:
                    pass
            else:
                try:
                    self._set_backlight(False)
                    logger.info("LCD: OFF (driver unavailable, GPIO fallback)")
                except Exception:
                    pass
                # Hard-force GPIO LOW on common backlight pins as a last resort
                try:
                    self._force_gpio_backlight_low()
                except Exception:
                    pass
            return
        try:
            if self._enabled:
                # Turn backlight on and push a blank frame to wake the panel
                self._set_backlight(True)
                time.sleep(0.05)
                w, h = self._get_dimensions()
                img = self._Image.new("RGB", (w, h), (0, 0, 0))
                display_fn = getattr(self._st7735, "display", None)
                if callable(display_fn):
                    display_fn(img)
                logger.info("LCD: ON")
            else:
                # Fill black then turn backlight off
                w, h = self._get_dimensions()
                img = self._Image.new("RGB", (w, h), (0, 0, 0))
                display_fn = getattr(self._st7735, "display", None)
                if callable(display_fn):
                    display_fn(img)
                # Ensure backlight is switched off even if display() raised
                try:
                    self._set_backlight(False)
                finally:
                    # Best-effort second attempt to force GPIO LOW in case library call failed silently
                    try:
                        self._set_backlight(False)
                    except Exception:
                        pass
                    # And unconditionally drive GPIO LOW on candidate pins to guarantee off
                    try:
                        self._force_gpio_backlight_low()
                    except Exception:
                        pass
                logger.info("LCD: OFF")
        except Exception as e:
            logger.warning("Error toggling display: %s", e)
            # On any error while disabling, force backlight off
            if not self._enabled:
                try:
                    self._set_backlight(False)
                except Exception:
                    pass
                try:
                    self._force_gpio_backlight_low()
                except Exception:
                    pass

    def is_enabled(self) -> bool:
        return self._enabled

    def _set_backlight(self, on: bool) -> None:
        """Best-effort backlight toggle across library variants, always mirroring state to GPIO as well.

        We attempt known library APIs first and then also drive candidate GPIO pins
        to the same state to ensure the physical BL pin follows, even if the library
        call is a no-op on this hardware.
        """
        used_path = None
        try:
            val_bool = bool(on)
            val_int = 1 if on else 0
            val_float = 1.0 if on else 0.0

            # Common Pimoroni API
            if hasattr(self._st7735, "set_backlight"):
                try:
                    self._st7735.set_backlight(val_int)
                    used_path = "set_backlight(int)"
                except Exception:
                    try:
                        self._st7735.set_backlight(val_float)
                        used_path = "set_backlight(float)"
                    except Exception:
                        self._st7735.set_backlight(val_bool)
                        used_path = "set_backlight(bool)"

            # Some versions expose .backlight as callable or property
            if hasattr(self._st7735, "backlight"):
                bl = getattr(self._st7735, "backlight")
                if callable(bl):
                    try:
                        bl(val_bool)
                        used_path = "backlight(bool)"
                    except Exception:
                        try:
                            bl(val_int)
                            used_path = "backlight(int)"
                        except Exception:
                            bl(val_float)
                            used_path = "backlight(float)"
                else:
                    try:
                        setattr(self._st7735, "backlight", val_bool)
                        used_path = "backlight=bool"
                    except Exception:
                        try:
                            setattr(self._st7735, "backlight", val_int)
                            used_path = "backlight=int"
                        except Exception:
                            setattr(self._st7735, "backlight", val_float)
                            used_path = "backlight=float"

            # Some libs expose LED control
            if hasattr(self._st7735, "set_led"):
                try:
                    self._st7735.set_led(val_bool)
                    used_path = "set_led(bool)"
                except Exception:
                    pass
            if hasattr(self._st7735, "led"):
                try:
                    setattr(self._st7735, "led", val_bool)
                    used_path = "led=bool"
                except Exception:
                    pass

            # Fallback: brightness attribute/method (0.0–1.0)
            if hasattr(self._st7735, "set_brightness"):
                try:
                    self._st7735.set_brightness(val_float)
                    used_path = "set_brightness(float)"
                except Exception:
                    pass
            if hasattr(self._st7735, "brightness"):
                try:
                    setattr(self._st7735, "brightness", val_float)
                    used_path = "brightness=float"
                except Exception:
                    pass
            # If we reached here without returning, log which path we used if any
            if used_path:
                logger.info("LCD backlight via %s: %s", used_path, "ON" if on else "OFF")
        except Exception:
            # Ignore and continue to GPIO fallback
            pass

        # Always mirror state to GPIO pins as well (acts as fallback and state enforcer)
        try:
            if self._gpio is None:
                try:
                    import RPi.GPIO as GPIO  # type: ignore
                except Exception as e:
                    logger.debug("RPi.GPIO import failed for backlight fallback: %s", e)
                    return
                try:
                    GPIO.setwarnings(False)
                    GPIO.setmode(GPIO.BCM)
                except Exception:
                    # setmode may already be configured; ignore
                    pass
                # Setup all candidate backlight pins
                for pin in self._bl_pins:
                    try:
                        GPIO.setup(pin, GPIO.OUT, initial=GPIO.HIGH if on else GPIO.LOW)
                    except Exception:
                        # If already set up, just proceed to output
                        pass
                self._gpio = GPIO

            # Output desired level
            level = self._gpio.HIGH if on else self._gpio.LOW
            ok_pins = []
            for pin in self._bl_pins:
                try:
                    self._gpio.output(pin, level)
                    ok_pins.append(pin)
                except Exception as e:
                    logger.debug("GPIO backlight control failed on pin %s: %s", pin, e)
            if ok_pins:
                # Remember the first successful pin as primary for subsequent operations
                self._bl_pin = ok_pins[0]
                logger.info("LCD backlight via GPIO (pins %s): %s", ok_pins, "ON" if on else "OFF")
                return
        except Exception:
            pass

    def _get_dimensions(self) -> tuple[int, int]:
        """Return display (width, height) using available attributes with sensible defaults.

        Supports both ST7735 instance objects (with .width/.height) and the
        enviroplus.lcd wrapper module (with WIDTH/HEIGHT constants).
        """
        # ST7735 instance usually exposes .width/.height
        w = getattr(self._st7735, "width", None)
        h = getattr(self._st7735, "height", None)
        if isinstance(w, int) and isinstance(h, int):
            return w, h
        # enviroplus.lcd exposes constants WIDTH/HEIGHT
        w = getattr(self._st7735, "WIDTH", None)
        h = getattr(self._st7735, "HEIGHT", None)
        if isinstance(w, int) and isinstance(h, int):
            return w, h
        # Fallback to typical Enviro+ LCD size
        return (160, 80)

    def _force_gpio_backlight_low(self) -> None:
        """Force the LCD backlight GPIO pin(s) LOW regardless of library state.

        This is a last-resort guard to ensure the physical backlight is off on
        hardware where the driver API call does not actually toggle the BL pin.
        """
        try:
            import RPi.GPIO as GPIO  # type: ignore
        except Exception:
            return
        try:
            GPIO.setwarnings(False)
            GPIO.setmode(GPIO.BCM)
        except Exception:
            pass
        for pin in self._bl_pins:
            try:
                GPIO.setup(pin, GPIO.OUT)
            except Exception:
                pass
            try:
                GPIO.output(pin, GPIO.LOW)
            except Exception:
                pass

    def render(self, reading: Dict[str, Any]) -> None:
        if not self.available() or not self._enabled:
            return
        try:
            width, height = self._get_dimensions()
            img = self._Image.new("RGB", (width, height), (0, 0, 0))
            draw = self._ImageDraw.Draw(img)

            # Choose a basic font
            try:
                font = self._ImageFont.load_default()
            except Exception:
                font = None

            lines = []
            def fmt(key: str, label: str, suffix: str = ""):
                if key in reading:
                    val = reading[key]
                    lines.append(f"{label}: {val}{suffix}")

            fmt("temperature_c", "Temp", "°C")
            fmt("humidity_pct", "Hum", "%")
            fmt("pressure_hpa", "Pres", " hPa")
            fmt("light_lux", "Lux", " lx")
            if "pm2_5" in reading or "pm10" in reading or "pm1_0" in reading:
                pm = [f"{reading.get('pm1_0','-')}", f"{reading.get('pm2_5','-')}", f"{reading.get('pm10','-')}"]
                lines.append("PM1/2.5/10: " + "/".join(map(str, pm)))

            # Show AQI instead of proximity
            if "aqi" in reading:
                lines.append(f"AQI: {reading['aqi']}")

            y = 2
            for line in lines[:6]:  # keep within small screen
                draw.text((2, y), line, fill=(0, 255, 0), font=font)
                y += 12

            # Some wrappers expose display() as a function on a module or a method on an instance
            display_fn = getattr(self._st7735, "display", None)
            if callable(display_fn):
                display_fn(img)
            else:
                # If no display function, there's nothing we can do; log once
                logger.warning("Display object has no display() method")
        except Exception as e:
            logger.warning("Error rendering to display: %s", e)
