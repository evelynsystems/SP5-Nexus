# SP5 Nexus

Linux desktop launcher for the experimental AnyPS5 engine. **Powered by AnyPS5**.

**Development status:** Experimental. Commercial PS5 game compatibility is not guaranteed. This source package does not include the AnyPS5 engine, firmware, decrypted game files, or game discs. NVIDIA and AMD GPU detection is supported, but compatibility requires testing on actual hardware.

The original AnyPS5 project is maintained separately: https://github.com/boykopovar/AnyPS5 . SP5 Nexus is not an official Sony product.

## Version 3.0: console interface (Desert Rat Software)

* **Home:** full-screen artwork with smooth cross-fades, a hero for the selected game, and
  shelves for All games, Recently played, Favorites and Installed.
* **Game Hub:** cover and background management, favourites, collections and categories,
  genre and description, per-game engine choice, play history, a dry-run conversion check
  (`relinker --dry-run`, nothing written), and per-game AnyPS5 runtime settings
  (the documented `ANYPS5_*` variables, validated before launch).
* **Library:** search, sort, favourites, collections, categories, and missing-game detection
  with "Locate executable".
* **Artwork:** only from the game's own `sce_sys` folder (`icon0.png`, `pic1.png`/`pic0.png`,
  `param.json` title) or images you choose. Games without artwork get generated artwork.
  Nothing is downloaded.
* **Controllers:** DualSense, DualSense Edge, DualShock 4 and other gamepads over USB or
  Bluetooth, read directly from Linux input devices; battery level for Sony controllers;
  confirm button, dead zone and repeat delay; disconnect and reconnect notices. Keyboard and
  mouse keep working. Nexus ignores controller input while a game has focus.
  If a controller shows "No permission", add your user to the `input` group or install a
  udev rule for it, then reconnect.
* **Engine:** several installed AnyPS5 builds with `--version` detection, `sce_module`,
  `sce_modules` and `prx` module folders, `--sce-module-path`, and a host readiness check
  against AnyPS5's documented requirements (`/dev/udmabuf`, CPU instruction sets, Vulkan 1.1
  features and extensions, the NVIDIA 610.47 driver problem).
* **Control Center** (F1 or the PS button): controller, GPU, CPU, Vulkan, Rust backend,
  engine, the running game, notifications, quick settings and full screen.
* Themes (Nexus blue, Aurora, Ember, Graphite) and motion levels (Full, Reduced, Off).

Keyboard: arrows move, Enter selects, Esc goes back, `[` and `]` switch shelves or pages,
`I` opens the Game Hub, `/` searches, F1 opens the Control Center, F11 toggles full screen.

SP5 Nexus does not publish game compatibility. The compatibility field is your own note.

# SP5 Nexus desktop edition

Powered by AnyPS5. This edition retains the exact HTML, CSS and JavaScript dashboard from the approved visual build, now inside a native Ubuntu Qt WebEngine window. **Firefox and Chrome are not opened.** The local service is launched only inside the desktop process and stops when the window closes.

## Ubuntu installation

Install the Ubuntu prerequisites:

```bash
sudo apt update
sudo apt install python3-venv python3-pip libnss3 libxkbcommon0 libdbus-1-3 libx11-xcb1 libxcomposite1 libxdamage1 libxrandr2 libasound2t64
```

Build the native Rust backend once (optional but recommended; see docs/BUILD.md):

```bash
sudo apt install cargo rustc python3-dev   # or install Rust from https://rustup.rs
./build_native.sh
```

Run:

```bash
chmod +x run.sh install_desktop_linux.sh
./run.sh
```

`./run.sh` never compiles Rust. Without the compiled module Nexus reports
**Rust Backend Unavailable** in Settings and Diagnostics and uses its Python
implementations. `python3 desktop.py --self-test` checks a setup without opening a window.

The first run creates a local Python environment and installs Qt libraries through pip. A network connection is required on the first run. Later runs reuse the installed libraries.

Optional Ubuntu app menu launcher:

```bash
./install_desktop_linux.sh
```

Press F11 to enter or leave full screen. Close the window to stop the application service.

## Important

This package includes the graphical launcher, engine installation manager and conversion hooks. **It does not contain an already compiled AnyPS5 engine or guarantee playable commercial PS5 titles.** Engine Manager still downloads and compiles upstream source on the user's machine. Artwork is not a compatibility claim. Original engine: https://github.com/boykopovar/AnyPS5 .

## AppImage

`packaging/build_appimage.sh` builds an AppImage containing a relocatable Python, PySide6
(Qt WebEngine), the interface and the compiled Rust module. The AnyPS5 engine is not
bundled; Engine Manager builds it in `~/.local/share/sp5-nexus/engine`. See docs/BUILD.md.
The AppDir it produces is a standard AppDir and can also be handed to MarkForge.
The AppImage has not yet been built or tested on the RTX 3090 PC.


## Repair build notes
This source includes safer configuration loading, private API access checks, a temporary
conversion output stage, cancellation for tracked subprocess groups, Git origin checks,
real GPU defaults, hardware Vulkan detection and interface refresh fixes.
Previously saved config is copied from ~/.config/anyps5-studio to ~/.config/sp5-nexus
when the new location does not already contain the relevant file. The original
files remain untouched. Back up both directories before installing.

Safe in-place AnyPS5 engine upgrades are intentionally disabled until separate build
staging is implemented. The Install button reuses a validated existing engine.
This ZIP contains launcher source only, not downloaded engine sources or build weights.
Hardware testing, AppImage validation, and commercial game compatibility are unverified.


## AMD Radeon and NVIDIA graphics

SP5 Nexus detects graphics hardware with `vulkaninfo --summary`. AMD Radeon GPUs work with a correctly installed Vulkan ICD, commonly RADV from Mesa. NVIDIA details are enhanced when `nvidia-smi` is present, but it is not required for AMD. Install `vulkan-tools` to enable the diagnostics. Hardware detection does not guarantee AnyPS5 game compatibility.

For Ubuntu AMD systems, verify Vulkan with `vulkaninfo --summary`. GPU selection is automatic by default.

## Optional gaming integrations

Settings → System performs a read only scan of Linux game controller devices, optical drives, and available tools. It lists the upstream projects and licenses as references. It does not download or bundle external projects, firmware, games or protected disc data.

Optional programs:

* Gamescope (ValveSoftware/gamescope): install separately using your distribution's packages where available. Enable in Settings to wrap converted-game launches.
* MangoHud (flightlessmango/MangoHud): install separately and opt in under Settings. It adds a performance overlay to converted-game launches.
* SDL GameControllerDB (mdqinc/SDL_GameControllerDB): controller mappings are documented; support depends on the game/runtime and SDL and is not claimed to be automatically enabled.
* pyudev (pyudev/pyudev): optional Linux hardware device monitoring library; the built in inspection works through sysfs and lsblk without this dependency.
* IGDB API (twitchtv/igdb-api-python): cover lookup requires user credentials and approval. No automatic network cover downloads occur.

The optical drive scan is strictly read only. It does not mount, copy, decrypt or execute PS5 discs. Device detection does not establish game compatibility.

**Hardware verification still required:** Sony controllers, AMD Radeon and NVIDIA GPU systems, optical drives, Gamescope, MangoHud, actual converted-game launches, and the final desktop window have not been tested on the user's PC in this build environment.


## Engine updates

Open Engine Manager and select Check for Updates. This reads the official AnyPS5 GitHub HEAD commit without changing the local source. If a newer commit is found, Install Verified Engine Update clones and builds a separate copy in engine/nexus_updates, validates the compiled relinker and runtime libraries, and only then changes the configured relinker paths. Your previous engine source and build remain in place for manual rollback in Settings. Network access, free disk space, and compiler dependencies are required. Do not close the app during a build; use Stop Current Task to cancel. No engine update is installed without confirmation.

Optional Gamescope, MangoHud, GameMode and Vulkan utilities are managed by Ubuntu; SP5 Nexus reports their presence but does not run unattended system package updates or sudo commands. SP5 Nexus does not have a configured self-update feed, so the button does not claim to update its own GUI code.


## Version 2.0: native Rust backend (Desert Rat Software)

SP5 Nexus 2.0 keeps the Python + Qt WebEngine + JavaScript application and adds a native
Rust backend (`native/`) for library scanning, process control, launch validation, runtime
staging, engine detection and hardware/Vulkan diagnostics. Every Rust operation has a
Python implementation with identical results (checked by `tests/test_parity.py`); Nexus
uses Rust only for operations where it measured faster or adds reliability.

* Communication: interface (JavaScript) → Qt WebEngine → Python controller (`server.py`)
  → Rust backend (`nexus_native.py`) → system / AnyPS5 engine.
* Status shown in Settings and Diagnostics: Rust Backend Active, Unavailable or Error.
* `SP5_DISABLE_RUST=1 ./run.sh` forces the Python implementations.
* Tests: `python3 -m pytest tests` and `cargo test` in `native/core`.
* Measurements: `python3 tools/benchmark.py`.

Complete verification on an Ubuntu desktop (dependencies, both bindings, real Qt window,
AppImage, full tests): `./verify_on_ubuntu.sh --install`, report in `verification/REPORT.md`.

Documentation: docs/BUILD.md, docs/RUST_ARCHITECTURE.md, docs/DEPENDENCIES.md.
