// Headless doomgeneric backend: no window, no sound, no real clock.
//
// Time is virtual: DG_SleepMs() advances the clock instead of sleeping, so the
// game runs as fast as the CPU allows and every run is deterministic. Input
// comes from a script of timed key events, and frames are written as PPM
// files. When the run ends, the player's state is printed as one JSON line.
//
// The game runs in singletics mode (as for -timedemo): every frame builds
// the input for exactly one tic and runs it, so key events keyed to gametic
// land on exactly that tic. Screen wipes draw extra frames while gametic
// stands still, so frames are counted separately, one per 1/35 s of video.
//
// Extra command-line options (besides the usual Doom ones like -iwad, -warp):
//   -keys FILE      key script, one "GAMETIC PRESSED KEYCODE" event per line
//   -frames DIR     write DIR/frame_FRAMENO_GAMETIC.ppm (320x200) every
//                   -every frames
//   -every N        frame interval (default 2, i.e. 17.5 fps)
//   -shot FILE      write the final frame to FILE at full 640x400
//   -maxtics N      stop once gametic reaches N (35 tics = 1 second;
//                   default 30 seconds)

#include "doomgeneric.h"
#include "doomkeys.h"
#include "d_event.h"
#include "doomstat.h"
#include "d_player.h"
#include "m_argv.h"

#include <stdio.h>
#include <stdlib.h>

#define MAX_EVENTS 65536

typedef struct
{
    int tic;
    int pressed;
    unsigned char key;
} key_event_t;

// Start above zero: I_GetTime() treats a base time of 0 as "not set yet".
#define CLOCK_START_MS 1000

static uint32_t clock_ms = CLOCK_START_MS;

static key_event_t events[MAX_EVENTS];
static int num_events = 0;
static int next_event = 0;

static const char *frames_dir = NULL;
static const char *shot_path = NULL;
static int frame_every = 2;
static int max_tics = 35 * 30;
static int frame_no = 0;

static const char *ArgValue(const char *name)
{
    int p = M_CheckParmWithArgs((char *) name, 1);

    return p ? myargv[p + 1] : NULL;
}

static void LoadKeyScript(const char *path)
{
    FILE *f = fopen(path, "r");
    char line[256];

    if (f == NULL)
    {
        fprintf(stderr, "headless: cannot open key script %s\n", path);
        exit(1);
    }

    while (fgets(line, sizeof(line), f) != NULL && num_events < MAX_EVENTS)
    {
        int tic, pressed, key;

        if (sscanf(line, "%d %d %d", &tic, &pressed, &key) == 3)
        {
            events[num_events].tic = tic;
            events[num_events].pressed = pressed;
            events[num_events].key = (unsigned char) key;
            num_events++;
        }
    }

    fclose(f);
}

// Pixels are 0x00RRGGBB; step 2 samples the 640x400 buffer down to Doom's
// native 320x200 (the buffer is a 2x pixel-doubled copy of it).
static void WritePPM(const char *path, int step)
{
    int w = DOOMGENERIC_RESX / step;
    int h = DOOMGENERIC_RESY / step;
    unsigned char *row = malloc(w * 3);
    FILE *f = fopen(path, "wb");
    int x, y;

    if (f == NULL)
    {
        fprintf(stderr, "headless: cannot write %s\n", path);
        exit(1);
    }

    fprintf(f, "P6\n%d %d\n255\n", w, h);

    for (y = 0; y < h; y++)
    {
        const uint32_t *src = &DG_ScreenBuffer[y * step * DOOMGENERIC_RESX];

        for (x = 0; x < w; x++)
        {
            uint32_t p = src[x * step];

            row[x * 3 + 0] = (p >> 16) & 0xff;
            row[x * 3 + 1] = (p >> 8) & 0xff;
            row[x * 3 + 2] = p & 0xff;
        }

        fwrite(row, 1, w * 3, f);
    }

    fclose(f);
    free(row);
}

static const char *GameStateName(void)
{
    switch (gamestate)
    {
        case GS_LEVEL:        return "level";
        case GS_INTERMISSION: return "intermission";
        case GS_FINALE:       return "finale";
        case GS_DEMOSCREEN:   return "demoscreen";
        default:              return "unknown";
    }
}

static void PrintStatusAndExit(void)
{
    player_t *p = &players[consoleplayer];
    static const char *weapons[] = {
        "fist", "pistol", "shotgun", "chaingun", "rocket launcher",
        "plasma rifle", "bfg9000", "chainsaw", "super shotgun"
    };
    const char *weapon = p->readyweapon < NUMWEAPONS
                       ? weapons[p->readyweapon] : "none";
    int ammo = -1;

    if (p->mo != NULL && weaponinfo[p->readyweapon].ammo != am_noammo)
    {
        ammo = p->ammo[weaponinfo[p->readyweapon].ammo];
    }

    printf("{\"tic\": %d, \"state\": \"%s\", \"episode\": %d, \"map\": %d, "
           "\"health\": %d, \"armor\": %d, \"weapon\": \"%s\", \"ammo\": %d, "
           "\"kills\": %d, \"total_kills\": %d, \"items\": %d, "
           "\"secrets\": %d, \"dead\": %s",
           gametic, GameStateName(), gameepisode, gamemap,
           p->mo ? p->health : 0, p->armorpoints, weapon, ammo,
           p->killcount, totalkills, p->itemcount, p->secretcount,
           p->playerstate == PST_DEAD ? "true" : "false");

    if (p->mo != NULL)
    {
        printf(", \"x\": %d, \"y\": %d, \"angle\": %d",
               p->mo->x >> FRACBITS, p->mo->y >> FRACBITS,
               (int) ((uint64_t) p->mo->angle * 360 >> 32));
    }

    printf("}\n");
    fflush(stdout);

    if (shot_path != NULL)
    {
        WritePPM(shot_path, 1);
    }

    exit(0);
}

void DG_Init(void)
{
    const char *value;

    if ((value = ArgValue("-keys")) != NULL)
    {
        LoadKeyScript(value);
    }
    if ((value = ArgValue("-every")) != NULL)
    {
        frame_every = atoi(value) > 0 ? atoi(value) : 1;
    }
    if ((value = ArgValue("-maxtics")) != NULL)
    {
        max_tics = atoi(value);
    }

    frames_dir = ArgValue("-frames");
    shot_path = ArgValue("-shot");
    singletics = true;
}

void DG_DrawFrame(void)
{
    if (frames_dir != NULL && frame_no % frame_every == 0)
    {
        char path[4096];

        snprintf(path, sizeof(path), "%s/frame_%06d_%06d.ppm",
                 frames_dir, frame_no, gametic);
        WritePPM(path, 2);
    }

    frame_no++;

    if (gametic >= max_tics)
    {
        PrintStatusAndExit();
    }
}

void DG_SleepMs(uint32_t ms)
{
    clock_ms += ms;
}

uint32_t DG_GetTicksMs(void)
{
    return clock_ms;
}

// Due events go straight into Doom's event queue. Returning them one at a time
// instead would go through I_GetEvent(), which stops reading after any key
// release and so pushes the rest of that tic's events to the next tic.
int DG_GetKey(int *pressed, unsigned char *key)
{
    while (next_event < num_events && events[next_event].tic <= gametic)
    {
        key_event_t *e = &events[next_event++];
        event_t ev = {0};

        ev.type = e->pressed ? ev_keydown : ev_keyup;
        ev.data1 = e->key;
        ev.data2 = e->pressed ? e->key : 0;
        D_PostEvent(&ev);
    }

    return 0;
}

void DG_SetWindowTitle(const char *title)
{
}

int main(int argc, char **argv)
{
    doomgeneric_Create(argc, argv);

    for (;;)
    {
        doomgeneric_Tick();
    }

    return 0;
}
