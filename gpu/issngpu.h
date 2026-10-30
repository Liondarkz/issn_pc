/* C interface of the GPU library (gpu/): shadPS4's Liverpool/Vulkan video core,
 * GnmDriver, VideoOut and kernel event queues, adapted to the native loader. */
#ifndef ISSNGPU_H
#define ISSNGPU_H
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
typedef struct {
    const char *title;          /* window title */
    const char *serial;         /* CUSA id, names the pipeline cache */
    const char *user_dir;       /* pipeline cache/logs directory */
    uint32_t sdk_version;       /* from the eboot's procparam */
    uint32_t psf_attributes;    /* param.sfo ATTRIBUTE */
    int32_t width, height;      /* initial window size */
    uint64_t eboot_base;        /* where the eboot's first segment is mapped (axylpup's game patches) */
    const char *game_dir;       /* the /app0 folder (game + update view): the shader seed reads the
                                   game's own shader code from it; NULL: none */
    const char *app_ver;        /* param.sfo APP_VER ("01.07"), logged with the seed */
} IssnGpuConfig;
/* Registers kernel event queues (needed with or without graphics). */
void issngpu_register_kernel(void);
/* Creates window, Vulkan device, presenter and GPU command processor. */
int issngpu_init(const IssnGpuConfig *config);
/* issn-axy: axylpup's Second Son code patches (particle readback one frame later, ...), applied by
 * the loader to the linked image before its pages are protected. */
void issngpu_apply_game_patches(void);
/* issn-axy: called on the thread that enters the eboot (the game's main loop): axylpup's core keys
 * its frame-end work (particle read-ahead) on it. */
void issngpu_mark_game_main_thread(void);
/* Function for an imported NID ("NID#lib#mod"), or 0 when the GPU library does not provide it. */
uintptr_t issngpu_resolve(const char *scoped_nid);
/* Called first by the loader's SIGSEGV handler: 1 when a GPU page-tracking fault was handled. */
int issngpu_handle_fault(void *ucontext, void *address);
/* ISSN_WRITE_LOG=1: prints the logged GPU-side writes to guest memory near the fault. */
void issngpu_dump_guest_writes(void *ucontext);
/* issn: the GPU emulation's state for the watchdog: queue waits, last fences, counters. */
void issngpu_dump_gpu_state(void);
/* issn: a guest memcpy/memset into GPU-modified memory written without a fault or GPU wait
 * (ISSN_WRITE_MERGE=1 with precise readbacks, from HayatoG's iss_pc); 0: write normally. */
/* issn 0.3.6: fill -2 = a file read (src/runtime_file.c): chunks of 64 KB or more are marked
 * CPU-written in one go and copied without a fault per page. */
int issngpu_cpu_write(void *dst, const void *src, int fill, uint64_t size);
/* issn: frames the guest has finished (sceGnmSubmitDone calls), for the hang watchdog. */
uint64_t issngpu_frame_count(void);
/* Keyboard text entry through the game window (IME dialog). begin returns 0 when
 * no window exists; poll returns 0 typing, 1 confirmed, 2 cancelled (UTF-8 text). */
int issngpu_text_input_begin(const char *initial_utf8, const char *prompt_utf8);
int issngpu_text_input_poll(char *out_utf8, uint64_t size);
/* A list for the player to choose from, drawn over the game (save list). images: a PNG path per
 * item drawn beside it (NULL array or entry: none). begin: 0 when no window exists.
 * poll: -1 while open, -2 cancelled, else the chosen index. */
int issngpu_choice_begin(const char *title, const char *const *items, const char *const *images, int count, int focus);
int issngpu_choice_poll(void);
/* 1 while the in-game settings menu is open: the game's pad input is held neutral. */
int issngpu_overlay_captures_input(void);
/* issn 0.3.6: issn.ini key_map (keys changed in the game's menu), "" when none. */
const char *issngpu_saved_key_map(void);
/* Number of symbols registered by the vendored libraries (diagnostics). */
unsigned issngpu_symbol_count(void);
#ifdef __cplusplus
}
#endif
#endif
