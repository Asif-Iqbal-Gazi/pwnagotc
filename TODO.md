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

## Daemon-integration items

### TODO-D1 — Vendor strings in the UI
- [ ] Once WiFiCapC populates `ap.new` / `sta.new` events with real
      `vendor` strings (WiFiCapC TODO-Q1), surface them next to the
      MAC in the web UI and the e-paper display.
- Today: `_on_ap_new` already reads `data.get("vendor", "")` so the
  field flows through; the UI just shows blank because the daemon
  emits `""`.
- Files: `pwnagotchi/ui/components/*`, possibly the web UI templates.

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

### TODO-D3 — Surface daemon stats in the agent UI
- [ ] Once WiFiCapC's `stats` reply expands (WiFiCapC TODO-Q4) to
      include `current_channel`, `hopping`, `attack_active`,
      `iface_mode`, the agent can poll `stats` (e.g. once per epoch)
      and reflect it in `_view`. Today the UI infers channel from
      our last `set_channel` echo, which goes stale during hopping.
- Files: `pwnagotchi/agent.py`, ui components.

### TODO-D4 — Call `delete_handshake` after successful wpa-sec upload
- [ ] When WiFiCapC adds the `delete_handshake` IPC (WiFiCapC TODO-Q5),
      have `wpa-sec.py` call it on `Status.SUCCESSFULL` rows. Today
      the handshake dir grows unbounded.
- Files: `pwnagotchi/plugins/default/wpa-sec.py`.

### TODO-D5 — Persistent recon table reload
- [ ] When WiFiCapC starts persisting AP/STA state across restarts
      (WiFiCapC TODO-R1), the agent needs to handle the wave of
      `ap.new`/`sta.new` events that arrive immediately after
      reconnect. Today reconnect already works (we emit `ap.new`
      from the daemon for everything in the table on subscribe);
      just verify nothing expects "fresh" implies "first-ever".
- Files: `pwnagotchi/agent.py`, `pwnagotchi/wificapc.py`.

### TODO-D6 — Subscribe to only the events we use
- [ ] When WiFiCapC implements per-client subscribe/unsubscribe
      (WiFiCapC TODO-X4), narrow the agent's subscriptions to
      `ap.new`, `ap.lost`, `sta.new`, `sta.lost`, `handshake.done`.
      Today we receive every `iface.channel` tick at 250 ms hop
      intervals (4 events/sec we don't act on).
- Files: `pwnagotchi/wificapc.py`, `pwnagotchi/agent.py`.

## Agent-only items (no daemon dependency)

### TODO-A1 — Confirm pwnagotchi self-update path
- [ ] Resolve the `TODO(pwnagotchi self-update)` left in
      `auto-update.py::install_source_archive`: read the venv path
      from config (don't hardcode `/opt/.pwn`), validate the
      package name against `pyproject.toml` before pip-installing.
      Today both assumptions hold for the standard image build but
      break on custom layouts.
- Files: `pwnagotchi/plugins/default/auto-update.py`.

### TODO-A2 — Tame `_history` growth
- [ ] `Agent._should_interact` increments per BSSID/MAC and never
      evicts. After many epochs the dict holds every AP/STA ever
      seen. Age out entries older than some window (mirror
      personality TTLs).
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

### TODO-A5 — Pin pwnagotchi clone in stage3/03-pwnagotchi
- [ ] `stage3/03-pwnagotchi/01-run-chroot.sh` clones master from
      GitHub (not the local checkout). Same problem as
      `stage3/01-wificapc/01-run-chroot.sh` had before we pinned
      `WIFICAPC_TAG`. Pin to the current pwnagotchi tag for
      reproducible image builds.
- Files: `stage3/03-pwnagotchi/01-run-chroot.sh`.

---

## How to use this file

- New session picks an item, opens a branch, ships a PR.
- When an item lands, mark `[x]` and link the PR. When a whole
  section's items all land, drop the section.
- Cross-repo items (`TODO-D*`) should land in WiFiCapC first, then
  the agent-side change here.
