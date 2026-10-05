# Operating modes — Manual / Agent / Engine

pwnagotc runs in one of **three modes** that decide *who drives the attack and
channel hopping*. They are switchable **live** (no restart) so you can A/B the
two attack paths on the same hardware and compare battery, CPU, stability and
handshake rate.

| Mode | Badge | Who hops | Who attacks | Use it to… |
|---|---|---|---|---|
| **Manual** | 😴 `MAN` | daemon | nobody | watch + upload only; passive capture keeps running, no deauth/assoc |
| **Agent**  | 🐍 `AGT` | **Python agent** | **Python agent** | run the original pwnagotchi path — the agent does recon → hop → assoc/deauth |
| **Engine** | ⚡ `ENG` | daemon (`--auto`) | daemon (`--auto`) | let the C engine self-drive the whole hunt; the agent just consumes + displays |

The emoji badge shows in the logs and the web UI; the 3-letter tag shows on the
e-ink/LCD display (no emoji there).

## What each mode actually does

All three keep **capture** and **uploads** running — a mode only changes who
owns channel control and whether attacks fire.

- **Manual 😴** — the daemon keeps hopping and capturing (so passive/opportunistic
  handshakes still land), but the active attack is held off (`set_attack false`).
  Internet uploads, stats and the display all keep working. This is the safe
  "just listen" mode.
- **Agent 🐍** — the agent tells the daemon to stop self-driving
  (`auto_stop`: capture stays up, channel control is handed to the client) and
  then runs the classic pwnagotchi loop itself: `recon` → walk channels with
  `set_channel` → `associate` (PMKID) / `deauth` clients. This is the
  Python-driven path, kept so you can compare it head-to-head with Engine.
- **Engine ⚡** — the daemon runs its own `--auto` orchestrator: it hops,
  captures, and attacks per channel entirely in C (`auto_start` +
  `set_attack true`). The agent is a pure consumer — it reads events, advances
  moods, updates the display and uploads. This is the efficient path (no Python
  in the hot loop).

Under the hood this is three WiFiCapC IPC calls — `auto_start`, `auto_stop`,
`set_attack` (see `WiFiCapC/docs/protocol.md`). The mode strategy lives in
`pwnagotchi/modes.py`; the switch logic is `agent.set_mode()`.

## Selecting a mode

Resolved at startup in this order (first wins):

1. **Runtime file** `/etc/pwnagotchi/.mode` — written whenever the mode changes,
   so the last choice survives a reboot.
2. **Config** `main.mode` in `/etc/pwnagotchi/config.toml` (default `engine`).
3. The legacy `--manual` flag (→ manual), else `engine`.

Ways to switch:

- **Web UI** — three buttons on the home page (`POST /mode/<name>`), live, no
  restart. The active mode is highlighted. A scripting client can
  `POST /mode/engine?json` and read back `{"ok": true, "mode": "engine"}`.
- **Config** — set `main.mode = "agent"` (applies next start).
- **By hand** — `echo agent | sudo tee /etc/pwnagotchi/.mode` then restart (or
  just use the web button).

```toml
[main]
# Operating mode: "manual" | "agent" | "engine". See docs/MODES.md.
# Overridden at runtime by /etc/pwnagotchi/.mode (the web mode buttons
# write that file), so this is just the first-boot default.
mode = "engine"
```

## A/B metrics

Every mode's run is tallied in `/etc/pwnagotchi/mode-stats.json` (override with
`main.mode_stats_file`) — per mode: wall-clock time, handshakes caught, epochs,
and average CPU load. Derived in `agent.mode_stats()` /
`modestats.ModeStats.summary()` as `secs`, `catches`, `rate_per_hr`,
`avg_cpu_pct`.

The display surfaces the comparison live:

- **mode tag** (bottom-right): `MAN` / `AGT` / `ENG`
- **A/B tally** (`E# A# M#`): handshakes caught per mode so far
- **PWND** score: session (all-time) handshakes

So a typical A/B session: run Engine for a while, flip to Agent from the web
button, let it run the same kind of area, and compare the two columns of the
tally (plus `avg_cpu_pct` in `mode-stats.json`) to see which path catches more
for less power.

> **Note on personality knobs.** The `[personality]` attack/recon settings
> (`deauth`, `associate`, `channels`, `recon_time`, `throttle_*`,
> `max_interactions`, TTLs, `min_rssi`, …) apply to **Agent** mode — the
> Python-driven path. **Engine** mode ignores them; the C daemon has its own
> scheduling. The mood thresholds (`excited/bored/sad_num_epochs`,
> `max_misses_for_recon`) apply in every mode.

## What was removed

The mesh (pwngrid/peers/bonding/advertise), the RL "brain", and the `auto-tune`
and `fix_services` plugins are gone — the modes above replace the old
manual-vs-auto split, and mode is no longer guessed from whether `usb0` is up.
