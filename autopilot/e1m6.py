#!/usr/bin/env python3
"""E1M6, straight after E1M5. See level.py.

The keys and switches are ones a god-mode scout found. The yellow key is a
trap: taking it shuts door 56 behind you (and lets out a closet of
monsters), but the switch that opens that door works again from the key's
side. The exit is behind the yellow door, across a pit that switch 1530
raises: that is left until last, so the way there isn't the long way round.
"""

from level import main

ROUTE = [
    ("get", (-672, 1184), "the soulsphere"),
    ("get", (-2000, 544), "the blue key"),
    ("get", (-1616, 640), "the armor on the way"),
    ("get", (-1048, -384), "a medikit on the way"),
    ("get", (-1600, -1504), "the red key"),
    ("press", 1464, "the door switch (tag 3)"),
    ("press", 2578, "the door switch (tag 9)"),
    ("get", (144, -480), "the yellow key"),
    ("press", 2785, "the switch that opens door 56 again (tag 3)"),
    ("press", 1530, "the switch that raises the pit floor (tag 1)"),
    ("exit", 1546),
]

main("E1M6", ROUTE, after=["e1m1-complete.txt", "e1m2-complete.txt", "e1m3-complete.txt",
                           "e1m4-complete.txt", "e1m5-complete.txt"])
