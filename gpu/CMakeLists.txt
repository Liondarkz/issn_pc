# issn GPU library: shadPS4 video core + shader recompiler + Gnm/VideoOut
# drivers, adapted to the native_probe runtime through shims in shim/.
cmake_minimum_required(VERSION 3.24)
project(issngpu LANGUAGES C CXX)
set(CMAKE_CXX_STANDARD 23)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)
if (NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE RelWithDebInfo)
endif()
# Frame pointers keep the loader's thread dumps (watchdog, SIGUSR2 sampling) walkable.
add_compile_options(-fno-omit-frame-pointer)

# port: link-time optimization (ISSN_LTO, default on) and profile-guided optimization
# (ISSN_PGO: off, generate = instrumented build writing into ISSN_PGO_DIR, use = build with that
# profile). Covers issngpu and the static libraries linked into it. No -march: the same build
# runs on the Steam Deck.
option(ISSN_LTO "Link-time optimization of issngpu" ON)
set(ISSN_PGO "off" CACHE STRING "Profile-guided optimization: off, generate, use")
set(ISSN_PGO_DIR "${CMAKE_CURRENT_SOURCE_DIR}/../pgo" CACHE PATH "Profile directory")
if (ISSN_LTO)
    include(CheckIPOSupported)
    check_ipo_supported(RESULT issn_ipo_ok OUTPUT issn_ipo_error)
    if (issn_ipo_ok)
        set(CMAKE_INTERPROCEDURAL_OPTIMIZATION ON)
    else()
        message(WARNING "LTO unavailable: ${issn_ipo_error}")
    endif()
endif()
if (ISSN_PGO STREQUAL "generate")
    # Atomic counters: the GPU, recording, preparation and copy threads run the same code.
    add_compile_options(-fprofile-generate=${ISSN_PGO_DIR} -fprofile-update=atomic)
    add_link_options(-fprofile-generate=${ISSN_PGO_DIR})
    add_compile_definitions(ISSN_PGO_GENERATE)
elseif (ISSN_PGO STREQUAL "use")
    # Partial training keeps unprofiled code optimized normally; counters from concurrent
    # threads can be slightly inconsistent (correction). Functions changed since the profile
    # (or built differently for it, issngpu_init) are compiled without it.
    add_compile_options(-fprofile-use=${ISSN_PGO_DIR} -fprofile-partial-training
                        -fprofile-correction -Wno-missing-profile -Wno-coverage-mismatch)
endif()

find_package(Vulkan REQUIRED)
find_package(fmt REQUIRED)
find_package(Boost REQUIRED)
# issn (Windows): MSYS2 has no magic_enum/miniz packages; the submodules are used instead.
find_package(magic_enum QUIET)
if (NOT magic_enum_FOUND)
    add_library(magic_enum INTERFACE)
    add_library(magic_enum::magic_enum ALIAS magic_enum)
    target_include_directories(magic_enum INTERFACE third_party/magic_enum/include
                                                    third_party/magic_enum/include/magic_enum)
endif()
find_package(tsl-robin-map REQUIRED)
find_package(VulkanMemoryAllocator CONFIG QUIET)
if (NOT VulkanMemoryAllocator_FOUND)
    # MSYS2 ships the header (include/vma/vk_mem_alloc.h) without a CMake package.
    find_path(VMA_INCLUDE_DIR vk_mem_alloc.h PATH_SUFFIXES vma REQUIRED)
    add_library(VulkanMemoryAllocator INTERFACE)
    add_library(GPUOpen::VulkanMemoryAllocator ALIAS VulkanMemoryAllocator)
    target_include_directories(VulkanMemoryAllocator INTERFACE ${VMA_INCLUDE_DIR})
endif()
find_package(PkgConfig REQUIRED)
pkg_check_modules(XXHASH REQUIRED IMPORTED_TARGET libxxhash)
pkg_check_modules(SDL3 REQUIRED IMPORTED_TARGET sdl3)
# macOS presents through MoltenVK's Metal surface (SDL), with no X11.
if (NOT WIN32 AND NOT APPLE)
    pkg_check_modules(X11 REQUIRED IMPORTED_TARGET x11)
endif()
find_package(miniz QUIET)
if (NOT miniz_FOUND)
    set(BUILD_EXAMPLES OFF CACHE BOOL "" FORCE)
    set(BUILD_TESTS OFF CACHE BOOL "" FORCE)
    set(BUILD_FUZZERS OFF CACHE BOOL "" FORCE)
    set(BUILD_SHARED_LIBS OFF)
    set(CMAKE_POLICY_VERSION_MINIMUM 3.5)
    add_subdirectory(third_party/miniz EXCLUDE_FROM_ALL)
    unset(CMAKE_POLICY_VERSION_MINIMUM)
    add_library(miniz::miniz ALIAS miniz)
endif()
find_package(Zydis REQUIRED)
find_program(GLSLANG_COMPILER NAMES glslang glslangValidator REQUIRED)

set(SIRIT_USE_SYSTEM_SPIRV_HEADERS OFF CACHE BOOL "" FORCE)
add_subdirectory(third_party/sirit EXCLUDE_FROM_ALL)



# Host shaders -> GLSL source strings compiled at run time by glslang (axylpup's tree, shadPS4 0.17).
add_subdirectory(shadps4/video_core/host_shaders)
set(SHADER_INCLUDE ${HOST_SHADERS_INCLUDE})
find_package(glslang CONFIG REQUIRED)

# issn-axy: FSR 4 Native AA (ISSN_FSR4, issn_fsr4.cpp) through FireBurn/FSR-Vulkan (MIT).
set(FFX_VK_PORTABLE_BUILD_TESTS OFF CACHE BOOL "" FORCE)
set(FFX_VK_PORTABLE_BUILD_PROBE OFF CACHE BOOL "" FORCE)
add_subdirectory(third_party/fsr-vulkan EXCLUDE_FROM_ALL)

file(GLOB_RECURSE VIDEO_CORE CONFIGURE_DEPENDS shadps4/video_core/*.cpp)
file(GLOB_RECURSE RECOMPILER CONFIGURE_DEPENDS shadps4/shader_recompiler/*.cpp)

# port: the shader cache key: a hash of every source that shapes the generated SPIR-V. The
# shader cache directory carries it, so a changed recompiler starts a fresh cache instead of
# reusing shaders translated by the old code (build.sh configures on every build).
file(GLOB_RECURSE ISSN_SHADER_KEY_SOURCES shadps4/shader_recompiler/*.cpp shadps4/shader_recompiler/*.h)
list(APPEND ISSN_SHADER_KEY_SOURCES
    ${CMAKE_CURRENT_SOURCE_DIR}/shadps4/video_core/amdgpu/resource.h
    ${CMAKE_CURRENT_SOURCE_DIR}/shadps4/video_core/renderer_vulkan/vk_pipeline_cache.cpp)
list(SORT ISSN_SHADER_KEY_SOURCES)
set(ISSN_SHADER_KEY_DATA "")
foreach(source ${ISSN_SHADER_KEY_SOURCES})
    file(SHA1 ${source} source_hash)
    string(APPEND ISSN_SHADER_KEY_DATA ${source_hash})
endforeach()
string(SHA1 ISSN_SHADER_KEY "${ISSN_SHADER_KEY_DATA}")
string(SUBSTRING ${ISSN_SHADER_KEY} 0 12 ISSN_SHADER_KEY)
add_compile_definitions(ISSN_SHADER_CACHE_KEY="${ISSN_SHADER_KEY}")
message(STATUS "Shader cache key: ${ISSN_SHADER_KEY}")
list(FILTER VIDEO_CORE EXCLUDE REGEX "renderdoc\\.cpp$")
set(COMMON
    shadps4/common/decoder.cpp shadps4/common/error.cpp
    shadps4/common/io_file.cpp shadps4/common/native_clock.cpp shadps4/common/number_utils.cpp
    shadps4/common/path_util.cpp shadps4/common/rdtsc.cpp shadps4/common/spin_lock.cpp shadps4/common/recursive_lock.cpp
    shadps4/common/string_util.cpp shadps4/common/thread.cpp shadps4/common/signal_context.cpp
    shadps4/common/fault_monitor.cpp shadps4/common/reserved_memory.cpp shadps4/common/stb.cpp
    shim/common/logging/log.cpp)
file(GLOB AVPLAYER CONFIGURE_DEPENDS shadps4/core/libraries/avplayer/*.cpp)
pkg_check_modules(FFMPEG REQUIRED IMPORTED_TARGET libavformat libavcodec libavutil libswscale libswresample)
file(GLOB SHIM_SOURCES CONFIGURE_DEPENDS shim/*.cpp shim/*/*.cpp)
# Windows: a DLL cannot leave the runtime_* symbols for issn-probe.exe to resolve, so the
# library is static there and linked into the executable.
if (WIN32)
    set(ISSNGPU_KIND STATIC)
else()
    set(ISSNGPU_KIND SHARED)
endif()
add_library(issngpu ${ISSNGPU_KIND} ${VIDEO_CORE} ${RECOMPILER} ${COMMON} ${SHIM_SOURCES}
    shadps4/core/libraries/gnmdriver/gnmdriver.cpp
    shadps4/core/libraries/videoout/driver.cpp shadps4/core/libraries/videoout/video_out.cpp
    shadps4/core/libraries/kernel/equeue.cpp ${AVPLAYER}
    shadps4/core/libraries/videodec/video_utils.cpp)
add_dependencies(issngpu host_shaders)
# In-game settings menu: Dear ImGui (third_party/imgui, MIT) with its Vulkan backend, and an
# embedded Cyrillic font (third_party/fonts).
target_sources(issngpu PRIVATE third_party/imgui/imgui.cpp third_party/imgui/imgui_draw.cpp
    third_party/imgui/imgui_tables.cpp third_party/imgui/imgui_widgets.cpp
    third_party/imgui/backends/imgui_impl_vulkan.cpp)
target_include_directories(issngpu PRIVATE third_party/imgui third_party/imgui/backends)
target_compile_definitions(issngpu PRIVATE IMGUI_IMPL_VULKAN_NO_PROTOTYPES
    ISSN_FONT_PATH="${CMAKE_CURRENT_SOURCE_DIR}/third_party/fonts/DejaVuSans.ttf")
set_source_files_properties(shim/issn_overlay.cpp PROPERTIES
    OBJECT_DEPENDS ${CMAKE_CURRENT_SOURCE_DIR}/third_party/fonts/DejaVuSans.ttf)
target_include_directories(issngpu PRIVATE shim shadps4 ${SHADER_INCLUDE} third_party/gcn/include third_party/half/include
    third_party/streamline-sdk/include third_party/stb)
target_compile_definitions(issngpu PRIVATE BOOST_ASIO_STANDALONE NDEBUG)
target_compile_options(issngpu PRIVATE -Wno-deprecated-declarations -Wno-missing-field-initializers)
# Runtime symbols (runtime_memory_*, runtime_process_*) resolve against issn-probe.
if (APPLE)
    target_link_options(issngpu PRIVATE -Wl,-undefined,dynamic_lookup)
elseif (NOT WIN32)
    target_link_options(issngpu PRIVATE -Wl,--allow-shlib-undefined)
endif()
# glslang's own CMake package: SPIRV and the default resource limits are separate targets in
# older releases only.
target_link_libraries(issngpu PRIVATE glslang::glslang ffx-vulkan::fsr4-v07-vulkan)
foreach(glslang_part glslang::SPIRV glslang::glslang-default-resource-limits)
    if (TARGET ${glslang_part})
        target_link_libraries(issngpu PRIVATE ${glslang_part})
    endif()
endforeach()
target_link_libraries(issngpu PUBLIC Vulkan::Headers fmt::fmt Boost::headers magic_enum::magic_enum
    tsl::robin_map GPUOpen::VulkanMemoryAllocator PkgConfig::XXHASH PkgConfig::SDL3 PkgConfig::FFMPEG miniz::miniz Zydis::Zydis sirit)
target_include_directories(issngpu PRIVATE third_party/xbyak)
if (WIN32)
    # Guest code is entered with the System V ABI; NOMINMAX/WIN32_LEAN_AND_MEAN keep
    # <windows.h> out of the way of std::min/max and the Vulkan/SDL headers.
    target_compile_definitions(issngpu PUBLIC NOMINMAX WIN32_LEAN_AND_MEAN)
    target_compile_options(issngpu PRIVATE -include ${CMAKE_CURRENT_SOURCE_DIR}/shim/issn_win_compat.h)
    target_link_libraries(issngpu PUBLIC Vulkan::Vulkan ws2_32 bcrypt ntdll synchronization winmm pdh)
elseif (APPLE)
    target_link_libraries(issngpu PUBLIC Vulkan::Vulkan)
else()
    target_link_libraries(issngpu PUBLIC PkgConfig::X11)
endif()

# issn (Windows): issngpu is a static library there, so the loader and the HLE runtime are
# linked here, where CMake knows its dependencies (build.sh builds them with ninja).
if (WIN32)
    enable_language(RC)
    file(GLOB ISSN_RUNTIME CONFIGURE_DEPENDS ../src/runtime*.c)
    file(GLOB ISSN_ATRAC9 CONFIGURE_DEPENDS ../third_party/LibAtrac9/C/src/*.c)
    add_library(atrac9 STATIC ${ISSN_ATRAC9})
    target_compile_options(atrac9 PRIVATE -w)
    set_target_properties(atrac9 PROPERTIES C_STANDARD 99 INTERPROCEDURAL_OPTIMIZATION OFF)
    add_executable(issn-probe ../src/probe.c ${ISSN_RUNTIME} ../src/compat_win.c ../src/cpu_check.c ../src/vulkan_smoke.c
        ../src/isa_rewrite.c ../src/isa_emulate.c
        ../packaging/windows/issn-probe.rc)
    set_target_properties(issn-probe PROPERTIES C_STANDARD 11 C_EXTENSIONS OFF)
    target_compile_options(issn-probe PRIVATE $<$<COMPILE_LANGUAGE:C>:-Wall -Wextra -Werror>)
    target_include_directories(issn-probe PRIVATE .. ../src)
    target_link_libraries(issn-probe PRIVATE issngpu atrac9 onecore bcrypt ws2_32 -pthread)
    # The PS4 range (64 GiB - 1 TiB) must stay free: no high-entropy ASLR for heaps and stacks.
    # The guest main thread runs on the process stack: 16 MiB, committed (no stack probes).
    target_link_options(issn-probe PRIVATE -Wl,--disable-high-entropy-va "SHELL:-Xlinker --stack -Xlinker 16777216,16777216")
    # port: runtime unit tests (ninja -C out/gpu mp3-test videodec-test services-test).
    add_executable(mp3-test EXCLUDE_FROM_ALL ../tests/test_mp3.c ../src/runtime_mp3.c ../src/compat_win.c)
    add_executable(videodec-test EXCLUDE_FROM_ALL ../tests/test_videodec.c ../src/compat_win.c)
    add_executable(services-test EXCLUDE_FROM_ALL ../tests/test_services.c ../src/compat_win.c)
    foreach(test mp3-test videodec-test services-test)
        set_target_properties(${test} PROPERTIES C_STANDARD 11 C_EXTENSIONS OFF)
        target_compile_options(${test} PRIVATE -Wall -Wextra -Werror)
        target_include_directories(${test} PRIVATE .. ../src)
        target_link_libraries(${test} PRIVATE atrac9 PkgConfig::FFMPEG bcrypt -pthread)
    endforeach()
    add_executable(pad-map-test EXCLUDE_FROM_ALL ../tests/test_pad_map.c ../src/compat_win.c)
    set_target_properties(pad-map-test PROPERTIES C_STANDARD 11 C_EXTENSIONS OFF)
    target_compile_options(pad-map-test PRIVATE -UNDEBUG -Wall -Wextra -Werror)
    target_include_directories(pad-map-test PRIVATE .. ../src)
    target_link_libraries(pad-map-test PRIVATE PkgConfig::SDL3 bcrypt -pthread)
    add_executable(audio-downmix-test EXCLUDE_FROM_ALL ../tests/test_audio_downmix.c ../src/compat_win.c)
    set_target_properties(audio-downmix-test PROPERTIES C_STANDARD 11 C_EXTENSIONS OFF)
    target_compile_options(audio-downmix-test PRIVATE -UNDEBUG -Wall -Wextra -Werror)
    target_include_directories(audio-downmix-test PRIVATE .. ../src)
    target_link_libraries(audio-downmix-test PRIVATE PkgConfig::SDL3 bcrypt -pthread)
    add_executable(cpu-check-test EXCLUDE_FROM_ALL ../tests/test_cpu_check.c)
    set_target_properties(cpu-check-test PROPERTIES C_STANDARD 11 C_EXTENSIONS OFF)
    target_compile_options(cpu-check-test PRIVATE -UNDEBUG -Wall -Wextra -Werror)
    target_include_directories(cpu-check-test PRIVATE .. ../src)
    # isa_emulate runs inside guest code: no XMM/YMM. The test runs the real instructions as its oracle.
    add_executable(isa-emulate-test EXCLUDE_FROM_ALL ../tests/test_isa_emulate.c ../src/isa_emulate.c)
    set_source_files_properties(../src/isa_emulate.c PROPERTIES COMPILE_OPTIONS "-mgeneral-regs-only")
    set_source_files_properties(../tests/test_isa_emulate.c PROPERTIES COMPILE_OPTIONS "-mbmi;-mlzcnt;-mmovbe")
    set_target_properties(isa-emulate-test PROPERTIES C_STANDARD 11 C_EXTENSIONS OFF)
    target_compile_options(isa-emulate-test PRIVATE -UNDEBUG -Wall -Wextra -Werror)
    target_include_directories(isa-emulate-test PRIVATE .. ../src)
    # Stubs and patched sites run for real in an RWX region; the fixture path is the first argument.
    add_executable(isa-stub-test EXCLUDE_FROM_ALL ../tests/test_isa_stub.c ../src/isa_rewrite.c ../src/isa_emulate.c)
    set_source_files_properties(../tests/test_isa_stub.c PROPERTIES COMPILE_OPTIONS "-mbmi;-mlzcnt")
    set_target_properties(isa-stub-test PROPERTIES C_STANDARD 11 C_EXTENSIONS OFF)
    target_compile_options(isa-stub-test PRIVATE -UNDEBUG -Wall -Wextra -Werror)
    target_include_directories(isa-stub-test PRIVATE .. ../src)
    target_link_libraries(isa-stub-test PRIVATE bcrypt -pthread)
    add_executable(tsc-clock-test EXCLUDE_FROM_ALL ../tests/test_tsc_clock.c ../src/compat_win.c)
    set_target_properties(tsc-clock-test PROPERTIES C_STANDARD 11 C_EXTENSIONS OFF)
    target_compile_options(tsc-clock-test PRIVATE -UNDEBUG -Wall -Wextra -Werror)
    target_include_directories(tsc-clock-test PRIVATE .. ../src)
    target_link_libraries(tsc-clock-test PRIVATE bcrypt -pthread)
    add_executable(issn-gpu-capabilities ../tools/gpu_capabilities.c ../packaging/windows/issn-gpu-capabilities.rc)
    target_include_directories(issn-gpu-capabilities PRIVATE shadps4)
    target_link_libraries(issn-gpu-capabilities PRIVATE Vulkan::Vulkan)
    # "Play inFAMOUS Second Son.exe": the game with the launcher's saved settings, no launcher window.
    add_executable(issn-play WIN32 ../packaging/windows/play.c ../packaging/windows/play.rc)
    target_compile_options(issn-play PRIVATE -municode)
    target_link_options(issn-play PRIVATE -municode)
endif()

# Explicit test targets; neither launches the game nor needs a Vulkan device.
# issn-axy: the tests of the old video core (pipeline binaries, FSR, TAA, motion, readback
# queue, ...) went with it; the shim and loader tests stay.
add_executable(png-decode-test EXCLUDE_FROM_ALL ../tests/test_png_decode.cpp shim/issn_png.cpp)
target_compile_options(png-decode-test PRIVATE -UNDEBUG -Wall -Wextra -Werror)
target_link_libraries(png-decode-test PRIVATE PkgConfig::FFMPEG)
add_executable(upscaler-support-test EXCLUDE_FROM_ALL ../tests/test_upscaler_support.cpp
    shim/issn_settings.cpp)
target_include_directories(upscaler-support-test PRIVATE shim)
target_compile_options(upscaler-support-test PRIVATE -UNDEBUG)
add_executable(orb-multiplier-test EXCLUDE_FROM_ALL ../tests/test_orb_multiplier.cpp
    shim/issn_orbs.cpp shim/issn_settings.cpp)
target_include_directories(orb-multiplier-test PRIVATE shim)
target_compile_options(orb-multiplier-test PRIVATE -UNDEBUG)
add_executable(stat-multipliers-test EXCLUDE_FROM_ALL ../tests/test_stat_multipliers.cpp
    shim/issn_settings.cpp)
target_include_directories(stat-multipliers-test PRIVATE shim)
target_compile_options(stat-multipliers-test PRIVATE -UNDEBUG)
add_executable(perf-overlay-test EXCLUDE_FROM_ALL ../tests/test_perf_overlay.cpp
    shim/issn_settings.cpp)
target_include_directories(perf-overlay-test PRIVATE shim)
target_compile_options(perf-overlay-test PRIVATE -UNDEBUG)
add_executable(cheat-hooks-test EXCLUDE_FROM_ALL ../tests/test_cheat_hooks.cpp)
target_compile_options(cheat-hooks-test PRIVATE -UNDEBUG)
add_executable(signatures-test EXCLUDE_FROM_ALL ../tests/test_signatures.cpp)
target_compile_options(signatures-test PRIVATE -UNDEBUG -Wall -Wextra -Werror)
add_executable(graphics-settings-test EXCLUDE_FROM_ALL ../tests/test_graphics_settings.cpp
    shim/issn_settings.cpp)
target_include_directories(graphics-settings-test PRIVATE shim)
target_compile_options(graphics-settings-test PRIVATE -UNDEBUG)
add_executable(actor-hook-test EXCLUDE_FROM_ALL ../tests/test_actor_hook.cpp)
target_include_directories(actor-hook-test PRIVATE shim)
target_compile_options(actor-hook-test PRIVATE -UNDEBUG -Wall -Wextra -Werror)
add_executable(cheat-hook-test ../tests/test_cheat_hook.cpp)
target_compile_options(cheat-hook-test PRIVATE -UNDEBUG -Wall -Wextra -Werror)
