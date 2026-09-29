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
//   -keys FILE      key script, one "GAMETIC PRESSED KEYCODE" event per line;
//                   PRESSED 2 is a mouse turn instead, with KEYCODE the
//                   horizontal mouse motion (8/65536 of a circle per unit,
//                   positive turns right)
//   -frames DIR     write DIR/frame_FRAMENO_GAMETIC.ppm (320x200) every
//                   -every frames
//   -framefd N      write the same frames to file descriptor N instead, each
//                   as two little-endian int32s (frame number, gametic) and
//                   320x200 RGB bytes; a pipe here makes Doom wait for its reader
//   -every N        frame interval (default 2, i.e. 17.5 fps)
//   -shot FILE      write the final frame to FILE at full 640x400
//   -smoothcam T    draw the view from a camera that eases toward the
//                   player's angle over about T tics instead of snapping to
//                   it (the game itself still uses the real angle)
//   -fastuntil N    draw nothing and write no frames before gametic N, to
//                   replay a long prefix fast (the game plays out the same)
//   -maxtics N      stop once gametic reaches N (35 tics = 1 second;
//                   default 30 seconds)
//   -interactive    at -maxtics, print the status and wait for commands on
//                   stdin instead of exiting:
//                     key GAMETIC PRESSED KEYCODE   queue a key event
//                     shot FILE                     write the current frame
//                     sectors                       print every sector's floor
//                                                   and ceiling height, in order
//                     items                         print the pickups left in
//                                                   the level (by doomednum)
//                     until GAMETIC                 run on, then report again
//                     quit                          exit (so does end of input)

#include "doomgeneric.h"
#include "doomkeys.h"
#include "d_event.h"
#include "doomstat.h"
#include "d_player.h"
#include "m_argv.h"
#include "p_local.h"
#include "r_state.h"
#include "r_main.h"

#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MAX_EVENTS 65536

typedef struct
{
    int tic;
    int pressed;    // 0 key up, 1 key down, 2 mouse turn
    int key;        // key code, or mouse motion for a mouse turn
} key_event_t;

// Start above zero: I_GetTime() treats a base time of 0 as "not set yet".
#define CLOCK_START_MS 1000

static uint32_t clock_ms = CLOCK_START_MS;

static key_event_t events[MAX_EVENTS];
static int num_events = 0;
static int next_event = 0;

static const char *frames_dir = NULL;
static FILE *frame_pipe = NULL;
static const char *shot_path = NULL;
static int frame_every = 2;
static int max_tics = 35 * 30;
static int frame_no = 0;
static int interactive = 0;

static int fast_until = 0;        // -fastuntil: no 3D view before this gametic
static double cam_smooth = 0;     // -smoothcam: smoothing time in tics, 0 = off
static double cam_angle, cam_vel; // camera yaw in degrees, and degrees per tic
static mobj_t *cam_mo = NULL;
static fixed_t cam_x, cam_y;

static const char *ArgValue(const char *name)
{
    int p = M_CheckParmWithArgs((char *) name, 1);

    return p ? myargv[p + 1] : NULL;
}

static void AddEvent(int tic, int pressed, int key)
{
    if (num_events == MAX_EVENTS)
    {
        fprintf(stderr, "headless: more than %d key events\n", MAX_EVENTS);
        exit(1);
    }

    events[num_events].tic = tic;
    events[num_events].pressed = pressed;
    events[num_events].key = key;
    num_events++;
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

    while (fgets(line, sizeof(line), f) != NULL)
    {
        int tic, pressed, key;

        if (sscanf(line, "%d %d %d", &tic, &pressed, &key) == 3)
        {
            AddEvent(tic, pressed, key);
        }
    }

    fclose(f);
}

// Pixels are 0x00RRGGBB; step 2 samples the 640x400 buffer down to Doom's
// native 320x200 (the buffer is a 2x pixel-doubled copy of it).
static void WriteRGB(FILE *f, int step)
{
    int w = DOOMGENERIC_RESX / step;
    int h = DOOMGENERIC_RESY / step;
    unsigned char *row = malloc(w * 3);
    int x, y;

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

    free(row);
}

static void WritePPM(const char *path, int step)
{
    FILE *f = fopen(path, "wb");

    if (f == NULL)
    {
        fprintf(stderr, "headless: cannot write %s\n", path);
        exit(1);
    }

    fprintf(f, "P6\n%d %d\n255\n", DOOMGENERIC_RESX / step, DOOMGENERIC_RESY / step);
    WriteRGB(f, step);
    fclose(f);
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

static const char *MonsterName(int doomednum)
{
    switch (doomednum)
    {
        case 3004: return "zombieman";
        case 9:    return "shotgun guy";
        case 65:   return "chaingunner";
        case 3001: return "imp";
        case 3002: return "demon";
        case 58:   return "spectre";
        case 3006: return "lost soul";
        case 3005: return "cacodemon";
        case 69:   return "hell knight";
        case 3003: return "baron of hell";
        case 68:   return "arachnotron";
        case 71:   return "pain elemental";
        case 66:   return "revenant";
        case 67:   return "mancubus";
        case 64:   return "arch-vile";
        case 7:    return "spider mastermind";
        case 16:   return "cyberdemon";
        case 84:   return "wolfenstein ss";
        default:   return "monster";
    }
}

// Keycards (cards and skull keys alike), weapons owned and all ammo.
static void PrintInventory(player_t *p)
{
    static const char *keys[] = { "blue", "yellow", "red" };
    static const char *weapons[] = {
        "fist", "pistol", "shotgun", "chaingun", "rocket launcher",
        "plasma rifle", "bfg9000", "chainsaw", "super shotgun"
    };
    const char *sep = "";
    int i;

    printf(", \"keys\": [");
    for (i = 0; i < 3; i++)
    {
        // it_bluecard, it_yellowcard, it_redcard, then the skulls in the same order
        if (p->cards[i] || p->cards[i + 3])
        {
            printf("%s\"%s\"", sep, keys[i]);
            sep = ", ";
        }
    }

    sep = "";
    printf("], \"weapons\": [");
    for (i = 0; i < NUMWEAPONS; i++)
    {
        if (p->weaponowned[i])
        {
            printf("%s\"%s\"", sep, weapons[i]);
            sep = ", ";
        }
    }

    printf("], \"ammo_all\": {\"bullets\": %d, \"shells\": %d, "
           "\"rockets\": %d, \"cells\": %d}",
           p->ammo[am_clip], p->ammo[am_shell], p->ammo[am_misl], p->ammo[am_cell]);
}

// Living monsters the player has a line of sight to, facing or not.
static void PrintMonstersInSight(player_t *p)
{
    thinker_t *th;
    const char *sep = "";

    printf(", \"monsters_in_sight\": [");

    for (th = thinkercap.next; p->mo != NULL && th != &thinkercap; th = th->next)
    {
        mobj_t *mo = (mobj_t *) th;

        if (th->function.acp1 != (actionf_p1) P_MobjThinker
         || !(mo->flags & MF_COUNTKILL) || mo->health <= 0
         || !P_CheckSight(p->mo, mo))
        {
            continue;
        }

        printf("%s{\"type\": \"%s\", \"x\": %d, \"y\": %d, \"health\": %d}",
               sep, MonsterName(mobjinfo[mo->type].doomednum),
               mo->x >> FRACBITS, mo->y >> FRACBITS, mo->health);
        sep = ", ";
    }

    printf("]");
}

static void PrintStatus(void)
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
        printf(", \"x\": %d, \"y\": %d, \"angle\": %.1f",
               p->mo->x >> FRACBITS, p->mo->y >> FRACBITS,
               p->mo->angle * (360.0 / 4294967296.0));
    }

    PrintInventory(p);
    PrintMonstersInSight(p);
    printf("}\n");
    fflush(stdout);
}

static void PrintSectors(void)
{
    int i;

    printf("{\"sectors\": [");
    for (i = 0; i < numsectors; i++)
    {
        printf("%s[%d, %d]", i ? ", " : "",
               sectors[i].floorheight >> FRACBITS, sectors[i].ceilingheight >> FRACBITS);
    }
    printf("]}\n");
    fflush(stdout);
}

static void PrintItems(void)
{
    thinker_t *th;
    const char *sep = "";

    printf("{\"items\": [");
    for (th = thinkercap.next; th != NULL && th != &thinkercap; th = th->next)
    {
        mobj_t *mo = (mobj_t *) th;

        if (th->function.acp1 == (actionf_p1) P_MobjThinker && (mo->flags & MF_SPECIAL))
        {
            printf("%s{\"type\": %d, \"x\": %d, \"y\": %d}", sep,
                   mobjinfo[mo->type].doomednum, mo->x >> FRACBITS, mo->y >> FRACBITS);
            sep = ", ";
        }
    }
    printf("]}\n");
    fflush(stdout);
}

// Read commands until one says to run on (see -interactive above).
static void ReadCommands(void)
{
    char line[4200];

    while (fgets(line, sizeof(line), stdin) != NULL)
    {
        int tic, pressed, key;
        char path[4096];

        if (sscanf(line, "key %d %d %d", &tic, &pressed, &key) == 3)
        {
            AddEvent(tic, pressed, key);
        }
        else if (sscanf(line, "shot %4095s", path) == 1)
        {
            WritePPM(path, 1);
        }
        else if (strncmp(line, "sectors", 7) == 0)
        {
            PrintSectors();
        }
        else if (strncmp(line, "items", 5) == 0)
        {
            PrintItems();
        }
        else if (sscanf(line, "until %d", &tic) == 1)
        {
            max_tics = tic;
            return;
        }
        else if (strncmp(line, "quit", 4) == 0)
        {
            break;
        }
        else
        {
            fprintf(stderr, "headless: unknown command: %s", line);
        }
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

    if ((value = ArgValue("-fastuntil")) != NULL)
    {
        fast_until = atoi(value);
    }
    if ((value = ArgValue("-smoothcam")) != NULL)
    {
        cam_smooth = atof(value);
    }

    frames_dir = ArgValue("-frames");
    if ((value = ArgValue("-framefd")) != NULL)
    {
        frame_pipe = fdopen(atoi(value), "wb");
        if (frame_pipe == NULL)
        {
            fprintf(stderr, "headless: cannot write to file descriptor %s\n", value);
            exit(1);
        }
    }
    shot_path = ArgValue("-shot");
    interactive = M_CheckParm("-interactive") > 0;
    singletics = true;
}

void DG_DrawFrame(void)
{
    if (frames_dir != NULL && frame_no % frame_every == 0 && gametic >= fast_until)
    {
        char path[4096];

        snprintf(path, sizeof(path), "%s/frame_%06d_%06d.ppm",
                 frames_dir, frame_no, gametic);
        WritePPM(path, 2);
    }

    if (frame_pipe != NULL && frame_no % frame_every == 0 && gametic >= fast_until)
    {
        int32_t header[2] = { frame_no, gametic };

        fwrite(header, sizeof(header), 1, frame_pipe);
        WriteRGB(frame_pipe, 2);
    }

    frame_no++;

    while (gametic >= max_tics)
    {
        PrintStatus();

        if (!interactive)
        {
            if (shot_path != NULL)
            {
                WritePPM(shot_path, 1);
            }
            exit(0);
        }

        ReadCommands();
    }
}

void __real_R_RenderPlayerView(player_t *player);

// Linked in place of R_RenderPlayerView (-Wl,--wrap in the Makefile). With
// -smoothcam, the view is drawn from a camera that follows the player's angle
// like a critically damped spring, so a one-tic turn becomes a quick, eased
// pan. Only the picture changes: the real angle is put back after drawing.
void __wrap_R_RenderPlayerView(player_t *player)
{
    mobj_t *mo = player->mo;
    angle_t real;
    double target;

    if (cam_smooth <= 0 || mo == NULL)
    {
        if (gametic >= fast_until)
        {
            __real_R_RenderPlayerView(player);
        }
        return;
    }

    real = mo->angle;
    target = real * (360.0 / 4294967296.0);

    // A new level, a respawn, a teleport or fast-forwarding starts over from
    // the real view.
    if (mo != cam_mo || gametic < fast_until || abs(mo->x - cam_x) > 64 * FRACUNIT
     || abs(mo->y - cam_y) > 64 * FRACUNIT)
    {
        cam_mo = mo;
        cam_angle = target;
        cam_vel = 0;
    }
    else
    {
        // SmoothDamp (Game Programming Gems 4, 1.10), one tic per step.
        double omega = 2.0 / cam_smooth;
        double decay = 1.0 / (1.0 + omega + 0.48 * omega * omega
                                 + 0.235 * omega * omega * omega);
        double change = -fmod(fmod(target - cam_angle, 360.0) + 540.0, 360.0) + 180.0;
        double goal = cam_angle - change;
        double temp = cam_vel + omega * change;

        cam_vel = (cam_vel - omega * temp) * decay;
        cam_angle = fmod(goal + (change + temp) * decay + 360.0, 360.0);
    }
    cam_x = mo->x;
    cam_y = mo->y;

    if (gametic < fast_until)
    {
        return;
    }

    mo->angle = (angle_t) (int64_t) (cam_angle * (4294967296.0 / 360.0));
    __real_R_RenderPlayerView(player);
    mo->angle = real;
}

void __real_I_FinishUpdate(void);

// Also linked in place of the original: while fast-forwarding, skip turning
// Doom's 8-bit screen into the 32-bit frame nobody will look at.
void __wrap_I_FinishUpdate(void)
{
    if (gametic < fast_until)
    {
        DG_DrawFrame();
        return;
    }

    __real_I_FinishUpdate();
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

        if (e->pressed == 2)
        {
            ev.type = ev_mouse;
            ev.data2 = e->key;
        }
        else
        {
            ev.type = e->pressed ? ev_keydown : ev_keyup;
            ev.data1 = e->key;
            ev.data2 = e->pressed ? e->key : 0;
        }
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
