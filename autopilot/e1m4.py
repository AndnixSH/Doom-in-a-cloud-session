#!/usr/bin/env python3
"""E1M4, straight after E1M3. See level.py.

The yellow key sits in a pit behind a raised bar: switch 712 opens the way
round to switch 977, which opens the bars into the room with the key, and
walking over the ring of lines round the key lowers it. The blue key is
the same, far to the east (the way there is through a teleporter).
"""

from level import main

ROUTE = [
    ("press", 712, "the door switch (tag 2)"),
    ("press", 977, "the switch that opens the bars (tag 3)"),
    ("cross", 1885, "the ring round the yellow key"),
    ("get", (-960, 640), "the yellow key"),
    ("cross", 1964, "the ring round the blue key"),
    ("get", (3456, 1472), "the blue key"),
    ("exit", 1996),
]

main("E1M4", ROUTE, after=["e1m1-complete.txt", "e1m2-complete.txt", "e1m3-complete.txt"])
