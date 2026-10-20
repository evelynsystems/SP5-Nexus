# SP5 Nexus

Linux desktop launcher for the experimental AnyPS5 engine. **Powered by AnyPS5**.

**Development status:** Experimental. Commercial PS5 game compatibility is not guaranteed. This source package does not include the AnyPS5 engine, firmware, decrypted game files, or game discs. NVIDIA and AMD GPU detection is supported, but compatibility requires testing on actual hardware.

The original AnyPS5 project is maintained separately: https://github.com/boykopovar/AnyPS5 . SP5 Nexus is not an official Sony product.

# SP5 Nexus desktop edition

Powered by AnyPS5. This edition retains the exact HTML, CSS and JavaScript dashboard from the approved visual build, now inside a native Ubuntu Qt WebEngine window. **Firefox and Chrome are not opened.** The local service is launched only inside the desktop process and stops when the window closes.

## Ubuntu installation

Install the Ubuntu prerequisites:

```bash
sudo apt update
sudo apt install python3-venv python3-pip libnss3 libxkbcommon0 libdbus-1-3 libx11-xcb1 libxcomposite1 libxdamage1 libxrandr2 libasound2t64
```

Run:

```bash
chmod +x run.sh install_desktop_linux.sh
./run.sh
```

The first run creates a local Python environment and installs Qt libraries through pip. A network connection is required on the first run. Later runs reuse the installed libraries.

Optional Ubuntu app menu launcher:

```bash
./install_desktop_linux.sh
```

Press F11 to enter or leave full screen. Close the window to stop the application service.

## Important

This package includes the graphical launcher, engine installation manager and conversion hooks. **It does not contain an already compiled AnyPS5 engine or guarantee playable commercial PS5 titles.** Engine Manager still downloads and compiles upstream source on the user's machine. The game cover previews are decorative and not compatibility claims. Original engine: https://github.com/boykopovar/AnyPS5 .

## AppImage

Use MarkForge to package the Linux app after installing its Python and Qt dependencies. AppImage construction and testing on the RTX 3090 have not yet been performed.


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

The Integrations screen performs a read only scan of Linux game controller devices, optical drives, and available tools. It lists the upstream projects and licenses as references. It does not download or bundle external projects, firmware, games or protected disc data.

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
