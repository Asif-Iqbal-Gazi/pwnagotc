"""M4: per-mode metrics for the Manual/Agent/Engine A/B.

Accumulates, per mode, the numbers that let you compare the Python-driven
(Agent 🐍) path against the C-engine-driven (Engine ⚡) path head to head:
wall-clock time spent, handshakes caught, catch rate, and average CPU load.
Persisted to a small JSON on the SD card — deliberately NOT under
/etc/pwnagotchi/log (that's a ~90 MB zram we must not thrash) — so the
tally survives restarts and mode switches.

Writes are throttled so an epoch tick every few seconds doesn't hammer the
card; catches and mode-enters flush immediately since they're rare and the
ones you don't want to lose.
"""
import json
import os
import time
import logging
import threading

DEFAULT_PATH = "/etc/pwnagotchi/mode-stats.json"
_SAVE_EVERY = 30.0  # seconds between throttled saves


class ModeStats:
    def __init__(self, path=DEFAULT_PATH):
        self._path = path
        self._lock = threading.Lock()
        self._last_save = 0.0
        self._data = self._load()

    @staticmethod
    def _blank():
        return {"secs": 0.0, "catches": 0, "epochs": 0,
                "cpu_sum": 0.0, "cpu_n": 0, "enters": 0}

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

    def on_epoch(self, mode, duration_secs, cpu_load):
        if not mode:
            return
        with self._lock:
            s = self._slot(mode)
            s["secs"] += max(0.0, float(duration_secs or 0))
            s["epochs"] += 1
            if cpu_load:
                s["cpu_sum"] += float(cpu_load)
                s["cpu_n"] += 1
            self._save()

    def on_catch(self, mode, n=1):
        if not mode or n <= 0:
            return
        with self._lock:
            self._slot(mode)["catches"] += n
            self._save(force=True)

    # ---- reporting ----

    def summary(self):
        """Per-mode dict: {mode: {secs, catches, rate_per_hr, avg_cpu_pct,
        epochs}} — ready for the display and the web UI."""
        out = {}
        with self._lock:
            for mode, s in self._data.items():
                secs = s.get("secs", 0.0)
                catches = s.get("catches", 0)
                hrs = secs / 3600.0
                rate = (catches / hrs) if hrs > 0.01 else 0.0
                cpu_n = s.get("cpu_n", 0)
                avg_cpu = (100.0 * s.get("cpu_sum", 0.0) / cpu_n) if cpu_n else 0.0
                out[mode] = {"secs": secs, "catches": catches,
                             "rate_per_hr": rate, "avg_cpu_pct": avg_cpu,
                             "epochs": s.get("epochs", 0)}
        return out

    def reset(self):
        with self._lock:
            self._data = {}
            self._save(force=True)
