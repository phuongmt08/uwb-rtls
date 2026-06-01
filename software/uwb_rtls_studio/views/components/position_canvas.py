"""
===============================================================================
  UWB RTLS Studio — Position Canvas Component
===============================================================================
  File        : views/components/position_canvas.py
  Description : Custom QWidget vẽ 2D position map cho Live Tracking tab.
                Render anchors, tag position, trajectory trail, grid.

  MVVM Role   : VIEW COMPONENT — reusable canvas widget.

  Features:
    - 2D coordinate grid (meter scale)
    - Anchor markers (fixed, labeled with ID)
    - Tag marker (moving dot, pulsing animation)
    - Trajectory trail (fading path of last N positions)
    - Distance lines (tag → each anchor, with distance label)
    - Pan (mouse drag) + Zoom (mouse wheel)
    - Coordinate tooltip on hover
    - Auto-fit view to anchor layout

  Rendering:
    - QPainter for 2D drawing
    - QTimer for animation updates (~30 FPS)
    - Coordinate transform: world (meters) → screen (pixels)

  Public API:
    - set_anchor_layout(anchors: list)
    - update_tag_position(x, y, z)
    - clear_trail()
    - set_zoom(level: float)
    - fit_to_view()
===============================================================================
"""
pass
