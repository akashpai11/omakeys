# Omakeys

Per-device keyboard customizer for Omarchy Quattro: universal remapping via
[keyd](https://github.com/rvaiya/keyd), VIA/QMK extras when a compatible
board is detected, and a keypress heatmap. Zero hardcoded colors — every
surface is pulled live from the shell's `Color`/`Style` singletons, so it
reskins instantly with the rest of Omarchy and holds up in both light and
dark themes.

## Status

- [x] Device auto-detect via udev, themed bar chip, panel listing every
      detected keyboard and whether `keyd` is installed/running.
- [x] Visual remap grid (generic 60% layout, Mac/Windows bottom-row toggle)
      writing to `keyd` — click a key, pick a target, apply with one
      `pkexec` authentication prompt.
- [x] VIA/QMK raw-HID detection (needs a one-time udev rule, installed via
      `pkexec`). Verified to correctly report "not detected" over
      Bluetooth; **not yet verified against a true positive** — needs
      testing over USB.
- [x] Polling-rate + connection-mode readout (USB `bInterval` sysfs walk;
      correctly reports "n/a" over Bluetooth). USB path unverified against
      real wired hardware so far.
- [x] Keypress heatmap: a long-running counting daemon (needs the `input`
      group — see Setup below), SQLite storage, a themed heatmap tab reusing
      the same key grid with a theme-accent gradient fill.
- [ ] RGB/macro control, suggestion engine, per-app layer switching,
      RGB-reactive heatmap.

## Setup

Two one-time privileged steps, each a single `pkexec` prompt from inside
the panel (or run manually — see below):

1. **keyd** must be installed for remapping to work:
   `sudo pacman -S keyd` (needs a real terminal — `sudo` won't prompt
   through automation). The plugin handles enabling the service and
   writing configs itself.
2. Raw HID (VIA detection) and raw keypress counting (heatmap) need your
   user in two groups: `keyd` and `input`. The plugin adds you to both via
   `pkexec usermod -aG ...` the first time it needs them, but **group
   membership only takes effect after you log out and back in** — this is
   a Linux session thing, not a plugin bug. Until then, remapping still
   works (`keyd` config writes go through their own `pkexec` call each
   time), while VIA detection and the heatmap silently wait and say so in
   the UI.

## Privacy

The heatmap counts keycodes only — never sequences, never timestamps
precise enough to reconstruct typing — and drops everything while the
session is locked. It's necessarily global (all keyboards combined) and
counts the *post-remap* key: once `keyd` is running it merges every
physical keyboard into one virtual output device, so per-device and
pre-remap attribution isn't recoverable downstream of it. Not solved (and
not really solvable at this layer): true per-application password-field
exclusion — that needs toolkit-level integration this plugin doesn't have.

## Dev loop

This repo lives directly under `~/.config/omarchy/plugins/omakeys/` — it
has to be a real directory there, not a symlink, or the shell's recursive
`inotifywait` watcher won't see edits inside it. (A convenience symlink
*from* somewhere else *to* this real location is fine.)

```bash
omarchy plugin validate ~/.config/omarchy/plugins/omakeys
omarchy plugin enable omakeys --section right
```

Most QML/script edits hot-reload live. Changes to `PanelWindow`-level
geometry (contentWidth/contentHeight) don't reliably apply via hot-reload —
run `omarchy restart shell` after those before judging the result.

Useful IPC calls against the running shell:

```bash
omarchy-shell omakeys toggle   # open/close the panel
omarchy-shell omakeys open
omarchy-shell omakeys close
```

Test scripts standalone (no shell needed):

```bash
python3 scripts/detect_devices.py | python3 -m json.tool
python3 scripts/heatmap_query.py 1 | python3 -m json.tool
```

## Publishing

Push this directory to a public repo, then anyone installs with:

```bash
omarchy plugin add https://github.com/<you>/omakeys.git --enable
```
