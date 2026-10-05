"""M4: per-mode metrics for the Manual/Agent/Engine A/B.

Accumulates, per mode, the numbers that let you compare the Python-driven
(Agent 🐍) path against the C-engine-driven (Engine ⚡) path head to head:
wall-clock time spent, handshakes caught, catch rate, average CPU load,
battery drain (when a battery is present), and how many times the unit booted
into each mode (a stability signal). Persisted to a small JSON on the SD card
— deliberately NOT under /etc/pwnagotchi/log (that's a ~90 MB zram we must not
thrash) — so the tally survives restarts and mode switches.

Writes are throttled so an epoch tick every few seconds doesn't hammer the
card; catches, boots and new records flush immediately since they're rare and
the ones you don't want to lose.

Reserved top-level keys (not modes) are prefixed with "__" and skipped when
summarizing — currently just "__best_session__", the all-time single-session
catch record, for the gamified "new record!" toast.
"""
import json
import os
import time
import logging
import threading

DEFAULT_PATH = "/etc/pwnagotchi/mode-stats.json"
_SAVE_EVERY = 30.0  # seconds between throttled saves
_BEST_KEY = "__best_session__"


class ModeStats:
    def __init__(self, path=DEFAULT_PATH):
        self._path = path
        self._lock = threading.Lock()
        self._last_save = 0.0
        # battery-drain tracking is incremental: we only credit a mode for a
        # drop while it was the active mode, and ignore charging / gaps.
        self._last_bat = None
        self._last_bat_mode = None
        self._data = self._load()

    @staticmethod
    def _blank():
        return {"secs": 0.0, "catches": 0, "epochs": 0,
                "cpu_sum": 0.0, "cpu_n": 0, "enters": 0,
                "boots": 0, "bat_drain": 0.0, "bat_secs": 0.0}

    def _load(self):
        try:
            with open(self._path) as f:
                d = json.load(f)
            if isinstance(d, dict):
                return d
        except FileNotFoundError:
            pass
        except Exception as e:
            logging.warning("modestats: could not load %s: %s", self._path, e)
        return {}

    def _save(self, force=False):
        now = time.time()
        if not force and (now - self._last_save) < _SAVE_EVERY:
            return
        self._last_save = now
        try:
            tmp = self._path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(self._data, f)
            os.replace(tmp, self._path)  # atomic
        except Exception as e:
            logging.warning("modestats: could not save %s: %s", self._path, e)

    def _slot(self, mode):
        slot = self._data.get(mode)
        if slot is None:
            slot = self._blank()
            self._data[mode] = slot
        else:
            # tolerate a file written by an older build missing keys
            for k, v in self._blank().items():
                slot.setdefault(k, v)
        return slot

    # ---- recording ----

    def on_enter(self, mode):
        if not mode:
            return
        with self._lock:
            self._slot(mode)["enters"] += 1
            self._save(force=True)

    def on_boot(self, mode):
        """Count a (re)start into this mode — the stability signal: lots of
        boots for little time in a mode means it's crash-looping."""
        if not mode:
            return
        with self._lock:
            self._slot(mode)["boots"] += 1
            self._save(force=True)

    def on_epoch(self, mode, duration_secs, cpu_load, battery=None):
        if not mode:
            return
        with self._lock:
            s = self._slot(mode)
            dur = max(0.0, float(duration_secs or 0))
            s["secs"] += dur
            s["epochs"] += 1
            if cpu_load:
                s["cpu_sum"] += float(cpu_load)
                s["cpu_n"] += 1
            # battery: only a same-mode, non-charging drop counts
            if battery is not None and battery > 0:
                if (self._last_bat is not None
                        and self._last_bat_mode == mode
                        and battery <= self._last_bat):
                    s["bat_drain"] += (self._last_bat - battery)
                    s["bat_secs"] += dur
                self._last_bat = battery
                self._last_bat_mode = mode
            self._save()

    def on_catch(self, mode, n=1):
        if not mode or n <= 0:
            return
        with self._lock:
            self._slot(mode)["catches"] += n
            self._save(force=True)

    # ---- gamification: the all-time single-session record ----

    def best_session(self):
        try:
            return int(self._data.get(_BEST_KEY, 0))
        except Exception:
            return 0

    def record_session(self, n):
        """Store n as the new best if it beats the record. Returns True on a
        new record (so the caller can throw a celebratory toast)."""
        with self._lock:
            if n > int(self._data.get(_BEST_KEY, 0)):
                self._data[_BEST_KEY] = int(n)
                self._save(force=True)
                return True
        return False

    # ---- reporting ----

    def summary(self):
        """Per-mode dict: {mode: {secs, catches, rate_per_hr, avg_cpu_pct,
        epochs, boots, bat_drain_per_hr}} — ready for the display + web UI."""
        out = {}
        with self._lock:
            for mode, s in self._data.items():
                if mode.startswith("__"):
                    continue  # reserved meta key, not a mode
                secs = s.get("secs", 0.0)
                catches = s.get("catches", 0)
                hrs = secs / 3600.0
                rate = (catches / hrs) if hrs > 0.01 else 0.0
                cpu_n = s.get("cpu_n", 0)
                avg_cpu = (100.0 * s.get("cpu_sum", 0.0) / cpu_n) if cpu_n else 0.0
                bat_secs = s.get("bat_secs", 0.0)
                bat_hrs = bat_secs / 3600.0
                bat_rate = (s.get("bat_drain", 0.0) / bat_hrs) if bat_hrs > 0.01 else 0.0
                out[mode] = {"secs": secs, "catches": catches,
                             "rate_per_hr": rate, "avg_cpu_pct": avg_cpu,
                             "epochs": s.get("epochs", 0),
                             "boots": s.get("boots", 0),
                             "bat_drain_per_hr": bat_rate}
        return out

    def reset(self):
        with self._lock:
            self._data = {}
            self._save(force=True)
