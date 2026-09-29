#!/usr/bin/env python3
"""E1M3, straight after E1M2. See level.py.

The route is the order a god-mode scout found that works: the lift presses
put the player where the next switch can be reached (switch 515 lowers the
lift up to a one-way door; going through it sends lift 96 down, the way up
to switch 494).
"""

from level import main

ROUTE = [
    ("press", 261, "the lift switch (tag 1)"),
    ("press", 361, "the door switch (tag 3)"),
    ("press", 265, "the lift switch (tag 2)"),
    ("press", 1314, "the lift switch (tag 19)"),
    ("press", 1130, "the lift switch (tag 23)"),
    ("press", 1132, "the other lift switch (tag 23)"),
    ("press", 515, "the lift switch (tag 5)"),
    ("press", 494, "the door switch (tag 8)"),
    ("press", 647, "the switch that lowers the blue key"),
    ("cross", 1574, "the line that opens the doors by the key"),
    ("get", (432, 2608), "the blue key"),
    ("press", 614, "the stairs switch"),
    ("exit", 1367),
]

if __name__ == "__main__":
    main("E1M3", ROUTE, after=["e1m1-complete.txt", "e1m2-complete.txt"])
