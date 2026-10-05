# Pwnagotchi — Improvement TODO

Items that need agent / plugin work, including the consumer side of new
WiFiCapC features. Cross-references to WiFiCapC's own list are tagged
`(WiFiCapC TODO-XYZ)`.

## Design ideas (not yet TODOs)

- [WiFiCapC docs/IDEAS/iface-and-driver-health.md](https://github.com/Asif-Iqbal-Gazi/WiFiCapC/blob/main/docs/IDEAS/iface-and-driver-health.md)
  — architecture for brcmfmac wedge detection and external
  recovery. Touches `fix_services.py` (Phase 3 — drop journalctl
  polling, subscribe to the new `iface.unhealthy` IPC event) and
  the pwnagotchi-image launcher (Phase 4 — eventually retire
  `wlan0mon` if/when in-place mode switch works).

## Tri-mode + gamification (M* — see docs/MODES.md)

Three runtime-selectable modes — **Manual** 😴 / **Agent** 🐍 / **Engine** ⚡ —
so the agent-driven (Python) and daemon-driven (C `--auto`) attack paths both
exist and can be A/B'd (handshakes/hr, CPU, battery, stability). Manual = no
attack; Agent = agent drives recon/hop/attack (daemon `auto_stop`); Engine =
daemon self-drives (`--auto`, `set_attack` on).

### M1 — modes.py strategy refactor + live 3-way switch
- [x] `pwnagotchi/modes.py`: Mode base + Manual/Agent/Engine (`enter/tick/leave`);
      collapse cli.py do_manual/do_auto into one mode-driven loop. Live switch
      (reconfigure daemon via set_attack/auto_start/auto_stop + swap loop),
      persisted via `main.mode` + `/etc/pwnagotchi/.mode`. Re-enabled the
      Agent-drive path. Needs WiFiCapC AU7. ✅ v3.1.0
### M2 — config schema + docs
- [x] `main.mode="engine"` default; grouped Agent-only attack knobs; dropped
      mesh keys. New `docs/MODES.md` (architecture + A/B method). README. ✅ v3.1.0
### M3 — retire relics
- [x] Removed pwngrid/peers/**bonding**/advertise everywhere (automata, epoch,
      voice, agent, log, config) — no mesh in this build. Retired **auto-tune**
      and **fix_services** plugins. Dropped the usb0 `is_auto_mode` heuristic. ✅ v3.1.0
### M4 — per-mode metrics (the A/B data)
- [x] `modestats.py`: per-mode time, catches, catches/hr, avg CPU% persisted to
      a throttled JSON (off the log zram); `agent.mode_stats()`. ✅ v3.1.0
      - [ ] battery delta + restart count (needs a battery source) — deferred.
### M5 — gamified "hunter" display
- [x] Mode tag (MAN/AGT/ENG), PWND score (session/all-time), on-screen **A/B
      panel** (`E# A# M#` per-mode tally), e-ink-safe. Moods rebuilt off the
      hunt (epoch activity), not peers. Classic faces kept (per request). ✅ v3.1.0
      - [ ] streak/combo + milestone toasts + richer web dashboard — deferred to
            the "full web dashboard later" scope.
### M6 — web UI mode selector
- [x] Live Manual/Agent/Engine selector in the web UI (`POST /mode/<name>`). ✅ v3.1.0

## Daemon-integration items

### TODO-D1 — Vendor strings in the UI ✅ v3.0.13 (assoc/deauth lines)
- [x] WiFiCapC Q1 (v0.6.17) now emits a real `vendor` in `ap.new`/`sta.new`.
      Added `_label(name, mac)` in `agent.py`; the association/deauth log +
      status lines render "Name (MAC)" — AP SSID or client OUI vendor — and
      fall back to the bare MAC when no name is known (unknown OUI, or a
      randomized/hidden address). e.g. `deauthing Apple (aa:bb:…) from
      HomeWiFi (11:22:…)`.
- [ ] (optional polish) also surface `vendor` as its own column in the web
      UI AP/STA tables. Files: `pwnagotchi/ui/web/*`.

### TODO-D2 — Handle pcap → pcapng path migration
- [ ] When WiFiCapC switches from `.pcap` to `.pcapng` (WiFiCapC
      TODO-Q2), the agent has to:
  - accept `.pcapng` paths in `data["file"]` from `handshake.done`
  - the wpa-sec sibling-resolution logic in `wpa-sec.py` needs to
    look for `.pcapng` first, fall back to `.pcap` for legacy files
  - `gps.py`'s sidecar is already extension-agnostic via
    `os.path.splitext`, but verify nothing else hardcodes `.pcap`.
- Files: `pwnagotchi/plugins/default/wpa-sec.py`,
  `pwnagotchi/plugins/default/gps.py` (verify), `pwnagotchi/agent.py`
  (only logging).

### TODO-D3 — Surface daemon stats in the agent UI ✅ v3.0.19
- [x] Once WiFiCapC's `stats` reply expands (WiFiCapC TODO-Q4) to
      include `current_channel`, `hopping`, `attack_active`,
      `iface_mode`, the agent can poll `stats` (e.g. once per epoch)
      and reflect it in `_view`. Today the UI infers channel from
      our last `set_channel` echo, which goes stale during hopping.
- Files: `pwnagotchi/agent.py`, ui components.

### TODO-D4 — Call `delete_handshake` after successful wpa-sec upload ✅ v3.0.8 (b9c1f13d)
- [x] Opt-in via `main.plugins.wpa-sec.delete_after_upload` (default
      off — users running offline hashcat against the .22000 want
      the files to stick around). Sends `delete_handshake` to the
      daemon and drops the local sqlite row.
- Files: `pwnagotchi/plugins/default/wpa-sec.py`.

### TODO-D5 — Persistent recon table reload ✅ v3.0.19 (verified)
- [x] When WiFiCapC starts persisting AP/STA state across restarts
      (WiFiCapC TODO-R1), the agent needs to handle the wave of
      `ap.new`/`sta.new` events that arrive immediately after
      reconnect. Today reconnect already works (we emit `ap.new`
      from the daemon for everything in the table on subscribe);
      just verify nothing expects "fresh" implies "first-ever".
- Files: `pwnagotchi/agent.py`, `pwnagotchi/wificapc.py`.

### TODO-D6 — Subscribe to only the events we use ✅ v3.0.19
- [x] When WiFiCapC implements per-client subscribe/unsubscribe
      (WiFiCapC TODO-X4), narrow the agent's subscriptions to
      `ap.new`, `ap.lost`, `sta.new`, `sta.lost`, `handshake.done`.
      Today we receive every `iface.channel` tick at 250 ms hop
      intervals (4 events/sec we don't act on).
- Files: `pwnagotchi/wificapc.py`, `pwnagotchi/agent.py`.

## Agent-only items (no daemon dependency)

### TODO-A1 — Confirm pwnagotchi self-update path ✅ v3.0.19
- [x] Resolve the `TODO(pwnagotchi self-update)` left in
      `auto-update.py::install_source_archive`: read the venv path
      from config (don't hardcode `/opt/.pwn`), validate the
      package name against `pyproject.toml` before pip-installing.
      Today both assumptions hold for the standard image build but
      break on custom layouts.
- Files: `pwnagotchi/plugins/default/auto-update.py`.

### TODO-A2 — Tame `_history` growth ✅ v3.0.8 (b9c1f13d)
- [x] `_history` shape changed from `{who: int}` to
      `{who: [count, last_seen_ts]}`. New `_evict_history` runs on
      every 5s stats tick and drops entries older than
      `personality.history_ttl` (default `4 * max(ap_ttl, sta_ttl)`).
      Backward-compat: int entries from old recovery files upgrade
      to `[int, now]` on first touch.
- Files: `pwnagotchi/agent.py`.

### TODO-A3 — Decouple `wlan0mon` from defaults
- [ ] `defaults.toml` ships `iface = "wlan0mon"` and the launcher
      script creates a sibling monitor interface via `iw phy ...
      interface add`. With WiFiCapC the daemon can put `wlan0`
      itself into monitor mode — a single iface is enough.
      Switch the default to `wlan0` and simplify
      `stage3/04-patches/files/pwnlib::start_monitor_interface`.
- Files: `pwnagotchi/defaults.toml`,
  `stage3/04-patches/files/pwnlib`, `wificapc-launcher`.

### TODO-A4 — Image build: investigate stage failures past
                 dependencies_check
- [ ] The pi-gen image-64bit workflow now clears the `bc`-missing
      hurdle. Watch the next runs; expected next failures are
      stage3/02-nexmon (kali .deb fetch), stage3/03-pwnagotchi
      (qemu pip install of compiled deps), or disk-space exhaust
      mid-stage. The new `pi-gen-logs` artifact captures per-stage
      `build.log` so we can iterate.
- Files: `.github/workflows/image-64bit.yml`,
  `stage3/02-nexmon/01-run-chroot.sh`,
  `stage3/03-pwnagotchi/01-run-chroot.sh`.

### TODO-A5 — Pin pwnagotchi clone in stage3/03-pwnagotchi ✅ v3.0.8 (b9c1f13d)
- [x] `PWNAGOTCHI_TAG` env var pinned (currently `v3.0.8`).
      `--depth 1 --branch`, retry-clone 3x with backoff, and
      assert `pwnagotchi` is on `$PATH` after the venv install.
      Mirrors the `stage3/01-wificapc/01-run-chroot.sh` pattern.
- Files: `stage3/03-pwnagotchi/01-run-chroot.sh`.

### TODO-A6 — Event-driven internet detection + reliable uploads ✅ v3.0.18 (07c1e2da)
- [x] Internet-reachability monitor now starts in **manual mode** too
      (`start_internet_monitor()` called from both `cli.do_manual_mode`
      and the auto path) — previously manual mode never probed, so a
      USB/BT-tethered pi left in manual never uploaded.
- [x] Netlink watcher (`RTMGRP_LINK|IPV4_IFADDR|IPV6_IFADDR`) triggers an
      immediate probe on interface/address change; periodic poll relaxed
      to a 120s safety net for silent far-side drops.
- [x] wpa-sec `_drain_uploads()` runs on the event, on a fresh capture,
      and on a 60s background loop (works in manual mode) — fixes
      transition-only uploads that left captures queued forever.
- Files: `pwnagotchi/agent.py`, `pwnagotchi/cli.py`,
      `pwnagotchi/plugins/default/wpa-sec.py`.

---

## How to use this file

- New session picks an item, opens a branch, ships a PR.
- When an item lands, mark `[x]` and link the PR. When a whole
  section's items all land, drop the section.
- Cross-repo items (`TODO-D*`) should land in WiFiCapC first, then
  the agent-side change here.
