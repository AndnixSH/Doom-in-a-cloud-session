#!/usr/bin/env python3
"""Run Doom headlessly in a Linux container and watch it as GIFs and PNGs.

    python3 doom.py build
    python3 doom.py play "hold forward 2s; turn right 90; hold fire 1s" --gif run.gif
    python3 doom.py play --session e1m1 "hold forward 1s"   # turn-based play

Doom runs on a virtual clock, so a run takes a fraction of its game time and
the same moves always give the same result. See README.md for the move
language.
"""

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BUILD = ROOT / "build"
BINARY = BUILD / "doomgeneric_headless"
WADS = BUILD / "wads"
SESSIONS = BUILD / "sessions"

DOOMGENERIC_REPO = "https://github.com/ozkl/doomgeneric"
DOOMGENERIC_COMMIT = "dcb7a8dbc7a16ce3dda29382ac9aae9d77d21284"
FREEDOOM_URL = ("https://github.com/freedoom/freedoom/releases/download/"
                "v0.13.0/freedoom-0.13.0.zip")
FREEDOOM_SHA256 = "3f9b264f3e3ce503b4fb7f6bdcb1f419d93c7b546f4df3e874dd878db9688f59"

TICRATE = 35
START_TIC = 1        # first gametic that scripted moves can land on
TAIL_TICS = 2        # let the last release register before the final frame
TAP_TICS = 2         # how long "tap" holds a key
TAP_GAP_TICS = 2     # pause after each tap so repeated taps stay separate

# Turning: the first 5 tics of a turn are slow (320 units), then 640 units a
# tic, out of 65536 for a full circle (g_game.c: angleturn, SLOWTURNTICS).
SLOW_TURN_TICS = 5
SLOW_TURN_DEG = 320 * 360 / 65536
TURN_DEG = 640 * 360 / 65536

KEYS = {
    "forward": 0xad, "up": 0xad,
    "back": 0xaf, "down": 0xaf,
    "left": 0xac, "right": 0xae,
    "strafeleft": 0xa0, "straferight": 0xa1,
    "use": 0xa2, "fire": 0xa3,
    "run": 0x80 + 0x36,
    "enter": 13, "esc": 27, "escape": 27,
    "tab": 9, "map": 9,
    "backspace": 0x7f, "pause": 0xff,
    **{f"f{n}": 0x80 + 0x3a + n for n in range(1, 11)},
    "f11": 0x80 + 0x57, "f12": 0x80 + 0x58,
}


class MoveError(ValueError):
    pass


def parse_duration(text):
    m = re.fullmatch(r"(\d+(?:\.\d+)?)(s|ms|t)?", text)
    if not m:
        raise MoveError(f"bad duration {text!r} (try 2s, 500ms or 10t)")
    value, unit = float(m[1]), m[2] or "s"
    tics = {"s": value * TICRATE, "ms": value * TICRATE / 1000, "t": value}[unit]
    return max(1, round(tics))


def parse_keys(text):
    codes = []
    for name in text.split("+"):
        if name in KEYS:
            codes.append(KEYS[name])
        elif len(name) == 1 and name.isalnum():
            codes.append(ord(name))
        else:
            raise MoveError(f"unknown key {name!r} (known: {', '.join(KEYS)}, "
                            "or a single letter or digit)")
    return codes


def turn_tics(degrees):
    slow = SLOW_TURN_TICS * SLOW_TURN_DEG
    if degrees <= slow:
        return max(1, round(degrees / SLOW_TURN_DEG))
    return SLOW_TURN_TICS + round((degrees - slow) / TURN_DEG)


def compile_moves(text, tic=START_TIC):
    """Turn move text into (gametic, pressed, keycode) events.

    Returns the events and the tic at which the moves end.
    """
    events = []

    def hold(codes, start, length):
        events.extend((start, 1, c) for c in codes)
        events.extend((start + length, 0, c) for c in codes)

    for raw in re.split(r"[;\n]", text):
        line = raw.split("#", 1)[0].strip().lower()
        if not line:
            continue
        cmd, *args = line.split()

        if cmd == "wait" and len(args) == 1:
            tic += parse_duration(args[0])
        elif cmd == "hold" and len(args) == 2:
            length = parse_duration(args[1])
            hold(parse_keys(args[0]), tic, length)
            tic += length
        elif cmd == "tap" and len(args) in (1, 2):
            count = 1
            if len(args) == 2:
                if not re.fullmatch(r"x\d+", args[1]):
                    raise MoveError(f"bad repeat {args[1]!r} (try x3)")
                count = int(args[1][1:])
            for _ in range(count):
                hold(parse_keys(args[0]), tic, TAP_TICS)
                tic += TAP_TICS + TAP_GAP_TICS
        elif cmd in ("press", "release") and len(args) == 1:
            events.extend((tic, int(cmd == "press"), c) for c in parse_keys(args[0]))
        elif cmd == "turn" and len(args) == 2 and args[0] in ("left", "right"):
            try:
                degrees = float(args[1].rstrip("°"))
            except ValueError:
                raise MoveError(f"bad angle {args[1]!r} (try 90)") from None
            length = turn_tics(degrees)
            hold(parse_keys(args[0]), tic, length)
            tic += length
        else:
            raise MoveError(f"don't understand {line!r}")

    # Stable sort: at equal tics, a release queued before a press stays first.
    events.sort(key=lambda e: e[0])
    return events, tic


def run(cmd, **kwargs):
    print("+", " ".join(str(c) for c in cmd), file=sys.stderr)
    subprocess.run([str(c) for c in cmd], check=True, **kwargs)


def fetch_freedoom():
    wanted = ("freedoom1.wad", "freedoom2.wad")
    if all((WADS / w).exists() for w in wanted):
        return
    print(f"Downloading Freedoom from {FREEDOOM_URL}", file=sys.stderr)
    with urllib.request.urlopen(FREEDOOM_URL, timeout=120) as resp:
        data = resp.read()
    digest = hashlib.sha256(data).hexdigest()
    if digest != FREEDOOM_SHA256:
        sys.exit(f"Freedoom download has sha256 {digest}, expected {FREEDOOM_SHA256}")
    WADS.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for info in z.infolist():
            name = Path(info.filename).name
            if name in wanted:
                (WADS / name).write_bytes(z.read(info))


def build():
    src = BUILD / "doomgeneric"
    if not (src / "doomgeneric" / "doomgeneric.c").exists():
        shutil.rmtree(src, ignore_errors=True)
        run(["git", "init", "-q", src])
        run(["git", "-C", src, "fetch", "-q", "--depth", "1",
             DOOMGENERIC_REPO, DOOMGENERIC_COMMIT])
        run(["git", "-C", src, "checkout", "-q", "FETCH_HEAD"])
    run(["make", "-s", "-C", ROOT / "src", f"-j{os.cpu_count() or 1}",
         f"DOOMGENERIC={src / 'doomgeneric'}", f"OBJDIR={BUILD / 'obj'}",
         f"OUTPUT={BINARY}"])
    fetch_freedoom()


def resolve_wad(name):
    if name in ("freedoom1", "freedoom2"):
        return WADS / f"{name}.wad"
    path = Path(name).expanduser()
    if not path.exists():
        sys.exit(f"No such WAD: {path}")
    return path.resolve()


def warp_args(level):
    """E1M1 -> -warp 1 1 (Doom 1 IWADs); MAP01 -> -warp 1 (Doom 2 IWADs)."""
    m = re.fullmatch(r"e(\d)m(\d)", level.lower())
    if m:
        return ["-warp", m[1], m[2]]
    m = re.fullmatch(r"map(\d\d?)", level.lower())
    if m:
        return ["-warp", str(int(m[1]))]
    sys.exit(f"Bad level {level!r} (try E1M1 or MAP01)")


def write_gif(frames, path, scale):
    """frames: list of (frame number at 35 fps, Path to PPM), in order."""
    from PIL import Image

    images = []
    for _, ppm in frames:
        im = Image.open(ppm)
        if scale != 1:
            im = im.resize((im.width * scale, im.height * scale), Image.NEAREST)
        # Doom draws with a 256-colour palette, so this is lossless.
        images.append(im.convert("P", palette=Image.Palette.ADAPTIVE, colors=256))

    # GIF delays are in 10 ms steps; round cumulatively so timing doesn't drift.
    tics = [t for t, _ in frames]
    tics.append(tics[-1] + TICRATE)  # hold the last frame for a second
    stamps = [round(t * 1000 / TICRATE, -1) for t in tics]
    durations = [max(10, b - a) for a, b in zip(stamps, stamps[1:])]

    path.parent.mkdir(parents=True, exist_ok=True)
    images[0].save(path, save_all=True, append_images=images[1:],
                   duration=durations, loop=0, optimize=True)


def play(args):
    if not BINARY.exists() or not (WADS / "freedoom1.wad").exists():
        build()

    moves = "; ".join(args.moves)
    new_from = START_TIC
    session = None
    if args.session:
        session = SESSIONS / f"{args.session}.json"
        state = {"wad": args.wad, "level": args.level, "skill": args.skill,
                 "moves": []}
        if session.exists() and not args.reset:
            state = json.loads(session.read_text())
            _, new_from = compile_moves("; ".join(state["moves"]))
        # A session's settings are fixed when it starts.
        args.wad, args.level, args.skill = state["wad"], state["level"], state["skill"]
        if moves.strip():
            state["moves"].append(moves)
        moves = "; ".join(state["moves"])

    try:
        events, end = compile_moves(moves)
    except MoveError as e:
        sys.exit(f"Move error: {e}")

    wad = resolve_wad(args.wad)
    level = args.level or ("MAP01" if wad.name.lower() == "freedoom2.wad" else "E1M1")

    with tempfile.TemporaryDirectory(prefix="doom-") as tmp:
        tmp = Path(tmp)
        keys = tmp / "keys.txt"
        keys.write_text("".join(f"{t} {p} {k}\n" for t, p, k in events))
        cmd = [BINARY, "-iwad", wad, "-keys", keys,
               "-maxtics", end + TAIL_TICS, "-shot", tmp / "last.ppm"]
        if not args.title:
            cmd += warp_args(level) + ["-skill", args.skill]
        if args.gif:
            (tmp / "frames").mkdir()
            cmd += ["-frames", tmp / "frames", "-every", args.every]

        proc = subprocess.run([str(c) for c in cmd], cwd=tmp, text=True,
                              capture_output=True, timeout=600)
        status_lines = [l for l in proc.stdout.splitlines() if l.startswith("{")]
        if proc.returncode != 0 or not status_lines:
            sys.stderr.write(proc.stdout[-2000:] + proc.stderr[-2000:])
            sys.exit(f"Doom exited with code {proc.returncode}")
        status = json.loads(status_lines[-1])

        from PIL import Image
        args.shot.parent.mkdir(parents=True, exist_ok=True)
        Image.open(tmp / "last.ppm").save(args.shot)

        if args.gif:
            # In a session, only show what the newest moves did. When warping,
            # skip the level-start wipe: it melts from uninitialised memory.
            first = max(new_from - 1, 0 if args.title else 1)
            frames = []
            for ppm in sorted((tmp / "frames").glob("frame_*.ppm")):
                _, frame_no, gametic = ppm.stem.split("_")
                if int(gametic) >= first:
                    frames.append((int(frame_no), ppm))
            write_gif(frames, args.gif, args.scale)

    if session:
        session.parent.mkdir(parents=True, exist_ok=True)
        session.write_text(json.dumps(state, indent=2) + "\n")

    print(json.dumps(status))
    print(f"screenshot: {args.shot}" + (f"\ngif: {args.gif}" if args.gif else ""),
          file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("build", help="fetch doomgeneric and Freedoom, then compile")

    p = sub.add_parser("play", help="run a sequence of moves")
    p.add_argument("moves", nargs="*", help='e.g. "hold forward 2s; tap fire"')
    p.add_argument("-f", "--file", type=Path, help="read moves from a file")
    p.add_argument("--wad", default="freedoom1",
                   help="freedoom1, freedoom2 or a path to any IWAD (default: freedoom1)")
    p.add_argument("--level", help="E1M1 (Doom 1 IWADs) or MAP01 (Doom 2 IWADs)")
    p.add_argument("--skill", type=int, default=3, choices=range(1, 6),
                   help="1 (easiest) to 5 (nightmare), default 3")
    p.add_argument("--title", action="store_true",
                   help="start at the title screen instead of warping to a level")
    p.add_argument("--shot", type=Path, default=Path("out/last.png"),
                   help="PNG of the final frame (default: out/last.png)")
    p.add_argument("--gif", type=Path, help="write an animated GIF of the run")
    p.add_argument("--every", type=int, default=2,
                   help="GIF frame interval in 1/35 s; 2 = 17.5 fps (default)")
    p.add_argument("--scale", type=int, default=1, help="GIF scale factor")
    p.add_argument("--session", help="keep playing a saved game: earlier moves are "
                   "replayed and the GIF shows only the new ones")
    p.add_argument("--reset", action="store_true", help="start the session over")

    args = parser.parse_args()
    if args.command == "build":
        build()
    else:
        if args.file:
            args.moves.append(args.file.read_text())
        play(args)


if __name__ == "__main__":
    main()
