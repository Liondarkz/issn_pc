/* libScePad on SDL3 gamepads, with a keyboard fallback. SDL events are pumped
 * by the window thread (gpu/shim/window.cpp); here state is only sampled.
 *
 * Default keyboard layout (axylpup's; ISSN_KEY_MAP changes the buttons, see apply_button_map):
 *   WASD left stick, IJKL right stick, arrows d-pad, Space Cross, LShift Circle, C Square,
 *   F Triangle (numpad 2/6/4/8 too), Q L1, E R1, 1 / right mouse L2, 3 / left mouse R2, U L3, O R3,
 *   Enter Options, R left touchpad (photo mode), G right touchpad (pause menu), Y mouse camera.
 *
 * issn: synthetic motion (axylpup's SyntheticMotionLocked) for a keyboard or a pad without motion
 * sensors, for inFAMOUS Second Son's spray can: Z held (or L3+R3 / H to toggle) tilts the pad on
 * its side, a tap of Z or X held (L1 while tilted on a pad) rattles it, and the mouse or the right
 * stick aims while tilted. Pose, gravity and angular velocity come from one quaternion, so the
 * accelerometer, the gyroscope and the orientation always agree.
 *
 * issn: the motion sensors of a DualShock 4/DualSense (SDL accelerometer and gyroscope) and the
 * orientation integrated from the gyroscope; scePadSetLightBar colors the pad's light bar. */
#define _GNU_SOURCE
#include "runtime.h"
#include "gpu/issngpu.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <pthread.h>
#include <time.h>
#include <math.h>
#include <SDL3/SDL.h>
#include <sys/stat.h>

#define ERR_INVALID_ARG ((int32_t)0x80920001)
#define ERR_INVALID_HANDLE ((int32_t)0x80920003)
#define ERR_ALREADY_OPENED ((int32_t)0x80920004)
#define ERR_NOT_INITIALIZED ((int32_t)0x80920005)
#define PAD_HANDLE 1

enum {
    BTN_L3=0x2, BTN_R3=0x4, BTN_OPTIONS=0x8, BTN_UP=0x10, BTN_RIGHT=0x20, BTN_DOWN=0x40, BTN_LEFT=0x80,
    BTN_L2=0x100, BTN_R2=0x200, BTN_L1=0x400, BTN_R1=0x800, BTN_TRIANGLE=0x1000, BTN_CIRCLE=0x2000,
    BTN_CROSS=0x4000, BTN_SQUARE=0x8000, BTN_TOUCHPAD=0x100000,
    /* Host-only sources, never shown to the game: a click on one side of the touchpad from a
     * button or key without a touch surface (play_click turns them into a touch and a click). */
    CLICK_LEFT=0x40000000, CLICK_RIGHT=0x80000000,
    /* issn: rattles the pad (synth_motion) while held: keyboard X, or a mapped pad button. */
    SHAKE=0x20000000,
    /* issn: turns the spray pose on and off (H; L3+R3 together on a pad). */
    SPRAY_POSE=0x10000000,
};
typedef struct { uint16_t x, y; uint8_t id, reserve[3]; } PadTouch;
typedef struct {
    uint32_t buttons;
    uint8_t left_x, left_y, right_x, right_y;
    uint8_t l2, r2, analog_padding[2];
    float orientation[4], acceleration[3], angular_velocity[3];
    uint8_t touch_count, touch_reserve[3];
    uint32_t touch_held_time;
    PadTouch touches[2];
    uint8_t connected, pad0[3];
    uint64_t timestamp;
    uint8_t extension[16];
    uint8_t connected_count, reserve[2], unique_length, unique[12];
} PadData;
typedef struct {
    float pixel_density; uint16_t resolution_x, resolution_y;
    uint8_t dead_zone_left, dead_zone_right, connection_type, connected_count;
    uint8_t connected, pad[3];
    int32_t device_class;
    uint8_t reserve[8];
} ControllerInfo;
_Static_assert(sizeof(PadData)==120,"OrbisPadData layout");
_Static_assert(sizeof(PadTouch)==8,"OrbisPadTouch layout");
_Static_assert(__builtin_offsetof(PadData,touches)==60,"OrbisPadData touch offset");
_Static_assert(__builtin_offsetof(PadData,timestamp)==80,"OrbisPadData timestamp offset");
_Static_assert(sizeof(ControllerInfo)==28,"OrbisPadControllerInformation layout");

static pthread_mutex_t lock=PTHREAD_MUTEX_INITIALIZER;
static int initialized, opened, sdl_ready;
static SDL_Gamepad *gamepad;
static size_t reads;
static uint8_t connected_count;

/* ---- issn: motion sensors and light bar ------------------------------------------------------
 * PS4 pads report acceleration in G (1.0 = gravity) and angular velocity in rad/s, with the pad's
 * axes: x to the right, y up, z toward the player; SDL uses the same axes, in m/s^2 and rad/s.
 * ISSN_PAD_ACCEL_UNITS=ms2 passes m/s^2 unchanged (as shadPS4 does) in case a game expects it.
 * Orientation: the gyroscope integrated per read (scePadResetOrientation starts it again). */
#define STANDARD_GRAVITY 9.80665f
enum { SENSOR_ACCEL=1, SENSOR_GYRO=2 };
static int motion_enabled=1;                       /* scePadSetMotionSensorState */
static int sensors;                                /* SENSOR_* of the open gamepad */
static float orientation_q[4]={0.0f,0.0f,0.0f,1.0f}; /* x y z w */
static uint64_t orientation_time;
static struct { uint8_t rgb[3], sent[3]; int set, applied; } light;

static float accel_scale(void) {
    static int mode=-1;
    if (mode<0) { const char *u=getenv("ISSN_PAD_ACCEL_UNITS"); mode=u && !strcmp(u,"ms2"); }
    return mode ? 1.0f : 1.0f/STANDARD_GRAVITY;
}

/* q' = q + dt/2 * q*(w,0), normalized (the same integration as shadPS4's pad). dt over 1 s (a
 * pause, the first read) keeps the orientation. */
static void integrate_orientation(float q[4], const float w[3], float dt) {
    if (dt<=0.0f || dt>1.0f) return;
    const float x=q[0], y=q[1], z=q[2], qw=q[3];
    const float dx=0.5f*( qw*w[0] + y*w[2] - z*w[1]);
    const float dy=0.5f*( qw*w[1] + z*w[0] - x*w[2]);
    const float dz=0.5f*( qw*w[2] + x*w[1] - y*w[0]);
    const float dw=0.5f*(-x*w[0] - y*w[1] - z*w[2]);
    q[0]+=dx*dt; q[1]+=dy*dt; q[2]+=dz*dt; q[3]+=dw*dt;
    const float n=sqrtf(q[0]*q[0]+q[1]*q[1]+q[2]*q[2]+q[3]*q[3]);
    if (n>0.0f) for (int i=0;i<4;++i) q[i]/=n;
}

/* issn: the L3+R3 / H toggle's edge (the synthetic motion sensor, synth_motion). */
static int spray_chord_held;

/* The pad's light bar takes the last color the game set (ISSN_LIGHTBAR=0 leaves it alone); sent
 * again after a reconnection, and only when it changes. Called under lock. */
static void apply_light(SDL_Gamepad *g) {
    static int enabled=-1;
    if (enabled<0) { const char *e=getenv("ISSN_LIGHTBAR"); enabled=!(e && e[0]=='0'); }
    if (!g || !enabled || !light.set) return;
    if (light.applied && !memcmp(light.sent,light.rgb,3)) return;
    if (SDL_SetGamepadLED(g,light.rgb[0],light.rgb[1],light.rgb[2])) {
        memcpy(light.sent,light.rgb,3); light.applied=1;
    }
}

/* issn: ISSN_PAD_EMULATION (launcher: Controls > Controller without touchpad): the touchpad
 * swipes and two fingers (Select/Back + right stick, + R1) and the spray pose (L3+R3) for pads
 * without a touchpad or motion sensors. auto (default): on when the pad lacks a touchpad or
 * motion sensors (Xbox, 8BitDo, ...), off on a DualShock 4 / DualSense, whose L3+R3 then reach the
 * game; 1: always; 0: never. */
static int pad_emulation_auto=1; /* set when a gamepad opens */
static bool pad_emulation(void) {
    static int mode=-2;
    if (mode==-2) {
        const char *e=getenv("ISSN_PAD_EMULATION");
        mode=e && e[0]=='1' ? 1 : e && e[0]=='0' ? 0 : -1;
        printf("Runtime: pad emulation for pads without a touchpad: %s\n",mode==1 ? "on" : mode==0 ? "off" : "auto");
    }
    return mode<0 ? pad_emulation_auto : mode;
}

/* Called under lock with the gamepad just opened: its motion sensors, its light bar. */
static void open_sensors(SDL_Gamepad *g) {
    sensors=0;
    if (SDL_GamepadHasSensor(g,SDL_SENSOR_ACCEL) && SDL_SetGamepadSensorEnabled(g,SDL_SENSOR_ACCEL,true)) sensors|=SENSOR_ACCEL;
    if (SDL_GamepadHasSensor(g,SDL_SENSOR_GYRO) && SDL_SetGamepadSensorEnabled(g,SDL_SENSOR_GYRO,true)) sensors|=SENSOR_GYRO;
    printf("Runtime: gamepad motion sensors: %s%s%s\n",sensors ? "" : "none",
           (sensors & SENSOR_ACCEL) ? "accelerometer " : "", (sensors & SENSOR_GYRO) ? "gyroscope" : "");
    pad_emulation_auto=SDL_GetNumGamepadTouchpads(g)<=0 || !(sensors & SENSOR_ACCEL);
    printf("Runtime: gamepad %s: touchpad emulation (Select + right stick, L3+R3 spray) %s\n",
           SDL_GetNumGamepadTouchpads(g)>0 ? "with a touchpad" : "without a touchpad",
           pad_emulation() ? "on" : "off");
    light.applied=0;
    apply_light(g);
}

/* Acceleration, angular velocity and orientation of this read; under lock. */
static void sample_motion(PadData *d, SDL_Gamepad *g) {
    const float scale=accel_scale();
    d->acceleration[0]=0.0f; d->acceleration[1]=STANDARD_GRAVITY*scale; d->acceleration[2]=0.0f;
    float w[3]={0.0f,0.0f,0.0f};
    if (g && motion_enabled) {
        float a[3];
        if ((sensors & SENSOR_ACCEL) && SDL_GetGamepadSensorData(g,SDL_SENSOR_ACCEL,a,3))
            for (int i=0;i<3;++i) d->acceleration[i]=a[i]*scale;
        if ((sensors & SENSOR_GYRO) && SDL_GetGamepadSensorData(g,SDL_SENSOR_GYRO,w,3))
            for (int i=0;i<3;++i) d->angular_velocity[i]=w[i];
    }
    if (orientation_time) integrate_orientation(orientation_q,d->angular_velocity,(float)(d->timestamp-orientation_time)/1e6f);
    orientation_time=d->timestamp;
    memcpy(d->orientation,orientation_q,sizeof(orientation_q));
}

static uint64_t now_us(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC,&t); return (uint64_t)t.tv_sec*1000000u+(uint64_t)t.tv_nsec/1000u; }
/* issn: ISSN_STICK_DEADZONE (0..0.5, default 0.05; 0.10 felt stiff on the camera): stick values inside it read as centered and
 * the rest is rescaled, so a stick that rests a little off center (8BitDo and worn sticks) does
 * not turn the camera on its own. As HayatoG's iss_pc (TLG_STICK_DEADZONE). */
static float stick_deadzone(void) {
    static float zone=-1.0f;
    if (zone<0.0f) {
        const char *e=getenv("ISSN_STICK_DEADZONE");
        zone=e && *e ? (float)atof(e) : 0.05f;
        if (zone<0.0f) zone=0.0f;
        if (zone>0.5f) zone=0.5f;
    }
    return zone;
}
static uint8_t axis(int16_t v) {
    float f=(float)v/32767.0f;
    const float zone=stick_deadzone();
    if (f>1.0f) f=1.0f;
    if (f<-1.0f) f=-1.0f;
    f=fabsf(f)<=zone ? 0.0f : (f>0.0f ? f-zone : f+zone)/(1.0f-zone);
    const int x=(int)lroundf(f*127.5f+127.5f);
    return (uint8_t)(x<0 ? 0 : x>255 ? 255 : x);
}
static uint8_t trigger(int16_t v) { int x=v>>7; return (uint8_t)(x<0 ? 0 : x>255 ? 255 : x); }
static uint16_t touch_axis(float v, int max) {
    return (uint16_t)(v<=0.0f ? 0 : v>=1.0f ? max : (int)(v*max+0.5f));
}

/* A click without a touch surface (Back/View, Tab, Backspace, the pad file) is played like a
 * finger: the touch is reported CLICK_LEAD_US before the button, the button stays at least
 * CLICK_HOLD_US, and the touch lingers CLICK_TAIL_US after the release. God of War III opens
 * photo mode on the release of a left click with the finger still down, and ignores a touch
 * that comes and goes with the button. The side is chosen when the click starts and kept: the
 * right source with L1 held is a left click (photo mode on pads without a touchpad). */
enum { CLICK_LEAD_US=50000, CLICK_HOLD_US=50000, CLICK_TAIL_US=100000 };
static int key_touch_held; /* issn: the keyboard's right touchpad key (G) is down this read */

static struct { int side; uint64_t pressed, released; } click={-1,0,0};
static void play_click(PadData *d) {
    const uint64_t now=d->timestamp;
    uint32_t sources=d->buttons & (CLICK_LEFT|CLICK_RIGHT);
    d->buttons&=~(CLICK_LEFT|CLICK_RIGHT);
    if ((d->buttons & BTN_TOUCHPAD) && !d->touch_count) { /* a replay, or a pad without fingers */
        d->buttons&=~BTN_TOUCHPAD;
        sources|=CLICK_LEFT;
    }
    const int side=(sources & CLICK_LEFT) ? 0 : (sources & CLICK_RIGHT) ? !(d->buttons & BTN_L1) : -1;
    if (side>=0 && (click.side<0 || click.released)) { click.side=side; click.pressed=now; click.released=0; }
    else if (side<0 && click.side>=0 && !click.released) click.released=now;
    if (click.side<0) return;
    const uint64_t down=click.pressed+CLICK_LEAD_US;
    uint64_t up=down+CLICK_HOLD_US;
    if (!click.released) up=UINT64_MAX;
    else if (click.released>up) up=click.released;
    if (now>=up && now-up>=CLICK_TAIL_US) { click.side=-1; return; }
    if (d->touch_count<2) {
        d->touches[d->touch_count]=(PadTouch){.x=click.side ? 1440 : 480,.y=471,.id=d->touch_count};
        ++d->touch_count;
    }
    /* issn: both sources held (keyboard R + G), or the keyboard's G alone: a finger on each half,
     * the click held while either is (axylpup 2026-10-18: the Reggie car scene of Second Son,
     * "Press and Hold the Touch Pad Button with both thumbs", needs both halves; the game's
     * drains take both thumbs too). The prompts show G on the touchpad (infamous_prompts). */
    static int both_was;
    const int both=((sources & CLICK_LEFT) && (sources & CLICK_RIGHT)) || (key_touch_held && (sources & CLICK_RIGHT));
    if (both && d->touch_count<2) {
        d->touches[d->touch_count]=(PadTouch){.x=click.side ? 480 : 1440,.y=471,.id=d->touch_count};
        ++d->touch_count;
    }
    if (both!=both_was) printf("Runtime: pad: %s\n",both ? "both touchpad halves held (two thumbs)" : "two thumbs lifted");
    both_was=both;
    if (now>=down && now<up) d->buttons|=BTN_TOUCHPAD;
}

/* issn: a swipe on pads without a touchpad (inFAMOUS Second Son: swipe up to lift a prisoner).
 * The right stick pushed while the right click source (Back/Select, Backspace) is held: a finger
 * crosses the touchpad in the stick's direction (one of four) in SWIPE_US, without a click; the
 * click in progress, if any, is dropped. The right stick is hidden from the game while the
 * source is held. Stick first, then Select: a swipe with no click at all. R1 with it: two
 * fingers (play_two_fingers). */
enum { SWIPE_US=250000, SWIPE_TAIL_US=50000 };
static struct { int active, used; float dx, dy; uint64_t start, held; uint8_t id; } swipe;
static float swipe_stick(uint8_t v) { return ((float)v-128.0f)/127.0f; }
/* issn: two fingers (Second Son: both thumbs on the touchpad to lift a tree). R1 with the right
 * click source held: one finger on each half of the touchpad and, 50 ms after they land, the
 * touchpad click, while both are held; the right stick drags both (up to 1200 units/s). */
enum { FINGERS_CLICK_US=50000 };
static struct { int active; float x, y; uint64_t last, start; uint8_t id; } fingers;
static bool play_two_fingers(PadData *d) {
    if (!(d->buttons & CLICK_RIGHT) || !(d->buttons & BTN_R1)) {
        if (fingers.active) printf("Runtime: pad: two fingers lifted\n");
        fingers.active=0;
        return false;
    }
    if (!fingers.active) {
        fingers.active=1; fingers.x=0.0f; fingers.y=0.0f; fingers.last=fingers.start=d->timestamp;
        fingers.id=(uint8_t)(fingers.id+2)&0x7e;
        click.side=-1; swipe.active=0; swipe.used=1;
        printf("Runtime: pad: two fingers on the touchpad\n");
    }
    const float dt=(float)(d->timestamp-fingers.last)/1e6f;
    fingers.last=d->timestamp;
    float sx=swipe_stick(d->right_x), sy=swipe_stick(d->right_y);
    if (fabsf(sx)<0.15f) sx=0.0f;
    if (fabsf(sy)<0.15f) sy=0.0f;
    fingers.x+=sx*1200.0f*dt; fingers.y+=sy*1200.0f*dt;
    if (fingers.x<-400.0f) fingers.x=-400.0f;
    if (fingers.x>400.0f) fingers.x=400.0f;
    if (fingers.y<-400.0f) fingers.y=-400.0f;
    if (fingers.y>400.0f) fingers.y=400.0f;
    d->buttons&=~(CLICK_RIGHT|BTN_R1);
    d->right_x=d->right_y=128;
    const float base[2]={520.0f,1400.0f};
    d->touch_count=0;
    for (int i=0;i<2;++i) {
        const float x=base[i]+fingers.x, y=471.0f+fingers.y;
        d->touches[d->touch_count++]=(PadTouch){.x=(uint16_t)(x<0 ? 0 : x>1919 ? 1919 : x),
                                                .y=(uint16_t)(y<0 ? 0 : y>942 ? 942 : y),
                                                .id=(uint8_t)(fingers.id+i)};
    }
    if (d->timestamp-fingers.start>=FINGERS_CLICK_US) d->buttons|=BTN_TOUCHPAD;
    return true;
}
static void play_swipe(PadData *d) {
    if (play_two_fingers(d)) return;
    const bool source=(d->buttons & CLICK_RIGHT)!=0;
    if (!source) {
        swipe.used=0;
    } else {
        const float sx=swipe_stick(d->right_x), sy=swipe_stick(d->right_y);
        /* issn: "swipe up and hold" (Second Son's mobile command cores): while the stick stays
         * pushed the same way, the finger stays at the end of the swipe. */
        if (swipe.active && sx*swipe.dx+sy*swipe.dy>0.5f) swipe.held=d->timestamp;
        if (!swipe.used && sx*sx+sy*sy>0.36f) {
            swipe.used=1; swipe.active=1; swipe.start=swipe.held=d->timestamp; swipe.id=(uint8_t)(swipe.id+1)&0x7f;
            if (fabsf(sx)>fabsf(sy)) { swipe.dx=sx>0 ? 1.0f : -1.0f; swipe.dy=0.0f; }
            else { swipe.dx=0.0f; swipe.dy=sy>0 ? 1.0f : -1.0f; }
            click.side=-1; /* no click with the swipe */
            printf("Runtime: pad: touchpad swipe %s\n",swipe.dy<0 ? "up" : swipe.dy>0 ? "down" : swipe.dx<0 ? "left" : "right");
        }
        if (swipe.used) d->buttons&=~CLICK_RIGHT;
        d->right_x=d->right_y=128;
    }
    if (!swipe.active) return;
    const uint64_t t=d->timestamp-swipe.start;
    const uint64_t end=swipe.held>swipe.start+SWIPE_US ? swipe.held : swipe.start+SWIPE_US;
    if (t>=SWIPE_US && d->timestamp>=end+SWIPE_TAIL_US) { swipe.active=0; return; }
    const float p=t>=SWIPE_US ? 1.0f : (float)t/(float)SWIPE_US;
    const float x=960.0f+swipe.dx*(p-0.5f)*1400.0f, y=471.0f+swipe.dy*(p-0.5f)*700.0f;
    if (d->touch_count<2) {
        d->touches[d->touch_count++]=(PadTouch){.x=(uint16_t)x,.y=(uint16_t)y,.id=swipe.id};
    }
}

/* issn: what SDL sees, once 3 s after the first read and whenever the device count changes
 * (tester 2026-10-22: "gamepad=none" with an 8BitDo Ultimate C plugged in). A joystick SDL has
 * no gamepad mapping for (a pad in DirectInput mode) is listed as "not a gamepad". */
static void log_joysticks(void) {
    static uint64_t first;
    static int last_count=-1;
    const uint64_t now=now_us();
    if (!first) first=now;
    if (now-first<3000000) return;
    int count=0;
    SDL_JoystickID *ids=SDL_GetJoysticks(&count);
    if (count!=last_count) {
        last_count=count;
        printf("Runtime: %d joystick(s) seen by SDL%s\n",count,count ? ":" : " (no controller connected or visible)");
        for (int i=0;i<count;++i)
            printf("Runtime:   %s, %04x:%04x, %s\n",SDL_GetJoystickNameForID(ids[i]) ? SDL_GetJoystickNameForID(ids[i]) : "?",
                   SDL_GetJoystickVendorForID(ids[i]),SDL_GetJoystickProductForID(ids[i]),
                   SDL_IsGamepad(ids[i]) ? "gamepad" : "not a gamepad (no SDL mapping)");
        fflush(stdout);
    }
    SDL_free(ids);
}

/* Opens the first gamepad SDL knows about; called under lock. */
static SDL_Gamepad *current_gamepad(void) {
    if (!sdl_ready) sdl_ready = SDL_WasInit(SDL_INIT_GAMEPAD) ? 1 : SDL_InitSubSystem(SDL_INIT_GAMEPAD) ? 1 : -1;
    if (sdl_ready<0) return NULL;
    log_joysticks();
    if (gamepad && !SDL_GamepadConnected(gamepad)) { SDL_CloseGamepad(gamepad); gamepad=NULL; }
    if (!gamepad) {
        int count=0;
        SDL_JoystickID *ids=SDL_GetGamepads(&count);
        if (ids && count>0) {
            gamepad=SDL_OpenGamepad(ids[0]);
            if (gamepad) {
                ++connected_count; printf("Runtime: gamepad connected: %s\n",SDL_GetGamepadName(gamepad));
                open_sensors(gamepad);
            }
        }
        SDL_free(ids);
    }
    return gamepad;
}
/* Button sources: a host button or key and the PS4 button it presses. The defaults below can be
 * changed per PS4 button with ISSN_PAD_MAP (gamepad) and ISSN_KEY_MAP (keyboard), e.g.
 * ISSN_KEY_MAP="cross=Q,square=Left Shift" or ISSN_PAD_MAP="cross=b,circle=a": comma-separated
 * <ps4 button>=<SDL key or gamepad button name>. A mapped PS4 button loses its default sources.
 * touchpad_left (alias touchpad) and touchpad_right are the two sides of the touchpad click:
 * photo mode and the pause menu. Back/View (Share on a DualSense) is the right click, and with
 * L1 held the left one. Sticks, triggers, the real touchpad click and the overlay shortcuts are
 * not remapped. */
static const struct { const char *name; uint32_t ps; } ps_names[]={
    {"cross",BTN_CROSS}, {"circle",BTN_CIRCLE}, {"square",BTN_SQUARE}, {"triangle",BTN_TRIANGLE},
    {"l1",BTN_L1}, {"r1",BTN_R1}, {"l2",BTN_L2}, {"r2",BTN_R2}, {"l3",BTN_L3}, {"r3",BTN_R3},
    {"options",BTN_OPTIONS}, {"touchpad",CLICK_LEFT}, {"touchpad_left",CLICK_LEFT}, {"touchpad_right",CLICK_RIGHT},
    {"up",BTN_UP}, {"down",BTN_DOWN}, {"left",BTN_LEFT}, {"right",BTN_RIGHT}, {"shake",SHAKE},
    {"spray_pose",SPRAY_POSE},
};
typedef struct { int source; uint32_t ps; } ButtonSource;
typedef struct { ButtonSource entries[48]; size_t count; } ButtonMap;
static ButtonMap pad_map={{
    {SDL_GAMEPAD_BUTTON_SOUTH,BTN_CROSS}, {SDL_GAMEPAD_BUTTON_EAST,BTN_CIRCLE},
    {SDL_GAMEPAD_BUTTON_WEST,BTN_SQUARE}, {SDL_GAMEPAD_BUTTON_NORTH,BTN_TRIANGLE},
    {SDL_GAMEPAD_BUTTON_LEFT_SHOULDER,BTN_L1}, {SDL_GAMEPAD_BUTTON_RIGHT_SHOULDER,BTN_R1},
    {SDL_GAMEPAD_BUTTON_LEFT_STICK,BTN_L3}, {SDL_GAMEPAD_BUTTON_RIGHT_STICK,BTN_R3},
    {SDL_GAMEPAD_BUTTON_START,BTN_OPTIONS}, {SDL_GAMEPAD_BUTTON_BACK,CLICK_RIGHT},
    {SDL_GAMEPAD_BUTTON_TOUCHPAD,BTN_TOUCHPAD},
    {SDL_GAMEPAD_BUTTON_DPAD_UP,BTN_UP}, {SDL_GAMEPAD_BUTTON_DPAD_DOWN,BTN_DOWN},
    {SDL_GAMEPAD_BUTTON_DPAD_LEFT,BTN_LEFT}, {SDL_GAMEPAD_BUTTON_DPAD_RIGHT,BTN_RIGHT},
    {SDL_GAMEPAD_BUTTON_MISC1,SHAKE},
},16};
/* issn: the keyboard layout of axylpup's Second Son build (github.com/axylpup/shadPS4, release
 * 1.0.2 input_config/default.ini): WASD moves, IJKL turns the camera, the arrows are the d-pad,
 * the mouse buttons are L2 (aim) and R2 (shoot), Z tilts the pad (held) or rattles it (a tap), X
 * rattles it while held, Y gives the camera to the mouse (motion_input). */
static ButtonMap key_map={{
    {SDL_SCANCODE_SPACE,BTN_CROSS}, {SDL_SCANCODE_LSHIFT,BTN_CIRCLE}, {SDL_SCANCODE_C,BTN_SQUARE},
    {SDL_SCANCODE_F,BTN_TRIANGLE}, {SDL_SCANCODE_Q,BTN_L1}, {SDL_SCANCODE_E,BTN_R1},
    {SDL_SCANCODE_1,BTN_L2}, {SDL_SCANCODE_3,BTN_R2}, {SDL_SCANCODE_U,BTN_L3}, {SDL_SCANCODE_O,BTN_R3},
    {SDL_SCANCODE_RETURN,BTN_OPTIONS}, {SDL_SCANCODE_R,CLICK_LEFT}, {SDL_SCANCODE_G,CLICK_RIGHT},
    {SDL_SCANCODE_UP,BTN_UP}, {SDL_SCANCODE_DOWN,BTN_DOWN}, {SDL_SCANCODE_LEFT,BTN_LEFT}, {SDL_SCANCODE_RIGHT,BTN_RIGHT},
    {SDL_SCANCODE_KP_2,BTN_CROSS}, {SDL_SCANCODE_KP_6,BTN_CIRCLE}, {SDL_SCANCODE_KP_4,BTN_SQUARE},
    {SDL_SCANCODE_KP_8,BTN_TRIANGLE},
    {SDL_SCANCODE_X,SHAKE}, {SDL_SCANCODE_H,SPRAY_POSE},
},23};

static char *trim(char *text) {
    while (*text==' ' || *text=='\t') ++text;
    char *end=text+strlen(text);
    while (end>text && (end[-1]==' ' || end[-1]=='\t' || end[-1]=='\r' || end[-1]=='\n')) *--end=0;
    return text;
}

/* Applies a ISSN_PAD_MAP/ISSN_KEY_MAP value; returns how many entries were used. Bad entries are
 * logged and skipped, so their PS4 button keeps its default. */
static int apply_button_map(ButtonMap *map, const char *spec, bool keyboard) {
    if (!spec || !*spec) return 0;
    char copy[1024];
    snprintf(copy,sizeof(copy),"%s",spec);
    int used=0;
    char *entry=copy;
    while (entry) {
        char *next=strchr(entry,',');
        if (next) *next++=0;
        char *eq=strchr(entry,'=');
        if (!eq) {
            if (*trim(entry)) printf("Runtime: pad map: ignored '%s' (expected button=name)\n",trim(entry));
            entry=next;
            continue;
        }
        *eq=0;
        const char *button=trim(entry), *name=trim(eq+1);
        entry=next;
        uint32_t ps=0;
        for (size_t i=0;i<sizeof(ps_names)/sizeof(*ps_names);++i) if (!SDL_strcasecmp(button,ps_names[i].name)) ps=ps_names[i].ps;
        const int source=!*name ? -1 : keyboard ? (int)SDL_GetScancodeFromName(name) : (int)SDL_GetGamepadButtonFromString(name);
        if (!ps || source<=(keyboard ? (int)SDL_SCANCODE_UNKNOWN : (int)SDL_GAMEPAD_BUTTON_INVALID)) {
            printf("Runtime: pad map: ignored '%s=%s'\n",button,name);
            continue;
        }
        size_t kept=0;
        /* issn: the default shake source yields to a button mapped on it (misc1 on the pad). */
        for (size_t i=0;i<map->count;++i)
            if (map->entries[i].ps!=ps && !(map->entries[i].ps==SHAKE && map->entries[i].source==source))
                map->entries[kept++]=map->entries[i];
        map->count=kept;
        if (map->count<sizeof(map->entries)/sizeof(*map->entries)) map->entries[map->count++]=(ButtonSource){source,ps};
        ++used;
    }
    return used;
}

static void load_button_maps(void) {
    static bool loaded;
    if (loaded) return;
    loaded=true;
    const int pad=apply_button_map(&pad_map,getenv("ISSN_PAD_MAP"),false);
    const int keys=apply_button_map(&key_map,getenv("ISSN_KEY_MAP"),true);
    if (pad || keys) printf("Runtime: pad map: %d gamepad and %d keyboard buttons remapped\n",pad,keys);
}

static void key_axis(uint8_t *axis, bool negative, bool positive) {
    if (negative || positive) *axis=(uint8_t)(128-(negative ? 128 : 0)+(positive ? 127 : 0));
}
/* issn: the mouse. Its buttons are L2 (right: aim) and R2 (left: shoot) while the game window has
 * it; its motion goes to the synthetic motion sensor while the pad is tilted (the spray can's aim)
 * and, while the window holds the mouse (Y in the game, issn_overlay.cpp), to the right stick as
 * the camera. ISSN_MOUSE_SPEED (pixels per second for a full stick, default 900). */
static float mouse_dx, mouse_dy; /* motion since the last read, for synth_motion */
/* issn: degrees the spray can turns per 100 px of mouse motion (ISSN_SPRAY_MOUSE; - / = while the
 * pad is tilted). axylpup's default, 2.5 (his tester found 5 too fast); 0.3.3 turned it 0.004 rad
 * per px = 23 degrees, which players found far too jumpy in the opening spray scenes. */
static float spray_sens=2.5f;
/* issn: degrees per second the spray can turns with the right stick pushed all the way
 * (ISSN_SPRAY_STICK; - / = while the pad is tilted scale it with the mouse's). The stick is
 * squared after its dead zone, so a light push or a tap moves the can a little. It was 2 rad/s
 * (115 degrees a second) and linear, which testers on Xbox / 8BitDo pads found far too fast: a tap
 * threw the can across the wall (10-24). */
static float spray_stick=30.0f;
static int pad_tilted;           /* the last read's tilt (spray can): the mouse aims the can */

/* issn: 1:1 mouse aim (src/mouse_hook.h, axylpup's src/core/mouse_aim.cpp). The loader patches
 * the camera's pan / tilt store to add what this mailbox holds; the mouse writes radians here.
 * ISSN_MOUSE_AIM_SENS: degrees per 100 px (default 5.12, axylpup's); - / = change it in play.
 * ISSN_MOUSE_AIM_FLIP=x, y or xy flips an axis (the game's own invert options also apply). */
#include "mouse_hook.h"
static _Alignas(64) IssnMouseMailbox aim_box;
static volatile unsigned char *aim_no_follow;
static const volatile unsigned char *aim_invert_x, *aim_invert_y;
static int aim_attached, aim_live;
static uint64_t aim_last_motion;
static float aim_sens=5.12f, aim_sign_x=-1.0f, aim_sign_y=1.0f;
IssnMouseMailbox *runtime_mouse_aim_mailbox(void) { return &aim_box; }
void runtime_mouse_aim_attach(volatile unsigned char *no_follow, const volatile unsigned char *invert_x,
                              const volatile unsigned char *invert_y) {
    aim_no_follow=no_follow; aim_invert_x=invert_x; aim_invert_y=invert_y;
    const char *s=getenv("ISSN_MOUSE_AIM_SENS");
    if (s && atof(s)>0.0) aim_sens=fmaxf(0.5f,fminf(100.0f,(float)atof(s)));
    const char *flip=getenv("ISSN_MOUSE_AIM_FLIP");
    if (flip && strchr(flip,'x')) aim_sign_x=-aim_sign_x;
    if (flip && strchr(flip,'y')) aim_sign_y=-aim_sign_y;
    aim_attached=1;
}
/* Every read: is the camera hook running (gameplay), and the follow byte. Motion parked while
 * the camera does not update (menus, cut-scenes, loading) is dropped, so the view does not jump
 * when play resumes. The game's auto-follow stays off for ISSN_MOUSE_AIM_FOLLOW_HOLD seconds
 * (default 5, axylpup's) after the last mouse motion, as the right stick turns it off. */
static void mouse_aim_tick(uint64_t now) {
    if (!aim_attached) return;
    static uint32_t last_frames;
    static uint64_t last_change, hold_us=1;
    static unsigned char written;
    if (hold_us==1) { const char *v=getenv("ISSN_MOUSE_AIM_FOLLOW_HOLD"); const double s=v ? atof(v) : 5.0; hold_us=(uint64_t)(fmax(0.0,fmin(100000.0,s))*1e6); }
    const uint32_t frames=__atomic_load_n(&aim_box.frames,__ATOMIC_RELAXED);
    if (frames!=last_frames) {
        if (!last_frames) { printf("Mouse aim: the camera hook runs\n"); fflush(stdout); }
        last_frames=frames; last_change=now;
    }
    aim_live=last_change && now-last_change<=250000;
    if (!aim_live) __atomic_store_n(&aim_box.delta,0,__ATOMIC_RELAXED);
    if (aim_no_follow && hold_us) {
        const unsigned char want=aim_last_motion && now-aim_last_motion<hold_us;
        if (want!=written) { *aim_no_follow=want; written=want; }
    }
}
static void mouse_aim_motion(float dx, float dy, uint64_t now) {
    if (dx==0.0f && dy==0.0f) return;
    aim_last_motion=now;
    const float k=aim_sens*(3.14159265f/180.0f)/100.0f;
    float yaw=dx*k*aim_sign_x, pitch=dy*k*aim_sign_y;
    if (aim_invert_x && *aim_invert_x) yaw=-yaw;
    if (aim_invert_y && *aim_invert_y) pitch=-pitch;
    uint64_t old=__atomic_load_n(&aim_box.delta,__ATOMIC_RELAXED), next;
    do {
        float f[2];
        memcpy(f,&old,sizeof(f));
        f[0]=fmaxf(-0.75f,fminf(0.75f,f[0]+yaw));
        f[1]=fmaxf(-0.75f,fminf(0.75f,f[1]+pitch));
        memcpy(&next,f,sizeof(next));
    } while (!__atomic_compare_exchange_n(&aim_box.delta,&old,next,true,__ATOMIC_RELAXED,__ATOMIC_RELAXED));
}

/* issn: the mouse, as in axylpup's build: while it holds the camera (relative mode: a click in
 * the window or Y), its motion turns the camera through the hook above (the right stick when the
 * hook is not there, ISSN_MOUSE_SPEED px/s = full tilt) and its buttons are R2 (left, shoot) and
 * L2 (right, aim); the click that takes the camera does not shoot. While the pad is tilted the
 * mouse aims the spray can (synth_motion). - / = scale the sensitivity. */
static void sample_mouse(PadData *d) {
    if (!SDL_WasInit(SDL_INIT_VIDEO)) return;
    SDL_Window *window=SDL_GetMouseFocus();
    float dx=0.0f, dy=0.0f;
    const SDL_MouseButtonFlags buttons=SDL_GetRelativeMouseState(&dx,&dy);
    mouse_aim_tick(d->timestamp);
    const bool relative=window && SDL_GetWindowRelativeMouseMode(window);
    static bool was_relative, wait_release;
    if (relative && !was_relative) wait_release=buttons!=0;
    was_relative=relative;
    if (!relative) { mouse_dx=mouse_dy=0.0f; return; }
    if (wait_release && !buttons) wait_release=false;
    if (!wait_release) {
        if (buttons & SDL_BUTTON_LMASK) { d->buttons|=BTN_R2; d->r2=255; }
        if (buttons & SDL_BUTTON_RMASK) { d->buttons|=BTN_L2; d->l2=255; }
    }
    {   /* - / = : sensitivity */
        const bool *k=SDL_GetKeyboardState(NULL);
        static bool minus_was, equals_was;
        const bool minus=k && k[SDL_SCANCODE_MINUS], equals=k && k[SDL_SCANCODE_EQUALS];
        if ((minus && !minus_was) || (equals && !equals_was)) {
            if (pad_tilted) {
                spray_sens=fmaxf(0.25f,fminf(25.0f,spray_sens*(minus ? 0.8f : 1.25f)));
                spray_stick=fmaxf(3.0f,fminf(360.0f,spray_stick*(minus ? 0.8f : 1.25f)));
                printf("Spray can: %.2f degrees per 100 px, right stick %.0f degrees a second (- / =)\n",
                       spray_sens,spray_stick);
            } else {
                aim_sens=fmaxf(0.5f,fminf(100.0f,aim_sens*(minus ? 0.8f : 1.25f)));
                printf("Mouse aim: %.2f degrees per 100 px (- / =)\n",aim_sens);
            }
            fflush(stdout);
        }
        minus_was=minus; equals_was=equals;
    }
    mouse_dx+=dx; mouse_dy+=dy;
    if (pad_tilted) return; /* the spray can takes the mouse */
    if (aim_attached && aim_live) { mouse_aim_motion(dx,dy,d->timestamp); return; }
    static float speed;
    if (speed==0.0f) { const char *e=getenv("ISSN_MOUSE_SPEED"); speed=e && atof(e)>50.0 ? (float)atof(e) : 900.0f; }
    static uint64_t last;
    const uint64_t now=d->timestamp;
    const float dt=last && now>last ? (float)(now-last)/1e6f : 0.016f;
    last=now;
    if (dt<=0.0f || dt>0.25f) return;
    const float sx=dx/dt/speed, sy=dy/dt/speed;
    if (sx!=0.0f) d->right_x=(uint8_t)lroundf(fmaxf(0.0f,fminf(255.0f,128.0f+127.0f*fmaxf(-1.0f,fminf(1.0f,sx)))));
    if (sy!=0.0f) d->right_y=(uint8_t)lroundf(fmaxf(0.0f,fminf(255.0f,128.0f+127.0f*fmaxf(-1.0f,fminf(1.0f,sy)))));
}

/* issn: synthetic motion sensor for pads without one and the keyboard, as axylpup's build models
 * it (src/input/controller.cpp SyntheticMotionLocked): the pad's pose is yaw * pitch * roll; held
 * tilted it rolls 90 degrees (eased over 80 ms), and the accelerometer reads gravity in the pad's
 * frame from that same pose (the game's own formula), with a 5 Hz rattle on top; the gyroscope
 * reads the pose's change. Aim while tilted: the mouse (spray_sens degrees per 100 px) or the right
 * stick (spray_stick degrees a second at full push, squared). */
static struct {
    float yaw, pitch, phi;
    float last_q[4];
    uint64_t last_t, t0, rattle_until, z_down;
    int pose_toggle, z_was;
} synth={.last_q={0.0f,0.0f,0.0f,1.0f}};
static void q_mul(const float a[4], const float b[4], float out[4]) { /* Hamilton product, x y z w */
    const float r[4]={a[3]*b[0]+a[0]*b[3]+a[1]*b[2]-a[2]*b[1], a[3]*b[1]+a[1]*b[3]+a[2]*b[0]-a[0]*b[2],
                      a[3]*b[2]+a[2]*b[3]+a[0]*b[1]-a[1]*b[0], a[3]*b[3]-a[0]*b[0]-a[1]*b[1]-a[2]*b[2]};
    memcpy(out,r,sizeof(r));
}
static void q_axis(float x, float y, float z, float angle, float out[4]) {
    const float s=sinf(angle*0.5f);
    out[0]=x*s; out[1]=y*s; out[2]=z*s; out[3]=cosf(angle*0.5f);
}
/* issn: the right stick as the spray can's aim: a radial dead zone of 0.15, the rest rescaled to
 * 0..1 and squared (fine control near the center, the full rate at the edge). */
static void spray_stick_aim(uint8_t rx, uint8_t ry, float *ax, float *ay) {
    const float x=((float)rx-128.0f)/127.0f, y=((float)ry-128.0f)/127.0f;
    const float m=sqrtf(x*x+y*y);
    *ax=*ay=0.0f;
    if (m<=0.15f) return;
    const float t=fminf(1.0f,(m-0.15f)/0.85f), scale=t*t/m;
    *ax=x*scale; *ay=y*scale;
}
static void synth_motion(PadData *d, bool tilt, bool rattle, float aim_x, float aim_y) {
    const float pi=3.14159265f;
    const uint64_t now=d->timestamp;
    if (!synth.t0) synth.t0=now;
    const float dt=synth.last_t && now>synth.last_t ? fminf((float)(now-synth.last_t)/1e6f,0.1f) : 0.0f;
    if (tilt) {
        static int sens_read;
        if (!sens_read) {
            const char *e=getenv("ISSN_SPRAY_MOUSE");
            if (e && atof(e)>0.0) spray_sens=fmaxf(0.25f,fminf(25.0f,(float)atof(e)));
            e=getenv("ISSN_SPRAY_STICK");
            if (e && atof(e)>0.0) spray_stick=fmaxf(3.0f,fminf(360.0f,(float)atof(e)));
            sens_read=1;
        }
        const float k=spray_sens*pi/180.0f/100.0f, rate=spray_stick*pi/180.0f;
        synth.yaw-=k*mouse_dx+rate*aim_x*dt;
        synth.pitch-=k*mouse_dy+rate*aim_y*dt;
        synth.pitch=fmaxf(-80.0f*pi/180.0f,fminf(80.0f*pi/180.0f,synth.pitch));
        if (synth.yaw>pi) synth.yaw-=2.0f*pi; else if (synth.yaw<-pi) synth.yaw+=2.0f*pi;
    } else {
        const float ease=1.0f-expf(-dt/0.15f); /* back to the pad held flat */
        synth.yaw-=synth.yaw*ease; synth.pitch-=synth.pitch*ease;
    }
    const float target=tilt ? pi*0.5f : 0.0f;
    synth.phi+=(target-synth.phi)*(1.0f-expf(-dt/0.08f));
    float qy[4], qp[4], qr[4], q[4];
    q_axis(0.0f,1.0f,0.0f,synth.yaw,qy); q_axis(1.0f,0.0f,0.0f,synth.pitch,qp); q_axis(0.0f,0.0f,1.0f,synth.phi,qr);
    q_mul(qy,qp,q); q_mul(q,qr,q);
    const float gx=2*q[0]*q[1]+2*q[3]*q[2], gy=1-2*q[0]*q[0]-2*q[2]*q[2], gz=2*q[1]*q[2]-2*q[3]*q[0];
    /* The rattle: gravity's magnitude swings 1 - 3.5 g at 5 Hz along the pose's own up (the game's
     * shake test wants an axis above 2.5 g while rolled > 30 degrees). It used to swing -1.5 - 3.5
     * g: below zero the gravity vector pointed the other way, the game read the pad upside down
     * and the spray can's aim turned left-right and up-down while X was held (tester 10-24). */
    const float shake=rattle ? 1.25f+1.25f*sinf(2.0f*pi*5.0f*(float)(now-synth.t0)/1e6f) : 0.0f;
    const float g=STANDARD_GRAVITY*(1.0f+shake)*accel_scale();
    d->acceleration[0]=gx*g; d->acceleration[1]=gy*g; d->acceleration[2]=gz*g;
    float w[3]={0.0f,0.0f,0.0f};
    if (dt>0.0f) {
        const float conj[4]={-synth.last_q[0],-synth.last_q[1],-synth.last_q[2],synth.last_q[3]};
        float dq[4];
        q_mul(conj,q,dq);
        if (dq[3]<0.0f) for (int i=0;i<4;++i) dq[i]=-dq[i];
        for (int i=0;i<3;++i) w[i]=2.0f*dq[i]/dt;
    }
    memcpy(d->angular_velocity,w,sizeof(w));
    memcpy(d->orientation,q,sizeof(q));
    memcpy(synth.last_q,q,sizeof(q));
    synth.last_t=now;
}

static void sample_host(PadData *d) {
    memset(d,0,sizeof(*d));
    d->left_x=d->left_y=d->right_x=d->right_y=128;
    d->orientation[3]=1.0f;
    d->connected=1; d->connected_count=connected_count ? connected_count : 1;
    d->timestamp=now_us();
    SDL_Gamepad *g=current_gamepad();
    sample_motion(d,g);
    key_touch_held=0;
    if (issngpu_overlay_captures_input()) return; /* settings menu open: neutral input */
    const bool *k=SDL_WasInit(SDL_INIT_VIDEO) ? SDL_GetKeyboardState(NULL) : NULL;
    {   /* issn: F10 asks for a snapshot of the guest threads (see runtime_snapshot_request). */
        static bool f10_was_down;
        const bool f10=k && k[SDL_SCANCODE_F10];
        if (f10 && !f10_was_down) {
            runtime_snapshot_request=1;
            printf("Runtime: F10: thread snapshot requested\n");
            fflush(stdout);
        }
        f10_was_down=f10;
    }
    load_button_maps();
    if (g) {
        for (size_t i=0;i<pad_map.count;++i)
            if (SDL_GetGamepadButton(g,(SDL_GamepadButton)pad_map.entries[i].source)) d->buttons|=pad_map.entries[i].ps;
        const uint32_t mapped=d->buttons;
        d->left_x=axis(SDL_GetGamepadAxis(g,SDL_GAMEPAD_AXIS_LEFTX)); d->left_y=axis(SDL_GetGamepadAxis(g,SDL_GAMEPAD_AXIS_LEFTY));
        d->right_x=axis(SDL_GetGamepadAxis(g,SDL_GAMEPAD_AXIS_RIGHTX)); d->right_y=axis(SDL_GetGamepadAxis(g,SDL_GAMEPAD_AXIS_RIGHTY));
        d->l2=trigger(SDL_GetGamepadAxis(g,SDL_GAMEPAD_AXIS_LEFT_TRIGGER)); d->r2=trigger(SDL_GetGamepadAxis(g,SDL_GAMEPAD_AXIS_RIGHT_TRIGGER));
        if (d->l2>30) d->buttons|=BTN_L2;
        if (d->r2>30) d->buttons|=BTN_R2;
        if (mapped & BTN_L2) d->l2=255;
        if (mapped & BTN_R2) d->r2=255;
        if (SDL_GetNumGamepadTouchpads(g)>0) {
            const int fingers=SDL_GetNumGamepadTouchpadFingers(g,0);
            for (int finger=0;finger<fingers && d->touch_count<2;++finger) {
                bool down=false;
                float x=0, y=0;
                if (SDL_GetGamepadTouchpadFinger(g,0,finger,&down,&x,&y,NULL) && down) {
                    d->touches[d->touch_count++]=(PadTouch){.x=touch_axis(x,1919),
                        .y=touch_axis(y,942),.id=(uint8_t)finger};
                }
            }
        }
    }
    /* The keyboard works with or without a gamepad: its buttons add to the pad's, and a stick
     * follows the keys only while one of them is held. */
    if (!k) return;
    for (size_t i=0;i<key_map.count;++i) if (k[key_map.entries[i].source]) d->buttons|=key_map.entries[i].ps;
    if (d->buttons & BTN_L2) d->l2=255;
    if (d->buttons & BTN_R2) d->r2=255;
    key_axis(&d->left_x,k[SDL_SCANCODE_A],k[SDL_SCANCODE_D]);
    key_axis(&d->left_y,k[SDL_SCANCODE_W],k[SDL_SCANCODE_S]);
    key_axis(&d->right_x,k[SDL_SCANCODE_J],k[SDL_SCANCODE_L]);
    key_axis(&d->right_y,k[SDL_SCANCODE_I],k[SDL_SCANCODE_K]);
    /* issn: swipes on the keyboard: the right touchpad key (G) held and W/A/S/D (or an arrow)
     * pressed is a swipe that way (play_swipe; a key kept down holds the finger at the end: "swipe
     * up and hold", the left hand alone, so the mouse can hit meanwhile); those keys then leave
     * the left stick and the d-pad alone. Second Son: the door, the wreckage, the Mobile Command
     * Center's core. */
    for (size_t i=0;i<key_map.count;++i)
        if (key_map.entries[i].ps==CLICK_RIGHT && k[key_map.entries[i].source]) key_touch_held=1;
    if (key_touch_held) {
        const bool up=k[SDL_SCANCODE_UP] || k[SDL_SCANCODE_W], down=k[SDL_SCANCODE_DOWN] || k[SDL_SCANCODE_S],
                   left=k[SDL_SCANCODE_LEFT] || k[SDL_SCANCODE_A], right=k[SDL_SCANCODE_RIGHT] || k[SDL_SCANCODE_D];
        if (up || down || left || right) {
            d->buttons&=~(BTN_UP|BTN_DOWN|BTN_LEFT|BTN_RIGHT);
            d->left_x=d->left_y=128;
            key_axis(&d->right_x,left,right);
            key_axis(&d->right_y,up,down);
        }
    }
    sample_mouse(d);
}

/* ISSN_PAD_FILE=<file>: scripted input for automated runs. The file holds whitespace-separated
 * tokens, re-read when it changes: button names (cross circle square triangle l1 r1 l2 r2 l3 r3
 * options touchpad touchpad_left touchpad_right up down left right) are held while listed;
 * touchpad is the left side; lx= ly= rx= ry= (0..255) override the sticks. An empty file
 * releases everything. */
static struct { uint32_t buttons; int stick[4]; } injected={0,{-1,-1,-1,-1}};
static int replay_armed;      /* 1 while a ISSN_PAD_REPLAY recording plays, 2 once it ended */
static uint64_t replay_start; /* 0: (re)start at the next sample */
static void read_inject(void) {
    static const char *path; static int checked; static uint64_t last_check; static struct timespec mtime;
    if (!checked) { path=getenv("ISSN_PAD_FILE"); checked=1; }
    if (!path || !*path) return;
    uint64_t now=now_us();
    if (now-last_check<20000) return;
    last_check=now;
    struct stat st;
    if (stat(path,&st)!=0) return;
#ifdef _WIN32
    /* Whole-second mtimes: the size tells two edits within a second apart. */
    if (st.st_mtime==mtime.tv_sec && st.st_size==mtime.tv_nsec) return;
    mtime=(struct timespec){st.st_mtime,(long)st.st_size};
#else
    if (st.st_mtim.tv_sec==mtime.tv_sec && st.st_mtim.tv_nsec==mtime.tv_nsec) return;
    mtime=st.st_mtim;
#endif
    FILE *f=fopen(path,"r");
    if (!f) return;
    static const char *sticks[]={"lx=","ly=","rx=","ry="};
    injected.buttons=0;
    for (int i=0;i<4;++i) injected.stick[i]=-1;
    char token[64];
    while (fscanf(f,"%63s",token)==1) {
        if (!strcmp(token,"replay") && replay_armed!=1) { replay_armed=1; replay_start=0; } /* ISSN_PAD_REPLAY */
        for (size_t i=0;i<sizeof(ps_names)/sizeof(*ps_names);++i) if (!strcmp(token,ps_names[i].name)) injected.buttons|=ps_names[i].ps;
        for (int i=0;i<4;++i) if (!strncmp(token,sticks[i],3)) { int v=atoi(token+3); injected.stick[i]=v<0 ? 0 : v>255 ? 255 : v; }
    }
    fclose(f);
    printf("Runtime: pad file: buttons 0x%x sticks %d %d %d %d\n",injected.buttons,
           injected.stick[0],injected.stick[1],injected.stick[2],injected.stick[3]);
}
/* ISSN_PAD_RECORD=<file>: F9 starts and stops recording the pad state (gamepad or keyboard) with
 * the time since F9; ISSN_PAD_REPLAY=<file> plays such a recording back, started by the token
 * "replay" in ISSN_PAD_FILE (scripted tests repeat a route the player ran once). Lines: ms buttons
 * lx ly rx ry l2 r2, written when the state changes. */
typedef struct { uint32_t ms, buttons; uint8_t axes[4], l2, r2; } PadSample;
static FILE *record_file;
static uint64_t record_start;
static PadSample record_last;
static void record_sample(const PadData *d) {
    static const char *path; static int checked, f9_was_down;
    if (!checked) { path=getenv("ISSN_PAD_RECORD"); checked=1; }
    if (!path || !*path || !sdl_ready) return;
    const bool *k=SDL_GetKeyboardState(NULL);
    const int f9=k && k[SDL_SCANCODE_F9];
    if (f9 && !f9_was_down) {
        if (record_file) {
            fclose(record_file); record_file=NULL;
            printf("Runtime: pad recording stopped (%s)\n",path);
        } else if ((record_file=fopen(path,"w"))) {
            record_start=now_us();
            memset(&record_last,0xff,sizeof(record_last));
            printf("Runtime: pad recording started (%s, F9 stops)\n",path);
        }
    }
    f9_was_down=f9;
    if (!record_file) return;
    PadSample s={(uint32_t)((now_us()-record_start)/1000),d->buttons,
                 {d->left_x,d->left_y,d->right_x,d->right_y},d->l2,d->r2};
    if (s.buttons==record_last.buttons && !memcmp(s.axes,record_last.axes,4) &&
        s.l2==record_last.l2 && s.r2==record_last.r2) return;
    record_last=s;
    fprintf(record_file,"%u %u %u %u %u %u %u %u\n",s.ms,s.buttons,s.axes[0],s.axes[1],s.axes[2],
            s.axes[3],s.l2,s.r2);
    fflush(record_file);
}
static PadSample *replay; static size_t replay_count, replay_next;
static void replay_sample(PadData *d) {
    if (!replay_armed) return;
    if (!replay_start) {
        static int loaded;
        if (!loaded) {
            loaded=1;
            const char *path=getenv("ISSN_PAD_REPLAY");
            FILE *f=path ? fopen(path,"r") : NULL;
            PadSample s; unsigned v[8]; size_t cap=0;
            while (f && fscanf(f,"%u %u %u %u %u %u %u %u",&v[0],&v[1],&v[2],&v[3],&v[4],&v[5],&v[6],&v[7])==8) {
                s=(PadSample){v[0],v[1],{(uint8_t)v[2],(uint8_t)v[3],(uint8_t)v[4],(uint8_t)v[5]},(uint8_t)v[6],(uint8_t)v[7]};
                if (replay_count==cap && !(replay=realloc(replay,(cap=cap ? cap*2 : 1024)*sizeof(*replay)))) break;
                replay[replay_count++]=s;
            }
            if (f) fclose(f);
            printf("Runtime: pad replay of %zu samples from %s\n",replay_count,path ? path : "(unset)");
        }
        replay_start=now_us();
        replay_next=0;
    }
    const uint32_t ms=(uint32_t)((now_us()-replay_start)/1000);
    while (replay_next<replay_count && replay[replay_next].ms<=ms) ++replay_next;
    if (!replay_next) return;
    if (replay_next==replay_count && ms>replay[replay_count-1].ms+500) {
        if (replay_armed==1) { puts("Runtime: pad replay finished"); replay_armed=2; }
        return;
    }
    const PadSample *s=&replay[replay_next-1];
    d->buttons=s->buttons;
    d->left_x=s->axes[0]; d->left_y=s->axes[1]; d->right_x=s->axes[2]; d->right_y=s->axes[3];
    d->l2=s->l2; d->r2=s->r2;
}
/* After the menu or the text dialog closes, buttons still held (the Cross that accepted a
 * name) stay hidden until released: the game would take them as a new press. */
static int hold_after_capture;
static void sample(PadData *d) {
    sample_host(d);
    if (issngpu_overlay_captures_input()) { hold_after_capture=1; click.side=-1; return; }
    record_sample(d); /* the click sources as they are: a replay plays them again */
    read_inject();
    replay_sample(d);
    d->buttons|=injected.buttons;
    if (injected.buttons & BTN_L2) d->l2=255;
    if (injected.buttons & BTN_R2) d->r2=255;
    uint8_t *axes[4]={&d->left_x,&d->left_y,&d->right_x,&d->right_y};
    for (int i=0;i<4;++i) if (injected.stick[i]>=0) *axes[i]=(uint8_t)injected.stick[i];
    /* issn: the motion sensor of a pad without one, and of the keyboard (synth_motion). The pad
     * is tilted while Z is held, while L3+R3 (pads without sensors) or H has toggled it on, and
     * while it rattles; it rattles while X (a SHAKE source) is held, for 600 ms after a quick tap
     * of Z, and on L1 while the pose is toggled on. A pad with real sensors keeps them. */
    const bool chord=pad_emulation() && (d->buttons & BTN_L3) && (d->buttons & BTN_R3);
    if (chord || (d->buttons & SPRAY_POSE)) {
        if (!spray_chord_held) {
            synth.pose_toggle=!synth.pose_toggle;
            printf("Runtime: pad tilted (spray can) %s\n",synth.pose_toggle ? "on" : "off");
        }
        spray_chord_held=1;
        d->buttons&=~(BTN_L3|BTN_R3|SPRAY_POSE);
    } else {
        spray_chord_held=0;
    }
    const bool *keys=SDL_WasInit(SDL_INIT_VIDEO) && !issngpu_overlay_captures_input() ? SDL_GetKeyboardState(NULL) : NULL;
    const bool z=keys && keys[SDL_SCANCODE_Z];
    if (z && !synth.z_was) synth.z_down=d->timestamp;
    if (!z && synth.z_was && d->timestamp-synth.z_down<250000) synth.rattle_until=d->timestamp+600000;
    synth.z_was=z;
    if (synth.pose_toggle && (d->buttons & BTN_L1)) d->buttons=(d->buttons&~BTN_L1)|SHAKE;
    const bool rattle=(d->buttons & SHAKE)!=0 || d->timestamp<synth.rattle_until;
    d->buttons&=~SHAKE; /* host-only: the game sees the motion, not a button */
    const bool tilt=z || synth.pose_toggle || rattle;
    const bool real_sensors=gamepad && (sensors & SENSOR_ACCEL) && motion_enabled;
    pad_tilted=tilt;
    if (!real_sensors || tilt) { /* Z / X / L3+R3 also drive a pad that has sensors */
        const bool stick_aims=tilt && !(d->buttons & CLICK_RIGHT);
        float ax=0.0f, ay=0.0f;
        if (stick_aims) {
            spray_stick_aim(d->right_x,d->right_y,&ax,&ay);
            d->right_x=d->right_y=128; /* the stick turns the can, not the game's camera too */
        }
        synth_motion(d,tilt,rattle,ax,ay);
        static int was_tilted;
        if (tilt!=was_tilted) printf("Runtime: pad %s\n",tilt ? "tilted (spray can)" : "level");
        was_tilted=tilt;
    }
    mouse_dx=mouse_dy=0.0f;
    if (pad_emulation() || key_touch_held) play_swipe(d); /* the keyboard has no touch surface either */
    play_click(d);
    if (hold_after_capture) {
        if (d->buttons) d->buttons=0;
        else hold_after_capture=0;
    }
}

static ABI int32_t pad_init(void) { pthread_mutex_lock(&lock); initialized=1; pthread_mutex_unlock(&lock); return 0; }
static ABI int32_t pad_open(int32_t user, int32_t type, int32_t index, const void *param) {
    (void)param;
    if (!initialized) return ERR_NOT_INITIALIZED;
    if (user!=1) return ERR_INVALID_ARG;
    if (type!=0 && type!=2) return ERR_INVALID_ARG; /* standard / special port */
    if (index) return ERR_INVALID_ARG;
    pthread_mutex_lock(&lock);
    int already=opened; opened=1;
    pthread_mutex_unlock(&lock);
    if (already) return ERR_ALREADY_OPENED;
    puts("Runtime: pad opened for user 1 (SDL gamepad or keyboard)");
    return PAD_HANDLE;
}
static ABI int32_t pad_close(int32_t handle) {
    if (handle!=PAD_HANDLE || !opened) return ERR_INVALID_HANDLE;
    opened=0; return 0;
}
static ABI int32_t pad_read_state(int32_t handle, PadData *data) {
    if (handle!=PAD_HANDLE || !opened) return ERR_INVALID_HANDLE;
    if (!data) return ERR_INVALID_ARG;
    pthread_mutex_lock(&lock);
    sample(data); ++reads;
    pthread_mutex_unlock(&lock);
    return 0;
}
/* Buffered read: the port samples once per call, so one entry is returned. */
static ABI int32_t pad_read(int32_t handle, PadData *data, int32_t count) {
    if (handle!=PAD_HANDLE || !opened) return ERR_INVALID_HANDLE;
    if (!data || count<1 || count>64) return ERR_INVALID_ARG;
    pad_read_state(handle,data);
    return 1;
}
static ABI int32_t pad_info(int32_t handle, ControllerInfo *info) {
    if (handle!=PAD_HANDLE || !opened) return ERR_INVALID_HANDLE;
    if (!info) return ERR_INVALID_ARG;
    memset(info,0,sizeof(*info));
    info->pixel_density=44.86f; info->resolution_x=1920; info->resolution_y=943;
    info->dead_zone_left=info->dead_zone_right=2;
    info->connection_type=0; info->connected=1; info->device_class=0;
    pthread_mutex_lock(&lock);
    current_gamepad();
    info->connected_count=connected_count ? connected_count : 1;
    pthread_mutex_unlock(&lock);
    return 0;
}
static ABI int32_t pad_vibration(int32_t handle, const uint8_t *param) {
    if (handle!=PAD_HANDLE || !opened) return ERR_INVALID_HANDLE;
    if (!param) return ERR_INVALID_ARG;
    pthread_mutex_lock(&lock);
    SDL_Gamepad *g=current_gamepad();
    if (g) SDL_RumbleGamepad(g,(uint16_t)(param[0]*257),(uint16_t)(param[1]*257),1000);
    pthread_mutex_unlock(&lock);
    return 0;
}
static ABI int32_t pad_ok_handle(int32_t handle) { return handle==PAD_HANDLE && opened ? 0 : ERR_INVALID_HANDLE; }
static ABI int32_t pad_ok_handle_flag(int32_t handle, uint8_t flag) { (void)flag; return pad_ok_handle(handle); }

static ABI int32_t pad_light_bar(int32_t handle, const uint8_t *rgb) {
    if (handle!=PAD_HANDLE || !opened) return ERR_INVALID_HANDLE;
    if (!rgb) return ERR_INVALID_ARG;
    pthread_mutex_lock(&lock);
    memcpy(light.rgb,rgb,3); light.set=1;
    apply_light(gamepad);
    pthread_mutex_unlock(&lock);
    return 0;
}
/* Back to the PS4's color for user 1 (blue). */
static ABI int32_t pad_reset_light_bar(int32_t handle) {
    static const uint8_t user1[3]={0,0,255};
    return pad_light_bar(handle,user1);
}
/* issn: what the game asks of the motion sensors (the spray can), logged when it changes. */
static void log_motion_call(const char *what, int value) {
    static int last[3]={-1,-1,-1};
    static unsigned resets;
    const int slot=!strcmp(what,"motion sensors") ? 0 : !strcmp(what,"tilt correction") ? 1 :
                   !strcmp(what,"angular velocity deadband") ? 2 : -1;
    if (slot<0) {
        if (++resets<=20) printf("Runtime: pad: game reset the orientation (%u)%s\n",resets,
                                  synth.pose_toggle ? " with the pad tilted" : "");
        return;
    }
    if (last[slot]==value) return;
    last[slot]=value;
    printf("Runtime: pad: game set %s %s%s\n",what,value ? "on" : "off",synth.pose_toggle ? " (pad tilted)" : "");
}
static ABI int32_t pad_tilt_correction(int32_t handle, uint8_t enable) {
    log_motion_call("tilt correction",enable!=0);
    return pad_ok_handle(handle);
}
static ABI int32_t pad_deadband(int32_t handle, uint8_t enable) {
    log_motion_call("angular velocity deadband",enable!=0);
    return pad_ok_handle(handle);
}
static ABI int32_t pad_motion_sensor_state(int32_t handle, uint8_t enable) {
    if (handle!=PAD_HANDLE || !opened) return ERR_INVALID_HANDLE;
    log_motion_call("motion sensors",enable!=0);
    pthread_mutex_lock(&lock);
    motion_enabled=enable!=0;
    pthread_mutex_unlock(&lock);
    return 0;
}
static ABI int32_t pad_reset_orientation(int32_t handle) {
    if (handle!=PAD_HANDLE || !opened) return ERR_INVALID_HANDLE;
    log_motion_call("reset",0);
    pthread_mutex_lock(&lock);
    orientation_q[0]=orientation_q[1]=orientation_q[2]=0.0f; orientation_q[3]=1.0f;
    orientation_time=0;
    pthread_mutex_unlock(&lock);
    return 0;
}
static ABI int32_t pad_get_handle(int32_t user, int32_t type, int32_t index) {
    if (!initialized) return ERR_NOT_INITIALIZED;
    if (user!=1 || (type!=0 && type!=2) || index) return ERR_INVALID_ARG;
    return opened ? PAD_HANDLE : ERR_INVALID_HANDLE;
}
static ABI int32_t pad_light_bar_blinking(int32_t handle, const void *param) { (void)param; return pad_ok_handle(handle); }
static const RuntimeExport exports[]={
    {"scePadSetLightBar",pad_light_bar}, {"scePadResetLightBar",pad_reset_light_bar},
    {"scePadSetLightBarBlinking",pad_light_bar_blinking}, {"scePadSetLightBarBaseBrightness",pad_ok_handle_flag},
    {"scePadGetHandle",pad_get_handle},
    {"scePadInit",pad_init}, {"scePadOpen",pad_open}, {"scePadClose",pad_close},
    {"scePadReadState",pad_read_state}, {"scePadRead",pad_read},
    {"scePadGetControllerInformation",pad_info}, {"scePadSetVibration",pad_vibration},
    {"scePadResetOrientation",pad_reset_orientation},
    {"scePadSetAngularVelocityDeadbandState",pad_deadband}, {"scePadSetTiltCorrectionState",pad_tilt_correction},
    {"scePadSetMotionSensorState",pad_motion_sensor_state},
};
uintptr_t runtime_pad_resolve(const char *name) { return RUNTIME_LOOKUP(exports,name); }
void runtime_pad_report(void) { printf("Runtime: pad reads=%zu, gamepad=%s\n",reads,gamepad ? SDL_GetGamepadName(gamepad) : "none"); }

/* probe --gamepad: runs before any game state exists, so it uses its own SDL gamepad and leaves
 * the lock and statics above alone. run.py parses this line; keep the format. */
static const char *label_name(SDL_GamepadButtonLabel label) {
    switch (label) {
    case SDL_GAMEPAD_BUTTON_LABEL_A: return "a";
    case SDL_GAMEPAD_BUTTON_LABEL_B: return "b";
    case SDL_GAMEPAD_BUTTON_LABEL_X: return "x";
    case SDL_GAMEPAD_BUTTON_LABEL_Y: return "y";
    case SDL_GAMEPAD_BUTTON_LABEL_CROSS: return "cross";
    case SDL_GAMEPAD_BUTTON_LABEL_CIRCLE: return "circle";
    case SDL_GAMEPAD_BUTTON_LABEL_SQUARE: return "square";
    case SDL_GAMEPAD_BUTTON_LABEL_TRIANGLE: return "triangle";
    default: return "unknown";
    }
}
int runtime_pad_describe(void) {
    SDL_Gamepad *g=NULL;
    const bool ready=SDL_InitSubSystem(SDL_INIT_GAMEPAD);
    /* No event loop yet, so pads show up only after a pump: poll for about 300 ms. */
    for (int waited=0; ready && !g && waited<30; ++waited) {
        SDL_PumpEvents();
        int count=0;
        SDL_JoystickID *ids=SDL_GetGamepads(&count);
        if (ids && count>0) g=SDL_OpenGamepad(ids[0]);
        SDL_free(ids);
        if (!g) SDL_Delay(10);
    }
    if (!g) puts("Gamepad: none");
    else {
        const char *type=SDL_GetGamepadStringForType(SDL_GetGamepadType(g));
        const char *name=SDL_GetGamepadName(g);
        printf("Gamepad: type=%s south=%s east=%s west=%s north=%s name=%s\n",
               type ? type : "unknown",
               label_name(SDL_GetGamepadButtonLabel(g,SDL_GAMEPAD_BUTTON_SOUTH)),
               label_name(SDL_GetGamepadButtonLabel(g,SDL_GAMEPAD_BUTTON_EAST)),
               label_name(SDL_GetGamepadButtonLabel(g,SDL_GAMEPAD_BUTTON_WEST)),
               label_name(SDL_GetGamepadButtonLabel(g,SDL_GAMEPAD_BUTTON_NORTH)),
               name ? name : "unknown");
        SDL_CloseGamepad(g);
    }
    if (ready) SDL_QuitSubSystem(SDL_INIT_GAMEPAD);
    return g!=NULL;
}
