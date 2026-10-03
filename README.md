# 🌀 Vortex

Vortex is a powerful, lightweight **all-in-one developer and power-user hub** written entirely in shell script (`sh`). It bridges the raw performance of native Unix utilities (`awk`, `sed`, `grep`, `ffmpeg`) with a clean, responsive, and enterprise-grade graphical user interface (GUI).

Designed for ultimate portability and resilience, Vortex runs seamlessly across any desktop environment, window manager, or hardware—from a resource-constrained Raspberry Pi to a fully-fledged developer workstation. To guarantee absolute stability across different window managers, Vortex features its **own custom-built file dialog system**, bypassing unstable desktop-native file pickers entirely.

---

## 🚀 Core Modules (Available Now)

* **📊 CSV Analytics & Data Structurer:** Fast table processing, searching, and sorting powered by high-performance shell utilities.
* **🎨 Modern Web Designer:** An intuitive visual drag-and-drop HTML layout builder with live viewport scaling (Desktop, Tablet, Mobile).
* **🔒 Metadata & Privacy Purge:** One-click module to completely strip EXIF and metadata from images and documents before sharing.
* **🎞️ Media & Format Converter:** Instant hardware-efficient conversion for videos (MP4, GIF), audio extraction (MP3), and images using `ffmpeg` and ImageMagick.
* **🖥️ Instant Local Dev-Server:** Host any local directory or project instantly on port `8080` with a single click.
* **📝 Focus Code Editor:** A clean, distraction-free embedded editor tailored for rapid scripting and production-ready coding.
* **📊 Minimalist Spreadsheet Program:** A lightweight spreadsheet tool for manipulating tabular data without the overhead of heavy office suites. (Integrated in the CSV Analystics Module)

---

## 🗺️ Roadmap & Planned Features

We are actively expanding Vortex into the ultimate hybrid desktop-cloud ecosystem. The following modules are currently in the development pipeline:

* **☁️ Seamless GitHub Actions Hybrid-Pipeline:** A native GitHub OAuth integration enabling you to compile workflows on the fly. Write your code locally, configure environment targets through the GUI, and run/monitor your code anywhere in the world using the GitHub Mobile App as a remote console.
* **✍️ Document & Word Processing Module:** A streamlined rich-text / markdown processing module for rapid documentation.
* **📂 Embedded File Explorer:** A fast, system-independent file browser designed to handle local infrastructure effortlessly.

---

## 🛠️ Prerequisites & Installation

Vortex relies heavily on lightweight native tools. Before running the script, ensure you have the required dependencies installed on your system (e.g., via `apt` on MX Linux/Debian or `pacman` on Arch):

```bash
# Clone the repository
git clone https://github.com/vntx-labs/Vortex
cd Vortex

# Build the natives
cd native

#For Linux/MacOS
./build.sh

#For Windows
./build.bat

# Make the launcher executable
chmod +x vortex.sh

# Launch the hub
./vortex.sh
```

---

## 📜 License & Community

Vortex is built with the Unix philosophy in mind: *Do one thing, and do it well.* Contributions, feature requests, and security audits from the open-source community are highly welcome.

*Built for developers, by developers. Carry on.*
