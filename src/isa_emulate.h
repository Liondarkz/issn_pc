/* Emulation of the guest's MOVBE, BMI1, LZCNT and F16C instructions for hosts that lack them (Sandy
 * and Ivy Bridge, #33, #66; F16C: Sandy Bridge). Each G3ISA1 record (scripts/isa_rewrite.py) describes one instruction
 * site; a stub calls isa_emulate with the guest's registers. Built with -mgeneral-regs-only and
 * no libc: it runs inside guest code and must leave the XMM/YMM registers untouched. */
#ifndef ISSN_ISA_EMULATE_H
#define ISSN_ISA_EMULATE_H
#include <stdint.h>
#ifndef ABI               /* same as runtime.h, which needs sigjmp_buf (_GNU_SOURCE) */
#define ABI __attribute__((sysv_abi))
#endif

/* ISA_EXTRQ (SSE4A, register form): its stub is generated code that works on the XMM registers
 * (isa_rewrite.c emit_extrq); isa_emulate never sees it. */
enum { ISA_MOVBE_LOAD = 1, ISA_MOVBE_STORE, ISA_LZCNT, ISA_TZCNT, ISA_ANDN, ISA_BEXTR, ISA_BLSI, ISA_BLSMSK, ISA_BLSR, ISA_VCVTPH2PS, ISA_VCVTPS2PH, ISA_EXTRQ };
enum { ISA_MODE_JMP = 0, ISA_MODE_JMP_RELOCATE, ISA_MODE_INT3, ISA_MODE_UNSUPPORTED };
enum { ISA_NO_REG = 0xff, ISA_MEM_NONE = 0, ISA_MEM_DST = 1, ISA_MEM_SRC = 2, ISA_MEM_RIP_RELATIVE = 1 };
typedef struct {                       /* one G3ISA1 record (spec 4.4), as stored in the file */
    uint64_t offset;
    uint8_t length, kind, width, mode, dst, src1, src2, mem_slot, base, index, scale, mem_flags;
    uint8_t reserved[4];
    int64_t disp;
    uint8_t original[15];
    uint8_t relocated_length;
    uint8_t relocated[15];
    uint8_t reserved2;
} IsaSite;
_Static_assert(sizeof(IsaSite) == 64, "G3ISA1 record");
typedef struct { uint64_t rflags; uint64_t gpr[16]; } IsaRegs;   /* gpr: x86 numbering; gpr[4] = guest rsp */
enum { ISA_CF = 1u << 0, ISA_ZF = 1u << 6, ISA_SF = 1u << 7, ISA_OF = 1u << 11 };
/* Memory operands with mem_flags & ISA_MEM_RIP_RELATIVE address image + disp. */
void isa_emulate_set_image(unsigned char *image);
/* Executes the site's instruction on regs (spec 5.6). */
void ABI isa_emulate(const IsaSite *site, IsaRegs *regs);
/* F16C (vcvtph2ps / vcvtps2ph; width = the vector's bytes, 16 for xmm or 32 for ymm): the stub stores the source vector register in
 * vec (32 bytes) before the call and loads the destination register from it afterwards, with a
 * VEX load that zeroes the bits above it. A memory operand is read or written here. vcvtps2ph
 * rounds by site->src2 (the immediate's low 3 bits: bit 2 = MXCSR's mode) and honours MXCSR.DAZ. */
void ABI isa_emulate_f16c(const IsaSite *site, IsaRegs *regs, unsigned char *vec);
/* The conversions themselves (rc: 0 nearest even, 1 down, 2 up, 3 toward zero). */
uint32_t isa_half_to_float(uint16_t half);
uint16_t isa_float_to_half(uint32_t value, unsigned rc, int daz);
/* Flags the instruction defines (the test compares only these). */
uint64_t isa_defined_flags(uint8_t kind);

#endif
