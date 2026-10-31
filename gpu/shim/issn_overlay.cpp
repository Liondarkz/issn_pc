// SPDX-License-Identifier: GPL-2.0-or-later
#include "issn_overlay.h"

#include <algorithm>
#include <atomic>
#include <cstdint>
#include <cfloat>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <mutex>
#include <string>
#include <vector>

#include <SDL3/SDL.h>
#include "issn_settings.h"
#include "issn_orbs.h"
#include "issn_actors.h"
#include "issn_graphics.h"
#include "issn_perf_stats.h"
#include "../../src/actor_hook.h"

extern IssnActorSlot issn_actor_slots[2];
extern "C" void runtime_restart(void); // src/probe.c: starts run.py again, ends this process
// issn 0.3.6: keys changed live from the Controls tab (src/runtime_pad.c).
extern "C" void runtime_pad_apply_key_map(const char* spec);
extern "C" const char* runtime_pad_key_name(const char* button);
#include "common/elf_info.h"
#include "imgui.h"
#include "imgui_internal.h" // RegisterUserTexture (save pictures)
#include "imgui_impl_vulkan.h"
#include "video_core/renderdoc.h"
#include "video_core/renderer_vulkan/vk_dlss.h"
#include "video_core/renderer_vulkan/issn_framegen.h"
#include "video_core/renderer_vulkan/vk_instance.h"
#include "video_core/renderer_vulkan/vk_scheduler.h"

// DejaVu Sans (Cyrillic), embedded (third_party/fonts, Bitstream Vera license).
#ifdef _WIN32
asm(".section .rdata,\"dr\"\n"
    ".balign 16\n"
    ".global issn_font_ttf\n"
    "issn_font_ttf:\n"
    ".incbin \"" ISSN_FONT_PATH "\"\n"
    ".global issn_font_ttf_end\n"
    "issn_font_ttf_end:\n"
    ".text\n");
#elif defined(__APPLE__)
// Mach-O: C symbols carry a leading underscore, read-only data is __TEXT,__const.
asm(".section __TEXT,__const\n"
    ".p2align 4\n"
    ".private_extern _issn_font_ttf\n"
    ".globl _issn_font_ttf\n"
    "_issn_font_ttf:\n"
    ".incbin \"" ISSN_FONT_PATH "\"\n"
    ".private_extern _issn_font_ttf_end\n"
    ".globl _issn_font_ttf_end\n"
    "_issn_font_ttf_end:\n"
    ".text\n");
#else
asm(".section .rodata\n"
    ".balign 16\n"
    ".hidden issn_font_ttf\n"
    ".global issn_font_ttf\n"
    "issn_font_ttf:\n"
    ".incbin \"" ISSN_FONT_PATH "\"\n"
    ".hidden issn_font_ttf_end\n"
    ".global issn_font_ttf_end\n"
    "issn_font_ttf_end:\n"
    ".previous\n");
#endif
extern "C" const unsigned char issn_font_ttf[];
extern "C" const unsigned char issn_font_ttf_end[];

namespace IssnOverlay {

namespace {

std::mutex imgui_mutex; // the ImGui context: window thread (input) and present thread
bool initialized = false;
std::atomic<bool> menu_open{false};
bool r3_down = false, l2_down = false;
/// issn: the mouse camera (Y / F7 off and on); a click in the window turns it on while wanted.
/// ISSN_MOUSE_CAMERA=0: only Y / F7 turn it on.
bool mouse_camera_wanted = [] {
    const char* v = std::getenv("ISSN_MOUSE_CAMERA");
    return !(v && v[0] == '0');
}();
std::atomic<bool> loading{false};
std::atomic<u32> loading_done{0}, loading_total{0};
std::atomic<u32> background_done{0}, background_total{0};
bool dirty = false; // settings changed while open: saved on close
int requested_tab = -1; // switch on the next frame (L1/R1, Q/E, menu opened)
// The game's text dialog (SetTextEntry), guarded by imgui_mutex.
bool text_entry_active = false;
std::string text_entry_prompt, text_entry_text;
// The game's save list (sceSaveDataDialog): a choice made with the arrows/D-pad.
std::atomic<bool> choice_active{false};
std::string choice_title;
std::vector<std::string> choice_items;
int choice_index = 0;
std::atomic<int> choice_result{-1}; // -1 open, -2 cancelled, else the chosen index
// Save pictures: decoded by BeginChoice, made textures on the present thread (null = none).
// Closed lists' textures wait in choice_retired until the backend has destroyed them.
std::vector<IssnPng::Image> choice_pictures;
std::vector<ImTextureData*> choice_textures, choice_retired;
std::atomic<bool> choice_retiring{false};
float base_scale = 1.0f;

// Present rate for the FPS counter.
std::chrono::steady_clock::time_point last_present{};
std::atomic<std::uint64_t> game_frames{0}; ///< NoteGameFrame
std::uint64_t seen_frames = 0;
float frame_ms_avg = 0.0f;   ///< between game frames (NoteGameFrame)
float present_ms_avg = 0.0f; ///< between frames on the screen (every present)

void SetOpen(bool value) {
    if (menu_open.exchange(value) == value) {
        return;
    }
    ImGui::GetIO().MouseDrawCursor = value;
    if (value) {
        requested_tab = IssnSettings::Get().menu_tab;
    }
    if (!value && dirty) {
        dirty = false;
        IssnSettings::Save();
    }
}

ImGuiKey KeyFromSdl(SDL_Keycode key) {
    switch (key) {
    case SDLK_TAB: return ImGuiKey_Tab;
    case SDLK_LEFT: return ImGuiKey_LeftArrow;
    case SDLK_RIGHT: return ImGuiKey_RightArrow;
    case SDLK_UP: return ImGuiKey_UpArrow;
    case SDLK_DOWN: return ImGuiKey_DownArrow;
    case SDLK_PAGEUP: return ImGuiKey_PageUp;
    case SDLK_PAGEDOWN: return ImGuiKey_PageDown;
    case SDLK_HOME: return ImGuiKey_Home;
    case SDLK_END: return ImGuiKey_End;
    case SDLK_DELETE: return ImGuiKey_Delete;
    case SDLK_BACKSPACE: return ImGuiKey_Backspace;
    case SDLK_SPACE: return ImGuiKey_Space;
    case SDLK_RETURN: return ImGuiKey_Enter;
    case SDLK_KP_ENTER: return ImGuiKey_KeypadEnter;
    case SDLK_ESCAPE: return ImGuiKey_Escape;
    case SDLK_Q: return ImGuiKey_Q;
    case SDLK_E: return ImGuiKey_E;
    case SDLK_LCTRL: return ImGuiKey_LeftCtrl;
    case SDLK_RCTRL: return ImGuiKey_RightCtrl;
    case SDLK_LSHIFT: return ImGuiKey_LeftShift;
    case SDLK_RSHIFT: return ImGuiKey_RightShift;
    case SDLK_LALT: return ImGuiKey_LeftAlt;
    case SDLK_RALT: return ImGuiKey_RightAlt;
    default: return ImGuiKey_None;
    }
}

ImGuiKey KeyFromGamepad(u8 button) {
    switch (button) {
    case SDL_GAMEPAD_BUTTON_SOUTH: return ImGuiKey_GamepadFaceDown;
    case SDL_GAMEPAD_BUTTON_EAST: return ImGuiKey_GamepadFaceRight;
    case SDL_GAMEPAD_BUTTON_WEST: return ImGuiKey_GamepadFaceLeft;
    case SDL_GAMEPAD_BUTTON_NORTH: return ImGuiKey_GamepadFaceUp;
    case SDL_GAMEPAD_BUTTON_DPAD_UP: return ImGuiKey_GamepadDpadUp;
    case SDL_GAMEPAD_BUTTON_DPAD_DOWN: return ImGuiKey_GamepadDpadDown;
    case SDL_GAMEPAD_BUTTON_DPAD_LEFT: return ImGuiKey_GamepadDpadLeft;
    case SDL_GAMEPAD_BUTTON_DPAD_RIGHT: return ImGuiKey_GamepadDpadRight;
    case SDL_GAMEPAD_BUTTON_LEFT_SHOULDER: return ImGuiKey_GamepadL1;
    case SDL_GAMEPAD_BUTTON_RIGHT_SHOULDER: return ImGuiKey_GamepadR1;
    case SDL_GAMEPAD_BUTTON_START: return ImGuiKey_GamepadStart;
    case SDL_GAMEPAD_BUTTON_BACK: return ImGuiKey_GamepadBack;
    default: return ImGuiKey_None;
    }
}

float PixelDensity(SDL_WindowID id) {
    SDL_Window* window = SDL_GetWindowFromID(id);
    const float density = window ? SDL_GetWindowPixelDensity(window) : 1.0f;
    return density > 0.0f ? density : 1.0f;
}

// Marks the settings dirty when a widget changed them.
template <typename T>
void Store(std::atomic<T>& target, T value, bool changed) {
    if (changed) {
        target = value;
        dirty = true;
    }
}

void Checkbox(const char* label, std::atomic<bool>& value) {
    bool v = value;
    const bool changed = ImGui::Checkbox(label, &v);
    Store(value, v, changed);
}

// A combo over `count` labels storing an index or a value from `values`.
void Choice(const char* label, std::atomic<int>& target, const char* const* labels, const int* values,
            int count) {
    int current = 0;
    for (int i = 0; i < count; ++i) {
        if ((values ? values[i] : i) == target.load()) current = i;
    }
    if (ImGui::Combo(label, &current, labels, count)) {
        Store(target, values ? values[current] : current, true);
    }
}

// Controller button names in the help text follow ISSN_GLYPHS (run.py sets xbox or nintendo when
// the glyph feature is on); unset is PlayStation. Read once: the environment is fixed for a run.
static const char* PadText(const char* playstation, const char* xbox, const char* nintendo) {
    static const char* style = std::getenv("ISSN_GLYPHS");
    if (!style) return playstation;
    return !std::strcmp(style, "xbox") ? xbox : !std::strcmp(style, "nintendo") ? nintendo : playstation;
}

// The note for the item under the mouse or the gamepad/keyboard focus, shown in the footer.
const char* footer_help = nullptr;

void Help(const char* text) {
    if (text && (ImGui::IsItemHovered(ImGuiHoveredFlags_AllowWhenDisabled) || ImGui::IsItemFocused())) {
        footer_help = text;
    }
}

// Two-column rows: the name on the left, the control on the right.
bool BeginRows(const char* id) {
    if (!ImGui::BeginTable(id, 2, ImGuiTableFlags_SizingStretchProp)) {
        return false;
    }
    ImGui::TableSetupColumn("name", ImGuiTableColumnFlags_WidthStretch, 0.52f);
    ImGui::TableSetupColumn("value", ImGuiTableColumnFlags_WidthStretch, 0.48f);
    return true;
}

void Row(const char* label) {
    ImGui::TableNextRow();
    ImGui::TableNextColumn();
    ImGui::AlignTextToFramePadding();
    ImGui::TextUnformatted(label);
    ImGui::TableNextColumn();
    ImGui::SetNextItemWidth(-FLT_MIN);
    ImGui::PushID(label);
}

void CheckRow(const char* label, std::atomic<bool>& value, const char* help = nullptr) {
    Row(label);
    Checkbox("##value", value);
    Help(help);
    ImGui::PopID();
}

void ChoiceRow(const char* label, std::atomic<int>& target, const char* const* labels, const int* values,
               int count, const char* help = nullptr) {
    Row(label);
    Choice("##value", target, labels, values, count);
    Help(help);
    ImGui::PopID();
}

// A 0.1x to 100x multiplier with a 1x button.
void MultiplierRow(const char* label, std::atomic<float>& value, const char* help) {
    Row(label);
    const ImGuiStyle& style = ImGui::GetStyle();
    ImGui::SetNextItemWidth(-(ImGui::CalcTextSize("1x").x + style.FramePadding.x * 2 + style.ItemSpacing.x));
    float v = value;
    if (ImGui::SliderFloat("##value", &v, 0.1f, 100.0f, "%.2fx",
                           ImGuiSliderFlags_Logarithmic | ImGuiSliderFlags_AlwaysClamp)) {
        value = IssnOrbs::ClampMultiplier(v);
        dirty = true;
    }
    Help(help);
    ImGui::SameLine();
    if (ImGui::SmallButton("1x")) {
        value = 1.0f;
        dirty = true;
    }
    Help(help);
    ImGui::PopID();
}

void InfoRow(const char* label, const char* text) {
    Row(label);
    ImGui::TextDisabled("%s", text);
    ImGui::PopID();
}

void SliderRow(const char* label, std::atomic<int>& value, int lo, int hi, const char* format, const char* help) {
    Row(label);
    int v = value;
    if (ImGui::SliderInt("##value", &v, lo, hi, format, ImGuiSliderFlags_AlwaysClamp)) {
        Store(value, std::clamp(v, lo, hi), true);
    }
    Help(help);
    ImGui::PopID();
}

const char* UpscalerLabel(int upscaler) {
    switch (upscaler) {
    case IssnSettings::UpscalerFsr3: return "FSR 3.1";
    case IssnSettings::UpscalerFsr4: return "FSR 4";
    case IssnSettings::UpscalerFsr411: return "FSR 4.1.1";
    case IssnSettings::UpscalerTaa: return "TAA";
    case IssnSettings::UpscalerDlss: return "DLSS";
    default: return "";
    }
}

const Vulkan::Instance* overlay_instance = nullptr; // for the overlay's measurements

// The performance overlay's text with the current settings (also the menu's preview).
std::string OverlayText() {
    namespace P = IssnPerfStats;
    const auto& s = IssnSettings::Get();
    const unsigned items = s.overlay_items;
    if ((items & (P::Gpu | P::Cpu | P::Ram | P::Vram)) && overlay_instance) {
        P::Start(*overlay_instance);
    }
    P::Sample sample = P::Latest();
    // The screen's frame rate, as other counters show it (user 10-30: MSI said 100, the
    // overlay 50 with frame pacing); the game's own next to it when it differs.
    sample.fps = present_ms_avg > 0.0f ? 1000.0f / present_ms_avg : 0.0f;
    sample.frame_ms = present_ms_avg;
    sample.game_fps = frame_ms_avg > 0.0f ? 1000.0f / frame_ms_avg : 0.0f;
    {
        // Generated frames per second over about a second (issn_framegen.h).
        static auto window_start = std::chrono::steady_clock::now();
        static u64 window_count = Vulkan::FrameGen::GeneratedCount();
        static float generated_fps = 0.0f;
        const auto now = std::chrono::steady_clock::now();
        const float seconds = std::chrono::duration<float>(now - window_start).count();
        if (seconds >= 1.0f) {
            const u64 count = Vulkan::FrameGen::GeneratedCount();
            generated_fps = float(count - window_count) / seconds;
            window_count = count;
            window_start = now;
        }
        sample.generated_fps = generated_fps;
    }
    sample.upscaler = UpscalerLabel(s.upscaler);
    unsigned shown = items;
    if (const int mode = Vulkan::Dlss::Mode(); mode == 1 || Vulkan::Dlss::OwnUpscaler()) {
        // issn-axy: the anti-aliasing in use (Home switches it), always shown with FSR 4 / XeSS /
        // DLSS.
        const bool on = Vulkan::Dlss::g_ready.load(std::memory_order_relaxed) &&
                        Vulkan::Dlss::g_user_on.load(std::memory_order_relaxed);
        sample.upscaler = on ? Vulkan::Dlss::ModeName() : "Game AA";
        shown |= P::Upscaler;
    }
    return P::Format(sample, shown, s.overlay_layout == 0);
}

constexpr const char* Unsupported = "Unavailable: game version or code signature mismatch.";

void GameTab() {
    auto& s = IssnSettings::Get();
    // port: the cheats and multipliers patch the base port's game (God of War III); inFAMOUS Second
    // Son has none yet, so the sections only show when one of them is supported.
    const bool any_cheat = s.cheats_supported.load() != 0 || s.red_orbs_supported.load() ||
                           s.orb_pickup_supported.load() || s.damage_supported.load();
    // issn-axy: Delsin's cheats (src/hero_hook.h, issn_hero.cpp).
    ImGui::SeparatorText("Cheats");
    if (s.hero_supported && BeginRows("##hero")) {
        Row("God Mode");
        bool god = s.hero_health.load() && s.hero_powers.load();
        if (ImGui::Checkbox("##value", &god)) {
            s.hero_health = god;
            s.hero_powers = god;
            dirty = true;
        }
        Help("Infinite health and infinite powers together.");
        ImGui::PopID();
        CheckRow("Infinite health", s.hero_health,
                 "Delsin does not lose health (falls and scripted deaths still happen). Only Delsin "
                 "is protected.");
        CheckRow("Infinite powers", s.hero_powers,
                 "The energy of the power in use stays full: no need to drain smoke, neon or other "
                 "sources.");
        CheckRow("Karma Bomb always ready", s.hero_karma,
                 "The Karma Bomb meter stays full.");
        ImGui::EndTable();
    } else if (!any_cheat) {
        ImGui::TextDisabled("No cheats for this game build.");
    }
    if (any_cheat && !s.hero_supported && BeginRows("##cheats")) {
        static const char* const names[] = {"Infinite health", "Infinite magic", "Infinite item meter",
                                            "Infinite Rage of Sparta", "Max red orbs",
                                            "Unlock Very Hard", "Infinite Double Jumps"};
        static const char* const helps[] = {
            "Health stays full. Only Kratos is protected. Turning it off does not undo past refills.",
            "Magic stays full.", "The item meter (bow, Helios' head, boots) stays full.",
            "Rage of Sparta stays full.", "Red orbs stay at the maximum.",
            "Enable before opening New Game. Does not change an existing save difficulty.",
            "Repeat air jumps without landing. Turn off for normal movement."};
        for (unsigned cheat = 0; cheat < ISSN_CHEAT_COUNT; ++cheat) {
            const bool available = (s.cheats_supported.load() & (1u << cheat)) != 0;
            ImGui::BeginDisabled(!available);
            CheckRow(names[cheat], s.cheats[cheat], available ? helps[cheat] : Unsupported);
            ImGui::EndDisabled();
        }
        ImGui::EndTable();
    }

    if (any_cheat) ImGui::SeparatorText("Multipliers");
    if (any_cheat && ImGui::Button("Reset all to 1x")) {
        for (auto* m : {&s.red_orb_multiplier, &s.green_orb_multiplier, &s.blue_orb_multiplier,
                        &s.gold_orb_multiplier, &s.damage_dealt, &s.damage_taken}) {
            *m = 1.0f;
        }
        dirty = true;
    }
    if (any_cheat) Help("Every multiplier back to 1x (the game's normal values).");
    if (any_cheat && BeginRows("##multipliers")) {
        const bool max_orbs = (s.cheats_supported.load() & (1u << 4)) && s.cheats[4].load();
        ImGui::BeginDisabled(!s.red_orbs_supported.load() || max_orbs);
        MultiplierRow("Red orbs", s.red_orb_multiplier,
                      !s.red_orbs_supported.load() ? Unsupported
                      : max_orbs ? "Turn off Max red orbs to use the multiplier."
                                 : "Red orbs gained. Orbs you have and prices stay the same.");
        ImGui::EndDisabled();
        ImGui::BeginDisabled(!s.orb_pickup_supported.load());
        const char* pickup = s.orb_pickup_supported.load() ? nullptr : Unsupported;
        MultiplierRow("Green orbs (health)", s.green_orb_multiplier,
                      pickup ? pickup : "Health from each green orb. The bar stays within its maximum.");
        MultiplierRow("Blue orbs (magic)", s.blue_orb_multiplier,
                      pickup ? pickup : "Magic from each blue orb. The bar stays within its maximum.");
        MultiplierRow("Gold orbs (Rage of Sparta)", s.gold_orb_multiplier,
                      pickup ? pickup : "Rage of Sparta from each gold orb. The bar stays within its maximum.");
        ImGui::EndDisabled();
        ImGui::BeginDisabled(!s.damage_supported.load());
        const char* damage = s.damage_supported.load() ? nullptr : Unsupported;
        MultiplierRow("Damage dealt", s.damage_dealt, damage ? damage : "Damage Kratos does to enemies.");
        MultiplierRow("Damage taken", s.damage_taken, damage ? damage : "Damage Kratos takes.");
        ImGui::EndDisabled();
        ImGui::EndTable();
    }

    if (s.actor_watch_supported.load()) ImGui::SeparatorText("HUD");
    if (s.actor_watch_supported.load() && BeginRows("##hud")) {
        ImGui::BeginDisabled(!s.actor_watch_supported.load());
        CheckRow("Enemy health bar", s.enemy_health_bar,
                 s.actor_watch_supported.load() ? "A thin bar at the top for the last enemy hit." : Unsupported);
        ImGui::EndDisabled();
        ImGui::EndTable();
    }
}

// issn 0.3.6: Controls: every PS4 button's key, changed here with the next key pressed (user
// 10-29: a quicker way than the launcher). The changes go to issn.ini key_map: only the buttons that
// differ from the port's layout, in the launcher's form (button=SDL key name).
struct KeyRow {
    const char* button;
    const char* label;
    const char* fallback; // the port's default key (src/runtime_pad.c key_map)
};
constexpr KeyRow KeyRows[] = {
    {"cross", "Cross (X): jump", "Space"},       {"circle", "Circle: dodge, drop", "Left Shift"},
    {"square", "Square: melee", "C"},            {"triangle", "Triangle: subdue, interact", "F"},
    {"l1", "L1: power", "Q"},                    {"r1", "R1: power", "E"},
    {"l3", "L3: sprint", "U"},                   {"r3", "R3: camera", "O"},
    {"options", "Options: pause menu", "Escape"}, {"touchpad_right", "Touchpad (held: both thumbs)", "R"},
    {"touchpad_left", "Touchpad left side (photo mode)", "G"},
    {"up", "D-pad up", "Up"},                    {"down", "D-pad down", "Down"},
    {"left", "D-pad left", "Left"},              {"right", "D-pad right", "Right"},
    {"shake", "Shake the spray can", "X"},
};
int capturing_key = -1; // KeyRows index waiting for a key, or -1

std::string KeyMapWith(const std::string& map, const char* button, const char* name) {
    // map: "button=Key,button=Key"; the button's entry replaced (or removed when it is the default).
    std::string out;
    size_t start = 0;
    while (start <= map.size()) {
        const size_t end = std::min(map.find(',', start), map.size());
        const std::string entry = map.substr(start, end - start);
        const size_t eq = entry.find('=');
        if (eq != std::string::npos && entry.substr(0, eq) != button && !entry.empty()) {
            out += (out.empty() ? "" : ",") + entry;
        }
        start = end + 1;
    }
    if (name && *name) {
        out += (out.empty() ? "" : ",") + std::string(button) + "=" + name;
    }
    return out;
}

void AssignKey(int row, SDL_Scancode code) {
    auto& s = IssnSettings::Get();
    const KeyRow& k = KeyRows[row];
    const char* name = SDL_GetScancodeName(code);
    if (!name || !*name) {
        return;
    }
    s.key_map = KeyMapWith(s.key_map, k.button, SDL_strcasecmp(name, k.fallback) ? name : nullptr);
    runtime_pad_apply_key_map(s.key_map.c_str());
    dirty = true;
}

void ControlsTab() {
    auto& s = IssnSettings::Get();
    ImGui::SeparatorText("Keyboard");
    ImGui::TextWrapped("Click Change, then press the key. Esc cancels. The mouse buttons stay L2 (aim) and R2 "
                       "(shoot); W/A/S/D move and the mouse turns the camera.");
    if (ImGui::BeginTable("##keys", 3, ImGuiTableFlags_SizingStretchProp | ImGuiTableFlags_RowBg)) {
        ImGui::TableSetupColumn("button", ImGuiTableColumnFlags_WidthStretch, 0.55f);
        ImGui::TableSetupColumn("key", ImGuiTableColumnFlags_WidthStretch, 0.25f);
        ImGui::TableSetupColumn("change", ImGuiTableColumnFlags_WidthStretch, 0.20f);
        for (int i = 0; i < int(std::size(KeyRows)); ++i) {
            const KeyRow& k = KeyRows[i];
            ImGui::PushID(i);
            ImGui::TableNextRow();
            ImGui::TableNextColumn();
            ImGui::AlignTextToFramePadding();
            ImGui::TextUnformatted(k.label);
            ImGui::TableNextColumn();
            if (capturing_key == i) {
                ImGui::TextColored(ImVec4(0.14f, 0.83f, 0.95f, 1.0f), "Press a key...");
            } else {
                const char* name = runtime_pad_key_name(k.button);
                ImGui::TextUnformatted(name && *name ? name : "-");
            }
            ImGui::TableNextColumn();
            if (ImGui::Button(capturing_key == i ? "Cancel" : "Change", ImVec2(-FLT_MIN, 0))) {
                capturing_key = capturing_key == i ? -1 : i;
            }
            ImGui::PopID();
        }
        ImGui::EndTable();
    }
    if (ImGui::Button("Reset all keys")) {
        s.key_map.clear();
        runtime_pad_apply_key_map("");
        capturing_key = -1;
        dirty = true;
    }
    Help("Every key back to the port's layout (and the launcher's own changes, if any).");
}

// A combo over float values: the entry nearest the current value is shown.
void FloatChoiceRow(const char* label, std::atomic<float>& target, const char* const* labels,
                    const float* values, int count, const char* help) {
    Row(label);
    int current = 0;
    for (int i = 1; i < count; ++i) {
        if (std::fabs(values[i] - target.load()) < std::fabs(values[current] - target.load())) current = i;
    }
    if (ImGui::Combo("##value", &current, labels, count)) {
        Store(target, values[current], true);
    }
    Help(help);
    ImGui::PopID();
}

// issn-axy (tester request 10-31): the picture settings in the game. Field of view and draw
// distance change at once; the launcher's anti-aliasing, upscaler quality, texture filtering
// and frame generation are written for the next start (menu_* keys in issn.ini, which the
// launcher takes over when the game closes).
void PictureRows() {
    auto& s = IssnSettings::Get();
    ImGui::SeparatorText("Picture (changes at once)");
    if (BeginRows("##picture")) {
        static const char* const fov_labels[] = {"0.9x (narrower)", "Original", "1.1x", "1.15x", "1.2x",
                                                 "1.3x (wide)", "1.4x (widest)"};
        static const float fov_values[] = {0.9f, 1.0f, 1.1f, 1.15f, 1.2f, 1.3f, 1.4f};
        if (s.fov_supported) {
            FloatChoiceRow("Field of view", s.fov, fov_labels, fov_values, 7,
                           "How much of the city the camera shows. Aiming follows the wider view.");
        } // hidden otherwise (src/probe.c install_fov: off unless ISSN_FOV=1 for now)
        static const char* const distance_labels[] = {"Short (faster)", "Original", "Long (less pop-in)",
                                                      "Very long (slower)"};
        if (s.view_supported) {
            const int before = s.menu_draw_distance;
            ChoiceRow("Draw distance", s.menu_draw_distance, distance_labels, nullptr, 4,
                      "How far away buildings and props keep their detail. Longer means less pop-in "
                      "and a few FPS less.");
            if (s.menu_draw_distance != before) {
                s.lod_scale = IssnSettings::DrawDistanceScales[std::clamp(s.menu_draw_distance.load(), 0, 3)];
            }
        } else {
            InfoRow("Draw distance", "unavailable (another game build)");
        }
        static const char* const aniso_labels[] = {"The game's own", "4x", "8x", "16x (sharpest)"};
        {
            const int before = s.menu_aniso;
            ChoiceRow("Texture filtering", s.menu_aniso, aniso_labels, nullptr, 4,
                      "Ground and walls seen at an angle stay sharp in the distance.");
            if (s.menu_aniso != before) IssnSettings::ApplyLiveChoices();
        }
        static const char* const fg_labels[] = {"Off", "On (test)"};
        if (Vulkan::FrameGen::Wanted()) {
            const int before = s.menu_frame_gen;
            ChoiceRow("Frame generation", s.menu_frame_gen, fg_labels, nullptr, 2,
                      "FSR 3.1: a frame between every two of the game (more FPS on the screen).");
            if (s.menu_frame_gen != before) IssnSettings::ApplyLiveChoices();
        } else {
            ChoiceRow("Frame generation (next start)", s.menu_frame_gen, fg_labels, nullptr, 2,
                      "Needs FSR 4, XeSS or DLSS as anti-aliasing: with the game's own it starts with "
                      "the next launch after choosing one of them below.");
        }
        ImGui::EndTable();
    }
    ImGui::SeparatorText("Next start (restart the game from the launcher)");
    if (BeginRows("##next_start")) {
        static const char* const aa_labels[] = {"The game's own", "FSR 4 (AMD)", "XeSS (Intel)", "DLSS (NVIDIA RTX)"};
        ChoiceRow("Anti-aliasing / upscaler", s.menu_anti_aliasing, aa_labels, nullptr, 4,
                  "FSR 4, XeSS or DLSS give the sharpest picture and allow the quality below and frame "
                  "generation. The launcher fetches XeSS / DLSS files when needed.");
        static const char* const quality_labels[] = {"Off (game resolution)", "Quality (screen / 1.5)",
                                                     "Balanced (screen / 1.7)", "Performance (screen / 2)",
                                                     "Ultra Performance (screen / 3)"};
        ChoiceRow("Upscaler quality", s.menu_upscale_quality, quality_labels, nullptr, 5,
                  "With FSR 4, XeSS or DLSS: the game draws smaller than the screen and the upscaler "
                  "rebuilds it. Quality looks closest to native; lower ones give more FPS.");
        ImGui::EndTable();
    }
    ImGui::TextDisabled("These two set up the graphics card and the game's render size when the game "
                        "starts.");
    const bool changed = s.menu_anti_aliasing != s.startup_anti_aliasing ||
                         s.menu_upscale_quality != s.startup_upscale_quality ||
                         (!Vulkan::FrameGen::Wanted() && s.menu_frame_gen != s.startup_frame_gen);
    if (changed) {
        ImGui::TextColored(ImVec4(1.0f, 0.78f, 0.3f, 1.0f),
                           "Changed: close the game and press Play in the launcher to use them.");
    }
}

void ImageTab() {
    namespace G = IssnGraphics;
    auto& s = IssnSettings::Get();
    PictureRows();
    ImGui::SeparatorText("Display");
    if (BeginRows("##display")) {
        ChoiceRow("Display mode", s.display_mode, G::DisplayModeNames.data(), nullptr, G::DisplayModeCount,
                  "Windowed, borderless window or exclusive fullscreen.");
        CheckRow("VSync (on restart)", s.vsync, "Waits for the monitor: no tearing, a little more input delay.");
        ImGui::EndTable();
    }
    const int iw = s.image_width, ih = s.image_height, ww = s.window_width, wh = s.window_height;
    ImGui::SeparatorText("Information");
    if (BeginRows("##image_info")) {
        char text[96];
        std::snprintf(text, sizeof(text), "%d x %d", iw, ih);
        InfoRow("Game image", text);
        std::snprintf(text, sizeof(text), "%d x %d", ww, wh);
        InfoRow("Window", text);
        ImGui::EndTable();
    }
}

// issn-axy: the new GPU core reads the frame rate limit (its vblank rate) and VSync (the present
// mode) when it starts, so both apply on restart; the old core's live options are gone.
struct StartupGraphics {
    int fps_limit = -1;
    bool vsync = false;
};
StartupGraphics& Startup() {
    static StartupGraphics startup = [] {
        const auto& s = IssnSettings::Get();
        return StartupGraphics{s.fps_limit.load(), s.vsync.load()};
    }();
    return startup;
}

const char* AntiAliasingName() {
    const char* dlss = std::getenv("SHADPS4_DLSS");
    if (dlss && dlss[0] == '1') {
        return "DLSS (NVIDIA)";
    }
    const char* fsr4 = std::getenv("ISSN_FSR4");
    if (fsr4 && fsr4[0] == '1') {
        return "FSR 4 Native AA (AMD)";
    }
    const char* xess = std::getenv("ISSN_XESS");
    if (xess && xess[0] == '1') {
        return "XeSS (Intel)";
    }
    return "The game's own (SMAA T2x)";
}

void PerformanceTab() {
    namespace G = IssnGraphics;
    auto& s = IssnSettings::Get();
    Startup();
    ImGui::SeparatorText("Applied on restart");
    if (BeginRows("##restart")) {
        static const char* const fps_labels[] = {"30", "60", "120", "240", "Unlimited"};
        ChoiceRow("Frame rate limit", s.fps_limit, fps_labels, G::FpsLimits.data(), int(G::FpsLimits.size()),
                  "The highest frame rate. Unlimited lets the game run as fast as the PC allows.");
        ImGui::EndTable();
    }
    ImGui::SeparatorText("Information");
    if (BeginRows("##perf_info")) {
        InfoRow("Anti-aliasing", AntiAliasingName());
        InfoRow("Change it in", "the Image tab (next start) or the launcher");
        ImGui::EndTable();
    }
}

void OverlayTab() {
    namespace P = IssnPerfStats;
    auto& s = IssnSettings::Get();
    if (BeginRows("##overlay_on")) {
        CheckRow("Show overlay", s.show_fps, "The performance overlay in a corner of the screen.");
        CheckRow("\"Game saved\" note", s.save_note,
                 "A short note at the top of the screen each time the game writes a save.");
        ImGui::EndTable();
    }
    ImGui::BeginDisabled(!s.show_fps);
    ImGui::SeparatorText("What to show");
    if (ImGui::BeginTable("##overlay_items", 2, ImGuiTableFlags_SizingStretchProp)) {
        ImGui::TableNextColumn();
        static const char* const names[] = {"FPS", "Frametime", "Upscaler", "GPU (name and use)", "CPU (game)",
                                            "RAM (game / total)", "VRAM (used / available)"};
        for (unsigned item = 0; item < 7; ++item) {
            const unsigned bit = 1u << item;
            bool on = (s.overlay_items & bit) != 0;
            if (ImGui::Checkbox(names[item], &on)) {
                s.overlay_items = on ? (s.overlay_items | bit) : (s.overlay_items & ~bit);
                dirty = true;
            }
        }
        ImGui::TableNextColumn();
        ImGui::TextDisabled("Preview");
        ImGui::BeginChild("##preview", ImVec2(0, 0), ImGuiChildFlags_Borders | ImGuiChildFlags_AutoResizeY);
        ImGui::SetWindowFontScale(s.overlay_scale / 100.0f);
        const std::string text = OverlayText();
        ImGui::TextUnformatted(text.empty() ? "(nothing selected)" : text.c_str());
        ImGui::SetWindowFontScale(1.0f);
        ImGui::EndChild();
        ImGui::EndTable();
    }
    ImGui::SeparatorText("Appearance");
    if (BeginRows("##appearance")) {
        SliderRow("Font size", s.overlay_scale, 50, 300, "%d%%", "Size of the overlay text.");
        static const char* const corners[] = {"Top left", "Top right", "Bottom left", "Bottom right"};
        ChoiceRow("Corner", s.overlay_corner, corners, nullptr, 4, "Where the overlay sits.");
        SliderRow("Background opacity", s.overlay_opacity, 0, 100, "%d%%", "0% is no background.");
        static const char* const layouts[] = {"One line", "One item per line"};
        ChoiceRow("Layout", s.overlay_layout, layouts, nullptr, 2, "Items side by side or stacked.");
        ImGui::EndTable();
    }
    ImGui::EndDisabled();
    ImGui::SeparatorText("About");
    if (BeginRows("##about")) {
        const auto& elf = Common::ElfInfo::Instance();
        char text[160];
        std::snprintf(text, sizeof(text), "%.*s v%.*s", int(elf.GameSerial().size()), elf.GameSerial().data(),
                      int(elf.AppVer().size()), elf.AppVer().data());
        InfoRow("Game", text);
        const u32 total = background_total;
        if (total) {
            std::snprintf(text, sizeof(text), "loading %u%%", u32(u64(background_done) * 100 / total));
        } else {
            std::snprintf(text, sizeof(text), "ready");
        }
        InfoRow("Shader cache", text);
        std::string hooks;
        const auto add = [&](bool on, const char* name) {
            if (on) {
                hooks += hooks.empty() ? name : std::string(", ") + name;
            }
        };
        add(s.cheats_supported.load() != 0, "cheats");
        add(s.red_orbs_supported.load(), "red orbs");
        add(s.orb_pickup_supported.load(), "orb pickups");
        add(s.damage_supported.load(), "damage");
        add(s.actor_watch_supported.load(), "enemy health");
        InfoRow("Game hooks", hooks.empty() ? "none" : hooks.c_str());
        ImGui::EndTable();
    }
}

// Startup options changed since this launch (applied by "Apply and restart").
int RestartChanges() {
    const auto& s = IssnSettings::Get();
    const auto& startup = Startup();
    return (s.fps_limit != startup.fps_limit) + (s.vsync != startup.vsync);
}

void Menu() {
    auto& s = IssnSettings::Get();
    const ImGuiViewport* viewport = ImGui::GetMainViewport();
    ImGui::SetNextWindowPos(ImVec2(viewport->WorkPos.x + viewport->WorkSize.x * 0.5f,
                                   viewport->WorkPos.y + viewport->WorkSize.y * 0.5f),
                            ImGuiCond_Appearing, ImVec2(0.5f, 0.5f));
    ImGui::SetNextWindowSize(ImVec2(std::min(760.0f * base_scale, viewport->WorkSize.x * 0.95f),
                                    std::min(680.0f * base_scale, viewport->WorkSize.y * 0.9f)),
                             ImGuiCond_Appearing);
    bool keep_open = true;
    // port: the game's own title (param.sfo), not a fixed one.
    static const std::string heading = [] {
        const std::string_view title = Common::ElfInfo::Instance().Title();
        return std::string(title.empty() ? "inFAMOUS Second Son" : title) + PadText("  (Insert / R3+L2)", "  (Insert / RS+LT)", "  (Insert / RS+ZL)");
    }();
    if (!ImGui::Begin(heading.c_str(), &keep_open, ImGuiWindowFlags_NoCollapse)) {
        ImGui::End();
        return;
    }
    footer_help = nullptr;
    constexpr int TabCount = 5;
    const ImGuiIO& io = ImGui::GetIO();
    if (!io.WantTextInput) {
        const int step = ImGui::IsKeyPressed(ImGuiKey_GamepadR1, false) || ImGui::IsKeyPressed(ImGuiKey_E, false) ? 1
                         : ImGui::IsKeyPressed(ImGuiKey_GamepadL1, false) || ImGui::IsKeyPressed(ImGuiKey_Q, false)
                             ? -1
                             : 0;
        if (step) {
            requested_tab = (s.menu_tab + step + TabCount) % TabCount;
        }
    }
    const float footer = ImGui::GetFrameHeightWithSpacing() * 2.0f + ImGui::GetTextLineHeightWithSpacing() * 2.0f;
    if (ImGui::BeginTabBar("##tabs")) {
        static const char* const tabs[TabCount] = {"Game", "Image", "Performance", "Controls", "Overlay"};
        for (int tab = 0; tab < TabCount; ++tab) {
            const ImGuiTabItemFlags flags = requested_tab == tab ? ImGuiTabItemFlags_SetSelected : 0;
            if (!ImGui::BeginTabItem(tabs[tab], nullptr, flags)) {
                continue;
            }
            if (s.menu_tab != tab && requested_tab < 0) {
                s.menu_tab = tab;
                dirty = true;
            }
            if (ImGui::BeginChild("##body", ImVec2(0, -footer))) {
                switch (tab) {
                case 0: GameTab(); break;
                case 1: ImageTab(); break;
                case 2: PerformanceTab(); break;
                case 3: ControlsTab(); break;
                default: OverlayTab(); break;
                }
            }
            ImGui::EndChild();
            ImGui::EndTabItem();
        }
        if (requested_tab >= 0) {
            s.menu_tab = requested_tab;
            dirty = true;
            requested_tab = -1;
        }
        ImGui::EndTabBar();
    }

    ImGui::Separator();
    ImGui::PushTextWrapPos(0.0f);
    ImGui::TextDisabled("%s", footer_help ? footer_help : PadText("L1 / R1 or Q / E: switch tabs. Settings are saved in issn.ini.", "LB / RB or Q / E: switch tabs. Settings are saved in issn.ini.", "L / R or Q / E: switch tabs. Settings are saved in issn.ini."));
    ImGui::PopTextWrapPos();
    if (const int changes = RestartChanges()) {
        ImGui::TextColored(ImVec4(0.85f, 0.72f, 0.45f, 1.0f), "%d change%s need%s a restart (unsaved progress is lost).",
                           changes, changes == 1 ? "" : "s", changes == 1 ? "s" : "");
        ImGui::SameLine();
        ImGui::BeginDisabled(!std::getenv("ISSN_RESTART_COMMAND"));
        if (ImGui::Button("Apply and restart")) {
            // The new launch confirms the options once the game shows; run.py restores the
            // previous ones if it never does (restart_unconfirmed).
            s.restart_unconfirmed = true;
            IssnSettings::Save();
            runtime_restart();
        }
        ImGui::EndDisabled();
    } else {
        ImGui::NewLine();
    }
    const float close_width = ImGui::CalcTextSize("Close").x + ImGui::GetStyle().FramePadding.x * 2;
    ImGui::SetCursorPosX(ImGui::GetCursorPosX() + ImGui::GetContentRegionAvail().x - close_width);
    if (ImGui::Button("Close")) {
        keep_open = false;
    }
    ImGui::End();
    if (!keep_open) {
        SetOpen(false);
    }
}

// Enemy health bar from the damage hook's records (issn_actors.cpp).
// Only the copied values are read, never the game's memory: an actor may be gone by now.
void HealthDisplay() {
    struct Seen {
        uint32_t seq = 0;
        std::chrono::steady_clock::time_point at{};
    };
    static Seen seen[2];
    const auto& s = IssnSettings::Get();
    if (!s.actor_watch_supported.load()) {
        return;
    }
    const auto now = std::chrono::steady_clock::now();
    IssnActorSlot slot[2];
    for (int i = 0; i < 2; ++i) {
        std::memcpy(&slot[i], const_cast<const IssnActorSlot*>(&issn_actor_slots[i]), sizeof(slot[i]));
        if (slot[i].seq != seen[i].seq) {
            seen[i] = {slot[i].seq, now};
        }
    }
    const auto recent = [&](int i, int seconds) {
        return seen[i].seq != 0 && now - seen[i].at < std::chrono::seconds(seconds);
    };
    const ImGuiViewport* viewport = ImGui::GetMainViewport();
    ImDrawList* draw = ImGui::GetForegroundDrawList();
    const float scale = base_scale;
    if (s.enemy_health_bar && recent(1, 4) && IssnActors::Valid(slot[1].health, slot[1].max_health)) {
        // Minimal: a thin green bar centered on the screen's top edge; it shrinks toward the center
        // so it stays symmetric, with the health in numbers below.
        const ImVec2 display = ImGui::GetIO().DisplaySize;
        const float width = 260.0f * scale, height = 5.0f * scale, center = display.x * 0.5f;
        const float top = 36.0f * scale;
        const float fraction = IssnActors::Fraction(slot[1].health, slot[1].max_health);
        draw->AddRectFilled(ImVec2(center - width * 0.5f - scale, top - scale),
                            ImVec2(center + width * 0.5f + scale, top + height + scale),
                            IM_COL32(0, 0, 0, 140), 2 * scale);
        const float half = width * 0.5f * fraction;
        draw->AddRectFilled(ImVec2(center - half, top), ImVec2(center + half, top + height),
                            IM_COL32(70, 190, 80, 220), 2 * scale);
        char text[48];
        std::snprintf(text, sizeof(text), "%.0f / %.0f", std::ceil(slot[1].health), slot[1].max_health);
        const float font = ImGui::GetFontSize() * 0.9f;
        const ImVec2 size = ImGui::GetFont()->CalcTextSizeA(font, FLT_MAX, 0.0f, text);
        const ImVec2 at{center - size.x * 0.5f, top + height + 4.0f * scale};
        draw->AddText(ImGui::GetFont(), font, ImVec2(at.x + scale, at.y + scale), IM_COL32(0, 0, 0, 160), text);
        draw->AddText(ImGui::GetFont(), font, at, IM_COL32(225, 235, 220, 220), text);
    }
}

// The game's text dialog: what is typed, and how to finish (keyboard or controller).
void TextEntryBox() {
    const ImGuiViewport* viewport = ImGui::GetMainViewport();
    ImGui::SetNextWindowPos(ImVec2(viewport->WorkPos.x + viewport->WorkSize.x * 0.5f,
                                   viewport->WorkPos.y + viewport->WorkSize.y * 0.42f),
                            ImGuiCond_Always, ImVec2(0.5f, 0.5f));
    ImGui::SetNextWindowSizeConstraints(ImVec2(viewport->WorkSize.x * 0.32f, 0.0f),
                                        ImVec2(viewport->WorkSize.x * 0.8f, FLT_MAX));
    ImGui::SetNextWindowBgAlpha(0.92f);
    ImGui::Begin("##text_entry", nullptr,
                 ImGuiWindowFlags_NoDecoration | ImGuiWindowFlags_AlwaysAutoResize |
                     ImGuiWindowFlags_NoInputs | ImGuiWindowFlags_NoNav |
                     ImGuiWindowFlags_NoFocusOnAppearing);
    ImGui::TextColored(ImVec4(0.85f, 0.72f, 0.45f, 1.0f), "%s", text_entry_prompt.c_str());
    ImGui::Separator();
    ImGui::SetWindowFontScale(1.4f);
    ImGui::Text("%s_", text_entry_text.c_str());
    ImGui::SetWindowFontScale(1.0f);
    ImGui::Separator();
    ImGui::TextDisabled("Keyboard: type, Backspace to erase, Enter = OK, Esc = cancel");
    ImGui::TextDisabled("%s", PadText("Controller: Cross (A) = OK, Circle (B) = cancel", "Controller: A = OK, B = cancel", "Controller: B = OK, A = cancel"));
    ImGui::End();
}

void ChoiceBox() {
    const ImGuiViewport* viewport = ImGui::GetMainViewport();
    ImGui::SetNextWindowPos(ImVec2(viewport->WorkPos.x + viewport->WorkSize.x * 0.5f,
                                   viewport->WorkPos.y + viewport->WorkSize.y * 0.5f),
                            ImGuiCond_Always, ImVec2(0.5f, 0.5f));
    ImGui::SetNextWindowSizeConstraints(ImVec2(viewport->WorkSize.x * 0.36f, 0.0f),
                                        ImVec2(viewport->WorkSize.x * 0.8f, viewport->WorkSize.y * 0.8f));
    ImGui::SetNextWindowBgAlpha(0.94f);
    ImGui::Begin("##choice", nullptr,
                 ImGuiWindowFlags_NoDecoration | ImGuiWindowFlags_AlwaysAutoResize |
                     ImGuiWindowFlags_NoInputs | ImGuiWindowFlags_NoNav |
                     ImGuiWindowFlags_NoFocusOnAppearing);
    ImGui::TextColored(ImVec4(0.85f, 0.72f, 0.45f, 1.0f), "%s", choice_title.c_str());
    ImGui::Separator();
    // Save pictures are 228x128; rows without one keep the text aligned when others have one.
    const bool pictures = std::any_of(choice_textures.begin(), choice_textures.end(),
                                      [](ImTextureData* tex) { return tex != nullptr; });
    const ImVec2 picture_size(228.0f * base_scale, 128.0f * base_scale);
    for (int i = 0; i < int(choice_items.size()); ++i) {
        const bool selected = i == choice_index;
        ImGui::BeginGroup();
        ImTextureData* tex = i < int(choice_textures.size()) ? choice_textures[i] : nullptr;
        if (tex) {
            ImGui::Image(tex->GetTexRef(), picture_size);
        } else if (pictures) {
            ImGui::Dummy(picture_size);
        }
        if (pictures) {
            ImGui::SameLine();
        }
        if (selected) {
            ImGui::TextColored(ImVec4(1.0f, 0.9f, 0.55f, 1.0f), "> %s", choice_items[i].c_str());
        } else {
            ImGui::Text("  %s", choice_items[i].c_str());
        }
        ImGui::EndGroup();
        if (selected) {
            if (pictures) {
                const ImVec2 min = ImGui::GetItemRectMin(), max = ImGui::GetItemRectMax();
                const float pad = 3.0f * base_scale;
                ImGui::GetWindowDrawList()->AddRect(ImVec2(min.x - pad, min.y - pad),
                                                    ImVec2(max.x + pad, max.y + pad),
                                                    IM_COL32(255, 230, 140, 255), 0.0f, 0,
                                                    2.0f * base_scale);
            }
            ImGui::SetScrollHereY();
        }
    }
    ImGui::Separator();
    ImGui::TextDisabled("%s", PadText("Up/Down or D-pad: choose   Enter or Cross (A): OK   Esc or Circle (B): cancel", "Up/Down or D-pad: choose   Enter or A: OK   Esc or B: cancel", "Up/Down or D-pad: choose   Enter or B: OK   Esc or A: cancel"));
    ImGui::End();
}

void LoadingScreen() {
    const ImGuiViewport* viewport = ImGui::GetMainViewport();
    ImGui::SetNextWindowPos(ImVec2(viewport->WorkPos.x + viewport->WorkSize.x * 0.5f,
                                   viewport->WorkPos.y + viewport->WorkSize.y * 0.5f),
                            ImGuiCond_Always, ImVec2(0.5f, 0.5f));
    ImGui::SetNextWindowSize(ImVec2(viewport->WorkSize.x * 0.4f, 0.0f), ImGuiCond_Always);
    ImGui::SetNextWindowBgAlpha(0.0f);
    ImGui::Begin("##loading", nullptr,
                 ImGuiWindowFlags_NoDecoration | ImGuiWindowFlags_NoInputs |
                     ImGuiWindowFlags_NoNav | ImGuiWindowFlags_NoFocusOnAppearing);
    const u32 done = loading_done, total = loading_total;
    ImGui::SetWindowFontScale(1.6f);
    ImGui::TextUnformatted(total ? "Loading shader cache..." : "Preparing graphics...");
    ImGui::SetWindowFontScale(1.0f);
    if (total) {
        char count[64];
        std::snprintf(count, sizeof(count), "%u%%", u32(u64(done) * 100 / total));
        ImGui::ProgressBar(float(done) / float(total), ImVec2(-1.0f, 0.0f), count);
    }
    ImGui::TextDisabled("The first start after an update takes longer.");
    ImGui::End();
}

void FpsCounter() {
    const auto& s = IssnSettings::Get();
    const std::string text = OverlayText();
    if (text.empty()) {
        return;
    }
    const ImGuiViewport* viewport = ImGui::GetMainViewport();
    const float pad = 12.0f * base_scale;
    const int corner = s.overlay_corner;
    const bool right = corner == 1 || corner == 3, bottom = corner >= 2;
    ImGui::SetNextWindowPos(ImVec2(right ? viewport->WorkPos.x + viewport->WorkSize.x - pad : viewport->WorkPos.x + pad,
                                   bottom ? viewport->WorkPos.y + viewport->WorkSize.y - pad : viewport->WorkPos.y + pad),
                            ImGuiCond_Always, ImVec2(right ? 1.0f : 0.0f, bottom ? 1.0f : 0.0f));
    ImGui::SetNextWindowBgAlpha(s.overlay_opacity / 100.0f);
    ImGui::Begin("##fps", nullptr,
                 ImGuiWindowFlags_NoDecoration | ImGuiWindowFlags_AlwaysAutoResize |
                     ImGuiWindowFlags_NoInputs | ImGuiWindowFlags_NoNav |
                     ImGuiWindowFlags_NoFocusOnAppearing);
    ImGui::SetWindowFontScale(s.overlay_scale / 100.0f);
    ImGui::TextUnformatted(text.c_str());
    ImGui::End();
}

// issn-axy: "Game saved" for a few seconds after the game writes a save (NoteGameSaved).
std::mutex save_note_mutex;
std::string save_note_text;
std::atomic<std::int64_t> save_note_until{0}; ///< steady_clock milliseconds; 0 = none
constexpr std::int64_t SaveNoteMs = 3500;

std::int64_t NowMs() {
    return std::chrono::duration_cast<std::chrono::milliseconds>(
               std::chrono::steady_clock::now().time_since_epoch())
        .count();
}

void SaveNote() {
    const std::int64_t left = save_note_until.load() - NowMs();
    if (left <= 0) {
        save_note_until = 0;
        return;
    }
    std::string text;
    {
        std::scoped_lock lock{save_note_mutex};
        text = save_note_text;
    }
    const float fade = std::clamp(float(left) / 500.0f, 0.0f, 1.0f); // the last half second
    const ImGuiViewport* viewport = ImGui::GetMainViewport();
    const float pad = 16.0f * base_scale;
    ImGui::SetNextWindowPos(ImVec2(viewport->WorkPos.x + viewport->WorkSize.x * 0.5f, viewport->WorkPos.y + pad),
                            ImGuiCond_Always, ImVec2(0.5f, 0.0f));
    ImGui::SetNextWindowBgAlpha(0.55f * fade);
    ImGui::PushStyleVar(ImGuiStyleVar_Alpha, fade);
    ImGui::Begin("##save_note", nullptr,
                 ImGuiWindowFlags_NoDecoration | ImGuiWindowFlags_AlwaysAutoResize |
                     ImGuiWindowFlags_NoInputs | ImGuiWindowFlags_NoNav |
                     ImGuiWindowFlags_NoFocusOnAppearing);
    ImGui::TextColored(ImVec4(0.45f, 0.9f, 1.0f, 1.0f), "Game saved");
    if (!text.empty()) {
        ImGui::SameLine();
        ImGui::TextDisabled("%s", text.c_str());
    }
    ImGui::End();
    ImGui::PopStyleVar();
}

void BackgroundLoading() {
    const u32 done = background_done, total = std::max(background_total.load(), 1u);
    const ImGuiViewport* viewport = ImGui::GetMainViewport();
    const float pad = 12.0f * base_scale;
    ImGui::SetNextWindowPos(ImVec2(viewport->WorkPos.x + pad,
                                   viewport->WorkPos.y + viewport->WorkSize.y - pad),
                            ImGuiCond_Always, ImVec2(0.0f, 1.0f));
    ImGui::SetNextWindowBgAlpha(0.35f);
    ImGui::Begin("##background_loading", nullptr,
                 ImGuiWindowFlags_NoDecoration | ImGuiWindowFlags_AlwaysAutoResize |
                     ImGuiWindowFlags_NoInputs | ImGuiWindowFlags_NoNav |
                     ImGuiWindowFlags_NoFocusOnAppearing);
    ImGui::TextDisabled("Loading shader cache %u%%", u32(u64(done) * 100 / total));
    ImGui::End();
}

} // namespace

void Init(const Vulkan::Instance& instance, vk::Format format, u32 image_count) {
    // The menu's live choices saved in issn.ini (a launch without the launcher keeps them too).
    IssnSettings::ApplyLiveChoices();
    std::scoped_lock lock{imgui_mutex};
    if (initialized) {
        return;
    }
    IMGUI_CHECKVERSION();
    ImGui::CreateContext();
    ImGuiIO& io = ImGui::GetIO();
    io.IniFilename = nullptr; // window positions are not kept
    io.ConfigFlags |= ImGuiConfigFlags_NavEnableKeyboard | ImGuiConfigFlags_NavEnableGamepad;
    io.BackendFlags |= ImGuiBackendFlags_HasGamepad;
    io.BackendPlatformName = "issn";

    ImGui::StyleColorsDark();
    ImGuiStyle& style = ImGui::GetStyle();
    style.WindowRounding = 6.0f;
    style.FrameRounding = 4.0f;
    style.GrabRounding = 4.0f;
    style.Colors[ImGuiCol_WindowBg].w = 0.92f;

    ImFontConfig font_config;
    font_config.FontDataOwnedByAtlas = false;
    io.Fonts->AddFontFromMemoryTTF(const_cast<unsigned char*>(issn_font_ttf),
                                   int(issn_font_ttf_end - issn_font_ttf), 18.0f, &font_config);

    const vk::Instance vk_instance = instance.GetInstance();
    ImGui_ImplVulkan_LoadFunctions(
        instance.ApiVersion(),
        [](const char* name, void* user) {
            return VULKAN_HPP_DEFAULT_DISPATCHER.vkGetInstanceProcAddr(
                *static_cast<const vk::Instance*>(user), name);
        },
        const_cast<vk::Instance*>(&vk_instance));

    const VkFormat color_format = static_cast<VkFormat>(format);
    ImGui_ImplVulkan_InitInfo info{};
    info.ApiVersion = instance.ApiVersion();
    info.Instance = vk_instance;
    info.PhysicalDevice = instance.GetPhysicalDevice();
    info.Device = instance.GetDevice();
    info.QueueFamily = instance.GetGraphicsQueueFamilyIndex();
    info.Queue = instance.GetGraphicsQueue();
    // Font atlas plus the save list pictures (up to 33, and a closed list's until freed).
    info.DescriptorPoolSize = 128;
    info.MinImageCount = std::max(image_count, 2u);
    info.ImageCount = std::max(image_count, 2u);
    info.UseDynamicRendering = true;
    info.PipelineInfoMain.PipelineRenderingCreateInfo = {
        .sType = VK_STRUCTURE_TYPE_PIPELINE_RENDERING_CREATE_INFO_KHR,
        .colorAttachmentCount = 1,
        .pColorAttachmentFormats = &color_format,
    };
    if (!ImGui_ImplVulkan_Init(&info)) {
        std::printf("Overlay: ImGui Vulkan backend init failed\n");
        ImGui::DestroyContext();
        return;
    }
    overlay_instance = &instance;
    initialized = true;
    std::printf("Overlay: menu ready (Insert or R3+L2)\n");
}

void UpdateTextInput(SDL_Window* window) {
    bool want = false;
    {
        std::scoped_lock lock{imgui_mutex};
        want = initialized && menu_open && ImGui::GetIO().WantTextInput;
    }
    if (want != SDL_TextInputActive(window)) {
        if (want) {
            SDL_StartTextInput(window);
        } else {
            SDL_StopTextInput(window);
        }
    }
}

// imgui_mutex held. The backend frees a texture some frames after its last use.
void RetireChoiceTextures() {
    for (ImTextureData* tex : choice_textures) {
        if (tex) {
            tex->WantDestroyNextFrame = true;
            choice_retired.push_back(tex);
        }
    }
    choice_textures.clear();
    choice_pictures.clear();
    choice_retiring = !choice_retired.empty();
}

// imgui_mutex held, before NewFrame: new pictures become textures, destroyed ones are freed.
void UpdateChoiceTextures() {
    if (!choice_pictures.empty()) {
        choice_textures.assign(choice_pictures.size(), nullptr);
        for (size_t i = 0; i < choice_pictures.size(); ++i) {
            const IssnPng::Image& picture = choice_pictures[i];
            if (picture.rgba.empty()) {
                continue;
            }
            ImTextureData* tex = IM_NEW(ImTextureData)();
            tex->Create(ImTextureFormat_RGBA32, picture.width, picture.height);
            std::memcpy(tex->GetPixels(), picture.rgba.data(), picture.rgba.size());
            ImGui::RegisterUserTexture(tex);
            choice_textures[i] = tex;
        }
        choice_pictures.clear();
    }
    std::erase_if(choice_retired, [](ImTextureData* tex) {
        if (tex->Status != ImTextureStatus_Destroyed) {
            return false;
        }
        ImGui::UnregisterUserTexture(tex);
        IM_DELETE(tex);
        return true;
    });
    choice_retiring = !choice_retired.empty();
}

// The save list takes the keys and buttons that move and confirm; the rest stays with the game
// (held neutral by CapturesInput).
bool HandleChoiceEvent(const SDL_Event& event) {
    int move = 0, finish = 0; // finish: 1 accept, 2 cancel
    if (event.type == SDL_EVENT_KEY_DOWN) {
        const SDL_Keycode key = event.key.key;
        move = key == SDLK_UP || key == SDLK_W ? -1 : key == SDLK_DOWN || key == SDLK_S ? 1 : 0;
        finish = key == SDLK_RETURN || key == SDLK_KP_ENTER || key == SDLK_SPACE ? 1
                 : key == SDLK_ESCAPE || key == SDLK_BACKSPACE                   ? 2
                                                                                 : 0;
    } else if (event.type == SDL_EVENT_GAMEPAD_BUTTON_DOWN) {
        const u8 button = event.gbutton.button;
        move = button == SDL_GAMEPAD_BUTTON_DPAD_UP ? -1 : button == SDL_GAMEPAD_BUTTON_DPAD_DOWN ? 1 : 0;
        finish = button == SDL_GAMEPAD_BUTTON_SOUTH ? 1 : button == SDL_GAMEPAD_BUTTON_EAST ? 2 : 0;
    } else {
        return event.type == SDL_EVENT_KEY_UP || event.type == SDL_EVENT_GAMEPAD_BUTTON_UP;
    }
    const int count = int(choice_items.size());
    if (move && count) {
        choice_index = (choice_index + move + count) % count;
    }
    if (finish) {
        choice_result = finish == 1 && count ? choice_index : -2;
        choice_active = false;
        RetireChoiceTextures();
    }
    return true;
}

bool HandleEvent(const SDL_Event& event) {
    std::scoped_lock lock{imgui_mutex};
    if (!initialized) {
        return false;
    }
    if (choice_active && !menu_open) {
        return HandleChoiceEvent(event);
    }
    ImGuiIO& io = ImGui::GetIO();
    const bool is_open = menu_open;
    switch (event.type) {
    case SDL_EVENT_KEY_DOWN:
    case SDL_EVENT_KEY_UP: {
        const bool down = event.type == SDL_EVENT_KEY_DOWN;
        if (is_open && capturing_key >= 0) { // Controls tab: this key is the new one
            if (down && !event.key.repeat) {
                if (event.key.scancode != SDL_SCANCODE_ESCAPE && event.key.scancode != SDL_SCANCODE_INSERT) {
                    AssignKey(capturing_key, event.key.scancode);
                }
                capturing_key = -1;
            }
            return true;
        }
        if (down && !event.key.repeat && (event.key.key == SDLK_Y || event.key.key == SDLK_F7) &&
            !is_open) {
            // issn: Y (or F7, axylpup's key) gives the camera to the mouse (runtime_pad.c
            // sample_mouse, the camera hook) and back. Off also stops the click-to-capture below.
            if (SDL_Window* window = SDL_GetWindowFromID(event.key.windowID)) {
                const bool on = !SDL_GetWindowRelativeMouseMode(window);
                mouse_camera_wanted = on;
                SDL_SetWindowRelativeMouseMode(window, on);
                std::printf("Mouse camera: %s (Y)\n", on ? "on" : "off");
                std::fflush(stdout);
            }
            return true;
        }
        if (down && !event.key.repeat && event.key.key == SDLK_HOME && !is_open &&
            (Vulkan::Dlss::Mode() == 1 || Vulkan::Dlss::OwnUpscaler())) {
            // issn-axy: Home switches FSR 4 / DLSS and the game's own AA live (axylpup's key),
            // to compare the picture and the frame rate in one spot (tester 2026-10-22).
            const bool on = !Vulkan::Dlss::g_user_on.load(std::memory_order_relaxed);
            Vulkan::Dlss::g_user_on.store(on, std::memory_order_relaxed);
            std::printf("Anti-aliasing: %s (Home)\n",
                        on ? Vulkan::Dlss::ModeName() : "the game's own");
            std::fflush(stdout);
            return true;
        }
        if (down && !event.key.repeat && event.key.key == SDLK_F12) {
            // issn-axy: screenshot of the game picture (user/screenshots, PNG).
            VideoCore::RequestScreenshot(VideoCore::ScreenshotRequest::GameOnly);
            return true;
        }
        if (down && !event.key.repeat && event.key.key == SDLK_INSERT && !is_open) {
            // The menu needs the cursor: the mouse camera stops (Y turns it on again).
            if (SDL_Window* window = SDL_GetWindowFromID(event.key.windowID)) {
                SDL_SetWindowRelativeMouseMode(window, false);
            }
        }
        if (down && !event.key.repeat &&
            (event.key.key == SDLK_INSERT || (is_open && event.key.key == SDLK_ESCAPE))) {
            SetOpen(event.key.key == SDLK_INSERT ? !is_open : false);
            return true;
        }
        if (!is_open) {
            return false;
        }
        io.AddKeyEvent(ImGuiMod_Ctrl, (event.key.mod & SDL_KMOD_CTRL) != 0);
        io.AddKeyEvent(ImGuiMod_Shift, (event.key.mod & SDL_KMOD_SHIFT) != 0);
        io.AddKeyEvent(ImGuiMod_Alt, (event.key.mod & SDL_KMOD_ALT) != 0);
        if (const ImGuiKey key = KeyFromSdl(event.key.key); key != ImGuiKey_None) {
            io.AddKeyEvent(key, down);
        }
        return true;
    }
    case SDL_EVENT_GAMEPAD_BUTTON_DOWN:
    case SDL_EVENT_GAMEPAD_BUTTON_UP: {
        const bool down = event.type == SDL_EVENT_GAMEPAD_BUTTON_DOWN;
        const u8 button = event.gbutton.button;
        // R3+L2 opens the menu: L3+R3 is a game action in God of War III.
        if (button == SDL_GAMEPAD_BUTTON_RIGHT_STICK) {
            r3_down = down;
            if (down && l2_down) {
                SetOpen(!is_open);
                return true;
            }
        }
        if (!is_open) {
            return false;
        }
        if (const ImGuiKey key = KeyFromGamepad(button); key != ImGuiKey_None) {
            io.AddKeyEvent(key, down);
        }
        return true;
    }
    case SDL_EVENT_GAMEPAD_AXIS_MOTION: {
        if (event.gaxis.axis == SDL_GAMEPAD_AXIS_LEFT_TRIGGER) {
            const bool down = event.gaxis.value > 16000;
            if (down && !l2_down && r3_down) {
                l2_down = true;
                SetOpen(!is_open);
                return true;
            }
            l2_down = down;
        }
        return false;
    }
    case SDL_EVENT_TEXT_INPUT: {
        // Typed characters (Ctrl+click on a slider, a text field): key events alone erase but
        // do not type. SDL sends them while text input is on (UpdateTextInput).
        if (!is_open) {
            return false;
        }
        io.AddInputCharactersUTF8(event.text.text);
        return true;
    }
    case SDL_EVENT_MOUSE_MOTION: {
        if (!is_open) {
            return false;
        }
        const float density = PixelDensity(event.motion.windowID);
        io.AddMousePosEvent(event.motion.x * density, event.motion.y * density);
        return true;
    }
    case SDL_EVENT_MOUSE_BUTTON_DOWN:
    case SDL_EVENT_MOUSE_BUTTON_UP: {
        if (!is_open) {
            // issn: a click in the game window gives the camera to the mouse, as in a PC game
            // (tester 2026-10-22: the mouse should aim without a key first); Y lets it go.
            if (event.type == SDL_EVENT_MOUSE_BUTTON_DOWN && mouse_camera_wanted) {
                if (SDL_Window* window = SDL_GetWindowFromID(event.button.windowID);
                    window && !SDL_GetWindowRelativeMouseMode(window)) {
                    SDL_SetWindowRelativeMouseMode(window, true);
                    std::printf("Mouse camera: on (click; Y lets the mouse go)\n");
                    std::fflush(stdout);
                }
            }
            return false;
        }
        const int button = event.button.button == SDL_BUTTON_LEFT    ? 0
                           : event.button.button == SDL_BUTTON_RIGHT  ? 1
                           : event.button.button == SDL_BUTTON_MIDDLE ? 2
                                                                      : -1;
        if (button >= 0) {
            io.AddMouseButtonEvent(button, event.type == SDL_EVENT_MOUSE_BUTTON_DOWN);
        }
        return true;
    }
    case SDL_EVENT_MOUSE_WHEEL:
        if (!is_open) {
            return false;
        }
        io.AddMouseWheelEvent(event.wheel.x, event.wheel.y);
        return true;
    default:
        return false;
    }
}

// After "Apply and restart", the new options are kept once the game has shown about ten
// seconds of frames after loading (run.py restores the previous ones otherwise).
void ConfirmRestart() {
    static u32 frames = 0;
    auto& s = IssnSettings::Get();
    if (!s.restart_unconfirmed || loading || ++frames < 600) {
        return;
    }
    s.restart_unconfirmed = false;
    IssnSettings::Save();
    std::puts("Settings: graphics options confirmed after restart");
}

void SetBackgroundLoading(u32 done, u32 total) {
    background_done = done;
    background_total = total;
}

void SetLoading(bool active, u32 done, u32 total) {
    loading_done = done;
    loading_total = total;
    loading = active;
}

bool Visible() {
    const auto& s = IssnSettings::Get();
    return initialized && (loading || background_total || menu_open || text_entry_active || choice_active ||
                           choice_retiring || s.show_fps || save_note_until.load() != 0 ||
                           (s.actor_watch_supported && s.enemy_health_bar));
}

bool CapturesInput() {
    return menu_open || text_entry_active || choice_active;
}

void BeginChoice(const std::string& title, const std::vector<std::string>& items,
                 std::vector<IssnPng::Image> pictures, int focus) {
    std::scoped_lock lock{imgui_mutex};
    RetireChoiceTextures();
    choice_pictures = std::move(pictures);
    choice_pictures.resize(items.size());
    choice_title = title;
    choice_items = items;
    choice_index = focus >= 0 && focus < int(items.size()) ? focus : 0;
    choice_result = -1;
    choice_active = true;
}

int PollChoice() {
    return choice_result;
}

void SetTextEntry(bool active, const std::string& prompt, const std::string& text) {
    std::scoped_lock lock{imgui_mutex};
    text_entry_active = active;
    text_entry_prompt = prompt;
    text_entry_text = text;
}

void NoteGameSaved(const std::string& what) {
    std::printf("Game saved%s%s\n", what.empty() ? "" : ": ", what.c_str());
    std::fflush(stdout);
    if (!IssnSettings::Get().save_note) {
        return;
    }
    {
        std::scoped_lock lock{save_note_mutex};
        save_note_text = what;
    }
    save_note_until = NowMs() + SaveNoteMs;
}

void NoteGameFrame() {
    game_frames.fetch_add(1, std::memory_order_relaxed);
}

void Render(vk::CommandBuffer cmdbuf, vk::ImageView view, vk::Extent2D extent) {
    // Game frame interval for the FPS readout (measured also while nothing is drawn). A present
    // that repeats the last frame (frame pacing) is not a new frame; before the driver's first
    // Flip every present counts.
    const auto now = std::chrono::steady_clock::now();
    // The menu's own clock: every present.
    static auto last_render = now;
    const float ms = std::chrono::duration<float, std::milli>(now - last_render).count();
    last_render = now;
    if (ms > 0.0f && ms < 1000.0f) {
        present_ms_avg = present_ms_avg == 0.0f ? ms : present_ms_avg * 0.95f + ms * 0.05f;
    }
    const std::uint64_t frames = game_frames.load(std::memory_order_relaxed);
    if (frames != seen_frames || frames == 0) {
        const std::uint64_t count = frames > seen_frames ? frames - seen_frames : 1;
        const float frame_ms =
            std::chrono::duration<float, std::milli>(now - last_present).count() / float(count);
        last_present = now;
        seen_frames = frames;
        if (frame_ms > 0.0f && frame_ms < 1000.0f) {
            frame_ms_avg =
                frame_ms_avg == 0.0f ? frame_ms : frame_ms_avg * 0.95f + frame_ms * 0.05f;
        }
    }
    ConfirmRestart();
    if (!Visible()) {
        return;
    }
    std::scoped_lock lock{imgui_mutex};
    ImGuiIO& io = ImGui::GetIO();
    io.DisplaySize = ImVec2(float(extent.width), float(extent.height));
    io.DeltaTime = ms > 0.0f && ms < 1000.0f ? ms / 1000.0f : 1.0f / 60.0f;
    // UI scale follows the display height (1080p = 1).
    const float scale = std::max(float(extent.height) / 1080.0f, 0.75f);
    if (std::abs(scale - base_scale) > 0.01f) {
        ImGuiStyle& style = ImGui::GetStyle();
        style.ScaleAllSizes(scale / base_scale);
        style.FontScaleMain = scale;
        base_scale = scale;
    }

    UpdateChoiceTextures();
    ImGui_ImplVulkan_NewFrame();
    ImGui::NewFrame();
    if (loading) {
        LoadingScreen();
    }
    if (menu_open) {
        Menu();
    }
    if (IssnSettings::Get().show_fps && !menu_open) {
        FpsCounter();
    }
    if (!loading && !menu_open) {
        HealthDisplay();
    }
    if (background_total && !loading && !menu_open) {
        BackgroundLoading();
    }
    if (save_note_until.load() != 0 && !loading && !menu_open) {
        SaveNote();
    }
    if (text_entry_active) {
        TextEntryBox();
    }
    if (choice_active && !menu_open) {
        ChoiceBox();
    }
    ImGui::Render();

    const vk::RenderingAttachmentInfo attachment{
        .imageView = view,
        .imageLayout = vk::ImageLayout::eColorAttachmentOptimal,
        .loadOp = vk::AttachmentLoadOp::eLoad,
        .storeOp = vk::AttachmentStoreOp::eStore,
    };
    cmdbuf.beginRendering(vk::RenderingInfo{
        .renderArea = {{0, 0}, extent},
        .layerCount = 1,
        .colorAttachmentCount = 1,
        .pColorAttachments = &attachment,
    });
    {
        // Font atlas uploads submit to the graphics queue themselves.
        std::scoped_lock submit_lock{Vulkan::Scheduler::submit_mutex};
        ImGui_ImplVulkan_RenderDrawData(ImGui::GetDrawData(), cmdbuf);
    }
    cmdbuf.endRendering();
}

} // namespace IssnOverlay
