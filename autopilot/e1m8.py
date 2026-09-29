#!/usr/bin/env python3
"""E1M8, the end of the episode, straight after E1M7. See level.py.

Three barons guard it, each shut in a pit until you take the armor in
the middle of the left-hand arena: the lines round it lower the pits'
walls. Killing all three lowers the wall (tag 666) between the arena and
the exit pad, and walking onto the pad ends the episode. (Most of the
ammo lying about is only there in multiplayer: on your own there are just
the rocket launcher and two boxes of rockets by the start.)
"""

from level import main

ROUTE = [
    ("get", (2464, -224), "the armor by the start"),
    ("get", (3200, -280), "the rocket launcher"),
    ("get", (416, -224), "the armor that lets the barons out"),
    ("get", (1160, -1480), "the first baron's pit"),
    ("get", (104, -1160), "the second baron's pit"),
    ("get", (-232, -248), "the third baron's pit"),
    ("exit", 165),
]

main("E1M8", ROUTE, after=["e1m1-complete.txt", "e1m2-complete.txt", "e1m3-complete.txt",
                           "e1m4-complete.txt", "e1m5-complete.txt", "e1m6-complete.txt",
                           "e1m7-complete.txt"])
