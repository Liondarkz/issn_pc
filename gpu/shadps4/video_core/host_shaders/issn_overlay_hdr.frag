// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

// The overlay's fragment shader on an HDR10 swapchain (issn_overlay.cpp). The CPU has already
// turned each vertex colour into BT.2020 PQ at the paper white; textures (the loading-screen art,
// the trophy icon, save pictures) are still sRGB. So the tint is decoded to nits, the texel goes
// sRGB -> linear -> BT.2020, the two multiply, and the result is encoded PQ. For the font atlas
// (white texels) this gives back the vertex colour itself, as the SDR shader's Color * texel does.

#version 450 core

layout(location = 0) out vec4 fColor;
layout(set = 0, binding = 0) uniform texture2D _Texture;
layout(set = 1, binding = 0) uniform sampler _Sampler;
layout(location = 0) in struct {
    vec4 Color;
    vec2 UV;
} In;

const float PQ_M1 = 0.1593017578125;
const float PQ_M2 = 78.84375;
const float PQ_C1 = 0.8359375;
const float PQ_C2 = 18.8515625;
const float PQ_C3 = 18.6875;

vec3 PqToNits(vec3 v) {
    vec3 p = pow(clamp(v, 0.0, 1.0), vec3(1.0 / PQ_M2));
    return 10000.0 * pow(max(p - PQ_C1, 0.0) / (PQ_C2 - PQ_C3 * p), vec3(1.0 / PQ_M1));
}

vec3 NitsToPq(vec3 nits) {
    vec3 y = pow(clamp(nits / 10000.0, 0.0, 1.0), vec3(PQ_M1));
    return pow((PQ_C1 + PQ_C2 * y) / (1.0 + PQ_C3 * y), vec3(PQ_M2));
}

vec3 SrgbToLinear(vec3 c) {
    return mix(c / 12.92, pow((c + 0.055) / 1.055, vec3(2.4)), greaterThan(c, vec3(0.04045)));
}

// BT.709 -> BT.2020 (column-major: each vec3 is a column); rows sum to 1, so white stays white.
const mat3 BT709_TO_BT2020 = mat3(0.6274, 0.0691, 0.0164,
                                  0.3293, 0.9195, 0.0880,
                                  0.0433, 0.0114, 0.8956);

void main() {
    vec4 texel = texture(sampler2D(_Texture, _Sampler), In.UV.st);
    vec3 tint = PqToNits(In.Color.rgb);
    vec3 tex = max(BT709_TO_BT2020 * SrgbToLinear(texel.rgb), 0.0);
    fColor = vec4(NitsToPq(tint * tex), In.Color.a * texel.a);
}
