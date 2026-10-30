#include "issn_write_log.h"
#include "issn_readback_diff.h"
// port: glue between the C loader and the vendored shadPS4 video core.
#include "issn_overlay.h"
#include "issn_png.h"
#include "issn_settings.h"
#include "issn_copy.h"
#ifndef _WIN32
#include <cstdint>
#include <cmath>
#include <sys/resource.h>
#endif
#include "issn_toggles.h"
#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <exception>
#include <atomic>
#include <condition_variable>
#include <boost/asio/io_context.hpp>
#include "common/polyfill_thread.h"
#include "video_core/renderdoc.h"
#include <cstring>
#include <mutex>
#include <string>
#include <chrono>
#include <thread>
#include <vector>
#include <array>
#include <SDL3/SDL.h>
#include "../issngpu.h"
#include "common/elf_info.h"
#include "common/release_mode.h"
#include <filesystem>
#include <fstream>
#include <iterator>
#include <xxhash.h>
#include "common/fault_monitor.h"
#include "common/logging/log.h"
#include "core/libraries/kernel/orbis_error.h"
#include "core/libraries/libs.h"
#include "core/memory.h"
#include "core/signals.h"
#include "sdl_window.h"
#include "video_core/renderer_vulkan/vk_rasterizer.h"

extern "C" {
// runtime_memory.c
int runtime_memory_is_mapped(uintptr_t address, uint64_t size);
int runtime_memory_write_backing(uintptr_t address, const void* data, uint64_t size);
int runtime_memory_read_backing(uintptr_t address, void* data, uint64_t size);
uint64_t runtime_memory_clamp(uintptr_t address, uint64_t size);
int runtime_memory_region(uintptr_t address, uintptr_t* start, uintptr_t* end, int* mapped);
void runtime_memory_gpu_protect(uintptr_t address, uint64_t size, int read, int write);
typedef void (*RuntimeGpuRange)(uintptr_t address, uint64_t size);
// runtime_kernel.c: one clock for guest and GPU timestamps
uint64_t runtime_process_time_us(void);
uint64_t runtime_process_time_counter(void);
uint64_t runtime_tsc_frequency(void);
uint64_t runtime_read_tsc(void);
int32_t* runtime_errno(void);
void runtime_memory_set_gpu_hooks(RuntimeGpuRange map, RuntimeGpuRange unmap, RuntimeGpuRange invalidate);
// runtime.c: exit counters (and the ISA site report), as on the guest's own exit
void runtime_report(void);
}

Frontend::WindowSDL* g_window = nullptr;

// issn_pc: the screen shape (issn_game_patches.cpp ApplyRenderSize).
namespace IssnShape {
double g_aspect = 16.0 / 9.0;
float g_movie_x = 0.0f, g_movie_y = 0.0f;
/// The game window's size in pixels; a fullscreen window: its display's desktop mode.
bool ScreenPixels(int& width, int& height) {
    SDL_Window* window = g_window ? g_window->GetSDLWindow() : nullptr;
    if (!window) {
        return false;
    }
    if (SDL_GetWindowFlags(window) & SDL_WINDOW_FULLSCREEN) {
        if (const SDL_DisplayMode* mode = SDL_GetDesktopDisplayMode(SDL_GetDisplayForWindow(window))) {
            width = mode->w;
            height = mode->h;
            return width > 0 && height > 0;
        }
    }
    return SDL_GetWindowSizeInPixels(window, &width, &height) && width > 0 && height > 0;
}
} // namespace IssnShape

// issn-axy: what axylpup's tree takes from its loader and patcher.
namespace MemoryPatcher {
uintptr_t g_eboot_address = 0;
std::string g_game_serial;
// issn-axy: axylpup's 1.1 live screen percentage lives in his patcher (not in this port yet):
// the core's hooks see a game that never changes its render size.
bool LiveResizeAvailable() {
    return false;
}
void OnSubmitDone() {}
bool TakeLivePurge(u64 sizes[2]) {
    (void)sizes;
    return false;
}
void RequeueLivePurge() {}
bool InLiveResizeWindow() {
    return false;
}
bool IsRenderShaped(u32 width, u32 height) {
    // The game's render shape (+-3%): 16:9, or the screen shape issn_game_patches.cpp set.
    const double aspect = IssnShape::g_aspect;
    return height != 0 && std::abs(double(width) / height - aspect) < 0.03 * aspect;
}
bool MovieFitRect(float& x, float& y) {
    x = IssnShape::g_movie_x;
    y = IssnShape::g_movie_y;
    return x > 0.0f;
}
s64 MsSinceLiveResize() {
    return INT64_MAX;
}
/// The game's own quality ladder level (0 = full ... 20), read live (eboot +0xf39978); -1
/// before the game is loaded.
int GameQualityLevel() {
    if (g_eboot_address == 0) {
        return -1;
    }
    s32 level;
    std::memcpy(&level, reinterpret_cast<const void*>(g_eboot_address + 0xf39978), 4);
    return level;
}
} // namespace MemoryPatcher
namespace Common {
extern const char g_scm_desc[];
const char g_scm_desc[] = "issn_pc (axylpup second-son 2212a345)";
} // namespace Common

namespace Libraries::GnmDriver { void RegisterLib(Core::Loader::SymbolsResolver* sym); }
namespace Libraries::AvPlayer { void RegisterLib(Core::Loader::SymbolsResolver* sym); }
namespace Libraries::VideoOut { void RegisterLib(Core::Loader::SymbolsResolver* sym); }
namespace Libraries::Kernel { void RegisterEventQueue(Core::Loader::SymbolsResolver* sym); }

namespace {
struct Symbol {
    std::string nid, library, module;
    u64 address;
};
std::vector<Symbol> g_symbols;
u32 g_sdk_version;
} // namespace

namespace Core::Loader {
void SymbolsResolver::AddSymbol(const char* nid, const char* library, const char* module, SymbolType,
                                u64 address) {
    g_symbols.push_back({nid, library, module, address});
}
} // namespace Core::Loader

// axylpup 1.1.1 (GitHub #5): the box shown before an out-of-memory assert closes the game.
namespace Common::ReleaseMode {
void ShowOutOfMemoryNotice(unsigned long long bytes) {
    static std::atomic<bool> shown{false};
    if (shown.exchange(true)) {
        return;
    }
    const std::string text = fmt::format(
        "Windows could not give the game {} MB of memory for the graphics card, so the game has "
        "to close.\n\nWhat usually helps:\n"
        "- close other programs (browser, Discord, overlays)\n"
        "- lower the internal resolution or turn Performance mode on (launcher: Performance)\n"
        "- turn the page file on: Advanced system settings > Performance Settings > Advanced > "
        "Virtual memory > \"Automatically manage paging file size\"\n"
        "- keep at least 20 GB free on C:\n\n"
        "user\\last_run.log has the details - please attach it to a bug report.",
        bytes >> 20);
    SDL_ShowSimpleMessageBox(SDL_MESSAGEBOX_ERROR, "inFAMOUS Second Son: out of memory",
                             text.c_str(), nullptr);
}
} // namespace Common::ReleaseMode

// ElfInfo's fields are private to Core::Emulator; the port fills them here.
namespace Core {
class Emulator {
public:
    static void FillElfInfo(const IssnGpuConfig& config) {
        auto& info = Common::ElfInfo::Instance();
        info.initialized = true;
        info.game_serial = config.serial ? config.serial : "UNKNOWN";
        info.title = config.title ? config.title : "";
        info.sdk_ver = config.sdk_version;
        info.psf_attributes.raw = config.psf_attributes;
        // issn_pc (2026-10-23): the shader seed (SHADPS4_CACHE_REBUILD) makes stages from the
        // game's own files in this folder and checks the game by its code hash; both were left
        // empty, so a verify run found "no code in the game index" for every stage.
        info.app_ver = config.app_ver ? config.app_ver : "";
        if (config.game_dir && *config.game_dir) {
            info.game_folder = std::filesystem::u8path(config.game_dir);
            const auto eboot = info.game_folder / "eboot.bin";
            info.eboot_code_hash = EbootCodeHash(eboot);
            if (info.eboot_code_hash == 0) {
                // Not an ELF / SELF this reads: the whole file still names the game version.
                std::ifstream in{eboot, std::ios::binary};
                const std::vector<char> bytes{std::istreambuf_iterator<char>(in),
                                              std::istreambuf_iterator<char>()};
                info.eboot_code_hash = bytes.empty() ? 0 : XXH3_64bits(bytes.data(), bytes.size());
            }
            std::printf("Shader seed: game code %016llx (%s %s)\n",
                        static_cast<unsigned long long>(info.eboot_code_hash),
                        info.game_serial.c_str(), info.app_ver.c_str());
        }
    }

    /// XXH3 of the eboot's executable PT_LOAD segments, chained by seed (axylpup's
    /// ReadEbootCodeHash): from a plain ELF or a plaintext PS4 SELF (scripts/prepare.py reads
    /// the same two); 0 for anything else.
    static u64 EbootCodeHash(const std::filesystem::path& path) {
        std::ifstream in{path, std::ios::binary};
        const std::vector<char> bytes{std::istreambuf_iterator<char>(in),
                                      std::istreambuf_iterator<char>()};
        const auto* data = reinterpret_cast<const unsigned char*>(bytes.data());
        const u64 size = bytes.size();
        const auto read = [&](u64 at, u64 width, u64& value) {
            value = 0;
            if (at > size || width > size - at) {
                return false;
            }
            std::memcpy(&value, data + at, width);
            return true;
        };
        // A SELF: 32-byte header, `count` 32-byte segment entries, then the ELF header.
        u64 elf = 0, count = 0;
        if (size >= 4 && std::memcmp(data, "\x4f\x15\x3d\x1d", 4) == 0) {
            if (!read(24, 2, count)) {
                return 0;
            }
            elf = 32 + count * 32;
        }
        u64 phoff, phentsize, phnum;
        if (elf + 64 > size || std::memcmp(data + elf, "\x7f" "ELF", 4) != 0 ||
            !read(elf + 0x20, 8, phoff) || !read(elf + 0x36, 2, phentsize) ||
            !read(elf + 0x38, 2, phnum) || phentsize < 56) {
            return 0;
        }
        u64 hash = 0;
        for (u64 i = 0; i < phnum; ++i) {
            const u64 at = elf + phoff + i * phentsize;
            u64 type, flags, offset, filesz;
            if (!read(at, 4, type) || !read(at + 4, 4, flags) || !read(at + 8, 8, offset) ||
                !read(at + 32, 8, filesz)) {
                return 0;
            }
            if (type != 1 || (flags & 1) == 0 || filesz == 0) {
                continue;
            }
            if (count != 0) {
                // The segment's bytes sit where its SELF entry (bit 11: has a segment, bits
                // 20-31: program header index) says.
                bool found = false;
                for (u64 e = 0; e < count && !found; ++e) {
                    u64 eflags, eoffset, esize;
                    if (!read(32 + e * 32, 8, eflags) || !read(40 + e * 32, 8, eoffset) ||
                        !read(48 + e * 32, 8, esize)) {
                        return 0;
                    }
                    if ((eflags & 0x800) != 0 && ((eflags >> 20) & 0xfff) == i && esize == filesz) {
                        offset = eoffset;
                        found = true;
                    }
                }
                if (!found) {
                    return 0;
                }
            }
            if (offset > size || filesz > size - offset) {
                return 0;
            }
            hash = XXH3_64bits_withSeed(data + offset, filesz, hash);
        }
        return hash;
    }
};

void MemoryManager::SetRasterizer(Vulkan::Rasterizer* rasterizer_) {
    rasterizer = rasterizer_;
    // Existing guest mappings are replayed by the runtime when hooks are installed.
    runtime_memory_set_gpu_hooks(
        [](uintptr_t address, uint64_t size) {
            Memory::Instance()->GetRasterizer()->MapMemory(address, size);
        },
        [](uintptr_t address, uint64_t size) { Memory::Instance()->GetRasterizer()->UnmapMemory(address, size); },
        [](uintptr_t address, uint64_t size) {
            Memory::Instance()->GetRasterizer()->InvalidateMemory(address, size);
        });
}
void MemoryManager::InvalidateMemory(VAddr address, u64 size) {
    if (rasterizer) rasterizer->InvalidateMemory(address, size);
}
u64 MemoryManager::ClampRangeSize(VAddr virtual_addr, u64 size) {
    return runtime_memory_clamp(virtual_addr, size);
}
bool MemoryManager::IsValidMapping(VAddr virtual_addr, u64 size) {
    return runtime_memory_is_mapped(virtual_addr, size ? size : 1) != 0;
}
u64 MemoryManager::TryClampRangeSize(VAddr virtual_addr, u64 size) {
    if (!runtime_memory_is_mapped(virtual_addr, 1)) {
        return 0;
    }
    return runtime_memory_clamp(virtual_addr, size);
}
bool MemoryManager::TryReadBacking(VAddr virtual_addr, u8* dest, u64 size) {
    return runtime_memory_read_backing(virtual_addr, dest, size) != 0;
}
bool MemoryManager::TryReadGuest(VAddr virtual_addr, u8* dest, u64 size) {
    if (TryReadBacking(virtual_addr, dest, size)) {
        return true;
    }
    std::memset(dest, 0, size);
    return false;
}
bool MemoryManager::FullyBacked(VAddr virtual_addr, u64 size) {
    // Every mapped guest range lives in the runtime's backing view.
    return runtime_memory_is_mapped(virtual_addr, size) != 0;
}
void MemoryManager::ExcludeFromDownloads(VAddr addr) {
    static std::mutex m;
    std::scoped_lock lk{m};
    const u32 n = num_download_exclusions.load(std::memory_order_acquire);
    for (u32 i = 0; i < n; ++i) {
        if (download_exclusions[i].load(std::memory_order_relaxed) == addr) {
            return;
        }
    }
    if (n < MaxDownloadExclusions) {
        download_exclusions[n].store(addr, std::memory_order_relaxed);
        num_download_exclusions.store(n + 1, std::memory_order_release);
    }
}
// From axylpup's memory.cpp: the download skips the excluded words (CP read pointers).
bool MemoryManager::TryWriteBackingDownload(void* address, const void* data, u64 size) {
    const u32 n = num_download_exclusions.load(std::memory_order_acquire);
    if (n == 0) [[likely]] {
        return TryWriteBacking(address, data, size);
    }
    const VAddr lo = reinterpret_cast<VAddr>(address);
    const VAddr hi = lo + size;
    std::array<VAddr, MaxDownloadExclusions> hits{};
    u32 h = 0;
    for (u32 i = 0; i < n; ++i) {
        const VAddr e = download_exclusions[i].load(std::memory_order_relaxed);
        if (e < hi && lo < e + 4) {
            hits[h++] = e;
        }
    }
    if (h == 0) {
        return TryWriteBacking(address, data, size);
    }
    std::sort(hits.begin(), hits.begin() + h);
    download_exclusion_hits.fetch_add(h, std::memory_order_relaxed);
    bool ok = true;
    VAddr cur = lo;
    const auto* src = static_cast<const u8*>(data);
    for (u32 i = 0; i <= h; ++i) {
        const VAddr stop = i < h ? std::max(hits[i], lo) : hi;
        if (stop > cur) {
            ok &= TryWriteBacking(reinterpret_cast<void*>(cur), src + (cur - lo), stop - cur);
        }
        if (i < h) {
            cur = std::max(cur, std::min(hits[i] + 4, hi));
        }
    }
    return ok;
}
static void CopySparseSerial(VAddr source, u8* dest, u64 size) {
    while (size) {
        uintptr_t start = 0, end = 0;
        int mapped = 0;
        if (!runtime_memory_region(source, &start, &end, &mapped)) {
            end = source + size;
        }
        const u64 n = std::min<u64>(size, end - source);
        // issn: read through the unprotected backing view so readback protection (precise
        // readbacks) cannot fault on the recorder/copy threads; direct read as the fallback.
        if (!mapped) std::memset(dest, 0, n);
        else if (!runtime_memory_read_backing(source, dest, n))
            std::memcpy(dest, reinterpret_cast<const void*>(source), n);
        source += n; dest += n; size -= n;
    }
}
void MemoryManager::CopySparseMemory(VAddr source, u8* dest, u64 size) {
    // port: large uploads (streaming) are split across the copy threads.
    constexpr u64 Chunk = 512 * 1024;
    if (size < 4 * Chunk || !IssnCopy::Enabled()) {
        return CopySparseSerial(source, dest, size);
    }
    IssnCopy::ParallelFor((size + Chunk - 1) / Chunk, [&](std::size_t i) {
        const u64 offset = i * Chunk;
        CopySparseSerial(source + offset, dest + offset, std::min(Chunk, size - offset));
    });
}
bool MemoryManager::TryWriteBacking(void* address, const void* data, u64 size) {
    if (const auto hook = g_backing_write_hook.load(std::memory_order_relaxed)) [[unlikely]] {
        hook(reinterpret_cast<VAddr>(address), size);
    }
    IssnWriteLog::Note(reinterpret_cast<uintptr_t>(address), data, size, IssnWriteLog::Backing);
    IssnReadbackDiff::NoteApply(reinterpret_cast<uintptr_t>(address), data, size);
    return runtime_memory_write_backing(reinterpret_cast<uintptr_t>(address), data, size) != 0;
}
void AddressSpace::Protect(VAddr virtual_addr, u64 size, MemoryPermission perms) {
    IssnStats::Timer timer{IssnStats::t_protect};
    const u64 pages = (size + 4095) / 4096;
    IssnStats::protect_calls.fetch_add(1, std::memory_order_relaxed);
    IssnStats::protect_pages.fetch_add(pages, std::memory_order_relaxed);
    if (!True(perms & MemoryPermission::Write)) {
        IssnStats::protect_revoke_calls.fetch_add(1, std::memory_order_relaxed);
        IssnStats::protect_revoke_pages.fetch_add(pages, std::memory_order_relaxed);
    }
    runtime_memory_gpu_protect(virtual_addr, size, True(perms & MemoryPermission::Read),
                               True(perms & MemoryPermission::Write));
}
boost::icl::interval_set<VAddr> AddressSpace::GetUsableRegions() {
    boost::icl::interval_set<VAddr> set;
    // issn-axy: guest memory also lives in shadPS4's range below the host window (runtime_memory.c).
    set += boost::icl::interval<VAddr>::right_open(0x2'0000'0000ULL, 0x8'0000'0000ULL);
    set += boost::icl::interval<VAddr>::right_open(0x10'0000'0000ULL, 0xfc'0000'0000ULL);
    return set;
}
} // namespace Core

// Kernel services the vendored libraries call directly.
namespace Libraries::Kernel {
u64 PS4_SYSV_ABI sceKernelGetTscFrequency() { return runtime_tsc_frequency(); }
u64 PS4_SYSV_ABI sceKernelReadTsc() { return runtime_read_tsc(); }
u64 PS4_SYSV_ABI sceKernelGetProcessTime() { return runtime_process_time_us(); }
u64 PS4_SYSV_ABI sceKernelGetProcessTimeCounter() { return runtime_process_time_counter(); }
u64 PS4_SYSV_ABI sceKernelGetProcessTimeCounterFrequency() { return 1'000'000'000; }
s32 PS4_SYSV_ABI sceKernelIsNeoMode() { return 0; } // base PS4
s32 PS4_SYSV_ABI sceKernelGetCompiledSdkVersion(s32* ver) {
    if (!ver) return ORBIS_KERNEL_ERROR_EINVAL;
    *ver = s32(g_sdk_version);
    return ORBIS_OK;
}
s32 PS4_SYSV_ABI sceKernelUsleep(u32 microseconds) {
    std::this_thread::sleep_for(std::chrono::microseconds(microseconds));
    return ORBIS_OK;
}
} // namespace Libraries::Kernel

namespace {
// SDL video must be driven from one thread: the window lives on its own host thread.
std::thread g_window_thread;
std::mutex g_window_mutex;
std::condition_variable g_window_cv;
bool g_window_ready;
} // namespace

bool IssnVsync() {
    return IssnSettings::Get().vsync.load();
}
int IssnFpsLimit() {
    return IssnSettings::Get().fps_limit.load();
}

u32 IssnDisplayRefreshHz() {
    static const u32 hz = [] {
        const SDL_DisplayMode* mode = SDL_GetCurrentDisplayMode(SDL_GetPrimaryDisplay());
        const u32 rate = mode && mode->refresh_rate > 0 ? u32(mode->refresh_rate + 0.5f) : 60;
        std::printf("GPU: vblank follows the display refresh rate, %u Hz\n", rate);
        return std::max<u32>(rate, 60);
    }();
    return hz;
}

#if defined(ISSN_PGO_GENERATE) && defined(ISSN_PGO_LLVM)
extern "C" int __llvm_profile_write_file(void);
extern "C" void __llvm_profile_set_filename(const char*);
// Instrumented Clang build: the counters are cumulative, so each write replaces the file with
// the totals so far (the game often ends through _exit, which skips the write at exit). One file
// per process: <user>/pgo/issn-<pid>.profraw.
static void StartProfileWriter() {
    static std::string path = [] {
        const char* user = std::getenv("ISSN_GPU_USER_DIR");
        const std::filesystem::path dir = std::filesystem::path(user && *user ? user : "user") / "pgo";
        std::error_code ec;
        std::filesystem::create_directories(dir, ec);
        return (dir / "issn-%p.profraw").string();
    }();
    __llvm_profile_set_filename(path.c_str());
    std::printf("PGO: instrumented build, profile in %s every 30 s\n", path.c_str());
    std::thread([] {
        Common::SetCurrentThreadName("port:pgo");
        for (;;) {
            std::this_thread::sleep_for(std::chrono::seconds(30));
            __llvm_profile_write_file();
            std::printf("PGO: profile written\n");
        }
    }).detach();
}
#elif defined(ISSN_PGO_GENERATE)
extern "C" void __gcov_dump(void);
extern "C" void __gcov_reset(void);
// Instrumented build (ISSN_PGO=generate): the game often ends through _exit (watchdog, guest
// exit), which skips the profile write at exit. Write every 30 s and reset: the files sum the
// intervals.
static void StartProfileWriter() {
    std::thread([] {
        Common::SetCurrentThreadName("port:pgo");
        for (;;) {
            std::this_thread::sleep_for(std::chrono::seconds(30));
            __gcov_dump();
            __gcov_reset();
            std::printf("PGO: profile written\n");
        }
    }).detach();
}
#endif

extern "C" int issngpu_init(const IssnGpuConfig* config) {
    // issn: an exception nobody catches (a worker thread, the shader compiler) ends the process
    // through std::terminate: name it before the loader's abort report prints the stack.
    std::set_terminate([] {
        std::string what = "no exception in flight";
        if (const auto exception = std::current_exception()) {
            try {
                std::rethrow_exception(exception);
            } catch (const std::exception& e) {
                what = std::string{"exception: "} + e.what();
            } catch (...) {
                what = "exception of unknown type";
            }
        }
        std::fprintf(stderr, "STOP: std::terminate (%s)\n", what.c_str());
        std::fflush(stderr);
        std::abort();
    });
    IssnSettings::Load();
#ifdef ISSN_PGO_GENERATE
    StartProfileWriter();
#endif
    g_sdk_version = config->sdk_version;
#ifdef _WIN32
    if (config->user_dir && !std::getenv("ISSN_GPU_USER_DIR")) _putenv_s("ISSN_GPU_USER_DIR", config->user_dir);
#else
    if (config->user_dir) setenv("ISSN_GPU_USER_DIR", config->user_dir, 0);
#endif
    Core::Emulator::FillElfInfo(*config);
    // issn-axy: axylpup's fault / frame report (FaultRate lines every 15 s at ISSN_GPU_LOG=info,
    // and user/log/fault_rate_*.csv).
    Common::FaultMonitor::Start();
    MemoryPatcher::g_eboot_address = static_cast<uintptr_t>(config->eboot_base);
    // The game-specific paths of the new core key on the EU serial; the US eboot's code is the
    // same (axylpup's seed stamp: one code hash for CUSA00004 and CUSA00223).
    MemoryPatcher::g_game_serial = "CUSA00004";
    const std::string title = config->title ? config->title : "inFAMOUS Second Son";
    const s32 width = config->width, height = config->height;
    g_window_thread = std::thread([title, width, height] {
        Common::SetCurrentThreadName("port:window");
        auto* window = new Frontend::WindowSDL(width, height, title.c_str());
        {
            std::scoped_lock lock{g_window_mutex};
            g_window = window;
            g_window_ready = true;
        }
        g_window_cv.notify_all();
        while (window->PollEvents()) {
            SDL_Delay(2);
        }
        LOG_INFO(Frontend, "Window closed by user");
        runtime_report();
        std::fflush(stdout);
        std::_Exit(0);
    });
    g_window_thread.detach();
    {
        std::unique_lock lock{g_window_mutex};
        g_window_cv.wait(lock, [] { return g_window_ready; });
    }
    Core::Loader::SymbolsResolver resolver;
    // GnmDriver creates the presenter that the VideoOut present thread uses.
    Libraries::GnmDriver::RegisterLib(&resolver);
    Libraries::VideoOut::RegisterLib(&resolver);
    return 0;
}

namespace Libraries::Kernel { void StartKernelService(); }
extern "C" void issngpu_register_kernel(void) {
    Libraries::Kernel::StartKernelService();
    Core::Loader::SymbolsResolver resolver;
    Libraries::Kernel::RegisterEventQueue(&resolver);
    Libraries::AvPlayer::RegisterLib(&resolver);
}

extern "C" uintptr_t issngpu_resolve(const char* scoped_nid) {
    const char* hash = std::strchr(scoped_nid, '#');
    const size_t length = hash ? size_t(hash - scoped_nid) : std::strlen(scoped_nid);
    for (const auto& symbol : g_symbols) {
        if (symbol.nid.size() == length && !std::memcmp(symbol.nid.data(), scoped_nid, length)) {
            return uintptr_t(symbol.address);
        }
    }
    return 0;
}

extern "C" int issngpu_handle_fault(void* ucontext, void* address) {
    return Core::Signals::Instance()->DispatchAccessViolation(ucontext, address) ? 1 : 0;
}

extern "C" void issngpu_mark_game_main_thread(void) {
    Common::FaultMonitor::MarkGameMainThread();
}

extern "C" unsigned issngpu_symbol_count(void) {
    return unsigned(g_symbols.size());
}

// Kernel service thread: runs boost::asio timers for equeue timer events.
namespace Libraries::Kernel {
boost::asio::io_context io_context;
static std::mutex m_asio_req;
static std::condition_variable_any cv_asio_req;
static std::atomic<u32> asio_requests;
static std::jthread service_thread;

void KernelSignalRequest() {
    std::unique_lock lock{m_asio_req};
    ++asio_requests;
    cv_asio_req.notify_one();
}

static void KernelServiceThread(std::stop_token stoken) {
    Common::SetCurrentThreadName("port:kernel_service");
    while (!stoken.stop_requested()) {
        {
            std::unique_lock lock{m_asio_req};
            cv_asio_req.wait(lock, stoken, [] { return asio_requests != 0; });
        }
        if (stoken.stop_requested()) break;
        io_context.run();
        io_context.restart();
        asio_requests = 0;
    }
}

void StartKernelService() {
    service_thread = std::jthread{KernelServiceThread};
}

int* PS4_SYSV_ABI __Error() {
    return runtime_errno();
}
} // namespace Libraries::Kernel

// RenderDoc capture hooks: not used by the port.
namespace VideoCore {
void LoadRenderDoc() {}
void StartCapture() {}
void EndCapture() {}
void TriggerCapture() {}
void OnFrameBoundary() {}
void SetOutputDir(const std::filesystem::path&, const std::string&) {}
bool IsRenderDocLoaded() { return false; }
// issn-axy: F12 (in-game menu's key handler) asks for a game-only screenshot; the presenter
// writes it as PNG into user/screenshots.
static std::atomic<u32> g_shots_game{0}, g_shots_overlay{0};
void RequestScreenshot(ScreenshotRequest request) {
    if (request == ScreenshotRequest::GameOnly) g_shots_game.fetch_add(1);
    else if (request == ScreenshotRequest::WithOverlays) g_shots_overlay.fetch_add(1);
}
u32 ConsumeGameOnlyScreenshotRequests() { return g_shots_game.exchange(0); }
u32 ConsumeWithOverlaysScreenshotRequests() { return g_shots_overlay.exchange(0); }
ScreenshotRequests ConsumeScreenshotRequests() {
    return {ConsumeGameOnlyScreenshotRequests(), ConsumeWithOverlaysScreenshotRequests()};
}
} // namespace VideoCore

extern "C" int issngpu_overlay_captures_input(void) {
    return IssnOverlay::CapturesInput() ? 1 : 0;
}

extern "C" int issngpu_text_input_begin(const char* initial, const char* prompt) {
    if (!g_window) return 0;
    g_window->BeginTextInput(initial ? initial : "", prompt ? prompt : "Text");
    return 1;
}

extern "C" int issngpu_choice_begin(const char* title, const char* const* items, const char* const* images,
                                    int count, int focus) {
    if (!g_window || count <= 0) return 0;
    // Pictures are decoded here, on the game's thread, before the list is shown.
    std::vector<IssnPng::Image> pictures(count);
    for (int i = 0; images && i < count; ++i) {
        if (images[i] && !IssnPng::LoadRgba(images[i], pictures[i])) {
            LOG_WARNING(Lib_SaveDataDialog, "save list: no picture from {}", images[i]);
        }
    }
    IssnOverlay::BeginChoice(title ? title : "", std::vector<std::string>(items, items + count),
                             std::move(pictures), focus);
    return 1;
}

extern "C" int issngpu_choice_poll(void) {
    return IssnOverlay::PollChoice();
}

extern "C" int issngpu_text_input_poll(char* out, uint64_t size) {
    if (!g_window) return 2;
    std::string text;
    const int state = g_window->PollTextInput(text);
    if (size) {
        const size_t n = std::min<size_t>(text.size(), size - 1);
        std::memcpy(out, text.data(), n);
        out[n] = 0;
    }
    return state;
}

// issn 0.3.6: a file read copied into GPU-tracked memory (src/runtime_file.c bounce_read). The
// game streams its city blocks this way, 10-30 MB at a time; page by page that was one write
// fault per 4 KB page (João's hitch lines 10-29: 4,000-7,600 faults and 50-170 ms in the frames
// that read a block). The whole chunk is now marked CPU-written first, exactly as its faults
// would have done (GuestWriteFault: GPU-written pages are read back, the rest unprotected), then
// copied without faulting. Small reads keep the fault path. ISSN_READ_INVALIDATE=0: off.
// Only file reads (fill -2, src/runtime_file.c): the guest memset / memcpy (src/runtime.c) call
// this too, memset with no source (the first try copied from it: crash at start, 10-29).
extern "C" int issngpu_cpu_write(void* dst, const void* src, int fill, uint64_t size) {
    static const bool enabled = [] {
        const char* v = std::getenv("ISSN_READ_INVALIDATE");
        return !(v && v[0] == '0');
    }();
    if (!enabled || fill != -2 || !src || size < (64u << 10)) {
        return 0;
    }
    auto* rasterizer = Core::Memory::Instance()->GetRasterizer();
    if (!rasterizer) {
        return 0;
    }
    rasterizer->InvalidateMemory(reinterpret_cast<VAddr>(dst), size, VideoCore::InvalidateOrigin::GuestWriteFault);
    std::memcpy(dst, src, size);
    return 1;
}
