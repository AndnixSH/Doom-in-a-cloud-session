#!/usr/bin/env python3
"""E1M3, straight after E1M2. See level.py.

The order of switches is one a god-mode scout found that works. Switch 515
is in a corridor behind a one-way door off the nukage pool; it lowers the
lift up to door 519, and going west through that door sends lift 96 down,
the way up to switch 494.
"""

from level import main

ROUTE = [
    ("press", 361, "the door switch (tag 3)"),
    ("get", (-60, 1768), "the soulsphere"),
    ("press", 515, "the lift switch (tag 5)"),
    ("press", 494, "the door switch (tag 8)"),
    ("press", 647, "the switch that lowers the blue key"),
    ("get", (432, 2608), "the blue key"),
    ("cross", 1574, "the line that opens the doors by the key"),
    ("get", (416, 2260), "a stimpack"),
    ("get", (420, 2956), "another stimpack"),
    ("press", 614, "the stairs switch"),
    ("exit", 1367),
]

main("E1M3", ROUTE, after=["e1m1-complete.txt", "e1m2-complete.txt"])
