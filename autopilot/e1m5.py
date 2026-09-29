#!/usr/bin/env python3
"""E1M5, straight after E1M4. See level.py.

Only the red key is needed: it opens the way to the exit switch.
"""

from level import main

ROUTE = [
    ("get", (1376, 2592), "the red key"),
    ("exit", 634),
]

main("E1M5", ROUTE, after=["e1m1-complete.txt", "e1m2-complete.txt", "e1m3-complete.txt",
                           "e1m4-complete.txt"])
