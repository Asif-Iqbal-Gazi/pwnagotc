import json
import logging
import os

import pwnagotchi.plugins as plugins
import pwnagotchi.ui.fonts as fonts
from pwnagotchi.ui.components import LabeledValue
from pwnagotchi.ui.view import BLACK


class GPS(plugins.Plugin):
    __author__ = "evilsocket@gmail.com"
    __version__ = "1.0.1"
    __license__ = "GPL3"
    __description__ = "Save GPS coordinates whenever an handshake is captured."

    LINE_SPACING = 10
    LABEL_SPACING = 0

    def __init__(self):
        self.running = False
        self.coordinates = None
        self.options = dict()

    def on_loaded(self):
        logging.info(f"gps plugin loaded for {self.options['device']}")

    def on_ready(self, agent):
        if os.path.exists(self.options["device"]) or ":" in self.options["device"]:
            logging.info(f"GPS device detected: {self.options['device']}")
            self.running = True
        else:
            logging.warning("no GPS detected")

    def _read_gpsd(self):
        import socket as _socket
        device = self.options.get("device", "localhost:2947")
        if ":" in device:
            host, port = device.split(":", 1)
            try:
                s = _socket.create_connection((host, int(port)), timeout=2)
                s.sendall(b'?WATCH={"enable":true,"json":true}\n')
                s.sendall(b'?POLL;\n')
                data = s.recv(4096).decode("utf-8", errors="replace")
                s.close()
                import json as _json
                for line in data.splitlines():
                    try:
                        obj = _json.loads(line)
                        if obj.get("class") == "TPV" and obj.get("lat"):
                            return {
                                "Latitude": obj["lat"],
                                "Longitude": obj["lon"],
                                "Altitude": obj.get("alt", 0.0),
                            }
                    except Exception:
                        pass
            except Exception as e:
                logging.debug("gpsd read error: %s", e)
        return None

    def on_handshake(self, agent, filename, access_point, client_station):
        if self.running:
            self.coordinates = self._read_gpsd()
            # Derive the sidecar by stripping the handshake's extension. The
            # old `replace(".pcap", ".gps.json")` was a no-op on the .22000
            # files wificapc writes — the open/json.dump that followed would
            # then overwrite the hashcat file with JSON.
            base, _ = os.path.splitext(filename)
            gps_filename = base + ".gps.json"

            if self.coordinates and all([
                # avoid 0.000... measurements
                self.coordinates["Latitude"], self.coordinates["Longitude"]
            ]):
                logging.info(f"saving GPS to {gps_filename} ({self.coordinates})")
                with open(gps_filename, "w+t") as fp:
                    json.dump(self.coordinates, fp)
            else:
                logging.info("not saving GPS. Couldn't find location.")

    def on_ui_setup(self, ui):
        try:
            # Configure line_spacing
            line_spacing = int(self.options['linespacing'])
        except Exception:
            # Set default value
            line_spacing = self.LINE_SPACING

        try:
            # Configure position
            pos = self.options['position'].split(',')
            pos = [int(x.strip()) for x in pos]
            lat_pos = (pos[0] + 5, pos[1])
            lon_pos = (pos[0], pos[1] + line_spacing)
            alt_pos = (pos[0] + 5, pos[1] + (2 * line_spacing))
        except Exception:
            # Set default value based on display type
            if ui.is_waveshare_v2():
                lat_pos = (127, 74)
                lon_pos = (122, 84)
                alt_pos = (127, 94)
            elif ui.is_waveshare_v1():
                lat_pos = (130, 70)
                lon_pos = (125, 80)
                alt_pos = (130, 90)
            elif ui.is_inky():
                lat_pos = (127, 60)
                lon_pos = (122, 70)
                alt_pos = (127, 80)
            elif ui.is_waveshare144lcd():
                # guessed values, add tested ones if you can
                lat_pos = (67, 73)
                lon_pos = (62, 83)
                alt_pos = (67, 93)
            elif ui.is_dfrobot_v2():
                lat_pos = (127, 74)
                lon_pos = (122, 84)
                alt_pos = (127, 94)
            elif ui.is_waveshare2in7():
                lat_pos = (6, 120)
                lon_pos = (1, 135)
                alt_pos = (6, 150)
            else:
                # guessed values, add tested ones if you can
                lat_pos = (127, 51)
                lon_pos = (122, 61)
                alt_pos = (127, 71)

        ui.add_element(
            "latitude",
            LabeledValue(
                color=BLACK,
                label="lat:",
                value="-",
                position=lat_pos,
                label_font=fonts.Small,
                text_font=fonts.Small,
                label_spacing=self.LABEL_SPACING,
            ),
        )
        ui.add_element(
            "longitude",
            LabeledValue(
                color=BLACK,
                label="long:",
                value="-",
                position=lon_pos,
                label_font=fonts.Small,
                text_font=fonts.Small,
                label_spacing=self.LABEL_SPACING,
            ),
        )
        ui.add_element(
            "altitude",
            LabeledValue(
                color=BLACK,
                label="alt:",
                value="-",
                position=alt_pos,
                label_font=fonts.Small,
                text_font=fonts.Small,
                label_spacing=self.LABEL_SPACING,
            ),
        )

    def on_unload(self, ui):
        with ui._lock:
            ui.remove_element('latitude')
            ui.remove_element('longitude')
            ui.remove_element('altitude')

    def on_ui_update(self, ui):
        with ui._lock:
            if self.coordinates and all([
                # avoid 0.000... measurements
                self.coordinates["Latitude"], self.coordinates["Longitude"]
            ]):
                # last char is sometimes not completely drawn ¯\_(ツ)_/¯
                # using an ending-whitespace as workaround on each line
                ui.set("latitude", f"{self.coordinates['Latitude']:.4f} ")
                ui.set("longitude", f"{self.coordinates['Longitude']:.4f} ")
                ui.set("altitude", f"{self.coordinates['Altitude']:.1f}m ")
