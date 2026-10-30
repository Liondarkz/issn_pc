// port: SDL3 window for the Vulkan swapchain (X11 or Wayland).
#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <SDL3/SDL.h>
#ifdef __APPLE__
#include <SDL3/SDL_metal.h>
#endif
#ifdef _WIN32
#include <windows.h>
#endif
#include "common/assert.h"
#include "common/logging/log.h"
#include "sdl_window.h"
#include "issn_overlay.h"
#include "issn_graphics.h"
#include "issn_settings.h"

namespace Frontend {

WindowSDL::WindowSDL(s32 width_, s32 height_, const char* title) : width{width_}, height{height_} {
    // port: the desktop shows this name and matches the app id with the launcher's .desktop entry
    // (Alt+Tab, dock). The launcher sets ISSN_APP_NAME / ISSN_APP_ID per game.
    {
        const char* app_name = std::getenv("ISSN_APP_NAME");
        const char* app_id = std::getenv("ISSN_APP_ID");
        SDL_SetAppMetadata(app_name && *app_name ? app_name : "The Last Guardian", nullptr,
                           app_id && *app_id ? app_id : "tlg-pc");
        SDL_SetHint(SDL_HINT_APP_ID, app_id && *app_id ? app_id : "tlg-pc");
    }
    // issn: started from Steam (a non-Steam shortcut to the launcher), the environment carries
    // Steam Input's device filter: SDL is to hide the physical pad and use Steam's virtual one,
    // which belongs to the launcher Steam started, not to this process. Tester 2026-10-22: an
    // 8BitDo Ultimate C that worked before was "gamepad=none" once launched from Steam. The
    // filter is dropped (ISSN_KEEP_STEAM_PAD_FILTER=1 keeps it).
    for (const char* hint : {SDL_HINT_GAMECONTROLLER_IGNORE_DEVICES,
                             SDL_HINT_GAMECONTROLLER_IGNORE_DEVICES_EXCEPT}) {
        const char* value = std::getenv(hint);
        const char* keep = std::getenv("ISSN_KEEP_STEAM_PAD_FILTER");
        if (value && *value && !(keep && keep[0] == '1')) {
            std::printf("Window: %s=%s from Steam ignored (the pads stay visible)\n", hint, value);
            SDL_SetHintWithPriority(hint, "", SDL_HINT_OVERRIDE);
        }
    }
    // issn: under Steam the physical pad can be hidden from this process while Steam Input
    // presents a virtual one (VID 0x28de), which SDL skips unless allowed (tester 2026-10-22:
    // with the filter dropped SDL still saw 0 joysticks).
    if (std::getenv("SteamGameId") || std::getenv("SteamAppId") || std::getenv("SteamClientLaunch")) {
        std::printf("Window: started from Steam (SteamGameId=%s); Steam's virtual pad allowed\n",
                    std::getenv("SteamGameId") ? std::getenv("SteamGameId") : "-");
        SDL_SetHintWithPriority("SDL_GAMECONTROLLER_ALLOW_STEAM_VIRTUAL_GAMEPAD", "1",
                                SDL_HINT_OVERRIDE);
    }
#ifdef _WIN32
    // What XInput itself reports, before SDL (a pad Steam hides from this process is missing here).
    if (HMODULE xinput = LoadLibraryA("xinput1_4.dll")) {
        using GetState = DWORD(WINAPI*)(DWORD, void*);
        if (auto get_state = reinterpret_cast<GetState>(GetProcAddress(xinput, "XInputGetState"))) {
            unsigned char state[16]{};
            std::string slots;
            for (DWORD i = 0; i < 4; ++i) {
                if (get_state(i, state) == 0) {
                    slots += (slots.empty() ? "" : ", ") + std::to_string(i + 1);
                }
            }
            std::printf("Window: XInput controllers connected: %s\n",
                        slots.empty() ? "none" : slots.c_str());
        }
    }
#endif
    // As axylpup's build: pads keep working while another window has the focus.
    SDL_SetHint(SDL_HINT_JOYSTICK_ALLOW_BACKGROUND_EVENTS, "1");
    // Gamepads are sampled by runtime_pad.c; their events are pumped here with the window's.
    if (!SDL_InitSubSystem(SDL_INIT_VIDEO | SDL_INIT_GAMEPAD)) {
        UNREACHABLE_MSG("Failed to initialize SDL video: {}", SDL_GetError());
    }
    // port: the window opens at the game's size (1920x1080) unless the screen is smaller: on a
    // 1366x768 laptop it was larger than the screen and the picture was cut. It then opens at the
    // largest same-shape size that fits the usable area (taskbar and title bar kept clear); it can
    // still be maximized, resized or made fullscreen.
    SDL_Rect usable{};
    if (width_ > 0 && height_ > 0 && SDL_GetDisplayUsableBounds(SDL_GetPrimaryDisplay(), &usable) &&
        usable.w > 0 && usable.h > 0) {
        const double fit = std::min({1.0, usable.w * 0.95 / width_, (usable.h - 40) * 0.95 / height_});
        if (fit < 1.0) {
            const s32 w = std::max<s32>(640, s32(width_ * fit)), h = std::max<s32>(360, s32(height_ * fit));
            LOG_INFO(Frontend, "Window {}x{} does not fit the screen ({}x{} usable): opening at {}x{}", width_,
                     height_, usable.w, usable.h, w, h);
            width_ = w;
            height_ = h;
        }
    }
    SDL_PropertiesID props = SDL_CreateProperties();
    SDL_SetStringProperty(props, SDL_PROP_WINDOW_CREATE_TITLE_STRING, title);
    SDL_SetNumberProperty(props, SDL_PROP_WINDOW_CREATE_X_NUMBER, SDL_WINDOWPOS_CENTERED);
    SDL_SetNumberProperty(props, SDL_PROP_WINDOW_CREATE_Y_NUMBER, SDL_WINDOWPOS_CENTERED);
    SDL_SetNumberProperty(props, SDL_PROP_WINDOW_CREATE_WIDTH_NUMBER, width_);
    SDL_SetNumberProperty(props, SDL_PROP_WINDOW_CREATE_HEIGHT_NUMBER, height_);
    SDL_SetBooleanProperty(props, SDL_PROP_WINDOW_CREATE_RESIZABLE_BOOLEAN, true);
    SDL_SetBooleanProperty(props, SDL_PROP_WINDOW_CREATE_VULKAN_BOOLEAN, true);
    base_title = title;
    window = SDL_CreateWindowWithProperties(props);
    SDL_DestroyProperties(props);
    ASSERT_MSG(window, "Failed to create window: {}", SDL_GetError());
    // port: the game's own icon (run.py converts sce_sys/icon0.png to BMP, SDL's built-in format).
    if (const char* icon_path = std::getenv("ISSN_WINDOW_ICON")) {
        if (SDL_Surface* icon = SDL_LoadBMP(icon_path)) {
            SDL_SetWindowIcon(window, icon);
            SDL_DestroySurface(icon);
        }
    }

    const char* driver = SDL_GetCurrentVideoDriver();
    const SDL_PropertiesID wp = SDL_GetWindowProperties(window);
#ifdef _WIN32
    if (driver && !std::strcmp(driver, "windows")) {
        window_info.type = WindowSystemType::Windows;
        window_info.render_surface = SDL_GetPointerProperty(wp, SDL_PROP_WINDOW_WIN32_HWND_POINTER, nullptr);
    } else
#elif defined(__APPLE__)
    // port: MoltenVK draws into the CAMetalLayer of an SDL Metal view.
    if (driver && !std::strcmp(driver, "cocoa")) {
        window_info.type = WindowSystemType::Metal;
        window_info.render_surface = SDL_Metal_GetLayer(SDL_Metal_CreateView(window));
    } else
#endif
    if (driver && !std::strcmp(driver, "x11")) {
        window_info.type = WindowSystemType::X11;
        window_info.display_connection = SDL_GetPointerProperty(wp, SDL_PROP_WINDOW_X11_DISPLAY_POINTER, nullptr);
        window_info.render_surface = reinterpret_cast<void*>(SDL_GetNumberProperty(wp, SDL_PROP_WINDOW_X11_WINDOW_NUMBER, 0));
    } else if (driver && !std::strcmp(driver, "wayland")) {
        window_info.type = WindowSystemType::Wayland;
        window_info.display_connection = SDL_GetPointerProperty(wp, SDL_PROP_WINDOW_WAYLAND_DISPLAY_POINTER, nullptr);
        window_info.render_surface = SDL_GetPointerProperty(wp, SDL_PROP_WINDOW_WAYLAND_SURFACE_POINTER, nullptr);
    } else {
        UNREACHABLE_MSG("Unsupported SDL video driver {}", driver ? driver : "(none)");
    }
    // port: ISSN_FULLSCREEN=1 (older launchers) still asks for fullscreen.
    if (const char* fullscreen = std::getenv("ISSN_FULLSCREEN"); fullscreen && fullscreen[0] == '1' &&
        IssnSettings::Get().display_mode == IssnGraphics::Windowed) {
        IssnSettings::Get().display_mode = IssnGraphics::Borderless;
    }
    ApplyDisplayMode(IssnSettings::Get().display_mode);
    // port: no system cursor over the game; the in-game menu draws its own (IssnOverlay::SetOpen).
    // (ps4_core: UpdateMouseCapture shows it again while Esc keeps the game's mouse released.)
    SDL_HideCursor();
    int w = 0, h = 0;
    SDL_GetWindowSizeInPixels(window, &w, &h);
    width = w;
    height = h;
    LOG_INFO(Frontend, "Window {}x{} on {}", w, h, driver);
}

// Windowed, borderless (desktop-sized, no mode change) or exclusive fullscreen at the desktop
// mode. Window thread only (SDL). The swapchain follows the size events.
void WindowSDL::ApplyDisplayMode(int mode) {
    display_mode = mode;
    if (mode == IssnGraphics::Windowed) {
        SDL_SetWindowFullscreen(window, false);
    } else {
        const SDL_DisplayMode* desktop = SDL_GetDesktopDisplayMode(SDL_GetDisplayForWindow(window));
        const bool exclusive = mode == IssnGraphics::Fullscreen && desktop;
        if (!SDL_SetWindowFullscreenMode(window, exclusive ? desktop : nullptr)) {
            LOG_WARNING(Frontend, "Fullscreen mode: {}", SDL_GetError());
        }
        SDL_SetWindowFullscreen(window, true);
    }
    SDL_SyncWindow(window);
    std::printf("Window: %s\n", IssnGraphics::DisplayModeNames[mode]);
}

WindowSDL::~WindowSDL() {
    SDL_DestroyWindow(window);
}

void WindowSDL::BeginTextInput(const std::string& initial, const std::string& prompt) {
    std::scoped_lock lock{text_mutex};
    text = initial;
    text_prompt = prompt;
    text_state = 0;
    text_requested = true;
}

int WindowSDL::PollTextInput(std::string& out) {
    std::scoped_lock lock{text_mutex};
    out = text;
    return text_state;
}

void WindowSDL::UpdateTextTitle() {
    const std::string title = text_active ? base_title + " \u2014 " + text_prompt + ": " + text + "_  (Enter = OK, Esc = cancel)"
                                          : base_title;
    SDL_SetWindowTitle(window, title.c_str());
    IssnOverlay::SetTextEntry(text_active, text_prompt, text);
}

bool WindowSDL::PollEvents() {
    {
        std::scoped_lock lock{text_mutex};
        if (text_requested) { // SDL text input must be toggled from the window thread
            text_requested = false;
            text_active = true;
            SDL_StartTextInput(window);
            UpdateTextTitle();
        }
    }
    if (!text_active) {
        IssnOverlay::UpdateTextInput(window);
    }
    if (const int mode = IssnSettings::Get().display_mode; mode != display_mode) {
        ApplyDisplayMode(mode);
    }
    SDL_Event event;
    while (SDL_PollEvent(&event)) {
        if (text_active && (event.type == SDL_EVENT_TEXT_INPUT || event.type == SDL_EVENT_KEY_DOWN)) {
            std::scoped_lock lock{text_mutex};
            if (event.type == SDL_EVENT_TEXT_INPUT) {
                text += event.text.text;
            } else if (event.key.key == SDLK_BACKSPACE && !text.empty()) {
                size_t cut = text.size() - 1; // drop one UTF-8 code point
                while (cut > 0 && (static_cast<unsigned char>(text[cut]) & 0xC0) == 0x80) --cut;
                text.erase(cut);
            } else if (event.key.key == SDLK_RETURN || event.key.key == SDLK_KP_ENTER || event.key.key == SDLK_ESCAPE) {
                text_state = event.key.key == SDLK_ESCAPE ? 2 : 1;
                text_active = false;
                SDL_StopTextInput(window);
            }
            UpdateTextTitle();
            continue;
        }
        // The controller finishes the text dialog too: Cross (A) accepts, Circle (B) cancels.
        if (text_active && event.type == SDL_EVENT_GAMEPAD_BUTTON_DOWN &&
            (event.gbutton.button == SDL_GAMEPAD_BUTTON_SOUTH ||
             event.gbutton.button == SDL_GAMEPAD_BUTTON_EAST)) {
            std::scoped_lock lock{text_mutex};
            text_state = event.gbutton.button == SDL_GAMEPAD_BUTTON_SOUTH ? 1 : 2;
            text_active = false;
            SDL_StopTextInput(window);
            UpdateTextTitle();
            continue;
        }
        if (IssnOverlay::HandleEvent(event)) {
            continue;
        }
        switch (event.type) {
        case SDL_EVENT_WINDOW_PIXEL_SIZE_CHANGED:
        case SDL_EVENT_WINDOW_RESIZED: {
            int w = 0, h = 0;
            SDL_GetWindowSizeInPixels(window, &w, &h);
            width = w;
            height = h;
            break;
        }
        case SDL_EVENT_QUIT:
        case SDL_EVENT_WINDOW_CLOSE_REQUESTED:
            // issn: say why the game closes (the log otherwise just ends with the counters).
            std::printf("Window: %s: closing the game\n",
                        event.type == SDL_EVENT_QUIT
                            ? "quit requested (Alt+F4, the console window closed or the system)"
                            : "window close button");
            std::fflush(stdout);
            is_open = false;
            break;
        default:
            break;
        }
    }
    return is_open;
}

} // namespace Frontend
