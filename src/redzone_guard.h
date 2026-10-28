/* issn: red-zone guards for guest leaf functions on Windows.
 *
 * The PS4's x86-64 ABI (FreeBSD SysV) lets a leaf function keep up to 128 bytes of locals below
 * rsp, the red zone. Linux signal delivery skips it; Windows does not: when guest code faults
 * on a write-tracked GPU page, the kernel builds the exception frame right below rsp and the
 * locals there are overwritten before the port's handler resumes the code. A leaf that keeps a
 * pointer in its red zone then reads garbage (tester 10-24, i5-11400 + RTX 3060 Ti: inFAMOUS
 * Second Son 1.07's text and sprite quad builder at 0x1cf4e0 reloaded rsi from [rsp-0x20] as 0
 * and wrote to 0x92, at guest offset 0x1cfaaf, right after the intro, every start).
 *
 * A guard moves such a function's red zone above rsp: rsp drops by `shift` after the prologue
 * (a stub around the entry instruction) and rises again before the epilogue (a stub around the
 * first pops); every [rsp - x] access gets disp8 `shift - x` (same length), and the stack
 * argument loads, now `shift` further away, run in stubs with a disp32. The function makes no
 * calls, switches only to targets inside itself and leaves only through that epilogue (checked
 * when the guard was written); the whole function is compared by its FNV-1a hash first, so any
 * other build or version, or bytes already changed by another patch, is left alone. */
#pragma once
#include <stdint.h>
#include <string.h>

typedef struct {
    uint32_t at;          /* the 5-byte instruction replaced by a jmp to its stub */
    uint8_t original[5];  /* its bytes */
    uint8_t code[12];     /* the stub's code before the jmp back */
    uint8_t code_length;
} RedZoneSite;

typedef struct {
    const char *name;
    uint32_t start, length;
    uint64_t fnv;
    uint8_t shift;
    const uint32_t *disp8;   /* offsets of the [rsp - x] displacement bytes */
    unsigned disp8_count;
    const RedZoneSite *sites;
    unsigned site_count;
} RedZoneGuard;

/* inFAMOUS Second Son 1.07 (CUSA00004 / CUSA00223): the text and sprite quad builder. Red zone
 * down to rsp-0x50; stack arguments at [rsp+0x38] (entry) and [rsp+0x40]. */
static const uint32_t issn_rz_quads_disp8[] = {
    0x1cf4ff, 0x1cf706, 0x1cf762, 0x1cf776, 0x1cf799, 0x1cf79f, 0x1cf7ab, 0x1cf7b0, 0x1cf7b9, 0x1cf7dc,
    0x1cf81a, 0x1cf83e, 0x1cf843, 0x1cf84c, 0x1cf892, 0x1cf8de, 0x1cf8e6, 0x1cf8ec, 0x1cf951, 0x1cf989,
    0x1cf9b0, 0x1cfafc, 0x1cfb01, 0x1cfb06, 0x1cfb0c, 0x1cfb1c, 0x1cfb22, 0x1cfb32,
};
static const RedZoneSite issn_rz_quads_sites[] = {
    /* mov r14d,[rsp+0x38]  ->  mov r14d,[rsp+0x38]; lea rsp,[rsp-0x58] */
    {0x1cf4ea, {0x44, 0x8b, 0x74, 0x24, 0x38}, {0x44, 0x8b, 0x74, 0x24, 0x38, 0x48, 0x8d, 0x64, 0x24, 0xa8}, 10},
    /* mov rbp,[rsp+0x40]   ->  mov rbp,[rsp+0x98] */
    {0x1cf8cf, {0x48, 0x8b, 0x6c, 0x24, 0x40}, {0x48, 0x8b, 0xac, 0x24, 0x98, 0x00, 0x00, 0x00}, 8},
    /* pop rbx; pop r12; pop r13  ->  lea rsp,[rsp+0x58]; pop rbx; pop r12; pop r13 */
    {0x1cfb43, {0x5b, 0x41, 0x5c, 0x41, 0x5d}, {0x48, 0x8d, 0x64, 0x24, 0x58, 0x5b, 0x41, 0x5c, 0x41, 0x5d}, 10},
};
static const RedZoneGuard issn_redzone_guards[] = {
    {"text and sprite quads", 0x1cf4e0, 0x66e, 0xb4740db6d90c0065ull, 0x58, issn_rz_quads_disp8,
     sizeof issn_rz_quads_disp8 / sizeof *issn_rz_quads_disp8, issn_rz_quads_sites,
     sizeof issn_rz_quads_sites / sizeof *issn_rz_quads_sites},
};

static inline uint64_t issn_rz_fnv(const unsigned char *p, uint32_t n) {
    uint64_t h = 0xcbf29ce484222325ull;
    for (uint32_t i = 0; i < n; ++i) h = (h ^ p[i]) * 0x100000001b3ull;
    return h;
}

enum { ISSN_RZ_STUB = 32 }; /* bytes of stub space per site */

/* Checks `g` against `image` and, when it matches, writes its stubs into `stubs` (site_count *
 * ISSN_RZ_STUB bytes) and patches the function. Returns 1 when applied, 0 when the function
 * differs, -1 when a stub is out of rel32 range (nothing changed). */
static inline int issn_redzone_guard_apply(const RedZoneGuard *g, unsigned char *image, unsigned char *stubs) {
    if (issn_rz_fnv(image + g->start, g->length) != g->fnv) return 0;
    for (unsigned i = 0; i < g->site_count; ++i) {
        const RedZoneSite *s = &g->sites[i];
        const int64_t to = (int64_t)(uintptr_t)(stubs + i * ISSN_RZ_STUB) - (int64_t)(uintptr_t)(image + s->at + 5);
        const int64_t back = (int64_t)(uintptr_t)(image + s->at + 5) -
                             (int64_t)(uintptr_t)(stubs + i * ISSN_RZ_STUB + s->code_length + 5);
        if (to < INT32_MIN || to > INT32_MAX || back < INT32_MIN || back > INT32_MAX) return -1;
        if (memcmp(image + s->at, s->original, 5) != 0) return 0;
    }
    for (unsigned i = 0; i < g->disp8_count; ++i) {
        const int8_t x = (int8_t)image[g->disp8[i]];
        if (x >= 0 || x + g->shift > 127) return 0;
    }
    for (unsigned i = 0; i < g->site_count; ++i) {
        const RedZoneSite *s = &g->sites[i];
        unsigned char *stub = stubs + i * ISSN_RZ_STUB;
        memcpy(stub, s->code, s->code_length);
        const int32_t back = (int32_t)((image + s->at + 5) - (stub + s->code_length + 5));
        stub[s->code_length] = 0xe9;
        memcpy(stub + s->code_length + 1, &back, 4);
    }
    for (unsigned i = 0; i < g->disp8_count; ++i) image[g->disp8[i]] = (unsigned char)((int8_t)image[g->disp8[i]] + g->shift);
    for (unsigned i = 0; i < g->site_count; ++i) {
        const RedZoneSite *s = &g->sites[i];
        const int32_t to = (int32_t)((stubs + i * ISSN_RZ_STUB) - (image + s->at + 5));
        image[s->at] = 0xe9;
        memcpy(image + s->at + 1, &to, 4);
    }
    return 1;
}
