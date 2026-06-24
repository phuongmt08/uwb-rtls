"""UWB zone profiles used by configure_zone.py.

Anchor IDs are global. Do not reuse an anchor ID in another real zone.
With NUM_ANCHORS=4 and MAX_ANCHORS_SUPPORTED=8, the practical default is:
Zone 1 -> A1..A4, Zone 2 -> A5..A8.
"""

ANCHOR_Z_M = 0.895

ZONES = {
    1: {
        "preamble": 17,
        "anchors": [
            {"id": 1, "x": 0.0, "y": 0.0, "z": ANCHOR_Z_M},
            {"id": 2, "x": 4.0, "y": 0.0, "z": ANCHOR_Z_M},
            {"id": 3, "x": 0.0, "y": 4.0, "z": ANCHOR_Z_M},
            {"id": 4, "x": 4.0, "y": 4.0, "z": ANCHOR_Z_M},
        ],
    },
    2: {
        "preamble": 18,
        "anchors": [
            {"id": 5, "x": 0.0, "y": 0.0, "z": ANCHOR_Z_M},
            {"id": 6, "x": 4.0, "y": 0.0, "z": ANCHOR_Z_M},
            {"id": 7, "x": 0.0, "y": 4.0, "z": ANCHOR_Z_M},
            {"id": 8, "x": 4.0, "y": 4.0, "z": ANCHOR_Z_M},
        ],
    },
}
