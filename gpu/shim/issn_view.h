// SPDX-License-Identifier: GPL-2.0-or-later
// port: ranges of the draw distance settings (issn_view.cpp, src/view_hook.h).
#pragma once
#include <algorithm>
#include <cmath>

namespace IssnView {
inline float Clamp(float value, float lo, float hi, float fallback) {
    return std::isfinite(value) ? std::clamp(value, lo, hi) : fallback;
}
/// Camera LOD factor multiplier: every LOD band (and the game's dynamic quality steps) scale with it.
inline float ClampLodScale(float v) { return Clamp(v, 0.25f, 4.0f, 1.0f); }
/// How much the regions' script LOD_ADJUST counts: 1 the game's, 0 ignored.
inline float ClampRegionLod(float v) { return Clamp(v, 0.0f, 1.0f, 1.0f); }
/// FADE_DISTANCE multiplier: the distance over which a region's settings blend in.
inline float ClampRegionFade(float v) { return Clamp(v, 0.25f, 4.0f, 1.0f); }
/// Default near plane of new cameras (game units, the game uses 10).
inline float ClampNearClip(float v) { return Clamp(v, 1.0f, 100.0f, 10.0f); }
/// Default far plane of new cameras (the game uses 1e7, effectively unlimited).
inline float ClampFarClip(float v) { return Clamp(v, 1000.0f, 1.0e7f, 1.0e7f); }
/// One LOD band distance (game units); the game's are 400, 750, 1500, 3500, 6000 and 15000.
inline float ClampBand(float v) { return Clamp(v, 1.0f, 1.0e6f, 400.0f); }
/// Extra texture LOD bias of the game's draws: negative is sharper, positive is blurrier.
inline float ClampMipLodBias(float v) { return Clamp(v, -3.0f, 3.0f, 0.0f); }
/// Field of view factor (src/fov_hook.h): 1 the game's angle.
inline float ClampFov(float v) { return Clamp(v, 0.75f, 1.6f, 1.0f); }
} // namespace IssnView
