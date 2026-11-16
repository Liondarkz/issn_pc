// SPDX-FileCopyrightText: Copyright 2024 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#version 450

layout (location = 0) in vec2 uv;
layout (location = 0) out vec4 color;

layout (binding = 0) uniform sampler2D texSampler;

layout (push_constant) uniform settings {
    float gamma;
    bool hdr;
    // issn 11-16: HDR display mapping (renderer_vulkan/issn_hdr.h)
    uint hdr_map;
    float hdr_peak_nits;     // the display's peak
    float hdr_paper_scale;   // the player's paper white / the game's
    float hdr_src_peak_nits; // the game's peak after that scale
    float hdr_saturation;
} pp;

const float cutoff = 0.0031308, a = 1.055, b = 0.055, d = 12.92;
vec3 gamma(vec3 rgb) {
    return mix(
        a * pow(rgb, vec3(1.0 / (2.4 + 1.0 - pp.gamma))) - b,
        d * rgb / pp.gamma,
        lessThan(rgb, vec3(cutoff))
    );
}

// SMPTE ST 2084 (PQ), nits <-> code value.
const float m1 = 2610.0 / 16384.0, m2 = 2523.0 / 4096.0 * 128.0;
const float c1 = 3424.0 / 4096.0, c2 = 2413.0 / 4096.0 * 32.0, c3 = 2392.0 / 4096.0 * 32.0;
vec3 PqToNits(vec3 e) {
    const vec3 p = pow(clamp(e, 0.0, 1.0), vec3(1.0 / m2));
    return 10000.0 * pow(max(p - c1, 0.0) / (c2 - c3 * p), vec3(1.0 / m1));
}
float NitsToPq(float n) {
    const float y = pow(clamp(n / 10000.0, 0.0, 1.0), m1);
    return pow((c1 + c2 * y) / (1.0 + c3 * y), m2);
}
vec3 NitsToPq(vec3 n) {
    const vec3 y = pow(clamp(n / 10000.0, 0.0, 1.0), vec3(m1));
    return pow((c1 + c2 * y) / (1.0 + c3 * y), vec3(m2));
}

// ITU-R BT.2390 EETF: a PQ value of a source mastered to src_max nits, rolled off to dst_max.
float Eetf(float e, float src_max_pq, float dst_max_pq) {
    const float e1 = e / src_max_pq;
    const float max_lum = dst_max_pq / src_max_pq;
    const float ks = 1.5 * max_lum - 0.5;
    if (e1 <= ks || ks >= 1.0) {
        return e;
    }
    const float t = (e1 - ks) / (1.0 - ks);
    const float t2 = t * t, t3 = t2 * t;
    const float e2 = (2.0 * t3 - 3.0 * t2 + 1.0) * ks + (t3 - 2.0 * t2 + t) * (1.0 - ks) +
                     (-2.0 * t3 + 3.0 * t2) * max_lum;
    return min(e2, max_lum) * src_max_pq;
}

vec3 MapHdr(vec3 pq) {
    vec3 nits = PqToNits(pq) * pp.hdr_paper_scale;
    // Saturation around BT.2020 luminance.
    const float y = dot(nits, vec3(0.2627, 0.6780, 0.0593));
    nits = max(vec3(y) + (nits - vec3(y)) * pp.hdr_saturation, vec3(0.0));
    // Roll the brightest channel off to the display's peak and scale all three alike (hue kept).
    const float m = max(max(nits.r, nits.g), nits.b);
    if (m > 0.0) {
        const float src = max(pp.hdr_src_peak_nits, pp.hdr_peak_nits);
        const float mapped =
            Eetf(NitsToPq(m), NitsToPq(src), NitsToPq(pp.hdr_peak_nits));
        const float m_out = PqToNits(vec3(mapped)).x;
        nits *= m_out / m;
    }
    return NitsToPq(nits);
}

void main() {
    vec4 color_linear = texture(texSampler, uv);
    if (pp.hdr) {
        color = pp.hdr_map != 0u ? vec4(MapHdr(color_linear.rgb), color_linear.a) : color_linear;
    } else {
        color = vec4(gamma(color_linear.rgb), color_linear.a);
    }
}
