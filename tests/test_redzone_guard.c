/* src/redzone_guard.h: a leaf function keeping locals in its red zone, with a call in the middle
 * standing in for the Windows exception frame (its return address and the callee's stack land
 * below rsp). Unguarded the locals are lost; guarded they survive and the result is right. */
#define _GNU_SOURCE
#include <assert.h>
#include <stdio.h>
#include <string.h>
#ifdef _WIN32
#include <windows.h>
#else
#include <sys/mman.h>
#endif
#include "redzone_guard.h"

enum { PAGE = 4096, F = 0x100, CALLER = 0x000, CLOBBER = 0x080 };

/* f(rdi=a, rsi=b, r9=clobber; stack: arg1, arg2) = a + b + arg1 + arg2 */
static const unsigned char F_CODE[] = {
    0x53, 0x41, 0x54, 0x41, 0x55,       /* push rbx; push r12; push r13 */
    0x44, 0x8b, 0x44, 0x24, 0x20,       /* mov r8d,[rsp+0x20]      (entry site, F+5) */
    0x48, 0x89, 0x7c, 0x24, 0xf0,       /* mov [rsp-0x10],rdi */
    0x48, 0x89, 0x74, 0x24, 0xf8,       /* mov [rsp-0x8],rsi */
    0x45, 0x89, 0xc4,                   /* mov r12d,r8d */
    0x41, 0xff, 0xd1,                   /* call r9 (the "exception frame") */
    0x48, 0x8b, 0x44, 0x24, 0xf0,       /* mov rax,[rsp-0x10] */
    0x48, 0x03, 0x44, 0x24, 0xf8,       /* add rax,[rsp-0x8] */
    0x4c, 0x01, 0xe0,                   /* add rax,r12 */
    0x4c, 0x8b, 0x6c, 0x24, 0x28,       /* mov r13,[rsp+0x28]      (stack argument site, F+39) */
    0x4c, 0x01, 0xe8,                   /* add rax,r13 */
    0x41, 0x5d, 0x41, 0x5c, 0x5b,       /* pop r13; pop r12; pop rbx (exit site, F+47) */
    0xc3,                               /* ret */
};
/* caller(rdi=a, rsi=b, rdx=arg1, rcx=arg2, r8=f, r9=clobber): pushes the stack arguments */
static const unsigned char CALLER_CODE[] = {
    0x55, 0x48, 0x89, 0xe5,             /* push rbp; mov rbp,rsp */
    0x48, 0x83, 0xe4, 0xf0,             /* and rsp,-16 */
    0x51, 0x52,                         /* push rcx; push rdx */
    0x41, 0xff, 0xd0,                   /* call r8 */
    0x48, 0x89, 0xec, 0x5d, 0xc3,       /* mov rsp,rbp; pop rbp; ret */
};
/* clobber: zeroes 256 bytes below its own stack pointer */
static const unsigned char CLOBBER_CODE[] = {
    0x48, 0x81, 0xec, 0x00, 0x01, 0x00, 0x00, /* sub rsp,0x100 */
    0x48, 0x89, 0xe7, 0x31, 0xc0,             /* mov rdi,rsp; xor eax,eax */
    0xb9, 0x20, 0x00, 0x00, 0x00,             /* mov ecx,32 */
    0xf3, 0x48, 0xab,                         /* rep stosq */
    0x48, 0x81, 0xc4, 0x00, 0x01, 0x00, 0x00, /* add rsp,0x100 */
    0xc3,
};

static unsigned char *region;

static long long run(long long a, long long b, long long arg1, long long arg2) {
    register long long r8 __asm__("r8") = (long long)(region + F);
    register long long r9 __asm__("r9") = (long long)(region + CLOBBER);
    long long result;
    __asm__ volatile("sub $128, %%rsp\n\t"
                     "call *%[caller]\n\t"
                     "add $128, %%rsp\n\t"
                     : "=a"(result), "+D"(a), "+S"(b), "+d"(arg1), "+c"(arg2), "+r"(r8), "+r"(r9)
                     : [caller] "r"(region + CALLER)
                     : "r10", "r11", "memory", "cc");
    return result;
}

int main(void) {
#ifdef _WIN32
    region = VirtualAlloc(NULL, 2 * PAGE, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
    assert(region != NULL);
#else
    region = mmap(NULL, 2 * PAGE, PROT_READ | PROT_WRITE | PROT_EXEC, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    assert(region != MAP_FAILED);
#endif
    memset(region, 0xcc, 2 * PAGE);
    memcpy(region + CALLER, CALLER_CODE, sizeof CALLER_CODE);
    memcpy(region + CLOBBER, CLOBBER_CODE, sizeof CLOBBER_CODE);
    memcpy(region + F, F_CODE, sizeof F_CODE);

    const long long a = 1000, b = 200, arg1 = 30, arg2 = 4;
    const long long broken = run(a, b, arg1, arg2);
    assert(broken != a + b + arg1 + arg2); /* the red zone was overwritten */

    const uint32_t disp8[] = {F + 14, F + 19, F + 30, F + 35};
    const RedZoneSite sites[] = {
        {F + 5, {0x44, 0x8b, 0x44, 0x24, 0x20}, {0x44, 0x8b, 0x44, 0x24, 0x20, 0x48, 0x8d, 0x64, 0x24, 0xe0}, 10},
        {F + 39, {0x4c, 0x8b, 0x6c, 0x24, 0x28}, {0x4c, 0x8b, 0xac, 0x24, 0x48, 0x00, 0x00, 0x00}, 8},
        {F + 47, {0x41, 0x5d, 0x41, 0x5c, 0x5b}, {0x48, 0x8d, 0x64, 0x24, 0x20, 0x41, 0x5d, 0x41, 0x5c, 0x5b}, 10},
    };
    RedZoneGuard g = {"test", F, sizeof F_CODE, issn_rz_fnv(F_CODE, sizeof F_CODE), 0x20,
                      disp8, 4, sites, 3};
    RedZoneGuard wrong = g;
    wrong.fnv ^= 1;
    assert(issn_redzone_guard_apply(&wrong, region, region + PAGE) == 0);
    assert(memcmp(region + F, F_CODE, sizeof F_CODE) == 0); /* another version: untouched */
    assert(issn_redzone_guard_apply(&g, region, region + PAGE) == 1);
    assert(region[F + 14] == 0x10 && region[F + 19] == 0x18 && region[F + 5] == 0xe9);
    for (int i = 0; i < 3; ++i) assert(run(a, b, arg1, arg2) == a + b + arg1 + arg2);

    /* The guard of the game's function: its own numbers agree with each other. */
    const RedZoneGuard *q = &issn_redzone_guards[0];
    assert(q->sites[0].at == q->start + 10 && q->shift >= 0x50 && q->shift % 8 == 0);
    assert(q->sites[1].code[4] == 0x40 + q->shift && q->sites[2].code[4] == q->shift &&
           (uint8_t)q->sites[0].code[9] == (uint8_t)(0x100 - q->shift));
    for (unsigned i = 0; i < q->disp8_count; ++i) assert(q->disp8[i] > q->start && q->disp8[i] < q->start + q->length);
    printf("redzone guard test: unguarded %lld, guarded %lld: passed\n", broken, a + b + arg1 + arg2);
    return 0;
}
