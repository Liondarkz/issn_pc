inFAMOUS Second Son for PC (issn_pc) - TEST BUILD 0.3.5.1
========================================================

A test build: the game plays from the start, at about 90-105 FPS standing in the open city on a
Ryzen 7 3700X + RX 9060 XT. The first start prepares the shaders once (a few minutes, with a
progress screen); new ones that still appear while playing compile in the background. Please
report how far you get and what goes wrong (see Reporting problems).

Community: Discord https://discord.gg/y82HQ5uTQg (news, help, test builds).

XeSS and DLSS (optional)
Intel's and NVIDIA's files are not included. When you choose XeSS or DLSS in Graphics >
Anti-aliasing, the launcher downloads them from the vendors' own GitHub releases (XeSS SDK 3.0.2,
about 60 MB; Streamline SDK 2.14.16, about 35 MB), checks each file and puts them in bin\xess and
bin\dlss next to issn-probe.exe. Graphics > Download XeSS now / Download DLSS now does the same.
XeSS runs on Intel Arc, Radeon RX 5000 and later and GeForce GTX 10 and later; DLSS needs an
NVIDIA RTX card. XeSS needs the Visual C++ 2015-2022 runtime (vc_redist.x64). Without the files
the game keeps its own anti-aliasing.

What's new in 0.3.6.1
- Fixed: after updating to 0.3.6 from the launcher, the game could start without its shader
  seed (only a user\cache\issn-seed-0.3.6.part folder was left) and compiled every shader while
  playing: heavy stutter. The seed is now placed even when Windows holds the new files for a
  moment; updating to 0.3.6.1 brings it.
- The log names the shader cache folder in use and why ("Shader cache: ...").
- The FPS counter shows the frames on the screen, as MSI Afterburner and similar tools count them,
  with the game's own frame rate next to it when they differ (frame pacing, frame generation):
  "100 FPS (game 50)"; with frame generation working, "100 FPS (game 50 + FG 50)".
- Performance > Frame generation (FSR 3.1, test): a frame between every two of the game; needs
  FSR 4, XeSS or DLSS as anti-aliasing. A first test version: the HUD can blur in motion.

What's new in 0.3.6
- XeSS (Intel) as anti-aliasing / upscaler: Graphics > Anti-aliasing > XeSS. It runs on Intel Arc,
  Radeon RX 5000 and later and GeForce GTX 10 and later, so cards without FSR 4 or DLSS get a
  modern anti-aliasing too (about 1 ms per frame on a Radeon RX 9060 XT).
- XeSS and DLSS files download by themselves: pick one and the launcher fetches Intel's and
  NVIDIA's files from their GitHub releases and checks them. No more copying DLLs by hand.
- Graphics > Your PC: the launcher finds your graphics card, processor and memory, says what to
  expect in the city and sets the graphics for a goal. Recommended is the testers' pick for most
  PCs: Balanced performance mode, original detail, shaders in the background and, on a 100 Hz or
  faster screen, even frame pacing at half the refresh rate.
- Performance > Frame pacing: half or a third of the screen's refresh rate with every frame shown
  for the same time, so motion is smooth without G-Sync / FreeSync. The FPS counter now shows the
  game's real frame rate in this mode (it showed the screen's refresh rate).
- Faster Windows build (link-time and profile-guided optimisation from a training run): about
  100 FPS in the city on a Ryzen 7 3700X in our tests.
- Fewer hitches while the city streams in: file reads into memory the graphics card uses no
  longer fault page by page.
- In-game menu (Insert / R3+L2) > Controls: change any key by pressing the new one, live.
- Keyboard: Options (Start) is Escape for everyone; R held with the mouse moved is a finger on the
  touchpad (hand scanner, engine pull) - G is still the touchpad's left side.
- Game > Skip the intro logos.
- Launcher: a new look (side bar with the pages, rounded buttons and switches), a What's new page
  with the release notes, a Help page that checks the installation and the last game log and
  says how to fix what it finds, Resolution on the Home page, and the Discord link. Updating from
  the launcher keeps the shader seed of the new release.

What's new in 0.3.5.1
- Fixed: the picture staying dark at night (only lights and glow visible) on some PCs. The
  launcher's Graphics > Effects at Light or Off kept the game from reading its light metering back
  from the graphics card; that choice is gone and a saved Light / Off now plays as Full.
- Fixed: on some NVIDIA PCs with DLSS the port's code fixes did not apply (the 0x1cfaaf crash
  guard, draw distance, 1:1 mouse aim); the log now shows them all as ready.
- The shader seed is back (0.3.5 shipped without one, so every shader compiled while playing),
  and it now also reaches a user folder in Documents. On NVIDIA and older AMD cards the effect
  shaders come ready; the rest still compile in the background while playing.
- Game > Controls > Stick dead zone (5% to 20%): raise it if Delsin walks or the camera turns on
  its own (a worn stick drifting).
- Black screen that never recovered on some PCs (the window reported as "suboptimal"): those
  frames are now shown; with HDR on and no frame shown for two seconds, HDR is turned off.
- Graphics > Texture filtering: anisotropic filtering 4x / 8x / 16x over the game's own.
- Resolution: 5K and 8K (experimental). DLSS waits for its inputs at render sizes other than
  1080p instead of stepping aside at once (untested: no NVIDIA card here).

What's new in 0.3.5
- Native resolutions (launcher: Graphics > Resolution, from 800x600-class 1064x600 up to 4K): the
  game draws at the chosen size itself, with the memory it needs above 1080p (no more black screen
  at 1440p / 4K). FSR 4 / DLSS quality is now its own setting next to it.
- Button icons for the keyboard and mouse or an Xbox controller (launcher: Game > Button
  icons; idea of u/humonculus87 on Reddit): the game's hints show the keys and mouse buttons (they
  follow your key changes) or the Xbox letters, and so do the prompts in the world: subdue and
  execute (F / C, or Y / X in the karma colours), the d-pad of missions and karma choices (the
  arrow keys, the lit one in its colour), the spray can's painted R2 (the left mouse button, or
  RT). The first start with them makes copies of four game packages (~470 MB) in the port's out
  folder; the game files are not changed.
- Keyboard: R is the touchpad (it "recharges" the powers), and holding it is both thumbs (the
  drains, the car on Reggie); R with W/A/S/D (or an arrow) is a swipe (doors, wreckage, the
  Mobile Command Center's core: hold R + W and click to hit it); G is now the touchpad's left side
  alone (photo mode). The touchpad prompts in the world show the R key (the View button with an
  Xbox pad), and the game's touchpad hints name the keys in English and Portuguese.
- Fixed: the game closing after the intro on some Intel + NVIDIA PCs (crash at 0x1cfaaf /
  0x1cf8f9); freezes when absorbing a power and on rooftops (1.07); Alt+Tab in fullscreen closing
  the game; FSR 4 Quality crashing at start; fixes from HayatoG's iss_pc (freezes, a memory error
  on start, error 1450).
- A game version other than 01.07 now stops at start with what to do, instead of a crash later
  (a 01.07 eboot with an older param.sfo is accepted).
- Spray can on the right stick: calmer (30 degrees a second at full push, gentle near the
  centre); Game > Spray can stick speed.
- Launcher: "Made by Liondarkz" and Advanced > About with the credits; Advanced > Support the
  project (Pix, Ko-fi) for whoever wants to help: the port stays free.

What's new in 0.3.4
- Intel processors: the game no longer closes right after the Sucker Punch logo. It uses AMD's
  SSE4A instruction EXTRQ (96 places), which no Intel CPU has; the port now replaces it with an
  equivalent on Intel (Windows and Linux).
- Ultrawide and other screen shapes (launcher: Graphics > Screen shape, from axylpup's 1.1): the
  game fills 21:9, 32:9, 16:10 ... and sees more at the sides (or above and below on 16:10); the
  HUD moves to the edges and movies keep their shape with black bars. Automatic follows the
  window: play in fullscreen on an ultrawide monitor.
- Spray can with the mouse: about 9x calmer (2.5 degrees per 100 px, as axylpup's build); - / =
  change its speed while the pad is tilted (ISSN_SPRAY_MOUSE sets it).

What's new in 0.3.3
- Fixed: after playing a while with 0.3.2 the game could stop opening (the shader cache paired
  shaders wrongly: a new shader took the slot of one the seed had left out). New shaders now
  never reuse a slot the cache still names, and the cache starts in a new folder
  (user\cache\issn-seed-0.3.3); user\cache\issn-seed-0.3.2 can be deleted.
- axylpup's 1.1.1 fixes in the graphics core: AMD cards whose driver refused a fast-path shader
  variant (RX 6800 XT, RX 570) no longer close; 6-12 GB cards keep the big mesh buffer in video
  memory and try smaller sizes before running out; large uploads go through in pieces when the
  driver refuses one big buffer; out of memory shows a message instead of closing silently.

What's new in 0.3.2
- Shaders prepared on the first start: the package carries a shader seed (the list of the game's
  shaders and their settings, no game code) and the first start makes them from your own game
  files, with a progress screen (a few minutes, once). Effect shaders (smoke, powers, particles)
  come ready on every graphics card; the other shaders on cards like the tester's (recent AMD on
  Windows), elsewhere they compile while playing. The log says which: "all stages" or "compute
  stages only".
- Shaders that are still new compile in the background: the game no longer stops for them, the
  thing they draw appears a moment later (launcher: Performance > Shader compilation; "In the
  frame" is the old behaviour). Effects get priority, since the game has to wait for those.
- Internal resolution (launcher: Performance > Internal resolution): the game draws at 83%, 67%,
  58% or 50% of 1080 lines. With FSR 4 the picture is rebuilt at the window size by the matching
  preset (67% = Quality, 58% = Balanced, 50% = Performance); with the game's own AA it is only
  scaled up. Helps when the graphics card is the limit, not the processor.
- Every 10 s while new shaders appear the log says how many and how long anything waited.
- The game writes user\last_run.log itself also with "Close the launcher on play".
- The shader cache moves to a new folder (user\cache\issn-seed-<version>); user\cache\CUSA00223 or
  CUSA00004 from older versions can be deleted.

What's new in 0.3.1
- axylpup's 1.1 fixes in the graphics core: texture-cache hangs, 8 GB AMD cards (RX 6600)
  closing while the world loads, less video memory below 10 GB and less RAM below 24 GB.
- CPUs with fewer than 20 threads no longer give a whole core to the GPU feeder (axylpup's
  measurement: 8-12% more FPS).
- Performance mode (launcher: Performance > Performance mode): Balanced / Maximum use the
  game's own lower settings. On the tester's Ryzen 7 3700X + RX 9060 XT in the city: about
  55-65 FPS before, 80+ with Maximum.
- FSR 4 / DLSS find the game's images by themselves on every game version; Home switches them
  and the game's own AA live; FSR 4's GPU time is in the log (about 1.9 ms on an RX 9060 XT).
- 1:1 mouse aim (axylpup's camera hook): click in the window; Y / F7 lets the mouse go.
- A damaged shader cache file is skipped instead of stopping the game.
- Pads keep working when the game is started from Steam.
- The first start after updating prepares the shaders again (a few minutes, some stutter).

What's new in 0.3.0
- New graphics core: axylpup's shadPS4 build for this game (github.com/axylpup/shadPS4, release
  1.0.2), about 2.5x the frame rate of 0.2 (city, standing: 38 -> 90-105 FPS on the tester's PC).
- Anti-aliasing (launcher: Graphics > Anti-aliasing): the game's own, FSR 4 Native AA (included;
  Radeon RX 7000 / 9000) or DLSS (NVIDIA RTX; NVIDIA's files are not included, the launcher shows
  where they go and where to download them). In the game, Home switches FSR 4 / DLSS and the
  game's own anti-aliasing live (the FPS bar shows which one runs).
- Frame rate limit and VSync from the launcher and the in-game menu (applied on restart);
  Unlimited by default, the game keeps its speed at any frame rate.
- Draw distance back to the game's own, with Long and Very long for less pop-in.
- F12 saves a screenshot (user\screenshots); a loading screen while the shader cache loads.
- New launcher: inFAMOUS look, the dump's art on the home page; "Add to Steam" closes and reopens
  Steam by itself and makes inFAMOUS pictures.

No game files are included. You need your own dump of inFAMOUS Second Son, made from your own
copy of the game on your own PS4: CUSA00004 (Europe) or CUSA00223, base game version 01.00, as
a folder that contains eboot.bin, sce_module and sce_sys. How to dump it: docs/issn/DUMP.md.

Built on the God of War III Remastered port (gow3_pc, by Felipe Prado), which comes from
bloodborne_pc (deadinside28, Supermedo). Graphics: axylpup's shadPS4 build for inFAMOUS Second Son
(github.com/axylpup/shadPS4), from the shadPS4 video core, with earlier work by Ayoub
(shadPS4-AYOUB1080p) and HayatoG (iss_pc). FSR 4: AMD FidelityFX (MIT). Launcher title face: Bebas
Neue (SIL Open Font License).

Requirements
- Windows 10 (1903 or newer) or Windows 11, 64-bit.
- A 64-bit processor with AVX.
- A graphics card with Vulkan 1.3 and an up-to-date driver.

Starting
1. Extract this folder anywhere, for example C:\Games\issn-windows (not inside the zip).
2. Open "inFAMOUS Second Son.exe". If Windows shows "Windows protected your PC", click More info,
   then Run anyway: the program is not digitally signed.
3. Choose the game folder (the one with eboot.bin), then click PLAY.

Controller
- DualShock 4 and DualSense: touchpad, motion sensors (shake and tilt the spray can) and the
  light bar work as on the PS4.
- Pads without a touchpad or motion sensors (Xbox, 8BitDo, ...), on by itself for them (launcher:
  Game > Controls > Controller without touchpad):
  - Spray can: L3 + R3 together tilts the pad like the spray can; L1 rattles it, the right
    stick aims (launcher: Spray can stick speed; - / = in the game). L3 + R3 again levels it.
  - Touchpad swipe (lift, push): hold Select/Back and push the right stick that way.
  - Two fingers on the touchpad (lifting the tree): hold Select/Back + RB (R1).
  - Touchpad click: Select/Back.
- Keyboard and mouse (axylpup's layout; launcher: Game > Controls changes the buttons):
  - WASD moves, IJKL turns the camera. Click in the game window and the mouse aims 1:1 (axylpup's
    camera hook); Y or F7 lets the mouse go and takes it back; - / = change the mouse speed.
  - Space Cross, Left Shift Circle, C Square, F Triangle (numpad 2/6/4/8 too), Q L1, E R1,
    U L3, O R3, Esc Options, arrows d-pad.
  - Left click R2 (shoot), right click L2 (aim) while the mouse aims; 1 / 3 also work.
  - R: the touchpad, held with both thumbs (drains, the car on Reggie); G: its left side alone
    (photo mode). R + W/A/S/D (or an arrow): a swipe that way (doors, wreckage, the Mobile Command
    Center core); keep the key down to hold the finger there and click to hit.
  - Spray can: hold Z to tilt the pad, tap Z or hold X to rattle it, move the mouse to aim
    (- / = change its speed while the pad is tilted).
  - F12 screenshot, Insert the port's menu.
- Started from Steam: the port ignores Steam Input's device filter so the controller is seen
  (ISSN_KEEP_STEAM_PAD_FILTER=1 keeps it). If a pad still is not found, the log lists what SDL
  sees ("joystick(s) seen by SDL").
- Text and voices: launcher > Game > Game language (Automatic follows the launcher: Portuguese
  gives the Brazilian dub on the US release).

Data
- Saves, shader cache and the log: user\ next to inFAMOUS Second Son.exe.
- Settings: issn.ini next to inFAMOUS Second Son.exe; launcher options in %APPDATA%\issn-launcher.
- Generated files (prepared game image, patches): out\.
- The game process is bin\issn-probe.exe (for RTSS, Afterburner or ReShade with Vulkan).

Reporting problems
- Click "Save a bug report" on the Advanced page (or send user\last_run.log) with a screenshot or a
  short video, and say where in the game it happened.
- If the game freezes, press F10 once, wait a few seconds, then close it: the log then shows
  what each thread was doing.
- The game does not open: check the Log page, and send user\last_run.log with your report.
- Check your dump: python tools\issn\inspect_dump.py "<game folder>".

This project is not affiliated with Sony Interactive Entertainment or Sucker Punch Productions.
inFAMOUS is a trademark of Sony Interactive Entertainment.
