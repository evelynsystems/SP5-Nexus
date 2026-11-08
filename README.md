<div align="center">

![SP5 Nexus banner](docs/assets/nexus-banner.svg)

# SP5 NEXUS

### Your Games. Your System.

**A console inspired gaming frontend for Linux, built with a native Rust core and an embedded Qt desktop interface.**

[![Linux](https://img.shields.io/badge/Platform-Linux-1693D1?style=for-the-badge&logo=linux&logoColor=white)](https://github.com/evelynsystems/SP5-Nexus)
[![Rust](https://img.shields.io/badge/Core-Rust-263445?style=for-the-badge&logo=rust&logoColor=white)](https://www.rust-lang.org/)
[![Python](https://img.shields.io/badge/Controller-Python-3478A2?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![Qt](https://img.shields.io/badge/Desktop-Qt%20WebEngine-28A879?style=for-the-badge&logo=qt&logoColor=white)](https://www.qt.io/)
[![License](https://img.shields.io/badge/License-MIT-6556D8?style=for-the-badge)](LICENSE)

**[Explore the interface](#the-console-experience) · [Features](#features) · [Install on Ubuntu](#installation) · [Engine compatibility](#anyps5-and-compatibility)**

</div>

---

## The console experience

![SP5 Nexus console interface running on Ubuntu](docs/assets/nexus-home.png)

*Real application screenshot provided from an Ubuntu desktop. Interface shown is an active Nexus 3.0 development design; the repository's published source and available releases may differ.*

Nexus brings your gaming library, engine controls, hardware diagnostics and launcher settings into one desktop application. The experience is designed around large game artwork, fast access to your library and a console style layout that stays useful on a mouse and keyboard.

> **Independent software.** SP5 Nexus is developed by **Desert Rat Software**. It is not affiliated with Sony or PlayStation. AnyPS5 is an independent, experimental project. A functioning launcher does not guarantee that commercial PS5 titles will run.

## Features

<table>
<tr><td width="50%" valign="top"><h3>🎮 Console inspired dashboard</h3><p>Game focused navigation, featured title display, searchable library and a layout designed for full screen use.</p></td><td width="50%" valign="top"><h3>⚙️ AnyPS5 engine tools</h3><p>Engine detection, configuration, relinker inspection, conversion workflows and diagnostic feedback.</p></td></tr>
<tr><td valign="top"><h3>🦀 Native Rust integration</h3><p>A Rust core connected to Python through PyO3, with an independently tested C API binding as a fallback in the locally verified 2.1 build.</p></td><td valign="top"><h3>🖥️ GPU and Vulkan diagnostics</h3><p>Hardware information for NVIDIA and AMD capable Linux systems, with Vulkan checks where the necessary utilities are installed.</p></td></tr>
<tr><td valign="top"><h3>📚 Game management</h3><p>Import, browse, filter and organize local game entries with metadata and artwork support. Available features depend on the source version.</p></td><td valign="top"><h3>🧩 Optional Linux integrations</h3><p>Detection and configuration hooks for companion tools such as Gamescope and MangoHud, where installed.</p></td></tr>
</table>

### Inside Nexus

| Area | Purpose |
| :--- | :--- |
| **Home and Library** | Browse your local game collection and select a title. |
| **Engine Manager** | Inspect the AnyPS5 installation, relinker and available updates. |
| **Conversion** | Configure supported conversion operations and review logs. |
| **Diagnostics** | Inspect GPU information, Vulkan availability and engine readiness. |
| **Settings** | Set paths, hardware preferences and launcher options. |

## Built for Linux

```text
                    SP5 NEXUS DESKTOP
                           │
                 Qt WebEngine interface
                           │
                    Python controller
                           │
                  Native Rust core
                    ╱           ╲
             PyO3 binding     C API fallback
                           │
             Local library and engine tools
```

Nexus runs in an application window, rather than opening Firefox or Chrome as its user interface. The local application service is intended to stop when its window closes.

**Hardware validation:** The Nexus 2.1 build was tested on Ubuntu with an NVIDIA GeForce RTX 3090. PyO3, the C API binding, Vulkan detection and Qt window startup passed local checks. These results do not establish support for every Linux distribution or every game.

## Installation

### Ubuntu source installation

Clone the repository or download the source ZIP. From the project folder:

```bash
chmod +x run.sh install_desktop_linux.sh
./run.sh
```

The launcher creates a Python environment and may install Qt dependencies on first use. See the scripts and [requirements.txt](requirements.txt) for the actual dependencies of the checked out version. Internet access is needed when dependencies are not already installed.

Optional application menu integration:

```bash
./install_desktop_linux.sh
```

For the traditional desktop interface, **F11** toggles full screen.

### Rust development builds

If your source version includes the native Rust directory and its scripts, consult its bundled build documentation and run the Rust verification script appropriate to that version. Python and PyO3 must be built for the same interpreter environment that runs the application.

Do not assume that GitHub's default branch contains every feature shown in a newer development screenshot.

### AppImage

An AppImage must be compiled and verified before it is distributed as a release. **Do not treat the source archive as a ready to install AppImage.** Download only a release package explicitly published and verified by this repository's maintainers.

## AnyPS5 and compatibility

Nexus is a frontend for the independently maintained [AnyPS5 project](https://github.com/boykopovar/AnyPS5). It does **not** ship the AnyPS5 engine, PS5 firmware, game discs, protected game files or decrypted assets.

Actual game compatibility depends on the engine's capabilities, graphics hardware, Vulkan support, Linux drivers and the particular title. NVIDIA or AMD GPU detection does not establish that a game is playable.

Engine Manager supports checks and configuration for an existing engine installation. Some engine features require a separate source checkout and compilation. Review the upstream documentation before running conversions.

## Security and responsible downloads

Use the official repository for Nexus source and verify release checksums when provided. Impersonation repositories and suspicious emulator downloads have been reported. Avoid unofficial executable bundles and installers that ask you to disable security protections.

The project does not include games or copyrighted console firmware. Use only software and content you are authorized to use.

## Project status

**Nexus 3.0 interface development is underway.** The screenshot above shows the proposed direction and should not be mistaken for proof that all planned features are available in the public source branch.

Development priorities include polished game presentation, controller navigation, richer game management and engine settings, better verification and portable Linux packaging. Features are announced as completed only after implementation and testing.

## Credits

**SP5 Nexus** is developed by **Desert Rat Software**.

The [AnyPS5 engine](https://github.com/boykopovar/AnyPS5) is a separate upstream project; its authors retain credit and control of their work. Nexus is not an official Sony product.

## License

See [LICENSE](LICENSE) for the project's MIT license. Third party dependencies and the separate AnyPS5 engine retain their own licenses.

<div align="center">

**SP5 NEXUS**

*Your Games. Your System.*

**Desert Rat Software**

</div>
