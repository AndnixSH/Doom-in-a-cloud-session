#!/usr/bin/env python3
"""E1M3, straight after E1M2. See level.py; the route was found with a scout."""

from level import main

ROUTE = [
    ("press", 361, "the door switch (tag 3)"),
    ("press", 494, "the door switch (tag 8)"),
    ("press", 647, "the switch that lowers the blue key"),
    ("get", (432, 2608), "the blue key"),
    ("press", 614, "the stairs switch"),
    ("exit", 1367),
]

if __name__ == "__main__":
    main("E1M3", ROUTE, after=["e1m1-complete.txt", "e1m2-complete.txt"])
