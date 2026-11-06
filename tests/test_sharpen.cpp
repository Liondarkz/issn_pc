// issn 11-05: who sharpens and with what (gpu issn_sharpen.h).
#include <cassert>
#include <cstdio>
#define ISSN_SHARPEN_LOGIC_ONLY 1
#include "video_core/renderer_vulkan/issn_sharpen.h"

namespace S = Vulkan::Sharpen;

int main() {
    // Strength and RCAS attenuation: 100 % = 0 stops (FsrRcasCon: exp2(-0) = 1), 0 % = 2 stops.
    assert(S::ClampStrength(-5) == 0 && S::ClampStrength(150) == 100 && S::ClampStrength(40) == 40);
    assert(S::Attenuation(100) == 0.0f && S::Attenuation(0) == 2.0f && S::Attenuation(50) == 1.0f);
    assert(S::RcasConstants(0.0f)[0] == 0x3f800000u);  // 1.0f
    assert(S::RcasConstants(2.0f)[0] == 0x3e800000u);  // 0.25f
    assert(S::RcasConstants(1.0f)[1] == 0 && S::RcasConstants(1.0f)[3] == 0);

    // Off by default: nobody sharpens.
    assert(S::Strength() == 0);
    assert(S::OwnerFor(0, 3, false, true) == S::Owner::None);
    // The game's own AA (0), Off (2), DLSS (1): the port's pass.
    assert(S::OwnerFor(40, 0, false, false) == S::Owner::Port);
    assert(S::OwnerFor(40, 2, false, true) == S::Owner::Port);
    assert(S::OwnerFor(40, 1, true, true) == S::Owner::Port);
    // FSR 4 sharpens itself; a frame it skipped (a plain copy) gets the port's.
    assert(S::OwnerFor(40, 3, false, true) == S::Owner::Fsr4);
    assert(S::OwnerFor(40, 3, false, false) == S::Owner::Port);
    // XeSS: the port's, unless OptiScaler takes its input.
    assert(S::OwnerFor(40, 4, false, true) == S::Owner::Port);
    assert(S::OwnerFor(40, 4, true, true) == S::Owner::OptiScaler);
    assert(S::OwnerFor(40, 4, true, false) == S::Owner::Port);
    std::puts("PASS: sharpening");
}
