/* isa_rewrite (src/isa_rewrite.c) + isa_emulate_f16c: F16C sites (Sandy Bridge lacks F16C) patched
 * in an RWX region and run for real through their stubs. Every case loads all sixteen ymm registers
 * and a memory buffer, runs one site and compares the whole register file, the memory, the flags and
 * the general registers against a C model of the instruction, and, on a host that has F16C, against
 * an unpatched copy of the same code. */
#define _GNU_SOURCE
#include <assert.h>
#include <cpuid.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#ifdef _WIN32
#include <windows.h>
#else
#include <sys/mman.h>
#endif
#include "isa_rewrite.h"

enum { PAGE = 4096, SITES = 6, STRIDE = 0x40 };
typedef struct { const char *name; uint8_t code[8]; uint8_t length, kind, width, dst, src, imm, mem_slot; } Case;
static const Case CASES[SITES] = {
    {"vcvtps2ph xmm1, xmm1, 8", {0xc4, 0xe3, 0x79, 0x1d, 0xc9, 0x08}, 6, ISA_VCVTPS2PH, 16, 1, 1, 0, ISA_MEM_NONE},
    {"vcvtph2ps xmm0, [rdi]", {0xc4, 0xe2, 0x79, 0x13, 0x07}, 5, ISA_VCVTPH2PS, 16, 0, ISA_NO_REG, ISA_NO_REG, ISA_MEM_SRC},
    {"vcvtph2ps xmm0, xmm0", {0xc4, 0xe2, 0x79, 0x13, 0xc0}, 5, ISA_VCVTPH2PS, 16, 0, 0, ISA_NO_REG, ISA_MEM_NONE},
    {"vcvtph2ps ymm9, xmm10", {0xc4, 0x42, 0x7d, 0x13, 0xca}, 5, ISA_VCVTPH2PS, 32, 9, 10, ISA_NO_REG, ISA_MEM_NONE},
    {"vcvtps2ph [rdi], ymm9, 4", {0xc4, 0x63, 0x7d, 0x1d, 0x0f, 0x04}, 6, ISA_VCVTPS2PH, 32, ISA_NO_REG, 9, 4, ISA_MEM_DST},
    {"vcvtps2ph xmm12, ymm3, 3", {0xc4, 0xc3, 0x7d, 0x1d, 0xdc, 0x03}, 6, ISA_VCVTPS2PH, 32, 12, 3, 3, ISA_MEM_NONE},
};

typedef struct {
    uint8_t ymm[16][32];
    uint8_t mem[32];
    uint64_t gpr[6];   /* rcx, rdx, rsi, r8, r11, flags after the call */
} State;

/* Runs fn with every ymm loaded from s->ymm and rdi = s->mem; stores them back. */
static void run(void *fn, State *s) {
    uint64_t rcx = 0x1111111111111111ull, rdx = 0x2222222222222222ull, rsi = 0x3333333333333333ull;
    register uint64_t r8 __asm__("r8") = 0x8888888888888888ull;
    register uint64_t r11 __asm__("r11") = 0xbbbbbbbbbbbbbbbbull;
    uint64_t flags;
    __asm__ volatile(
        "vmovdqu 0x000(%[y]), %%ymm0\n\tvmovdqu 0x020(%[y]), %%ymm1\n\tvmovdqu 0x040(%[y]), %%ymm2\n\t"
        "vmovdqu 0x060(%[y]), %%ymm3\n\tvmovdqu 0x080(%[y]), %%ymm4\n\tvmovdqu 0x0a0(%[y]), %%ymm5\n\t"
        "vmovdqu 0x0c0(%[y]), %%ymm6\n\tvmovdqu 0x0e0(%[y]), %%ymm7\n\tvmovdqu 0x100(%[y]), %%ymm8\n\t"
        "vmovdqu 0x120(%[y]), %%ymm9\n\tvmovdqu 0x140(%[y]), %%ymm10\n\tvmovdqu 0x160(%[y]), %%ymm11\n\t"
        "vmovdqu 0x180(%[y]), %%ymm12\n\tvmovdqu 0x1a0(%[y]), %%ymm13\n\tvmovdqu 0x1c0(%[y]), %%ymm14\n\t"
        "vmovdqu 0x1e0(%[y]), %%ymm15\n\t"
        "push %[y]\n\t"
        "lea 0x200(%[y]), %%rdi\n\t"
        "pushq $0x243\n\tpopfq\n\t"   /* CF and ZF set, the other flags clear */
        "call *%[fn]\n\t"
        "pushfq\n\tpopq %[fl]\n\t"
        "pop %[y]\n\t"
        "vmovdqu %%ymm0, 0x000(%[y])\n\tvmovdqu %%ymm1, 0x020(%[y])\n\tvmovdqu %%ymm2, 0x040(%[y])\n\t"
        "vmovdqu %%ymm3, 0x060(%[y])\n\tvmovdqu %%ymm4, 0x080(%[y])\n\tvmovdqu %%ymm5, 0x0a0(%[y])\n\t"
        "vmovdqu %%ymm6, 0x0c0(%[y])\n\tvmovdqu %%ymm7, 0x0e0(%[y])\n\tvmovdqu %%ymm8, 0x100(%[y])\n\t"
        "vmovdqu %%ymm9, 0x120(%[y])\n\tvmovdqu %%ymm10, 0x140(%[y])\n\tvmovdqu %%ymm11, 0x160(%[y])\n\t"
        "vmovdqu %%ymm12, 0x180(%[y])\n\tvmovdqu %%ymm13, 0x1a0(%[y])\n\tvmovdqu %%ymm14, 0x1c0(%[y])\n\t"
        "vmovdqu %%ymm15, 0x1e0(%[y])\n\t"
        "vzeroupper\n\t"
        : [fl] "=&r"(flags), "+c"(rcx), "+d"(rdx), "+S"(rsi), "+r"(r8), "+r"(r11)
        : [y] "b"(s->ymm), [fn] "a"(fn)
        : "rdi", "r9", "r10", "xmm0", "xmm1", "xmm2", "xmm3", "xmm4", "xmm5", "xmm6", "xmm7", "xmm8", "xmm9",
          "xmm10", "xmm11", "xmm12", "xmm13", "xmm14", "xmm15", "memory", "cc");
    s->gpr[0] = rcx, s->gpr[1] = rdx, s->gpr[2] = rsi, s->gpr[3] = r8, s->gpr[4] = r11, s->gpr[5] = flags & 0x8d5;
}

static unsigned get_rc(void) {
    uint32_t mxcsr;
    __asm__ volatile("stmxcsr %0" : "=m"(mxcsr));
    return mxcsr >> 13 & 3;
}

/* The instruction on the state, in C. */
static void model(const Case *c, State *s) {
    const unsigned lanes = c->width == 32 ? 8 : 4;
    if (c->kind == ISA_VCVTPH2PS) {
        uint16_t in[8];
        uint32_t out[8];
        memcpy(in, c->mem_slot == ISA_MEM_SRC ? s->mem : s->ymm[c->src], 2 * lanes);
        for (unsigned i = 0; i < lanes; i++) out[i] = isa_half_to_float(in[i]);
        memset(s->ymm[c->dst], 0, 32);
        memcpy(s->ymm[c->dst], out, 4 * lanes);
        return;
    }
    uint32_t in[8];
    uint16_t out[8];
    const unsigned rc = c->imm & 4 ? get_rc() : c->imm & 3;
    memcpy(in, s->ymm[c->src], 4 * lanes);
    for (unsigned i = 0; i < lanes; i++) out[i] = isa_float_to_half(in[i], rc, 0);
    if (c->mem_slot == ISA_MEM_DST) {
        memcpy(s->mem, out, 2 * lanes);
    } else {
        memset(s->ymm[c->dst], 0, 32);
        memcpy(s->ymm[c->dst], out, 2 * lanes);
    }
}

static uint64_t rng = 0x9e3779b97f4a7c15ull;
static uint32_t next32(void) {
    rng ^= rng << 13, rng ^= rng >> 7, rng ^= rng << 17;
    return (uint32_t)(rng >> 16);
}
/* Floats around the half range, specials and plain random bits. */
static uint32_t pick_float(void) {
    static const uint32_t special[] = {0, 0x80000000u, 0x7f800000u, 0xff800000u, 0x7fc00001u, 0x7f800001u,
                                       0x477fe000u, 0x477ff000u, 0x38800000u, 0x33800000u, 0x33000000u, 0x00000001u};
    const uint32_t r = next32();
    switch (r & 3) {
    case 0: return special[next32() % (sizeof special / sizeof *special)];
    case 1: return (next32() & 0x807fffffu) | (uint32_t)(0x66 + next32() % 0x20) << 23;
    default: return next32();
    }
}

static void fill(State *s) {
    for (int r = 0; r < 16; r++)
        for (int i = 0; i < 32; i += 4) {
            const uint32_t v = pick_float();
            memcpy(&s->ymm[r][i], &v, 4);
        }
    for (int i = 0; i < 32; i++) s->mem[i] = (uint8_t)next32();
}

static void set_rc(unsigned rc) {
    uint32_t mxcsr;
    __asm__ volatile("stmxcsr %0" : "=m"(mxcsr));
    mxcsr = (mxcsr & ~0x6000u) | rc << 13;
    __asm__ volatile("ldmxcsr %0" : : "m"(mxcsr));
}

int main(void) {
#ifdef _WIN32
    unsigned char *region = VirtualAlloc(NULL, 4 * PAGE, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
    assert(region != NULL);
#else
    unsigned char *region = mmap(NULL, 4 * PAGE, PROT_READ | PROT_WRITE | PROT_EXEC, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    assert(region != MAP_FAILED);
#endif
    unsigned char *image = region, *stubs = region + PAGE, *original = region + 3 * PAGE;
    memset(image, 0xcc, PAGE);
    IsaSite sites[SITES];
    for (int i = 0; i < SITES; i++) {
        const Case *c = &CASES[i];
        memcpy(image + i * STRIDE, c->code, c->length);
        image[i * STRIDE + c->length] = 0xc3;   /* ret */
        IsaSite *s = &sites[i];
        memset(s, 0, sizeof *s);
        s->offset = (uint64_t)i * STRIDE;
        s->length = c->length;
        s->kind = c->kind;
        s->width = c->width;
        s->mode = ISA_MODE_JMP;
        s->dst = c->dst;
        s->src1 = c->src;
        s->src2 = c->imm;
        s->mem_slot = c->mem_slot;
        s->base = c->mem_slot ? 7 : ISA_NO_REG;   /* [rdi] */
        s->index = ISA_NO_REG;
        s->scale = c->mem_slot ? 1 : 0;
        memcpy(s->original, c->code, c->length);
    }
    memcpy(original, image, PAGE);
    IsaTable t = {0};
    t.sites = sites;
    t.hits = calloc(SITES, sizeof *t.hits);
    t.count = SITES;
    t.image_size = PAGE;
    IsaReport r = isa_rewrite_apply(&t, image, stubs, CPU_SET_F16C);
    assert(r.rewritten == SITES && r.jmp == SITES && r.skipped == 0 && r.unsupported_needed == 0);
    for (int i = 0; i < SITES; i++) assert(image[i * STRIDE] == 0xe9);

    unsigned a, b, c, d;
    const int hardware = __get_cpuid(1, &a, &b, &c, &d) && (c >> 29 & 1);
    unsigned cases = 0;
    for (unsigned round = 0; round < 4000; round++) {
        set_rc(round & 3);
        for (int i = 0; i < SITES; i++) {
            State in, patched, expected;
            fill(&in);
            patched = expected = in;
            run(image + i * STRIDE, &patched);
            model(&CASES[i], &expected);
            static const uint64_t gpr[5] = {0x1111111111111111ull, 0x2222222222222222ull, 0x3333333333333333ull,
                                            0x8888888888888888ull, 0xbbbbbbbbbbbbbbbbull};
            memcpy(expected.gpr, gpr, sizeof gpr);
            expected.gpr[5] = 0x41;   /* CF and ZF, as set before the call */
            if (memcmp(&patched, &expected, sizeof patched)) {
                fprintf(stderr, "%s: stub differs from the model (round %u)\n", CASES[i].name, round);
                return 1;
            }
            if (hardware) {
                State real = in;
                run(original + i * STRIDE, &real);
                if (memcmp(&patched, &real, sizeof patched)) {
                    fprintf(stderr, "%s: stub differs from the CPU (round %u)\n", CASES[i].name, round);
                    return 1;
                }
            }
            cases++;
        }
    }
    set_rc(0);
    for (int i = 0; i < SITES; i++) assert(t.hits[i] == 4000);
    printf("isa f16c test: %u cases on %d sites passed (%s)\n", cases, SITES,
           hardware ? "model and CPU" : "model only: this CPU has no F16C");
    return 0;
}
