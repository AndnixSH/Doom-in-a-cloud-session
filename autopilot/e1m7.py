#!/usr/bin/env python3
"""E1M7, straight after E1M6. See level.py.

The order of switches and keys is the one a god-mode scout found. Most of
the switches open the way on; the yellow key sits on a pillar that switch
4155 lowers, and switch 4279 opens the doors on the way to the exit. E1M6
ends on little health and this level is full of monsters, so the route
also fetches the soulsphere and some health on the way.
"""

from level import main

ROUTE = [
    ("get", (136, 40), "a stimpack"),
    ("get", (-640, 1456), "a medikit"),
    ("press", 1807, "the door switch (tag 1)"),
    ("press", 283, "the door switch (tag 5)"),
    ("press", 2553, "the floor switch (tag 6)"),
    ("press", 2538, "the floor switch (tag 7)"),
    ("press", 2619, "the door switch (tag 9)"),
    ("get", (-1744, 2192), "the soulsphere"),
    ("press", 1181, "the door switch (tag 17)"),
    ("get", (2456, 1392), "a stimpack"),
    ("get", (3184, 1424), "another stimpack"),
    ("press", 1090, "the floor switch (tag 18)"),
    ("get", (3104, 992), "the blue key"),
    ("get", (1952, 1960), "a stimpack"),
    ("get", (2144, 672), "a medikit"),
    ("get", (1888, 384), "another medikit"),
    ("cross", 4153, "the floor trigger (tag 59)", {"grab": False}),   # (no detours up north)
    ("press", 4155, "the switch that lowers the yellow key"),
    # The yellow key's hall has eight spectres at its far end: in for the
    # key (and the stimpack by the door) and straight out again.
    ("get", (3224, 952), "the stimpack by the yellow key", {"outrun": True, "grab": False}),
    ("get", (3424, 992), "the yellow key", {"outrun": True, "grab": False}),
    ("get", (2632, 824), "the way out", {"outrun": True, "grab": False}),
    ("press", 4279, "the door switch (tag 61)", {"outrun": True}),
    ("exit", 1436, "", {"outrun": True}),
]

main("E1M7", ROUTE, after=["e1m1-complete.txt", "e1m2-complete.txt", "e1m3-complete.txt",
                           "e1m4-complete.txt", "e1m5-complete.txt", "e1m6-complete.txt"])
