# Pwnagotchi — WiFiCapC Edition

> (⌐■_■) passive sniffing and active provocation of WPA handshake material, no bettercap required.

## Overview

This fork replaces the original bettercap + pwngrid stack with **[WiFiCapC](https://github.com/Asif-Iqbal-Gazi/WiFiCapC)** — a lean native C daemon (~5 KLoC) that owns the entire Wi-Fi control plane over a Unix socket IPC. Machine learning and mesh networking have been removed entirely; behaviour is driven by rule-based state machines.

**Target hardware:** Raspberry Pi Zero 2 W (aarch64)

## What changed from upstream

| Upstream | This fork |
|---|---|
| bettercap (Go) | WiFiCapC (C, ~5 KLoC) |
| pwngrid mesh | removed |
| Go + Rust toolchain in image | removed |
| `.pcap` capture | `.22000` (hashcat-compatible) |
| `bettercap.service` + `pwngrid-peer.service` | `wificapc.service` |

## Architecture

```
wificapc (C daemon)
  │  AF_UNIX /run/wificapc.sock  (line-delimited JSON)
  └──► pwnagotchi (Python)
         │  plugins, UI, epoch logic
         └──► e-ink / LCD display
```

Pwnagotchi connects to wificapc at boot, sends `iface_set → monitor_on → recon_start → hop_start`, then listens for `ap.new`, `sta.new`, `handshake.done` events and updates the UI / mood accordingly. If wificapc restarts, the Python side reconnects and re-initialises automatically.

## Building

Requirements: x86-64 Linux host, ~20 GB free disk, Docker or native pi-gen dependencies.

```bash
# Install pi-gen build deps
sudo apt-get install -y make git quilt qemu-user-static debootstrap zerofree \
  libarchive-tools curl pigz arch-test gcc-aarch64-linux-gnu

# Build 64-bit image
cd pwnagotc
make 64bit
```

The finished image lands in `~/images/`. Flash it with `dd` or Raspberry Pi Imager.

### Build stages

| Stage | What it does |
|---|---|
| `01-pwn-packages` | APT packages incl. `libnl-genl-3-dev`, `libpcap-dev` |
| `02-libpcap` | Builds libpcap from source |
| `03-wificapc` | Clones & builds WiFiCapC |
| `04-nexmon` | Nexmon firmware patch for monitor mode |
| `05-install-pwnagotchi` | Installs pwnagotchi into `/opt/.pwn` venv |
| `06-hcxtools` | Builds hcxtools for `.22000` conversion |
| `07-patches` | systemd units, launchers, config |

## First boot

The device presents as a USB Ethernet gadget at `10.0.0.2`. Connect and open the web UI at `http://10.0.0.2:8080`.

Configuration: `/etc/pwnagotchi/config.toml`  
Custom plugins: `/etc/pwnagotchi/custom-plugins/`  
Handshakes: `/etc/pwnagotchi/handshakes/`

Platform connection scripts are in `scripts/`:

```
scripts/linux_connection_share.sh
scripts/macos_connection_share.sh
scripts/win_connection_share.ps1
```

## Configuration

`config.toml` is merged on top of `defaults.toml`. Key wificapc section:

```toml
[wificapc]
socket = "/run/wificapc.sock"
handshakes = "/etc/pwnagotchi/handshakes"
hop_interval_ms = 250
```

## Plugins

Default plugins retained from upstream (GPS, wpa-sec upload, wigle, auto-backup, auto-update, etc.). bettercap-specific plugins have been updated or removed.

Custom plugins go in `/etc/pwnagotchi/custom-plugins/` and are enabled in `config.toml`:

```toml
[main.custom_plugins]
enabled = true
```

## Systemd services

```
wificapc.service   — WiFi capture daemon (starts first)
pwnagotchi.service — Python agent (After=wificapc.service)
```

```bash
sudo systemctl status wificapc
sudo systemctl status pwnagotchi
sudo journalctl -fu pwnagotchi
```

## License

GPL-3.0 — see [LICENSE.md](LICENSE.md)
