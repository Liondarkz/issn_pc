// issn 11-04: the motion blur / chromatic aberration switches (gpu issn_postfx.h).
#include <cassert>
#include <cstdio>
#include <cstdlib>
#include "video_core/renderer_vulkan/issn_postfx.h"

namespace P = Vulkan::PostFx;

int main() {
    // The hash lists: built-in ones plus ISSN_POSTFX_MB / ISSN_POSTFX_CA (read once, at first use).
#ifdef _WIN32
    _putenv_s("ISSN_POSTFX_MB", "abcd1234, deadbeef,0");
#else
    setenv("ISSN_POSTFX_MB", "abcd1234, deadbeef,0", 1);
#endif
    assert(P::Known(P::MotionBlur) && P::Programs()[P::MotionBlur].count == 2);
    assert(!P::Known(P::Chromatic));
    P::EffectPrograms parsed{};
    P::ParseHashList("x1,2;;3", parsed);
    assert(parsed.count == 3 && parsed.hashes[0] == 1 && parsed.hashes[2] == 3);

    // Everything on: nothing to do on the back.
    assert(!P::Active() && P::Decide(0xdeadbeef) == P::Action::Draw);
    P::g_off[P::MotionBlur] = true;
    assert(P::Active());
    assert(P::Decide(0xdeadbeef) == P::Action::Copy && P::Decide(0xabcd1234) == P::Action::Copy);
    assert(P::Decide(0x1234) == P::Action::Draw && P::Decide(0) == P::Action::Draw);
    P::g_off[P::MotionBlur] = false;

    // The test list: copy by default, drop when asked, back on.
    P::SetTestOff(0x77, true);
    P::SetTestOff(0x77, true); // once only
    assert(P::IsTestOff(0x77) && P::Decide(0x77) == P::Action::Copy);
    P::g_test_mode = 1;
    assert(P::Decide(0x77) == P::Action::Drop);
    P::SetTestOff(0x77, false);
    assert(!P::IsTestOff(0x77) && P::Decide(0x77) == P::Action::Draw && !P::Active());
    for (u64 h = 1; h <= 20; ++h) {
        P::SetTestOff(h, true); // 16 slots: the rest are not kept
    }
    assert(P::IsTestOff(16) && !P::IsTestOff(17));
    std::puts("PASS: post effects");
}
