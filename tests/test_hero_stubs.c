/* SPDX-License-Identifier: GPL-2.0-or-later */
/* src/dash_hook.h and src/hero_hook.h: the neon, video and damage stubs run on a fake image whose
 * jump targets are small landing functions, with the flag off and on; the missile and Karma Bomb
 * checks run on written bytes (no game code is used). */
#include "dash_hook.h"
#include "hero_hook.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#ifdef _WIN32
#include <windows.h>
#else
#include <sys/mman.h>
#endif

#define IMAGE_SIZE 0x1e00000u
#define STUB_AT 0x100000u

static int failures;
#define CHECK(c)                                                         \
    do {                                                                 \
        if (!(c)) {                                                      \
            fprintf(stderr, "%s:%d: check failed: %s\n", __FILE__, __LINE__, #c); \
            ++failures;                                                  \
        }                                                                \
    } while (0)

static unsigned char *map_rwx(size_t size) {
#ifdef _WIN32
    return VirtualAlloc(NULL, size, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
#else
    void *p = mmap(NULL, size, PROT_READ | PROT_WRITE | PROT_EXEC, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    return p == MAP_FAILED ? NULL : p;
#endif
}

static void put(unsigned char *image, uint32_t at, const unsigned char *code, size_t n) {
    memcpy(image + at, code, n);
}

/* Calls `stub` with rdi set; returns rax at its ret. */
static uint64_t run_rdi(const unsigned char *stub, uint64_t rdi) {
    uint64_t out;
    __asm__ volatile("call *%[fn]"
                     : "=a"(out), "+D"(rdi)
                     : [fn] "r"(stub)
                     : "rcx", "rdx", "rsi", "r8", "r9", "r10", "r11", "xmm0", "memory", "cc");
    return out;
}

/* Calls `stub` with al and xmm0 set; returns rax at the landing function's ret. */
static uint64_t run(const unsigned char *stub, uint64_t rax_in, float xmm0) {
    uint32_t bits;
    memcpy(&bits, &xmm0, 4);
    uint64_t out;
    __asm__ volatile("vmovd %k[bits], %%xmm0\n\t"
                     "call *%[fn]"
                     : "=a"(out)
                     : [bits] "r"(bits), [fn] "r"(stub), "a"(rax_in)
                     : "xmm0", "rcx", "rdx", "rsi", "rdi", "r8", "r9", "r10", "r11", "memory", "cc");
    return out;
}

int main(void) {
    unsigned char *image = map_rwx(IMAGE_SIZE);
    if (!image) {
        fprintf(stderr, "cannot map the fake image\n");
        return 1;
    }
    memset(image, 0xcc, IMAGE_SIZE);
    put(image, ISSN_DASH_NEON_SITE, issn_dash_neon_code, sizeof issn_dash_neon_code);
    put(image, ISSN_DASH_TV_SITE, issn_dash_tv_code, sizeof issn_dash_tv_code);
    const float limit = 1.5f;
    memcpy(image + ISSN_DASH_TV_LIMIT, &limit, 4);
    CHECK(issn_dash_neon_match(image, IMAGE_SIZE));
    CHECK(issn_dash_tv_match(image, IMAGE_SIZE));
    CHECK(!issn_dash_neon_match(image, ISSN_DASH_NEON_SITE + 4));

    /* Landing functions. neon back: rax = rdi + rsi (the check's two arguments). */
    static const unsigned char back[] = {0x48, 0x8d, 0x04, 0x37, 0xc3};          /* lea (%rdi,%rsi),%rax; ret */
    static const unsigned char cont[] = {0x48, 0xc7, 0xc0, 0xff, 0xff, 0xff, 0xff, 0xc3}; /* mov $-1,%rax; ret */
    /* video: rax = al | 0x100 (button check) or al | 0x200 (end set) */
    static const unsigned char held[] = {0x0f, 0xb6, 0xc0, 0x0d, 0x00, 0x01, 0x00, 0x00, 0xc3};
    static const unsigned char set_end[] = {0x0f, 0xb6, 0xc0, 0x0d, 0x00, 0x02, 0x00, 0x00, 0xc3};
    put(image, ISSN_DASH_NEON_BACK, back, sizeof back);
    put(image, ISSN_DASH_NEON_CONTINUE, cont, sizeof cont);
    put(image, ISSN_DASH_TV_HELD, held, sizeof held);
    put(image, ISSN_DASH_TV_SET_END, set_end, sizeof set_end);

    static volatile unsigned char neon_flag = 0, tv_flag = 0;
    unsigned char *neon = image + STUB_AT, *tv = image + STUB_AT + 256;
    const unsigned neon_n = issn_dash_build_neon_stub(neon, image, &neon_flag);
    const unsigned tv_n = issn_dash_build_tv_stub(tv, image, &tv_flag);
    CHECK(neon_n <= 256 && tv_n <= 256);

    const uint64_t check_args = (uint64_t)(uintptr_t)(image + ISSN_DASH_ABILITIES) + 0x59;
    neon_flag = 0;
    CHECK(run(neon, 0x1234, 0.0f) == check_args);          /* off: the game's ability check */
    neon_flag = 1;
    CHECK(run(neon, 0x1234, 0.0f) == UINT64_MAX);          /* on: the run goes on */

    tv_flag = 0;
    CHECK(run(tv, 0x01, 1.0f) == 0x101);  /* held, under 1.5 s: the button check, al kept */
    CHECK(run(tv, 0x01, 1.6f) == 0x201);  /* held, past 1.5 s: end */
    CHECK(run(tv, 0x00, 1.0f) == 0x100);  /* released: the button check (it ends there) */
    tv_flag = 1;
    CHECK(run(tv, 0x01, 1.6f) == 0x101);  /* on: past 1.5 s only the button counts */
    CHECK(run(tv, 0x00, 9.0f) == 0x100);
    CHECK(run(tv, 0x01, 100.0f) == 0x101);

    /* Damage stub: the method's first instructions, then a landing that undoes them. */
    put(image, ISSN_HERO_TAKE_HIT, issn_hero_take_hit_code, sizeof issn_hero_take_hit_code);
    CHECK(issn_hero_take_hit_match(image, IMAGE_SIZE));
    static const unsigned char hit_back[] = {0x41, 0x5c, 0x41, 0x5e, 0x41, 0x5f, 0x5d, /* pop r12,r14,r15,rbp */
                                             0xb8, 0x01, 0, 0, 0, 0xc3};              /* mov $1,%eax; ret */
    put(image, ISSN_HERO_TAKE_HIT + ISSN_HERO_TAKE_HIT_LENGTH, hit_back, sizeof hit_back);
    static unsigned char hero_object[0x100], character_object[0x10], other_object[0x10];
    uint64_t hero_at = (uint64_t)(uintptr_t)hero_object, character_at = (uint64_t)(uintptr_t)character_object;
    memcpy(hero_object + ISSN_HERO_CHARACTER, &character_at, 8);
    static volatile unsigned char health_flag = 0;
    unsigned char *damage = image + STUB_AT + 512;
    CHECK(issn_hero_build_damage_stub(damage, image, &health_flag) <= 256);
    memset(image + ISSN_HERO_GLOBAL, 0, 8);
    CHECK(run_rdi(damage, character_at) == 1);                         /* no hero: the hit */
    memcpy(image + ISSN_HERO_GLOBAL, &hero_at, 8);
    CHECK(run_rdi(damage, character_at) == 1);                         /* flag off: the hit */
    health_flag = 1;
    CHECK(run_rdi(damage, character_at) == character_at);              /* on: returns at once */
    CHECK(run_rdi(damage, (uint64_t)(uintptr_t)other_object) == 1);   /* others still get hit */

    /* Missile and Karma Bomb checks: the getters and setter as the 01.07 eboot has them. */
    CHECK(!issn_hero_heavy_match(image, IMAGE_SIZE));
    unsigned char lea[7] = {0x48, 0x8d, 0x05};
    int32_t disp = (int32_t)(ISSN_HERO_PLAYER - (ISSN_HERO_HEAVY_GETTER + 7));
    memcpy(lea + 3, &disp, 4);
    put(image, ISSN_HERO_HEAVY_GETTER, lea, 7);
    put(image, ISSN_HERO_HEAVY_GETTER + 7, issn_hero_heavy_getter_tail, sizeof issn_hero_heavy_getter_tail);
    put(image, ISSN_HERO_HEAVY_SETTER, issn_hero_heavy_setter_code, sizeof issn_hero_heavy_setter_code);
    CHECK(issn_hero_heavy_match(image, IMAGE_SIZE));
    CHECK(!issn_hero_heavy_match(image, ISSN_HERO_PLAYER));
    disp = (int32_t)(ISSN_HERO_PLAYER - (ISSN_HERO_STREAK_REQUIRED_GETTER + 7));
    memcpy(lea + 3, &disp, 4);
    put(image, ISSN_HERO_STREAK_GETTER, issn_hero_streak_getter_code, sizeof issn_hero_streak_getter_code);
    put(image, ISSN_HERO_STREAK_REQUIRED_GETTER, lea, 7);
    CHECK(!issn_hero_karma_match(image, IMAGE_SIZE));
    put(image, ISSN_HERO_STREAK_REQUIRED_GETTER + 7, issn_hero_streak_required_tail,
        sizeof issn_hero_streak_required_tail);
    CHECK(issn_hero_karma_match(image, IMAGE_SIZE));

    if (failures) {
        fprintf(stderr, "hero-stubs-test: %d failure(s)\n", failures);
        return 1;
    }
    puts("hero-stubs-test: ok");
    return 0;
}
