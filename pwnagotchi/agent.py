import json
import logging
import os
import socket
import threading
import time

import pwnagotchi
import pwnagotchi.plugins as plugins
import pwnagotchi.utils as utils
from pwnagotchi.automata import Automata
from pwnagotchi.log import LastSession
from pwnagotchi.ui.web.server import Server
from pwnagotchi.wificapc import WificapcClient

RECOVERY_DATA_FILE = "/root/.pwnagotchi-recovery"


class Agent(Automata):
    def __init__(self, view, config, keypair):
        Automata.__init__(self, config, view)

        self._keypair = keypair
        self._started_at = time.time()
        self._current_channel = 0
        self._tot_aps = 0
        self._aps_on_channel = 0
        self._supported_channels = utils.iface_channels(config["main"]["iface"])
        self._view = view
        self._view.set_agent(self)
        self._web_ui = Server(self, config["ui"])

        # AP/STA tables populated by wificapc events on the reader thread
        # and read from the main thread — guard with a single lock.
        self._tables_lock = threading.Lock()
        self._aps = {}  # bssid -> ap dict
        self._stas = {}  # mac -> sta dict
        self._access_points = []

        # Internet-reachability probe: periodic DNS check that drives the
        # plugins.on('internet_available') / plugins.on('internet_unavailable')
        # transitions. Plugins should subscribe to those events instead of
        # firing off network calls speculatively (the bt-tether per-epoch
        # fire used to be the only signal and it pinged regardless of
        # whether DNS actually worked, which is what burned wpa-sec uploads).
        self._internet_lock = threading.Lock()
        self._internet_ok = None  # None = first probe pending

        # Empty peers dict kept for automata mood compatibility (no mesh)
        self._peers = {}

        self._last_pwnd = None
        self._history = {}
        self._handshakes = {}
        self.last_session = LastSession(self._config)
        self.mode = "auto"

        hs_dir = config["wificapc"]["handshakes"]
        os.makedirs(hs_dir, exist_ok=True)
        self._handshake_dir = hs_dir

        self._wificapc = WificapcClient(
            config["wificapc"].get("socket", "/run/wificapc.sock")
        )

        logging.info(
            "%s@%s (v%s)", pwnagotchi.name(), self.fingerprint(), pwnagotchi.__version__
        )
        for _, plugin in plugins.loaded.items():
            logging.debug(
                "plugin '%s' v%s", plugin.__class__.__name__, plugin.__version__
            )

    def fingerprint(self):
        return self._keypair.fingerprint

    def config(self):
        return self._config

    def view(self):
        return self._view

    def supported_channels(self):
        return self._supported_channels

    def run(self, command, verbose_errors=True):
        logging.warning(
            "agent.run('%s') called but bettercap is no longer used", command
        )
        return {"success": False}

    # ---- wificapc event handlers ----

    def _on_ap_new(self, event, data):
        bssid = data.get("bssid", "").lower()
        if not bssid:
            return
        with self._tables_lock:
            existing_clients = self._aps.get(bssid, {}).get("clients", [])
            self._aps[bssid] = {
                "mac": bssid,
                "hostname": data.get("ssid", ""),
                "channel": data.get("channel", 0),
                "rssi": data.get("rssi", 0),
                "vendor": data.get("vendor", ""),
                "encryption": "WPA2",
                "clients": existing_clients,
            }
        self._rebuild_access_points()

    def _on_ap_lost(self, event, data):
        bssid = data.get("bssid", "").lower()
        with self._tables_lock:
            removed = self._aps.pop(bssid, None)
        if removed is not None:
            self._rebuild_access_points()

    def _on_sta_new(self, event, data):
        mac = data.get("mac", "").lower()
        ap_bssid = data.get("ap_bssid", "").lower()
        if not mac:
            return
        sta = {"mac": mac, "vendor": data.get("vendor", "")}
        with self._tables_lock:
            self._stas[mac] = sta
            if ap_bssid and ap_bssid in self._aps:
                clients = self._aps[ap_bssid]["clients"]
                if not any(c["mac"] == mac for c in clients):
                    clients.append(sta)
        self._rebuild_access_points()

    def _on_sta_lost(self, event, data):
        mac = data.get("mac", "").lower()
        with self._tables_lock:
            self._stas.pop(mac, None)
            for ap in self._aps.values():
                ap["clients"] = [c for c in ap["clients"] if c["mac"] != mac]
        self._rebuild_access_points()

    def _on_handshake_done(self, event, data):
        ap_bssid = data.get("ap_bssid", "").lower()
        sta_mac = data.get("sta_mac", "").lower()

        # The daemon emits two paths after fclose (race-free):
        #   data["file"]      -> per-pair .pcap (cached beacon + EAPOL frames),
        #                        what wpa-sec.org accepts
        #   data["hash22000"] -> hashcat .22000 for offline cracking
        # Either may be absent for an incomplete handshake (e.g. only M3
        # captured). Skip silently in that case — it's not an error, just
        # an unfinished pair.
        pcap_file = data.get("file")
        hash22000 = data.get("hash22000")
        if not pcap_file and not hash22000:
            logging.debug("handshake.done with no usable file (incomplete pair)")
            return

        # Prefer the pcap for plugin consumers (wpa-sec uploads it directly,
        # gps writes a sibling .gps.json next to it). Fall back to .22000
        # only if the daemon didn't write the pcap.
        filename = pcap_file or hash22000
        if not os.path.exists(filename):
            logging.warning("handshake.done file vanished: %s", filename)
            return
        key = "%s -> %s" % (sta_mac, ap_bssid)

        if key not in self._handshakes:
            self._handshakes[key] = data
            with self._tables_lock:
                ap = self._aps.get(ap_bssid)
                sta = self._stas.get(sta_mac, {"mac": sta_mac, "vendor": ""})

            if ap is None:
                # Synthesize a minimal AP dict so plugins always receive
                # the same shape. Without this, on_handshake handlers that
                # do access_point["mac"] / ["hostname"] / etc. would crash
                # (or silently except-out) on the unknown-AP path.
                ap = {
                    "mac": ap_bssid,
                    "hostname": "",
                    "channel": data.get("channel", 0),
                    "rssi": data.get("rssi", 0),
                    "vendor": "",
                    "encryption": "WPA2",
                    "clients": [],
                }
                logging.warning("!!! captured new handshake: %s !!!", key)
                self._last_pwnd = ap_bssid
            else:
                self._last_pwnd = (
                    ap["hostname"]
                    if ap["hostname"] not in ("", "<hidden>")
                    else ap_bssid
                )
                logging.warning(
                    "!!! captured new handshake on channel %d, %d dBm: %s (%s) -> %s [%s (%s)] !!!",
                    ap["channel"],
                    ap["rssi"],
                    sta["mac"],
                    sta["vendor"],
                    ap["hostname"],
                    ap["mac"],
                    ap["vendor"],
                )
            plugins.on("handshake", self, filename, ap, sta)

            self._update_handshakes(1)
        else:
            self._update_handshakes(0)

    def _rebuild_access_points(self):
        whitelist = self._config["main"]["whitelist"]
        # Snapshot under the lock so concurrent _on_ap_*/_on_sta_* mutations
        # can't trigger 'dictionary changed size during iteration'.
        with self._tables_lock:
            snapshot = list(self._aps.values())
        aps = []
        for ap in snapshot:
            if ap["hostname"] in whitelist:
                continue
            if ap["mac"][:13].lower() in whitelist or ap["mac"].lower() in whitelist:
                continue
            aps.append(ap)
        aps.sort(key=lambda a: a["channel"])
        self._access_points = aps
        plugins.on("wifi_update", self, aps)
        self._epoch.observe(aps, [])

    # ---- startup ----

    def _wait_wificapc(self):
        self._wificapc.connect()

    def _register_events(self):
        self._wificapc.on("ap.new", self._on_ap_new)
        self._wificapc.on("ap.lost", self._on_ap_lost)
        self._wificapc.on("sta.new", self._on_sta_new)
        self._wificapc.on("sta.lost", self._on_sta_lost)
        self._wificapc.on("handshake.done", self._on_handshake_done)

    def start_monitor_mode(self):
        cfg = self._config["wificapc"]
        mon_iface = self._config["main"]["iface"]

        # iface_set is the foundation: without it nothing else makes sense.
        # Retry briefly to absorb transient conditions (e.g. USB Wi-Fi
        # adapter still enumerating, brcmfmac firmware reload in flight),
        # then raise so the agent fails loudly instead of running blind.
        last_err = None
        for attempt in range(5):
            try:
                self._wificapc.cmd("iface_set", name=mon_iface)
                last_err = None
                break
            except Exception as e:
                last_err = e
                logging.warning("wificapc iface_set (attempt %d/5): %s", attempt + 1, e)
                time.sleep(2)
        if last_err is not None:
            raise RuntimeError("wificapc iface_set failed for %s: %s" % (mon_iface, last_err))

        try:
            self._wificapc.cmd("monitor_on")
        except Exception as e:
            logging.warning("wificapc monitor_on: %s", e)

        try:
            self._wificapc.cmd("set_handshake_dir", path=cfg["handshakes"])
        except Exception as e:
            logging.warning("wificapc set_handshake_dir: %s", e)

        # Stealth: toggle MAC randomization on every inject_assoc per
        # [wificapc] mac_rand in config.toml. Default off (matches the
        # daemon's --mac-rand opt-in semantics). Best-effort — older
        # daemons (pre-v0.6.10) reply "unknown command" and we proceed.
        mac_rand = bool(cfg.get("mac_rand", False))
        try:
            self._wificapc.cmd("set_mac_rand", enabled=int(mac_rand))
            logging.info("wificapc mac_rand: %s", "on" if mac_rand else "off")
        except Exception as e:
            logging.warning("wificapc set_mac_rand: %s", e)

        self.push_ttls()

        logging.info("supported channels: %s", self._supported_channels)
        logging.info("handshakes will be collected inside %s", cfg["handshakes"])

    def push_ttls(self):
        """Re-send personality TTLs / min_rssi to wificapc.

        Public so plugins (e.g. auto-tune) that mutate personality at
        runtime can flush the changes through to the daemon — otherwise
        their tuning is agent-side only.
        """
        try:
            self._wificapc.cmd(
                "set_ttls",
                ap_ttl=self._config["personality"]["ap_ttl"],
                sta_ttl=self._config["personality"]["sta_ttl"],
                min_rssi=self._config["personality"]["min_rssi"],
            )
        except Exception as e:
            logging.warning("wificapc set_ttls: %s", e)

    def _on_wificapc_reconnect(self):
        logging.info("wificapc reconnected, re-initializing...")
        self.start_monitor_mode()
        try:
            hop_channels = (
                self._config["personality"]["channels"] or self._supported_channels
            )
            self._wificapc.cmd("recon_start")
            self._wificapc.cmd(
                "hop_start",
                channels=hop_channels,
                interval_ms=self._config["wificapc"].get("hop_interval_ms", 250),
            )
        except Exception as e:
            logging.warning("wificapc re-init after reconnect: %s", e)

    def start(self):
        self._wait_wificapc()
        self._register_events()
        self._wificapc.on_reconnect(self._on_wificapc_reconnect)
        self.set_starting()
        self.start_monitor_mode()
        self._load_recovery_data()
        self.start_session_fetcher()
        self.next_epoch()
        self.set_ready()

    # ---- recon / channel ----

    def recon(self):
        recon_time = self._config["personality"]["recon_time"]
        max_inactive = self._config["personality"]["max_inactive_scale"]
        recon_mul = self._config["personality"]["recon_inactive_multiplier"]
        channels = self._config["personality"]["channels"]

        if self._epoch.inactive_for >= max_inactive:
            recon_time *= recon_mul

        self._view.set("channel", "*")
        self._current_channel = 0

        hop_channels = list(channels) if channels else self._supported_channels

        try:
            self._wificapc.cmd("recon_start")
        except Exception as e:
            logging.warning("wificapc recon_start: %s", e)

        try:
            self._wificapc.cmd(
                "hop_start",
                channels=hop_channels,
                interval_ms=self._config["wificapc"].get("hop_interval_ms", 250),
            )
        except Exception as e:
            logging.warning("wificapc hop_start: %s", e)

        if not channels:
            logging.debug("RECON %ds", recon_time)
        else:
            logging.debug(
                "RECON %ds ON CHANNELS %s", recon_time, ",".join(map(str, channels))
            )

        self.wait_for(recon_time, sleeping=False)

    def set_access_points(self, aps):
        self._access_points = aps
        plugins.on("wifi_update", self, aps)
        self._epoch.observe(aps, [])
        return self._access_points

    def get_access_points(self):
        return self._access_points

    def get_total_aps(self):
        return self._tot_aps

    def get_aps_on_channel(self):
        return self._aps_on_channel

    def get_current_channel(self):
        return self._current_channel

    def get_access_points_by_channel(self):
        channels = self._config["personality"]["channels"]
        grouped = {}
        for ap in self._access_points:
            ch = ap["channel"]
            if channels and ch not in channels:
                continue
            grouped.setdefault(ch, []).append(ap)
        return sorted(grouped.items(), key=lambda kv: len(kv[1]), reverse=True)

    # ---- stats ----

    def _update_uptime(self):
        secs = pwnagotchi.uptime()
        self._view.set("uptime", utils.secs_to_hhmmss(secs))

    def _update_counters(self):
        self._tot_aps = len(self._access_points)
        tot_stas = sum(len(ap["clients"]) for ap in self._access_points)
        if self._current_channel == 0:
            self._view.set("aps", "%d" % self._tot_aps)
            self._view.set("sta", "%d" % tot_stas)
        else:
            self._aps_on_channel = len(
                [
                    ap
                    for ap in self._access_points
                    if ap["channel"] == self._current_channel
                ]
            )
            stas_on_channel = sum(
                len(ap["clients"])
                for ap in self._access_points
                if ap["channel"] == self._current_channel
            )
            self._view.set("aps", "%d (%d)" % (self._aps_on_channel, self._tot_aps))
            self._view.set("sta", "%d (%d)" % (stas_on_channel, tot_stas))

    def _update_handshakes(self, new_shakes=0):
        if new_shakes > 0:
            self._epoch.track(handshake=True, inc=new_shakes)

        tot = utils.total_unique_handshakes(self._config["wificapc"]["handshakes"])
        txt = "%d (%d)" % (len(self._handshakes), tot)
        if self._last_pwnd is not None:
            txt += " [%s]" % self._last_pwnd
        self._view.set("shakes", txt)
        if new_shakes > 0:
            self._view.on_handshakes(new_shakes)

    # ---- internet reachability probe ----

    def _probe_internet(self):
        """Return True iff DNS + a TCP socket out work right now.

        Two-step probe so we catch both flavors of failure:
        1. A DNS resolution against a stable name. This was the failure
           mode we kept hitting on bt-tether: the link was up, the BT
           NAP profile reported connected, but DNS was not actually
           routed — every requests.get(...) blew up with
           NameResolutionError.
        2. A short TCP connect to the resolved address on a port we
           know it serves. Confirms outbound packets actually reach the
           internet (some captive-portal-style links resolve DNS but
           block outbound TCP).

        Default is `cloudflare-dns.com:443` — the host resolves to
        Cloudflare's CDN edge which serves HTTPS (and DoH) on 443.
        We previously defaulted to port 53 which only works against a
        literal DNS-resolver IP like 1.1.1.1; resolving the *name*
        cloudflare-dns.com lands on the CDN IP where 53 is closed,
        so the probe always returned False. If a user wants DNS-port
        semantics they can set host=1.1.1.1, port=53.

        Both steps have a 2 s ceiling. `wpa-sec.cracked.potfile`-style
        downstream calls handle their own timeouts on top.
        """
        cfg = self._config.get("main", {})
        host    = cfg.get("internet_probe_host", "cloudflare-dns.com")
        port    = int(cfg.get("internet_probe_port", 443))
        timeout = float(cfg.get("internet_probe_timeout", 2.0))
        try:
            socket.setdefaulttimeout(timeout)
            ip = socket.gethostbyname(host)
        except OSError:
            return False
        try:
            with socket.create_connection((ip, port), timeout=timeout):
                return True
        except OSError:
            return False
        finally:
            socket.setdefaulttimeout(None)

    def _internet_loop(self):
        # Probe interval: short enough to react to a phone toggling data
        # off, long enough to not hammer the DNS resolver. 30s is a
        # reasonable default; user can tune via config if needed.
        interval = int(self._config.get("main", {}).get("internet_probe_interval", 30))
        while True:
            ok = self._probe_internet()
            transitioned = False
            with self._internet_lock:
                prev = self._internet_ok
                if ok != prev:
                    self._internet_ok = ok
                    transitioned = True
            if transitioned:
                event = "internet_available" if ok else "internet_unavailable"
                logging.info("agent: %s (probe -> %s)", event, ok)
                try:
                    plugins.on(event, self)
                except Exception:
                    logging.exception("agent: %s plugin handler error", event)
            time.sleep(interval)

    def is_internet_reachable(self):
        """Cheap accessor for plugins that want to read the cached state
        instead of running their own probe."""
        with self._internet_lock:
            return bool(self._internet_ok)

    def start_session_fetcher(self):
        threading.Thread(
            target=self._fetch_stats, args=(), name="Session Fetcher", daemon=True
        ).start()
        threading.Thread(
            target=self._internet_loop, args=(), name="InternetMonitor", daemon=True
        ).start()

    def _fetch_stats(self):
        while True:
            try:
                self._update_uptime()
            except Exception as err:
                logging.error("[agent:_fetch_stats] update_uptime: %s", repr(err))
            try:
                self._update_counters()
            except Exception as err:
                logging.error("[agent:_fetch_stats] update_counters: %s", repr(err))
            try:
                self._update_handshakes(0)
            except Exception as err:
                logging.error("[agent:_fetch_stats] update_handshakes: %s", repr(err))
            try:
                self._evict_history()
            except Exception as err:
                logging.error("[agent:_fetch_stats] evict_history: %s", repr(err))
            time.sleep(5)

    # ---- recovery ----

    def _reboot(self):
        self.set_rebooting()
        self._save_recovery_data()
        pwnagotchi.reboot()

    def _restart(self, mode="AUTO"):
        self._save_recovery_data()
        pwnagotchi.restart(mode)

    def _save_recovery_data(self):
        logging.warning("writing recovery data to %s ...", RECOVERY_DATA_FILE)
        with open(RECOVERY_DATA_FILE, "w") as fp:
            data = {
                "started_at": self._started_at,
                "epoch": self._epoch.epoch,
                "history": self._history,
                "handshakes": self._handshakes,
                "last_pwnd": self._last_pwnd,
            }
            json.dump(data, fp)

    def _load_recovery_data(self, delete=True, no_exceptions=True):
        try:
            with open(RECOVERY_DATA_FILE, "rt") as fp:
                data = json.load(fp)
                logging.info("found recovery data: %s", data)
                self._started_at = data["started_at"]
                self._epoch.epoch = data["epoch"]
                self._handshakes = data["handshakes"]
                self._history = data["history"]
                self._last_pwnd = data["last_pwnd"]
                if delete:
                    logging.info("deleting %s", RECOVERY_DATA_FILE)
                    os.unlink(RECOVERY_DATA_FILE)
        except:
            if not no_exceptions:
                raise

    # ---- attack actions ----

    def _has_handshake(self, bssid):
        for key in self._handshakes:
            if bssid.lower() in key:
                return True
        return False

    # _history maps "who" (BSSID/MAC) to [interaction_count, last_seen_ts].
    # Without TTL eviction the dict grew unbounded — every AP/STA ever
    # seen, kept forever. _evict_history drops entries older than the
    # configured TTL on every periodic stats tick.

    def _history_ttl_seconds(self):
        # Default to the longer of the two recon TTLs so we don't forget
        # a target the daemon might still be tracking. Optional explicit
        # personality.history_ttl overrides.
        p = self._config["personality"]
        return int(p.get(
            "history_ttl",
            max(p.get("ap_ttl", 120), p.get("sta_ttl", 300)) * 4,
        ))

    def _should_interact(self, who):
        if self._has_handshake(who):
            return False
        now = time.time()
        entry = self._history.get(who)
        if entry is None:
            self._history[who] = [1, now]
            return True
        # Backward-compat: old recovery files stored {who: int}.
        if isinstance(entry, int):
            entry = [entry, now]
        entry[0] += 1
        entry[1] = now
        self._history[who] = entry
        return entry[0] < self._config["personality"]["max_interactions"]

    def _evict_history(self):
        ttl = self._history_ttl_seconds()
        cutoff = time.time() - ttl
        # Build a stale-key list first; iterating-and-mutating a dict
        # is fine here (single-threaded) but the explicit two-pass keeps
        # the intent obvious.
        stale = []
        for who, entry in self._history.items():
            if isinstance(entry, list) and entry[1] < cutoff:
                stale.append(who)
        for who in stale:
            del self._history[who]
        if stale:
            logging.debug("agent: evicted %d stale history entries", len(stale))

    def associate(self, ap, throttle=-1):
        if self.is_stale():
            logging.debug("recon is stale, skipping assoc(%s)", ap["mac"])
            return
        if throttle == -1 and "throttle_a" in self._config["personality"]:
            throttle = self._config["personality"]["throttle_a"]

        if self._config["personality"]["associate"] and self._should_interact(
            ap["mac"]
        ):
            self._view.on_assoc(ap)
            try:
                logging.info(
                    "sending association frame to %s (%s %s) on channel %d [%d clients], %d dBm...",
                    ap["hostname"],
                    ap["mac"],
                    ap["vendor"],
                    ap["channel"],
                    len(ap["clients"]),
                    ap["rssi"],
                )
                self._wificapc.cmd("assoc", bssid=ap["mac"])
                self._epoch.track(assoc=True)
            except Exception as e:
                self._on_error(ap["mac"], e)
            plugins.on("association", self, ap)
            if throttle > 0:
                time.sleep(throttle)
            self._view.on_normal()

    def deauth(self, ap, sta, throttle=-1):
        if self.is_stale():
            logging.debug("recon is stale, skipping deauth(%s)", sta["mac"])
            return
        if throttle == -1 and "throttle_d" in self._config["personality"]:
            throttle = self._config["personality"]["throttle_d"]

        if self._config["personality"]["deauth"] and self._should_interact(sta["mac"]):
            self._view.on_deauth(sta)
            try:
                logging.info(
                    "deauthing %s (%s) from %s (%s %s) on channel %d, %d dBm ...",
                    sta["mac"],
                    sta["vendor"],
                    ap["hostname"],
                    ap["mac"],
                    ap["vendor"],
                    ap["channel"],
                    ap["rssi"],
                )
                self._wificapc.cmd("deauth", bssid=ap["mac"], sta=sta["mac"])
                self._epoch.track(deauth=True)
            except Exception as e:
                self._on_error(sta["mac"], e)
            plugins.on("deauthentication", self, ap, sta)
            if throttle > 0:
                time.sleep(throttle)
            self._view.on_normal()

    def set_channel(self, channel, verbose=True):
        if self.is_stale():
            logging.debug("recon is stale, skipping set_channel(%d)", channel)
            return

        wait = 0
        if self._epoch.did_deauth:
            wait = self._config["personality"]["hop_recon_time"]
        elif self._epoch.did_associate:
            wait = self._config["personality"]["min_recon_time"]

        if channel != self._current_channel:
            if self._current_channel != 0 and wait > 0:
                if verbose:
                    logging.info(
                        "waiting for %ds on channel %d ...", wait, self._current_channel
                    )
                else:
                    logging.debug(
                        "waiting for %ds on channel %d ...", wait, self._current_channel
                    )
                self.wait_for(wait)
            if verbose and self._epoch.any_activity:
                logging.info("CHANNEL %d", channel)
            try:
                self._wificapc.cmd("set_channel", channel=channel)
                self._current_channel = channel
                self._epoch.track(hop=True)
                self._view.set("channel", "%d" % channel)
                plugins.on("channel_hop", self, channel)
            except Exception as e:
                logging.error("Error while setting channel: %s", e)
