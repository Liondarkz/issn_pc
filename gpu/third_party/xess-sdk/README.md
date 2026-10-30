# XeSS SDK headers

`include/xess/xess.h` and `include/xess/xess_vk.h` from Intel's XeSS SDK
(https://github.com/intel/xess), MIT-licensed (see the SPDX line in each file).

Only the headers are here. The runtime, `libxess.dll`, is Intel's binary under the Intel Simplified
Software License; the port loads it at run time from the `xess` folder next to `issn-probe.exe`
(ISSN_XESS=1, `gpu/shadps4/video_core/renderer_vulkan/issn_xess.cpp`).
