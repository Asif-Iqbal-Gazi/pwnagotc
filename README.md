# PwnaGotC

```
______                      _____       _   _____ _
| ___ \                    |  __ \     | | /  __ \ |
| |_/ /_      ___ __   __ _| |  \/ ___ | |_| /  \/ |
|  __/\ \ /\ / / '_ \ / _` | | __ / _ \| __| |   | |
| |    \ V  V /| | | | (_| | |_\ \ (_) | |_| \__/\_|
\_|     \_/\_/ |_| |_|\__,_|\____/\___/ \__|\____(_)
```

> (⌐■_■)  passive sniffing and active provocation of WPA handshake material — no bettercap, no Go, no mesh, no ML.

## Overview

This fork replaces the original bettercap + pwngrid stack with **[WiFiCapC](https://github.com/Asif-Iqbal-Gazi/WiFiCapC)** — a lean native C daemon that owns the entire Wi-Fi control plane behind a Unix-domain socket. The Python agent connects over IPC, drives policy (recon, channel hopping, attacks), and reacts to handshake events.

Machine learning, mesh networking, and the Go/Rust toolchains have been removed entirely. Behavior is rule-based, deterministic, and small enough to debug on a Pi Zero 2 W with `journalctl`.

## Status

Stable on Raspberry Pi OS 64-bit (kernel 6.12, brcmfmac + Nexmon). Wheel + sdist released on every `v*` tag; full pi-gen disk image released on `image-v*` tags via GitHub Actions.

| Component         | Latest |
|-------------------|--------|
| pwnagotchi (this) | `v3.0.7` |
| WiFiCapC daemon   | `v0.6.6` |

**Target hardware:** Raspberry Pi Zero 2 W (aarch64), with a Bluetooth-tethered phone for upstream and an e-paper / waveshare display attached.

## What changed from upstream

| Upstream                                 | This fork                                |
|------------------------------------------|------------------------------------------|
| bettercap (Go)                           | WiFiCapC (C, single static binary)       |
| pwngrid mesh network                     | removed                                  |
| Go + Rust toolchain in image             | removed                                  |
| ML personality (gym/keras)               | removed (deterministic rule-based)       |
| `.pcap` capture                          | per-pair `.pcap` + hashcat `.22000`      |
| `bettercap.service` + `pwngrid-peer`     | `wificapc.service` + `wificapc-prep`     |
| `internet_available` fired on link-up    | probe-driven (DNS + TCP), with symmetric `internet_unavailable` |
| `wigle` / `webgpsmap` plugins            | dropped (depended on `.pcap` captures)   |

## Architecture

```
                 ┌──────────────────────────────────────┐
   802.11 →─────►│ wificapc (native C, root + caps)     │
   monitor       │   AF_PACKET RX → recon → handshakes  │──► /etc/pwnagotchi/handshakes/
                 │   nl80211 ◄ channel/mode             │
                 │   AF_PACKET TX  (deauth/assoc)       │
                 └────────────┬─────────────────────────┘
                              │ AF_UNIX /run/wificapc.sock
                              │ line-delimited JSON
                              ▼
                 ┌──────────────────────────────────────┐
                 │ pwnagotchi.service (Python 3.11)     │
                 │   agent + automata + plugins         │
                 │   internet probe ◄─ bt-tether        │
                 │   web UI :8080                       │──► e-paper / LCD
                 └──────────────────────────────────────┘
```

The agent sends `iface_set → monitor_on → recon_start → hop_start` at boot, then listens for `ap.new`, `sta.new`, `handshake.done` events and updates the UI / mood. If wificapc dies (firmware crash, USB unplug), `wificapc-prep.service` cycles `brcmfmac` and the daemon comes back; the Python side reconnects and reissues setup automatically.

## Quickstart — flashing a built image

Easiest path is to grab the latest image from the [releases page](https://github.com/Asif-Iqbal-Gazi/pwnagotc/releases) (look for `image-v*` tags) and flash it with `dd` or Raspberry Pi Imager.

```bash
xz -d pwnagotchi-64bit-image-v3.0.7.img.xz
sudo dd if=pwnagotchi-64bit-image-v3.0.7.img of=/dev/sdX bs=4M conv=fsync status=progress
```

First-boot behavior:
- Hostname `PwnaGotC`, default user `pi` / password `raspberry` (change it).
- Presents as a USB Ethernet gadget at `10.0.0.2` — connect and open `http://10.0.0.2:8080` for the web UI.
- The pi pairs with a phone over Bluetooth for upstream; configuration is in `bt-tether` plugin settings.

Platform connection helpers in `scripts/`:

```
scripts/linux_connection_share.sh
scripts/macos_connection_share.sh
scripts/win_connection_share.ps1
```

## Building from source

### The wheel (Python package only)

Pure Python, no compilation. Useful for testing the agent against a daemon already on the device.

```bash
python -m pip install build
python -m build
# dist/pwnagotchi-X.Y.Z-py3-none-any.whl, dist/pwnagotchi-X.Y.Z.tar.gz
```

The release workflow (`.github/workflows/release.yml`) does this on every `v*` tag and attaches the wheel + sdist + `SHA256SUMS` to a GitHub Release.

### The full pi-gen image

Heavy lift — runs all four pi-gen stages cross-arch via qemu-user, takes 30–90 minutes, needs ~12 GB working space and ~20 GB free disk overall.

```bash
# Install pi-gen build deps
sudo apt-get install -y \
    arch-test bc binfmt-support curl debootstrap dosfstools file \
    gcc-aarch64-linux-gnu gcc-arm-linux-gnueabihf git gpg kmod kpartx \
    libarchive-tools libcap2-bin make parted pigz qemu-system-arm \
    qemu-user qemu-user-static qemu-utils quilt rsync xxd xz-utils \
    zerofree zip

# Build 64-bit image
make 64bit
# → ~/images/<date>-pwnagotchi-64bit.img.xz
```

The image build can also be triggered in CI by pushing an `image-v*` tag:

```bash
git tag image-v3.0.7
git push origin image-v3.0.7
# .github/workflows/image-64bit.yml builds and publishes the .img.xz
```

### Pi-gen stages (this fork's stage3)

| Stage             | What it does                                              |
|-------------------|-----------------------------------------------------------|
| `00-packages`     | APT packages (libnl-genl-3-dev, hcxtools, build-essential, …) |
| `01-wificapc`     | Clones WiFiCapC at the pinned `WIFICAPC_TAG`, builds, installs the binary |
| `02-nexmon`       | Drops in Nexmon firmware + brcmfmac DKMS for monitor mode |
| `03-pwnagotchi`   | Clones this repo, creates `/opt/.pwn` venv, pip-installs  |
| `04-patches`      | systemd units, launcher scripts, sudoers, profile aliases |
| `05-pwnstore`     | Plugin store / community plugins                          |

Stage layout follows [pi-gen](https://github.com/RPi-Distro/pi-gen)'s conventions. Each stage's `01-run-chroot.sh` runs inside the qemu-emulated arm64 chroot.

## Configuration

`/etc/pwnagotchi/config.toml` is merged on top of `defaults.toml`. Key sections:

```toml
[main]
iface = "wlan0mon"
internet_probe_host = "cloudflare-dns.com"   # set "" to disable the probe
internet_probe_interval = 30                 # seconds between probes
internet_probe_timeout = 2                   # seconds per probe

[wificapc]
socket = "/run/wificapc.sock"
handshakes = "/etc/pwnagotchi/handshakes"
hop_interval_ms = 250

[main.plugins.auto-update]
enabled = true
install = true
interval = 24
token = ""                                   # GitHub PAT, optional
```

Handshakes land in `/etc/pwnagotchi/handshakes/<ap_hex>_<sta_hex>.{pcap,22000}` and stay until something explicitly cleans them up. The wpa-sec plugin uploads the `.pcap` (which wpa-sec runs through hcxpcapngtool); the `.22000` is for offline cracking.

## Default plugins

Retained from upstream and adapted for the WiFiCapC event surface:

- `auto-update` — checks GitHub for new pwnagotchi (wheel) + WiFiCapC (binary) releases. Per-target failure isolation: a network blip on one repo no longer blocks the other.
- `wpa-sec` — uploads handshakes to wpa-sec.stanev.org, downloads cracked passwords. Uses the daemon's per-pair `.pcap` directly.
- `gps` — saves a `.gps.json` sidecar next to each captured handshake.
- `bt-tether` — Bluetooth NAP profile for phone tethering.
- `auto-tune` — runtime tuning of personality (deauth/assoc throttles, RSSI floor, TTLs); pushes TTL changes through to wificapc via `set_ttls`.
- `auto-backup`, `cache`, `fix_services`, `gpio_buttons`, `logtail`, `memtemp`, `ohcapi`, `pisugarx`, `pwncrack`, `session-stats`, `ups_lite`, `webcfg`, `wittypi`.

Removed from upstream: `wigle`, `webgpsmap` (both depended on `.pcap` files we no longer write at the pwnagotchi level — the daemon's per-pair `.pcap` is wpa-sec / hcxpcapngtool format, not the bettercap-era continuous capture).

Custom plugins go in `/etc/pwnagotchi/custom-plugins/` and are enabled in `config.toml`:

```toml
[main.custom_plugins]
enabled = true
```

## Systemd services

| Service              | Order                              | Purpose                                                    |
|----------------------|------------------------------------|------------------------------------------------------------|
| `wificapc-prep.service` | before `wificapc` (Wants)         | rmmod/modprobe brcmfmac, `rfkill unblock wifi`             |
| `wificapc.service`   | first                              | the C daemon (root, CAP_NET_RAW + CAP_NET_ADMIN)           |
| `pwnagotchi.service` | `After=wificapc.service`           | the Python agent (Type=simple, Restart=always)             |

```bash
sudo systemctl status wificapc
sudo systemctl status pwnagotchi
sudo journalctl -fu pwnagotchi
sudo journalctl -fu wificapc
```

## Project layout

```
pwnagotchi/                 Python package (agent, plugins, UI)
  agent.py                  Automata + WiFiCapC IPC client + internet probe
  wificapc.py               JSON-over-Unix-socket client wrapper
  plugins/default/          Built-in plugins
scripts/                    Phone connection share helpers
stage3/                     pi-gen stages for the image build
  00-packages/              APT package list
  01-wificapc/              clone + build WiFiCapC at WIFICAPC_TAG
  02-nexmon/                Broadcom monitor-mode firmware
  03-pwnagotchi/            install this repo into /opt/.pwn
  04-patches/files/         service files, launcher scripts, sudoers
  05-pwnstore/              plugin store
.github/workflows/
  release.yml               wheel + sdist on v* tag
  image-64bit.yml           full pi-gen image on image-v* tag
config-64bit                pi-gen config (deploy / work dirs)
Makefile                    `make 64bit`, `make 32bit`, locale helpers
```

## Improvements / future work

See [TODO.md](TODO.md). Items are categorized by whether they need WiFiCapC daemon work first or are pure agent-side changes, with cross-references to [the daemon's TODO.md](https://github.com/Asif-Iqbal-Gazi/WiFiCapC/blob/main/TODO.md). Short list:

- daemon vendor lookup → vendor strings in UI (TODO-D1)
- daemon `delete_handshake` → wpa-sec cleanup after upload (TODO-D4)
- daemon subscribe filter → narrow the agent's event stream (TODO-D6)
- wlan0mon → wlan0 (skip the sibling-iface dance) (TODO-A3)
- pin `stage3/03-pwnagotchi` clone like `01-wificapc` is pinned (TODO-A5)

## Releases

Both lightweight (wheel/sdist) and heavy (full image) releases are GitHub Actions–driven and signed:

- Push `vX.Y.Z` → wheel + sdist + SHA256 attached at `/releases/tag/vX.Y.Z`.
- Push `image-vX.Y.Z` → cross-arch pi-gen build, `.img.xz` + SHA256 attached at `/releases/tag/image-vX.Y.Z`.

The `auto-update` plugin pulls the wheel from the wheel-tag release; pi-gen's stage3 pins WiFiCapC to a tag for reproducible image builds.

## License

GPL-3.0 — see [LICENSE.md](LICENSE.md).

Original work by [Evilsocket](https://github.com/evilsocket); WiFiCapC migration and this fork by [Asif-Iqbal-Gazi](https://github.com/Asif-Iqbal-Gazi).
