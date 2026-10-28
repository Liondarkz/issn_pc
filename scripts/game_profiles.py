# SPDX-License-Identifier: GPL-2.0-or-later
"""Settings that a game needs, chosen by the title ID of its param.sfo.

run.py adds them to the environment of the game unless the variable is already set (an explicit
ISSN_... value always wins), and ISSN_GAME_PROFILE=0 leaves every game on the defaults.
"""
from pathlib import Path

# inFAMOUS Second Son (US, EU). From the settings measured by the shadPS4 build made for this game
# (AYOUB1080p, github.com/weshayoub92/shadPS4-perf): compute workgroups wider than a host subgroup
# need barriers where the game relies on GCN waves of 64 running in lockstep.
INFAMOUS_SECOND_SON = ('CUSA00004', 'CUSA00223')
# issn: sha256 of the 01.07 eboot.bin (the same for CUSA00004 and CUSA00223), whatever APP_VER
# the folder's param.sfo says.
INFAMOUS_107_EBOOTS = frozenset({'2d1ca79630d7bbe6fa29575f59aa7996d43071d74ac8e4f7d40137967d9039ab'})

# issn-axy: the GPU core of axylpup's inFAMOUS Second Son build (github.com/axylpup/shadPS4,
# second-son 2212a345 = release v1.0.2), with the switches of its release (shadps4-flags.txt).
# Left out: what lives outside the vendored core (mouse aim, button-icon textures, the pad, save
# dialog, video decoder and kernel mutex parts, its shader-cache name); DLSS is the launcher's
# switch (NVIDIA only). The boot screen is the port's loading screen (presenter).
AXY_RELEASE_FLAGS = {
    'SHADPS4_A2': '1',
    'SHADPS4_A2_INDIRECT': '1',
    'SHADPS4_A2_MAT_MEMO': '1',
    'SHADPS4_A2_PARK': '1',
    'SHADPS4_A2_VBUF2': '1',
    'SHADPS4_A2_WORKERS': '6',
    'SHADPS4_ARENA_READ_PREFETCH': '1',
    'SHADPS4_ASC_EARLY': '1',
    'SHADPS4_ASC_YIELD': '1',
    'SHADPS4_ASYNC_COMPUTE_PIPELINES': '1',
    'SHADPS4_ASYNC_COMPUTE_THREADS': '12',
    # issn_pc 2026-10-23 (players: stutter while shaders compile): a new graphics pipeline compiles on
    # background threads and its draws are skipped meanwhile (the core's switch, off in axylpup's
    # release, which ships a pre-made cache instead). The launcher's Shader compilation choice.
    'SHADPS4_ASYNC_PIPELINES': '1',
    'SHADPS4_ASYNC_RECORDING': '1',
    'SHADPS4_ASYNC_RECORDING_TIMING': '0',
    'SHADPS4_ASYNC_SUBMIT': '1',
    'SHADPS4_ASYNC_UPLOAD_RECORD': '1',
    'SHADPS4_BACK_CLEAN': '1',
    'SHADPS4_BOOT_SCREEN': '1',
    'SHADPS4_BIG_GROW': '1',
    'SHADPS4_BUFFER_RANGES_LOCK': '2',
    'SHADPS4_BUFFER_SAMPLE': '0',
    'SHADPS4_CACHE_PREFETCH': '3',
    'SHADPS4_CACHE_READBACKS_MODE': '1',
    'SHADPS4_CACHE_REBUILD': '1',
    'SHADPS4_CLAIM_WAKE': '1',
    'SHADPS4_CP_QOS': 'auto',  # axylpup 1.1: 3 from 20 logical processors, else 0 (+8-12% below)
    'SHADPS4_CP_SPLIT': '2',
    'SHADPS4_CP_SPLIT_DEPTH': '1024',
    'SHADPS4_CP_SPLIT_DISP_GUARD': '0',
    'SHADPS4_CP_SPLIT_DONE_BATCH': '16',
    'SHADPS4_CP_SPLIT_FRONT_DERIVE': '1',
    'SHADPS4_CP_SPLIT_POST_ASC': '1',
    'SHADPS4_CP_SPLIT_POST_DMA': '1',
    'SHADPS4_CP_SPLIT_POST_FENCE': '1',
    'SHADPS4_CP_SPLIT_POST_FLIP': '1',
    'SHADPS4_CP_SPLIT_POST_ORDER': '1',
    'SHADPS4_CP_SPLIT_POST_TASK_DONE': '1',
    'SHADPS4_CP_SPLIT_POST_WDATA': '1',
    'SHADPS4_CP_SPLIT_REG_BATCH': '1',
    'SHADPS4_CP_SPLIT_RPTR_BACKING': '1',
    'SHADPS4_CP_SPLIT_TASK_GUARD': '3',
    'SHADPS4_CP_SPLIT_WAIT_BARRIER': '1',
    'SHADPS4_DECODE_IMAGE_VERIFY': '0',
    'SHADPS4_DEFERRED_PROTECT': '1',
    'SHADPS4_DEFER_READ_PROTECT': '1',
    'SHADPS4_DERIVE_INDIRECT': '1',
    'SHADPS4_DESCRIPTOR_TEMPLATES': '0',
    'SHADPS4_DESCRIPTOR_TEMPLATES_VERIFY': '0',
    'SHADPS4_DRAW_PACKET': '1',
    'SHADPS4_DRAW_PACKET_DELTA': '1',
    'SHADPS4_DRAW_REPEAT': '1',
    'SHADPS4_DRAW_SAMPLE': '0',
    'SHADPS4_DRAW_SPLIT': '0',
    'SHADPS4_EPOCH_DEFER_UPLOAD': '0',
    'SHADPS4_EXPOSURE_STALE_READ': '1',
    'SHADPS4_FAST_LANE_PRODUCER': '1',
    'SHADPS4_FAST_TEX_MUTEX': '1',
    'SHADPS4_FAULT_GUEST_COPY': '1',
    'SHADPS4_FAULT_OVERLAP_RETRY': '1',
    'SHADPS4_FAULT_VETO_EXACT': '1',
    'SHADPS4_FAULT_WRITE_SKIP_SETTLE': '1',
    'SHADPS4_FENCE_FORWARD': '1',
    'SHADPS4_FETCH_RESULTS': '0',
    'SHADPS4_FETCH_RESULTS_VERIFY': '0',
    'SHADPS4_FLUSH_EVERY_DRAWS': '1000',
    'SHADPS4_FLUSH_EVERY_OPS': '64',
    'SHADPS4_GPU_MOD_RELAXED': '1',
    'SHADPS4_GPU_RANGES_FLAT': '1',
    'SHADPS4_GPU_TIMESTAMPS': '0',
    'SHADPS4_GUEST_DISPATCH_ORDER': '1',
    'SHADPS4_HAIR_STENCIL_TRACE': '0',
    'SHADPS4_HEAP_WRITE_BLOCK': '65536',
    'SHADPS4_HOT_PAGE_PREFETCH': '1',
    'SHADPS4_HOT_PREFETCH_AT_WRITE': '1',
    'SHADPS4_IB_TASK_FREE': '1',
    'SHADPS4_LEAN': '1',
    'SHADPS4_LEAN_BIND': '1',
    'SHADPS4_LEAN_PARSE': '2',
    'SHADPS4_LEAN_PERDRAW': '1',
    'SHADPS4_LEAN_SPLIT_ANY': '128',
    'SHADPS4_LEAN_WORKERS': '8',
    'SHADPS4_NATIVE_BINDLESS': '1',
    'SHADPS4_NATIVE_BINDLESS_VS': '1',
    'SHADPS4_NATIVE_COHERENT': '1',
    'SHADPS4_NATIVE_COLOUR': '1',
    'SHADPS4_NATIVE_COLOUR_UNTEXTURED': '1',
    'SHADPS4_NATIVE_DIRECT': '1',
    'SHADPS4_NATIVE_GPU_DIRECT': '1',
    'SHADPS4_NATIVE_PASS': '1',
    'SHADPS4_NATIVE_PT': '1',
    'SHADPS4_NATIVE_REC': '0',
    'SHADPS4_NATIVE_SPARSE_LAYOUT': '1',
    'SHADPS4_NR_ITYPE_LATE': '1',
    'SHADPS4_NR_SHARDED': '2',
    'SHADPS4_NR_WINDOW': '16384',
    'SHADPS4_NR_WORKER': '1',
    'SHADPS4_OCCLUSION_QUERIES': '1',
    'SHADPS4_PAGE_OS_LOCK': '1',
    'SHADPS4_PARALLEL_WARMUP': '1',
    'SHADPS4_PARTICLE_ARENA_BLOCK': '1',
    'SHADPS4_PARTICLE_ARENA_DISCARD': '1',
    'SHADPS4_PARTICLE_LATE_READ': '1',
    'SHADPS4_PAR_REC': '2',
    'SHADPS4_PENDING_OPS_LOCK': '1',
    'SHADPS4_PGM_PREFETCH': '1',
    'SHADPS4_POOL_AUTO': '1',
    'SHADPS4_POP_FASTPATH': '1',
    'SHADPS4_PRECOMPILE_WAIT': '1',
    'SHADPS4_PREPARED_IMAGES': '1',
    'SHADPS4_PREPARED_IMAGES_VERIFY': '0',
    'SHADPS4_PREPARED_LOCAL': '1',
    'SHADPS4_PREPARED_PIPELINES': '1',
    'SHADPS4_PREPARED_PIPELINES_VERIFY': '0',
    'SHADPS4_PREPARED_PROGRAMS': '1',
    'SHADPS4_PREPARED_PROGRAMS_TWO_ENTRY': '0',
    'SHADPS4_PREPARED_PROGRAMS_VERIFY': '0',
    'SHADPS4_PREPARED_TARGETS': '1',
    'SHADPS4_PREPARED_TARGETS_VERIFY': '0',
    'SHADPS4_PRESENT_ASYNC': '1',
    'SHADPS4_PROTECT_LOCK_PROFILE': '0',
    'SHADPS4_PROTECT_STEAL': '2',
    'SHADPS4_READBACK_FAST_LANE': '1',
    'SHADPS4_READBACK_OFFLOAD': '1',
    'SHADPS4_READBACK_OFFLOAD_PROFILE': '0',
    'SHADPS4_READBACK_OFFLOAD_VERIFY': '0',
    'SHADPS4_READBACK_PREBATCH': '2',
    'SHADPS4_READBACK_PREFETCH': '0',
    'SHADPS4_READBACK_PREFETCH_VERIFY': '0',
    'SHADPS4_READBACK_QUEUE': '3',
    'SHADPS4_READ_BIND_MEMO': '3',
    'SHADPS4_READ_FAULT_WIDEN_SIZE': '1048576',
    'SHADPS4_READ_MEMO_PREFETCH': '1',
    'SHADPS4_READ_SYNC_FAST': '1',
    'SHADPS4_READ_UNTRACK_FENCE': '1',
    'SHADPS4_REC_CHUNKS': '64',
    'SHADPS4_RENDER_STATE_MEMO': '1',
    'SHADPS4_RESOLVED_RO_BUFFERS': '0',
    'SHADPS4_RESOLVED_RO_BUFFERS_VERIFY': '0',
    'SHADPS4_SHARED_FETCH_VERIFY': '0',
    'SHADPS4_SHARED_PROTECT': '1',
    'SHADPS4_SKIP_EMPTY_DYNSTATE': '1',
    'SHADPS4_SKIP_EMPTY_UPLOADS': '0',
    'SHADPS4_SKIP_EMPTY_UPLOADS_VERIFY': '0',
    'SHADPS4_SMART_BUFFER_BARRIERS': '1',
    'SHADPS4_SMOKE_SORT_SYNC': '1',
    'SHADPS4_SPINLOCK_PARK': '1',
    'SHADPS4_STENCIL_REPLACE_OP': '1',
    'SHADPS4_STREAM_MAX_SIZE': '0',
    'SHADPS4_SUBMIT_LOCK_SKIP': '1',
    'SHADPS4_SWAPCHAIN_SAFE_WAIT': '1',
    'SHADPS4_TEXTURE_STAGE_BATCH': '0',
    'SHADPS4_TEXTURE_STAGE_BATCH_PROFILE': '0',
    'SHADPS4_TEX_BIND_MEMO': '1',
    'SHADPS4_TEX_INVALIDATE_PRECHECK': '1',
    'SHADPS4_TEX_LOCKFREE_HIT': '1',
    'SHADPS4_TEX_MEMO_HASH': '1',
    'SHADPS4_TEX_MEMO_LOCAL_GEN': '1',
    'SHADPS4_TEX_MEMO_WAYS': '32',
    'SHADPS4_TEX_TABLE': '3',
    'SHADPS4_TEX_TABLE_V2': '1',
    'SHADPS4_TEX_TRANSIT_SKIP': '1',
    'SHADPS4_TEX_UNTRACK_OUTSIDE': '1',
    'SHADPS4_TRANSFER_COALESCE': '1',
    'SHADPS4_UPLOAD_AHEAD': '64',
    'SHADPS4_VIEWPORT_MEMO': '1',
    'SHADPS4_WATCH_MAPCHECK': '0',
    'SHADPS4_WATER_REFLECTION_SCALE': '4',
    'SHADPS4_WRITE_FAULT_OFFLOAD': '1',
    'SHADPS4_WRITE_FAULT_PAGES': '0',
    'SHADPS4_WRITE_FAULT_WIDEN': '2',
    'SHADPS4_WRM_PARTIAL': '1',
}

PROFILES = {
    title: {
        # Precise GPU readbacks: the game reads GPU particle results back (the smoke to drain,
        # drones); without them those effects are missing (axylpup's config: readbacks_mode 2).
        'ISSN_READBACKS': '2',
        # Draw distance (1.07 only: src/view_hook.h; launcher: Draw distance). The old core needed
        # a shorter LOD distance (0.6) for fewer draws; the new one runs the city at 90-105 FPS
        # on the tester's PC, so the game's own distance is back (less pop-in).
        'ISSN_LOD_SCALE': '1.0',
        'ISSN_REGION_FADE': '1.0',
        # The game reads rdtsc itself and scales it by sceKernelGetTscFrequency: the host's TSC
        # and frequency, or it runs several times too fast (runtime_kernel.c).
        'ISSN_TSC': 'host',
        # Diagnostic, printed only by the watchdog (ISSN_TIMEOUT): the render thread's fence
        # labels (pointer to the table) and the frame fence it waits on (target, label index)
        # in the 01.00 EU eboot (EP9000); the GPU writes those labels with end-of-pipe events.
    }
    for title in INFAMOUS_SECOND_SON
}
for _title in INFAMOUS_SECOND_SON:
    PROFILES[_title].update(AXY_RELEASE_FLAGS)
    # axylpup's per-game config (custom_configs/CUSA00004.json): direct memory access on.
    PROFILES[_title]['ISSN_DIRECT_MEMORY_ACCESS'] = '1'
    # ... and no readback of linear images (run.py's default, from God of War III): with it the
    # GPU command thread spent half its time downloading render targets behind a full GPU wait
    # (tester, city idle: 55-60 FPS, back never idle).
    PROFILES[_title]['ISSN_READBACK_LINEAR'] = '0'
PROFILES['CUSA00004']['ISSN_WATCHDOG_PEEK'] = '0x11c0380,0x4ec7310'
# The US release has the Brazilian Portuguese dub and text (lang_brazilian_*): the tester's
# choice. ISSN_LANGUAGE=1 gives English.
PROFILES['CUSA00223']['ISSN_LANGUAGE'] = '17'


def title_id(game):
    """TITLE_ID of the game folder's param.sfo, or None."""
    try:
        from prepare import read_sfo
    except ImportError:
        return None
    sfo = read_sfo(Path(game) / 'sce_sys' / 'param.sfo') or {}
    value = sfo.get('TITLE_ID')
    return str(value) if value else None


def environment(game, env):
    """(title ID, {variable: value}) to add for this game: only variables env does not set."""
    title = title_id(game)
    if env.get('ISSN_GAME_PROFILE') == '0' or title not in PROFILES:
        return title, {}
    return title, {key: value for key, value in PROFILES[title].items() if key not in env}
