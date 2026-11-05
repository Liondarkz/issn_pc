// SPDX-License-Identifier: GPL-2.0-or-later
// issn 11-04: the exposure meter's mailbox (src/exposure_hook.h). The game's thread writes it
// through the cave; the menu reads it and can hold the value (dark-night diagnosis).
#include "issn_exposure.h"

#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>

#include "../../src/exposure_hook.h"
#include "common/memory_patcher.h"

namespace {
alignas(64) IssnExposureMailbox g_box{};
std::atomic<bool> g_attached{false};

float Load(const float& f) {
    float v;
    std::memcpy(&v, const_cast<const float*>(&f), sizeof(v));
    return v;
}
} // namespace

extern "C" void* issngpu_exposure_mailbox(void) {
    return &g_box;
}

extern "C" void issngpu_exposure_attached(void) {
    g_attached = true;
}

namespace IssnExposure {

Reading Read() {
    Reading r;
    r.attached = g_attached.load();
    const volatile IssnExposureMailbox& box = g_box;
    r.value = Load(const_cast<const IssnExposureMailbox&>(box).value);
    r.sum = Load(const_cast<const IssnExposureMailbox&>(box).sum);
    r.rows = box.rows;
    r.calls = box.calls;
    r.held = box.held != 0;
    return r;
}

void Hold(bool on) {
    volatile IssnExposureMailbox& box = g_box;
    if (!on) {
        box.held = 0;
        std::printf("Exposure meter: following the scene again\n");
    } else {
        float v = Read().value;
        uint32_t bits;
        std::memcpy(&bits, &v, 4);
        box.held = bits ? bits : 1u;
        std::printf("Exposure meter: held at %g\n", v);
    }
    std::fflush(stdout);
}

namespace {
constexpr uintptr_t TodState = 0x7f8e800; // eboot .bss: current, fallback, wanted id
std::atomic<uint64_t> g_last_good{0};

uint64_t Read64(uintptr_t address) {
    uint64_t v;
    std::memcpy(&v, reinterpret_cast<const void*>(address), 8);
    return v;
}
// A guest heap pointer: inside the 64-bit guest space the port maps (not null, aligned).
bool Plausible(uint64_t p) {
    return p >= 0x10000 && p < (1ull << 47) && (p & 7) == 0;
}
bool PlausibleText(uint64_t p) {
    return p >= 0x10000 && p < (1ull << 47);
}
// A guest C string (printable ASCII only, cut at size-1).
void CopyText(uint64_t p, char* out, size_t size) {
    out[0] = 0;
    if (!PlausibleText(p)) {
        return;
    }
    const char* s = reinterpret_cast<const char*>(p);
    size_t i = 0;
    for (; i + 1 < size && s[i]; ++i) {
        const unsigned char c = static_cast<unsigned char>(s[i]);
        out[i] = c >= 0x20 && c < 0x7f ? static_cast<char>(c) : '?';
    }
    out[i] = 0;
}
uint32_t Read32(uintptr_t address) {
    uint32_t v;
    std::memcpy(&v, reinterpret_cast<const void*>(address), 4);
    return v;
}
// The manager's table: (id, name, node) of each entry, up to max.
struct TodNode {
    uint64_t id;
    uint64_t obj;
};
int WalkTable(uintptr_t state, TodNode* out, int max) {
    const uint32_t count = Read32(state + 0x30);
    const uint64_t buckets = Read64(state + 0x38);
    if (count == 0 || count > 65536 || !Plausible(buckets)) {
        return 0;
    }
    int n = 0;
    for (uint32_t b = 0; b < count && n < max; ++b) {
        uint64_t node = Read64(buckets + 8ull * b);
        for (int guard = 0; Plausible(node) && guard < 64 && n < max; ++guard) {
            out[n++] = {Read64(node + 8), Read64(node + 0x10)};
            node = Read64(node + 0x18);
        }
    }
    return n;
}
void NameOf(uint64_t obj, char* out, size_t size) {
    out[0] = 0;
    if (!Plausible(obj)) {
        return;
    }
    const uint64_t data = Read64(obj + 0x28);
    if (Plausible(data)) {
        CopyText(Read64(data + 0x58), out, size);
    }
}
} // namespace

TimeOfDay ReadTimeOfDay() {
    TimeOfDay t;
    const uintptr_t base = MemoryPatcher::g_eboot_address;
    if (base == 0) {
        return t;
    }
    t.known = true;
    const uintptr_t state = base + TodState;
    const uint64_t current = Read64(state), fallback = Read64(state + 8);
    t.wanted = Read64(state + 0x10);
    t.current = ~0ull;
    if (Plausible(current)) {
        const uint64_t data = Read64(current + 0x28);
        if (Plausible(data)) {
            t.current = Read64(data + 0x18);
            t.lighting = Read64(data + 0x68) != 0;
        }
        t.fallback = current == fallback;
    }
    if (t.lighting && !t.fallback && t.current != ~0ull) {
        g_last_good = t.current;
    }
    t.last_good = g_last_good.load();
    return t;
}

bool ForceLastGoodTimeOfDay() {
    const TimeOfDay t = ReadTimeOfDay();
    if (!t.known || t.last_good == 0) {
        return false;
    }
    const uint64_t id = t.last_good;
    std::memcpy(reinterpret_cast<void*>(MemoryPatcher::g_eboot_address + TodState + 0x10), &id, 8);
    std::printf("Time of day: asked the game for %#llx (test)\n", static_cast<unsigned long long>(id));
    std::fflush(stdout);
    return true;
}

TodDetail ReadTodDetail() {
    TodDetail d;
    const uintptr_t base = MemoryPatcher::g_eboot_address;
    if (base == 0) {
        return d;
    }
    d.known = true;
    const uintptr_t state = base + TodState;
    d.wanted = Read64(state + 0x10);
    TodNode nodes[64];
    const int n = WalkTable(state, nodes, 64);
    d.entries = static_cast<uint32_t>(n);
    size_t used = 0;
    for (int i = 0; i < n; ++i) {
        char name[40];
        NameOf(nodes[i].obj, name, sizeof(name));
        if (nodes[i].id == d.wanted) {
            d.in_table = true;
            std::snprintf(d.name, sizeof(d.name), "%s", name);
        }
        if (used + 1 < sizeof(d.list)) {
            const int w = std::snprintf(d.list + used, sizeof(d.list) - used, "%s%s", i ? ", " : "",
                                        name[0] ? name : "?");
            used += w > 0 ? static_cast<size_t>(w) : 0;
        }
    }
    if (!d.in_table || !d.name[0]) {
        return d;
    }
    // The section name and its hash (eboot 0x5ed7c0: h = h * 101 + tolower(c); "Nil" = none).
    char world[48];
    CopyText(Read64(base + 0x7f111e0 + 0x1c0a0) + 2, world, sizeof(world));
    std::snprintf(d.section, sizeof(d.section), "%s_x_%s", world, d.name);
    uint64_t h = 0;
    for (const char* c = d.section; *c; ++c) {
        const unsigned char u = static_cast<unsigned char>(*c);
        h = h * 101 + static_cast<uint64_t>(u >= 'A' && u <= 'Z' ? u + 32 : u);
    }
    // The world's section table (eboot 0x3c4e40): 997 buckets at +0x198, node {next, id, +0x10 index}.
    const uintptr_t table = base + 0x7d37610;
    const uint32_t key = static_cast<uint32_t>(h ^ (h >> 32)) % 997u;
    uint64_t node = Read64(table + 0x198 + 8ull * key);
    for (int guard = 0; Plausible(node) && guard < 256; ++guard) {
        if (Read64(node + 8) == h) {
            break;
        }
        node = Read64(node);
    }
    if (!Plausible(node) || Read64(node + 8) != h) {
        return d; // no such section: the game does not wait for one
    }
    int16_t raw;
    std::memcpy(&raw, reinterpret_cast<const void*>(node + 0x10), 2);
    d.section_index = raw >> 8;
    // eboot 0x3c4db0: ready unless the section object says it is still on its way.
    const uint32_t count = Read32(table + 0xd0);
    const uint64_t array = Read64(table + 0xc8);
    if (static_cast<uint32_t>(d.section_index) >= count || !Plausible(array)) {
        d.section_state = 1;
        return d;
    }
    const uint64_t sec = Read64(array + 8ull * static_cast<uint32_t>(d.section_index));
    if (!Plausible(sec)) {
        d.section_state = 3;
        return d;
    }
    std::memcpy(d.flags, reinterpret_cast<const void*>(sec + 0x44), 4);
    bool ready;
    if (d.flags[3]) {
        ready = true;
    } else if (d.flags[2]) {
        ready = false;
    } else if (Read64(table + 0xf8) == 0) {
        ready = true;
    } else {
        ready = d.flags[1] && d.flags[0];
    }
    d.section_state = ready ? 1 : 2;
    return d;
}

bool ForceNextTimeOfDay() {
    const uintptr_t base = MemoryPatcher::g_eboot_address;
    if (base == 0) {
        return false;
    }
    const uintptr_t state = base + TodState;
    TodNode nodes[64];
    const int n = WalkTable(state, nodes, 64);
    if (n == 0) {
        return false;
    }
    const uint64_t wanted = Read64(state + 0x10);
    int next = 0;
    for (int i = 0; i < n; ++i) {
        if (nodes[i].id == wanted) {
            next = (i + 1) % n;
            break;
        }
    }
    const uint64_t id = nodes[next].id;
    char name[40];
    NameOf(nodes[next].obj, name, sizeof(name));
    std::memcpy(reinterpret_cast<void*>(state + 0x10), &id, 8);
    std::printf("Time of day: asked the game for %#llx '%s' (test, %d of %d)\n",
                static_cast<unsigned long long>(id), name, next + 1, n);
    std::fflush(stdout);
    return true;
}

namespace {
// issn 11-05 (3): the black-city fix. Tested by João on Lvcn's save (24%, "Drogas no Lixo"): the
// game wants time_of_day_dismal, whose world section (world_seaport_x_time_of_day_dismal) never
// finishes streaming in (flags 3 1 1 0: on its way, for ever), so the manager waits for ever and
// no time of day is in use: no sky, no ambient light, black city. Asking for early_morning lit it
// at once. Fix: when the wanted one has waited for its section with nothing in use for 30 s, ask
// for another (and the next one when that one also hangs 15 s). ISSN_TOD_RESCUE=0 turns it off.
constexpr const char* kRescueOrder[] = {
    "time_of_day_blustery", "time_of_day_early_morning", "time_of_day_hazy_sunset",
    "time_of_day_sunset_candy", "time_of_day_hdr_night",
};
struct RescueState {
    uint64_t stuck_id = 0;
    std::chrono::steady_clock::time_point since{};
    uint64_t asked[8] = {};
    int asked_count = 0;
    bool last_was_ours = false;
    char status[160] = "nothing to do";
};
RescueState g_rescue;
std::atomic<int> g_rescues{0};

bool RescueEnabled() {
    static const bool on = [] {
        const char* e = std::getenv("ISSN_TOD_RESCUE");
        return !(e && e[0] == '0');
    }();
    return on;
}

void RescueTick() {
    if (!RescueEnabled() || MemoryPatcher::g_eboot_address == 0) {
        return;
    }
    using Clock = std::chrono::steady_clock;
    static auto next = Clock::now();
    const auto now = Clock::now();
    if (now < next) {
        return;
    }
    next = now + std::chrono::seconds(1);
    auto& r = g_rescue;
    const TimeOfDay t = ReadTimeOfDay();
    const TodDetail d = ReadTodDetail();
    const bool stuck = d.in_table && d.section_state == 2 && t.current == ~0ull;
    if (!stuck) {
        if (r.stuck_id != 0 && t.lighting) {
            std::snprintf(r.status, sizeof(r.status), "%s", g_rescues.load() ? "fixed (see the log)" : "nothing to do");
        }
        r.stuck_id = 0;
        return;
    }
    if (d.wanted != r.stuck_id) {
        r.last_was_ours = false;
        for (int i = 0; i < r.asked_count; ++i) {
            r.last_was_ours = r.last_was_ours || r.asked[i] == d.wanted;
        }
        r.stuck_id = d.wanted;
        r.since = now;
        std::printf("Black-city fix: '%s' waits for its section '%s' and no time of day is in use\n",
                    d.name, d.section);
        std::fflush(stdout);
    }
    const auto waited = std::chrono::duration_cast<std::chrono::seconds>(now - r.since).count();
    const long long limit = r.last_was_ours ? 15 : 30;
    if (waited < limit) {
        std::snprintf(r.status, sizeof(r.status), "'%s' stuck for %llds (acts at %llds)", d.name,
                      static_cast<long long>(waited), limit);
        return;
    }
    // The first one of the list in the game's table, not tried yet this session.
    const uintptr_t state = MemoryPatcher::g_eboot_address + TodState;
    TodNode nodes[64];
    const int n = WalkTable(state, nodes, 64);
    for (const char* want : kRescueOrder) {
        for (int i = 0; i < n; ++i) {
            char name[40];
            NameOf(nodes[i].obj, name, sizeof(name));
            if (std::strcmp(name, want) != 0 || nodes[i].id == d.wanted) {
                continue;
            }
            bool tried = false;
            for (int k = 0; k < r.asked_count; ++k) {
                tried = tried || r.asked[k] == nodes[i].id;
            }
            if (tried) {
                continue;
            }
            const uint64_t id = nodes[i].id;
            std::memcpy(reinterpret_cast<void*>(state + 0x10), &id, 8);
            if (r.asked_count < 8) {
                r.asked[r.asked_count++] = id;
            }
            g_rescues.fetch_add(1);
            std::printf("Black-city fix: '%s' hung %llds waiting for its section; asked the game for '%s'\n",
                        d.name, static_cast<long long>(waited), name);
            std::fflush(stdout);
            std::snprintf(r.status, sizeof(r.status), "'%s' hung: asked for '%s'", d.name, name);
            r.stuck_id = 0;
            return;
        }
    }
    std::snprintf(r.status, sizeof(r.status), "'%s' stuck, no other time of day left to try", d.name);
}
} // namespace

const char* RescueStatus() {
    return RescueEnabled() ? g_rescue.status : "off (ISSN_TOD_RESCUE=0)";
}

void Tick() {
    RescueTick();
    if (!g_attached.load(std::memory_order_relaxed)) {
        return;
    }
    using Clock = std::chrono::steady_clock;
    static const auto start = Clock::now();
    static auto next = start + std::chrono::seconds(5);
    const auto now = Clock::now();
    if (now < next) {
        return;
    }
    const bool early = now - start < std::chrono::minutes(2);
    next = now + std::chrono::seconds(early ? 5 : 30);
    const Reading r = Read();
    const TimeOfDay t = ReadTimeOfDay();
    std::printf("Exposure meter: value %g, tile sum %g, %u tile rows, %u frames%s; time of day wanted "
                "%#llx, in use %#llx%s, lighting %s\n",
                r.value, r.sum, r.rows, r.calls, r.held ? " (held)" : "",
                static_cast<unsigned long long>(t.wanted), static_cast<unsigned long long>(t.current),
                t.fallback ? " (the game's fallback)" : "", t.lighting ? "loaded" : "NONE");
    // The reason, when it changes (and the table once).
    const TodDetail d = ReadTodDetail();
    static uint64_t last_wanted = 1;
    static int last_state = -1;
    static bool listed = false;
    if (d.known && (d.wanted != last_wanted || d.section_state != last_state || !listed)) {
        last_wanted = d.wanted;
        last_state = d.section_state;
        static const char* const states[] = {"no section to wait for", "section ready",
                                             "WAITING for the section (not streamed in)",
                                             "section object missing"};
        std::printf("Time of day detail: wanted %#llx %s '%s'; section '%s' index %d: %s (flags %u %u %u %u)\n",
                    static_cast<unsigned long long>(d.wanted),
                    d.in_table ? "is in the table as" : "is NOT in the table", d.name, d.section,
                    d.section_index, states[d.section_state & 3], d.flags[0], d.flags[1], d.flags[2],
                    d.flags[3]);
        if (!listed && d.entries) {
            std::printf("Time of day detail: the table has %u: %s\n", d.entries, d.list);
            listed = true;
        }
    }
    std::fflush(stdout);
}

} // namespace IssnExposure
