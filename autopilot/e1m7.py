#!/usr/bin/env python3
"""E1M7, straight after E1M6. See level.py.

The order of switches and keys is the one a god-mode scout found. Most of
the switches open the way on; the yellow key sits on a pillar that switch
4155 lowers, and switch 4279 opens the doors on the way to the exit.
"""

from level import main

ROUTE = [
    ("press", 1807, "the door switch (tag 1)"),
    ("press", 283, "the door switch (tag 5)"),
    ("press", 2553, "the floor switch (tag 6)"),
    ("press", 2538, "the floor switch (tag 7)"),
    ("press", 2619, "the door switch (tag 9)"),
    ("press", 1181, "the door switch (tag 17)"),
    ("press", 1090, "the floor switch (tag 18)"),
    ("get", (3104, 992), "the blue key"),
    ("cross", 4153, "the floor trigger (tag 59)"),
    ("press", 4155, "the switch that lowers the yellow key"),
    ("get", (3424, 992), "the yellow key"),
    ("press", 4279, "the door switch (tag 61)"),
    ("exit", 1436),
]

main("E1M7", ROUTE, after=["e1m1-complete.txt", "e1m2-complete.txt", "e1m3-complete.txt",
                           "e1m4-complete.txt", "e1m5-complete.txt", "e1m6-complete.txt"])
