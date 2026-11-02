inFAMOUS Second Son for PC (issn_pc) - Linux AppImage - TEST BUILD
==================================================================

The same port as the Windows build, as one AppImage: the launcher, the game and its libraries.
A test build: the game plays from the start; the first time each place or effect appears its
shaders compile and the game stutters, which goes away as the cache fills. Please report how far
you get and what goes wrong (see Reporting problems).

No game files are included. You need your own dump of inFAMOUS Second Son, made from your own copy
of the game on your own PS4: CUSA00223 (US) or CUSA00004 (Europe), as a folder that contains
eboot.bin, sce_module and sce_sys (with the 01.07 update laid over it or as a separate folder).

Requirements
- A 64-bit Linux desktop with glibc 2.38 or newer (ldd --version shows it: Ubuntu 24.04, Fedora
  39, current Arch and SteamOS), X11 or Wayland (XWayland).
- A 64-bit processor with AVX.
- A graphics card with Vulkan 1.3 and a recent driver: Mesa RADV for AMD (Mesa 24 or newer for
  FSR 4), the proprietary driver for NVIDIA.

Starting
1. Make the file executable: chmod +x issn-x86_64.AppImage (or in the file manager: Properties >
   Permissions > Allow executing as a program).
2. Run it. Choose the game folder (the one with eboot.bin), then click PLAY.
3. The launcher adds itself to your applications menu. "Add to Steam" (Home page) creates a Steam
   shortcut that starts the game directly; Steam is closed and opened again for it.
   If the AppImage does not start: install libfuse2 (Ubuntu: sudo apt install libfuse2t64), or
   run it with --appimage-extract-and-run.

Graphics
- Anti-aliasing (launcher: Graphics > Anti-aliasing): the game's own or FSR 4 (included; Radeon
  RX 7000 / 9000 on RADV). DLSS is Windows only (NVIDIA's Streamline). In the game, Home switches
  FSR 4 and the game's own anti-aliasing live.
- Internal resolution and Performance mode (launcher: Performance), Screen shape (Graphics: ultrawide
  and other shapes; play fullscreen): as on Windows.
- F12 saves a screenshot (user/screenshots), Insert opens the port's menu.

Controls
- DualShock 4 and DualSense: touchpad, motion sensors and the light bar work as on the PS4.
- Pads without a touchpad or motion sensors (Xbox, 8BitDo, ...): Select/Back is the touchpad
  click (with L1: the left side; hold it and push the right stick to swipe), L3 + R3 tilts the pad
  for the spray can, L1 rattles it, the right stick aims (launcher: Spray can stick speed).
- Keyboard and mouse: WASD moves, IJKL turns the camera; click in the game window and the mouse
  aims 1:1 (Y or F7 lets it go); left click shoots (R2), right click aims (L2); Space / Left Shift
  / C / F are Cross / Circle / Square / Triangle; Z held tilts the spray can, X rattles it, the
  mouse aims it (- / = change its speed).
  The launcher's Game > Controls page changes the buttons.

Data
- Saves, shader cache, the log and generated files: ~/.local/share/issn (ISSN_DATA_DIR moves it).
- Launcher options: ~/.config/issn-launcher.

Reporting problems
- Send ~/.local/share/issn/user/last_run.log with your distribution, graphics card and driver
  (vulkaninfo --summary), and say where in the game it happened.
- If the game freezes, press F10 once, wait a few seconds, then close it: the log then shows what
  each thread was doing.

Credits: axylpup's shadPS4 build for inFAMOUS Second Son (graphics), earlier inFAMOUS work by
Ayoub and HayatoG (iss_pc, github.com/HayatoG/iss_pc: video core, draw distance, sensor mode), the God of War III Remastered port (gow3_pc, Felipe Prado) and bloodborne_pc
(deadinside28, Supermedo) it is built on, the shadPS4 project. FSR 4: AMD FidelityFX (MIT).
Launcher title face: Bebas Neue (SIL Open Font License). Source: github.com/Liondarkz/issn_pc
(GPL-2.0-or-later).

This project is not affiliated with Sony Interactive Entertainment or Sucker Punch Productions.
inFAMOUS is a trademark of Sony Interactive Entertainment.
