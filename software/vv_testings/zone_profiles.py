"""UWB zone profiles used by configure_zone.py.

Anchor IDs are global. Do not reuse an anchor ID in another real zone.
Firmware accepts 3..6 anchors in each zone. Zone 1 currently owns A1..A6;
Zone 2 is intentionally disabled.
"""

ANCHOR_Z_M = 0.895

ZONES = {
    1: {
        "preamble": 17,
        "anchors": [
            {"id": 1, "x": 0.7, "y": 0.03, "z": ANCHOR_Z_M},
            {"id": 2, "x": 2.7, "y": 8.37, "z": ANCHOR_Z_M},
            {"id": 3, "x": 7.5, "y": 8.37, "z": ANCHOR_Z_M},
            {"id": 4, "x": 7.5, "y": 0.03, "z": ANCHOR_Z_M},
            {"id": 5, "x": 0.0, "y": 0.0, "z": ANCHOR_Z_M},
            {"id": 6, "x": 10.0, "y": 0.0, "z": ANCHOR_Z_M},
        ],
    },
}
