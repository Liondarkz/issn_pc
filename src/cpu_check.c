#include "cpu_check.h"
#include <cpuid.h>
#include <stdio.h>

CpuFeatureWords cpu_feature_words(void) {
    CpuFeatureWords words = {0, 0, 0};
    unsigned a = 0, b = 0, c = 0, d = 0;
    if (__get_cpuid(1, &a, &b, &c, &d)) words.leaf1_ecx = c;
    if (__get_cpuid_count(7, 0, &a, &b, &c, &d)) words.leaf7_ebx = b;
    if (__get_cpuid(0x80000001, &a, &b, &c, &d)) words.ext1_ecx = c;
    (void)a; (void)d;
    return words;
}

/* Jaguar's sets beyond the x86-64 baseline. A missing fatal set stops the game; the rewritable
 * ones can be emulated: MOVBE, BMI1 and LZCNT (Intel has them from Haswell on), F16C (from Ivy
 * Bridge on; issn_pc 10-31: 13 sites in inFAMOUS Second Son, a Sandy Bridge player stopped) and SSE4A,
 * which no Intel CPU has (issn_pc 2026-10-24: inFAMOUS Second Son runs EXTRQ, 96 sites; God of War
 * III, where this list comes from, had none, and an i7-10700K closed on its first one). */
enum { LEAF1, LEAF7, EXT1 };
typedef struct { const char *name; int word; uint32_t bit; unsigned set; } CpuSet;
static const CpuSet fatal_sets[] = {
    {"SSE4.2", LEAF1, 1u << 20, 0}, {"POPCNT", LEAF1, 1u << 23, 0}, {"AVX", LEAF1, 1u << 28, 0},
};
static const CpuSet rewritable_sets[] = {
    {"MOVBE", LEAF1, 1u << 22, CPU_SET_MOVBE}, {"F16C", LEAF1, 1u << 29, CPU_SET_F16C},
    {"BMI1", LEAF7, 1u << 3, CPU_SET_BMI1},    {"LZCNT", EXT1, 1u << 5, CPU_SET_LZCNT},
    {"SSE4A", EXT1, 1u << 6, CPU_SET_SSE4A},
};

/* Names the missing sets, comma separated in out; returns how many, and their CPU_SET_* bits in *mask. */
static int missing_sets(CpuFeatureWords words, const CpuSet *sets, size_t count, char *out, size_t size,
                        unsigned *mask) {
    const uint32_t values[] = {words.leaf1_ecx, words.leaf7_ebx, words.ext1_ecx};
    int missing = 0;
    size_t used = 0;
    *mask = 0;
    if (size) out[0] = 0;
    for (size_t i = 0; i < count; ++i) {
        if (values[sets[i].word] & sets[i].bit) continue;
        if (used < size) {
            const int n = snprintf(out + used, size - used, "%s%s", missing ? ", " : "", sets[i].name);
            used = n < 0 ? size : used + (size_t)n;
            if (used >= size) used = size - 1;
        }
        *mask |= sets[i].set;
        ++missing;
    }
    return missing;
}

int cpu_missing_fatal(CpuFeatureWords words, char *out, size_t size) {
    unsigned mask;
    return missing_sets(words, fatal_sets, sizeof fatal_sets / sizeof *fatal_sets, out, size, &mask);
}

unsigned cpu_missing_rewritable(CpuFeatureWords words, char *out, size_t size) {
    unsigned mask;
    missing_sets(words, rewritable_sets, sizeof rewritable_sets / sizeof *rewritable_sets, out, size, &mask);
    return mask;
}
