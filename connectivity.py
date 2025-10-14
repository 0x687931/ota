"""
Multi-Connectivity Manager for OTA Updates

Provides intelligent fallback between WiFi, LoRa, and Cellular connections.
Designed for remote IoT deployments where WiFi may be unreliable.
"""

import os
import sys

try:
    import ujson as json
except Exception:
    import json

MICROPYTHON = sys.implementation.name == "micropython"


class ConnectivityError(Exception):
    """Connectivity operation error."""
    pass


class Transport:
    """Base class for network transports."""

    def __init__(self, config):
        self.config = config
        self.connected = False

    def connect(self):
        """Establish connection. Returns True if successful."""
        raise NotImplementedError

    def disconnect(self):
        """Close connection."""
        raise NotImplementedError

    def fetch_json(self, url):
        """Fetch JSON data from URL."""
        raise NotImplementedError

    def fetch_binary(self, url, max_size=None):
        """Fetch binary data from URL. Returns bytes."""
        raise NotImplementedError

    def get_cost_per_kb(self):
        """Get cost per KB in USD (0 for free connections)."""
        return 0.0

    def get_bandwidth(self):
        """Get approximate bandwidth category: 'high', 'medium', 'low', 'very_low'."""
        return "medium"

    def get_signal_strength(self):
        """Get signal strength 0-100, or None if unavailable."""
        return None


class WiFiTransport(Transport):
    """WiFi transport using standard MicroPython network module."""

    def connect(self):
        """Connect to WiFi."""
        if not MICROPYTHON:
            # CPython mock for testing
            self.connected = True
            return True

        try:
            import network  # type: ignore

            ssid = self.config.get("wifi_ssid") or self.config.get("ssid")
            password = self.config.get("wifi_password") or self.config.get("password")

            if not ssid:
                raise ConnectivityError("WiFi SSID not configured")

            sta = network.WLAN(network.STA_IF)
            sta.active(True)

            # Power management
            try:
                sta.config(pm=0xA11140)
            except Exception:
                pass

            if not sta.isconnected():
                if password:
                    sta.connect(ssid, password)
                else:
                    sta.connect(ssid, "")

                # Wait for connection with timeout
                import time
                timeout = int(self.config.get("wifi_timeout_sec", 20))
                start = time.time()
                while not sta.isconnected() and (time.time() - start) < timeout:
                    time.sleep(0.5)

            self.connected = sta.isconnected()
            if self.connected:
                self._sta = sta
            return self.connected

        except Exception as e:
            raise ConnectivityError(f"WiFi connection failed: {e}")

    def disconnect(self):
        """Disconnect WiFi."""
        if MICROPYTHON and hasattr(self, "_sta"):
            self._sta.disconnect()
        self.connected = False

    def fetch_json(self, url):
        """Fetch JSON using HTTP."""
        if MICROPYTHON:
            import urequests as requests  # type: ignore
        else:
            import requests  # type: ignore

        r = requests.get(url)
        try:
            return r.json()
        finally:
            r.close()

    def fetch_binary(self, url, max_size=None):
        """Fetch binary data."""
        if MICROPYTHON:
            import urequests as requests  # type: ignore
        else:
            import requests  # type: ignore

        r = requests.get(url)
        try:
            data = r.content if hasattr(r, "content") else r.read()
            if max_size and len(data) > max_size:
                raise ConnectivityError(f"Data exceeds max size: {len(data)} > {max_size}")
            return data
        finally:
            r.close()

    def get_bandwidth(self):
        return "high"

    def get_signal_strength(self):
        """Get WiFi RSSI."""
        if not MICROPYTHON or not hasattr(self, "_sta"):
            return None
        try:
            rssi = self._sta.status("rssi")
            if isinstance(rssi, int):
                # Convert RSSI to 0-100 scale
                # -30 dBm (excellent) = 100, -90 dBm (poor) = 0
                return max(0, min(100, int((rssi + 90) * 100 / 60)))
        except Exception:
            return None


class LoRaTransport(Transport):
    """LoRa/LoRaWAN transport for long-range, low-bandwidth communication."""

    def connect(self):
        """Initialize LoRa module."""
        if not MICROPYTHON:
            # CPython mock
            self.connected = True
            return True

        try:
            # This would require a LoRa library like sx127x or similar
            # Example for SX1276/SX1278 modules
            from machine import Pin, SPI  # type: ignore

            # LoRa configuration
            spi_pins = self.config.get("lora_spi_pins", (18, 19, 16))  # SCK, MOSI, MISO
            cs_pin = self.config.get("lora_cs_pin", 17)
            rst_pin = self.config.get("lora_rst_pin", 20)
            freq = self.config.get("lora_freq", 915000000)  # US frequency

            # Initialize SPI
            spi = SPI(
                0,
                baudrate=10000000,
                polarity=0,
                phase=0,
                sck=Pin(spi_pins[0]),
                mosi=Pin(spi_pins[1]),
                miso=Pin(spi_pins[2]),
            )

            # This is a placeholder - actual LoRa initialization would depend on library
            # For production, use a proper LoRa library
            self._lora_spi = spi
            self._lora_cs = Pin(cs_pin, Pin.OUT)
            self._lora_rst = Pin(rst_pin, Pin.OUT)

            self.connected = True
            return True

        except Exception as e:
            raise ConnectivityError(f"LoRa initialization failed: {e}")

    def disconnect(self):
        """Disconnect LoRa."""
        self.connected = False

    def fetch_json(self, url):
        """
        LoRa cannot directly fetch HTTP URLs.
        This would require a custom protocol where the device sends a request packet
        and waits for the server to respond via LoRa gateway.
        """
        raise ConnectivityError("LoRa does not support direct HTTP - use for metadata only")

    def fetch_binary(self, url, max_size=None):
        """LoRa has very limited payload size - not suitable for file downloads."""
        raise ConnectivityError("LoRa not suitable for file downloads")

    def get_cost_per_kb(self):
        return 0.0  # LoRa is typically free (no airtime charges)

    def get_bandwidth(self):
        return "very_low"  # LoRa is 0.3-50 kbps

    def send_metadata(self, data):
        """
        Send small metadata packet via LoRa.
        Useful for triggering device to check for updates via WiFi/Cellular.
        """
        # This is a placeholder for actual LoRa transmission
        # Would require proper LoRa protocol implementation
        pass


class CellularTransport(Transport):
    """Cellular transport (NB-IoT, LTE-M, 2G/3G/4G)."""

    def connect(self):
        """Connect to cellular network."""
        if not MICROPYTHON:
            # CPython mock
            self.connected = True
            return True

        try:
            # This would depend on the cellular modem being used
            # Common modules: SIM800, SIM7000, SIM7600, etc.
            # Example for AT command based modems

            from machine import UART, Pin  # type: ignore
            import time

            # Cellular modem configuration
            uart_id = self.config.get("cellular_uart", 1)
            tx_pin = self.config.get("cellular_tx_pin", 4)
            rx_pin = self.config.get("cellular_rx_pin", 5)
            baud = self.config.get("cellular_baud", 115200)

            apn = self.config.get("cellular_apn", "")
            if not apn:
                raise ConnectivityError("Cellular APN not configured")

            # Initialize UART
            uart = UART(
                uart_id,
                baudrate=baud,
                tx=Pin(tx_pin),
                rx=Pin(rx_pin),
                timeout=1000,
            )

            # Basic AT commands to initialize modem
            def send_at(cmd, wait_ms=1000):
                uart.write(cmd + "\r\n")
                time.sleep_ms(wait_ms)
                response = uart.read()
                return response.decode() if response else ""

            # Check modem
            if "OK" not in send_at("AT"):
                raise ConnectivityError("Modem not responding")

            # Configure APN
            send_at(f'AT+CGDCONT=1,"IP","{apn}"')

            # Attach to network
            send_at("AT+CGATT=1", 5000)

            # Activate PDP context
            send_at("AT+CGACT=1,1", 3000)

            self._uart = uart
            self.connected = True
            return True

        except Exception as e:
            raise ConnectivityError(f"Cellular connection failed: {e}")

    def disconnect(self):
        """Disconnect cellular."""
        if hasattr(self, "_uart"):
            self._uart.write("AT+CGATT=0\r\n")
        self.connected = False

    def fetch_json(self, url):
        """Fetch JSON via HTTP over cellular."""
        # This would require implementing HTTP over AT commands
        # or using a modem with built-in TCP/IP stack
        raise NotImplementedError("HTTP over cellular requires modem-specific implementation")

    def fetch_binary(self, url, max_size=None):
        """Fetch binary data via cellular."""
        raise NotImplementedError("HTTP over cellular requires modem-specific implementation")

    def get_cost_per_kb(self):
        """Get cellular data cost."""
        # Typical NB-IoT/LTE-M costs range from $0.10 to $1.00 per MB
        return float(self.config.get("cellular_cost_per_mb", 0.50)) / 1024

    def get_bandwidth(self):
        """Cellular bandwidth varies by technology."""
        tech = self.config.get("cellular_tech", "nbiot")
        if tech == "nbiot":
            return "low"  # ~20-100 kbps
        elif tech == "lte-m":
            return "medium"  # ~200 kbps - 1 Mbps
        else:
            return "high"  # 2G/3G/4G


class ConnectivityManager:
    """Manages multiple network transports with intelligent fallback."""

    # Transport priority (lower = higher priority)
    PRIORITY = {
        "wifi": 1,
        "cellular": 2,
        "lora": 3,
    }

    def __init__(self, config):
        self.config = config
        self.transports = {}
        self.active_transport = None

        # Initialize enabled transports
        if config.get("wifi_enabled", True):
            self.transports["wifi"] = WiFiTransport(config)

        if config.get("cellular_enabled", False):
            self.transports["cellular"] = CellularTransport(config)

        if config.get("lora_enabled", False):
            self.transports["lora"] = LoRaTransport(config)

    def connect_best_available(self):
        """
        Try to connect using the best available transport.
        Returns (transport_name, transport_obj) or raises ConnectivityError.
        """
        # Sort by priority
        sorted_transports = sorted(
            self.transports.items(), key=lambda x: self.PRIORITY.get(x[0], 99)
        )

        last_error = None
        for name, transport in sorted_transports:
            try:
                print(f"[Connectivity] Trying {name}...")
                if transport.connect():
                    self.active_transport = (name, transport)
                    print(f"[Connectivity] Connected via {name}")
                    return self.active_transport
            except Exception as e:
                print(f"[Connectivity] {name} failed: {e}")
                last_error = e
                continue

        raise ConnectivityError(f"All connectivity options failed. Last error: {last_error}")

    def get_active_transport(self):
        """Get currently active transport."""
        if not self.active_transport:
            raise ConnectivityError("No active connection")
        return self.active_transport

    def disconnect(self):
        """Disconnect active transport."""
        if self.active_transport:
            name, transport = self.active_transport
            transport.disconnect()
            self.active_transport = None

    def get_signal_quality(self):
        """Get signal quality of active connection."""
        if not self.active_transport:
            return None
        return self.active_transport[1].get_signal_strength()

    def estimate_update_cost(self, size_bytes):
        """Estimate cost of update in USD."""
        if not self.active_transport:
            return 0.0
        transport = self.active_transport[1]
        cost_per_kb = transport.get_cost_per_kb()
        return (size_bytes / 1024) * cost_per_kb

    def should_use_delta(self):
        """Determine if delta updates should be used based on active transport."""
        if not self.active_transport:
            return True  # Default to delta if available

        bandwidth = self.active_transport[1].get_bandwidth()
        # Use delta for low bandwidth or costly connections
        return bandwidth in ("low", "very_low") or self.active_transport[1].get_cost_per_kb() > 0
