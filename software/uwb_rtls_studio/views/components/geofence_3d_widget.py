"""
===============================================================================
  UWB RTLS Studio — Geofence 3D Viewer Canvas Component
===============================================================================
  File        : views/components/geofence_3d_widget.py
  Description : 3D visualization of Geofence maps using pyqtgraph.opengl.
  MVVM Role   : VIEW — 3D layout rendering.
===============================================================================
"""
import logging
import numpy as np
import math
from PyQt6.QtCore import Qt, QPointF
from PyQt6.QtGui import QColor, QPolygonF, QPainterPath

log = logging.getLogger(__name__)

OPENGL_AVAILABLE = False
try:
    import pyqtgraph.opengl as gl
    OPENGL_AVAILABLE = True
except ImportError:
    log.warning("PyOpenGL or pyqtgraph.opengl is not installed. 3D visualization will be disabled.")


if OPENGL_AVAILABLE:
    # Register a custom shader for subtle, high-brightness shading
    try:
        from pyqtgraph.opengl.shaders import ShaderProgram, VertexShader, FragmentShader
        if 'custom_shaded' not in ShaderProgram.names:
            ShaderProgram('custom_shaded', [
                VertexShader("""
                    uniform mat4 u_mvp;
                    uniform mat3 u_normal;
                    attribute vec4 a_position;
                    attribute vec3 a_normal;
                    attribute vec4 a_color;
                    varying vec4 v_color;
                    varying vec3 v_normal;
                    void main() {
                        v_normal = normalize(u_normal * a_normal);
                        v_color = a_color;
                        gl_Position = u_mvp * a_position;
                    }
                """),
                FragmentShader("""
                    #ifdef GL_ES
                    precision mediump float;
                    #endif
                    varying vec4 v_color;
                    varying vec3 v_normal;
                    void main() {
                        // Light comes from top-front-right
                        float p = dot(v_normal, normalize(vec3(0.5, -0.5, -1.0)));
                        p = p < 0. ? 0. : p * 0.35;
                        // High ambient term (0.75) + subtle shading (0.35 * p)
                        // Minimum brightness is 75%, keeping colors bright and true to 2D
                        vec3 rgb = v_color.rgb * (0.75 + p);
                        gl_FragColor = vec4(rgb, v_color.a);
                    }
                """)
            ])
    except Exception as e:
        log.warning(f"Could not register custom_shaded shader: {e}")

    class Geofence3DWidget(gl.GLViewWidget):
        def __init__(self, viewmodel, parent=None):
            super().__init__(parent)
            self._vm = viewmodel
            self._tag_pos = [0.0, 0.0, 0.0]
            self._tag_yaw = 0.0
            self._trail_points = []
            self._mesh_items = []
            self._anchor_items = []  # separate list so anchors survive zone redraws
            self.setBackgroundColor(QColor("#FAF9F6"))

            # Setup camera
            self.setCameraPosition(distance=15, elevation=30, azimuth=-45)

            # Initialize 3D elements
            self._init_3d_view()
            if self._vm:
                self._connect_signals()

        def set_viewmodel(self, vm):
            self._vm = vm
            self._connect_signals()

        def _init_3d_view(self):
            # 1. Add major and minor grid floors matching the 2D canvas style (dark grid on light bg)
            major_grid = gl.GLGridItem()
            major_grid.setSize(200, 200, 1)
            major_grid.setSpacing(1, 1, 0)
            major_grid.setColor(QColor(100, 116, 139, 90))
            self.addItem(major_grid)

            minor_grid = gl.GLGridItem()
            minor_grid.setSize(200, 200, 1)
            minor_grid.setSpacing(0.2, 0.2, 0)
            minor_grid.setColor(QColor(100, 116, 139, 35))
            self.addItem(minor_grid)

            # 2. Add Coordinate Axes (X = Red, Y = Green, Z = Blue)
            axes = gl.GLAxisItem()
            axes.setSize(5, 5, 5)
            self.addItem(axes)

            # 3. Add Tag 3D Directional Arrow
            verts, faces = self._make_3d_arrow(0.0, 0.0, 0.0, 0.0)
            self.tag_arrow = gl.GLMeshItem(
                vertexes=verts,
                faces=faces,
                color=[239/255, 68/255, 68/255, 1.0],  # Red tag color
                smooth=False,
                shader='shaded',
                drawEdges=True,
                edgeColor=QColor("#EF4444")
            )
            self.addItem(self.tag_arrow)

            # 4. Add Tag trail line
            self.tag_trail = gl.GLLinePlotItem(
                pos=np.array([[0.0, 0.0, 0.0]]),
                color=QColor("#60A5FA"),
                width=2.5,
                antialias=True
            )
            self.addItem(self.tag_trail)

            # 5. Anchor items are built dynamically in set_anchors()

        def _make_3d_arrow(self, x, y, z, yaw_deg):
            yaw_rad = math.radians(yaw_deg)
            cos_y = math.cos(yaw_rad)
            sin_y = math.sin(yaw_rad)

            # Base geometry of arrow centered at origin (pointing along +X axis)
            base_verts = np.array([
                # Bottom face (z = -0.08)
                [0.35, 0.0, -0.08],     # 0: tip
                [-0.2, -0.15, -0.08],   # 1: left back
                [-0.08, 0.0, -0.08],    # 2: center back
                [-0.2, 0.15, -0.08],    # 3: right back
                # Top face (z = 0.08)
                [0.35, 0.0, 0.08],      # 4: tip
                [-0.2, -0.15, 0.08],    # 5: left back
                [-0.08, 0.0, 0.08],     # 6: center back
                [-0.2, 0.15, 0.08]      # 7: right back
            ])

            # Rotate vertices around Z-axis by yaw
            rotated_verts = np.zeros_like(base_verts)
            rotated_verts[:, 0] = base_verts[:, 0] * cos_y - base_verts[:, 1] * sin_y
            rotated_verts[:, 1] = base_verts[:, 0] * sin_y + base_verts[:, 1] * cos_y
            rotated_verts[:, 2] = base_verts[:, 2]  # z remains unchanged

            # Translate
            rotated_verts[:, 0] += x
            rotated_verts[:, 1] += y
            rotated_verts[:, 2] += z

            faces = np.array([
                # Bottom
                [0, 2, 1], [0, 3, 2],
                # Top
                [4, 5, 6], [4, 6, 7],
                # Sides
                [0, 1, 5], [0, 5, 4],
                [1, 2, 6], [1, 6, 5],
                [2, 3, 7], [2, 7, 6],
                [3, 0, 4], [3, 4, 7]
            ])

            return rotated_verts, faces

        def _update_tag_mesh(self, x, y, z, yaw_deg):
            verts, faces = self._make_3d_arrow(x, y, z, yaw_deg)
            self.tag_arrow.setMeshData(vertexes=verts, faces=faces)

        def _hex_to_rgba(self, hex_str, alpha):
            if not hex_str or not hex_str.startswith("#"):
                return (1.0, 1.0, 1.0, alpha)
            try:
                r = int(hex_str[1:3], 16) / 255.0
                g = int(hex_str[3:5], 16) / 255.0
                b = int(hex_str[5:7], 16) / 255.0
                return (r, g, b, alpha)
            except Exception:
                return (1.0, 1.0, 1.0, alpha)

        def _draw_prism_outlines(self, points, min_z, max_z, edge_color, gl_options=None):
            N = len(points)
            if N < 2:
                return

            from OpenGL import GL
            if gl_options is None or gl_options == 'opaque':
                gl_opts = {
                    GL.GL_DEPTH_TEST: True,
                    GL.GL_BLEND: False,
                    GL.GL_CULL_FACE: False,
                    'glDepthFunc': (GL.GL_LEQUAL,),
                }
            elif gl_options == 'translucent':
                gl_opts = {
                    GL.GL_DEPTH_TEST: True,
                    GL.GL_BLEND: True,
                    GL.GL_CULL_FACE: False,
                    'glBlendFuncSeparate': (GL.GL_SRC_ALPHA, GL.GL_ONE_MINUS_SRC_ALPHA,
                                            GL.GL_ONE, GL.GL_ONE_MINUS_SRC_ALPHA),
                    'glDepthFunc': (GL.GL_LEQUAL,),
                }
            elif isinstance(gl_options, dict):
                gl_opts = gl_options.copy()
                if 'glDepthFunc' not in gl_opts:
                    gl_opts['glDepthFunc'] = (GL.GL_LEQUAL,)
            else:
                gl_opts = gl_options

            # Bottom loop
            bot = [[pt[0], pt[1], min_z] for pt in points] + [[points[0][0], points[0][1], min_z]]
            bot_line = gl.GLLinePlotItem(pos=np.array(bot), color=edge_color, width=1.5, mode='line_strip', antialias=True, glOptions=gl_opts)
            self.addItem(bot_line)
            self._mesh_items.append(bot_line)

            # Top loop
            top = [[pt[0], pt[1], max_z] for pt in points] + [[points[0][0], points[0][1], max_z]]
            top_line = gl.GLLinePlotItem(pos=np.array(top), color=edge_color, width=1.5, mode='line_strip', antialias=True, glOptions=gl_opts)
            self.addItem(top_line)
            self._mesh_items.append(top_line)

            # Vertical pillars
            for i in range(N):
                pillar = gl.GLLinePlotItem(
                    pos=np.array([[points[i][0], points[i][1], min_z], [points[i][0], points[i][1], max_z]]),
                    color=edge_color, width=1.5, mode='line_strip', antialias=True, glOptions=gl_opts
                )
                self.addItem(pillar)
                self._mesh_items.append(pillar)

        def _normalize_winding(self, points):
            """Ensure points are in counter-clockwise order."""
            if len(points) < 3:
                return points
            ans = 0.0
            for i in range(len(points)):
                x1, y1 = points[i]
                x2, y2 = points[(i + 1) % len(points)]
                ans += (x1 * y2 - x2 * y1)
            if ans < 0:
                return list(reversed(points))
            return points

        def _draw_prism_mesh(self, points, min_z, max_z, color, edge_color, gl_options=None, draw_outlines=False):
            points = self._normalize_winding(points)
            N = len(points)
            if N < 3:
                return
            verts = []
            for pt in points:
                verts.append([pt[0], pt[1], min_z])
            for pt in points:
                verts.append([pt[0], pt[1], max_z])

            verts = np.array(verts)

            faces = []
            for i in range(1, N - 1):
                faces.append([0, i, i + 1])
            for i in range(1, N - 1):
                faces.append([N, N + i, N + i + 1])
            for i in range(N):
                next_i = (i + 1) % N
                faces.append([i, next_i, next_i + N])
                faces.append([i, next_i + N, i + N])

            faces = np.array(faces)

            mesh = gl.GLMeshItem(
                vertexes=verts,
                faces=faces,
                color=color,
                smooth=False,
                shader='custom_shaded',
                drawEdges=False,
                edgeColor=edge_color
            )
            if gl_options is not None:
                mesh.setGLOptions(gl_options)
            self.addItem(mesh)
            self._mesh_items.append(mesh)
            if draw_outlines:
                self._draw_prism_outlines(points, min_z, max_z, edge_color, gl_options=gl_options)

        def _room_by_id(self, room_id, zones):
            if not room_id:
                return None
            return next(
                (
                    zone for zone in zones
                    if getattr(zone, "object_type", "zone") == "room" and zone.id == room_id
                ),
                None,
            )

        def _is_inside_polygon(self, poly_points, wx, wy):
            if len(poly_points or []) < 3:
                return False
            poly = QPolygonF()
            for pt in poly_points:
                poly.append(QPointF(pt[0], pt[1]))
            return poly.containsPoint(QPointF(wx, wy), Qt.FillRule.OddEvenFill)

        def _wall_outward_normal(self, zone, p1, p2, zones):
            """Return the outward unit normal for a boundary wall segment, or None."""
            x1, y1 = p1
            x2, y2 = p2
            dx = x2 - x1
            dy = y2 - y1
            length = math.hypot(dx, dy)
            if length <= 1e-9:
                return None
            nx = -dy / length
            ny = dx / length
            host_room = self._room_by_id(getattr(zone, "host_room_id", None), zones)
            if getattr(zone, "wall_mode", "free_standing") != "boundary_outside" or host_room is None:
                return None
            mid_x = (x1 + x2) * 0.5
            mid_y = (y1 + y2) * 0.5
            thickness = max(0.0, float(getattr(zone, "thickness_m", 0.1)))
            epsilon = min(0.02, max(thickness * 0.25, 0.001))
            plus_is_inside = self._is_inside_polygon(
                host_room.points, mid_x + nx * epsilon, mid_y + ny * epsilon
            )
            return (-nx, -ny) if plus_is_inside else (nx, ny)

        def _wall_segment_footprint(self, zone, p1, p2, zones):
            """Return one wall segment footprint in world coordinates."""
            x1, y1 = p1
            x2, y2 = p2
            dx = x2 - x1
            dy = y2 - y1
            length = math.hypot(dx, dy)
            thickness = max(0.0, float(getattr(zone, "thickness_m", 0.1)))
            if length <= 1e-9 or thickness <= 0.0:
                return []

            outward = self._wall_outward_normal(zone, p1, p2, zones)
            if outward is not None:
                outward_x, outward_y = outward
                return [
                    (x1, y1),
                    (x2, y2),
                    (x2 + outward_x * thickness, y2 + outward_y * thickness),
                    (x1 + outward_x * thickness, y1 + outward_y * thickness),
                ]

            nx = -dy / length
            ny = dx / length
            half = thickness * 0.5
            return [
                (x1 + nx * half, y1 + ny * half),
                (x2 + nx * half, y2 + ny * half),
                (x2 - nx * half, y2 - ny * half),
                (x1 - nx * half, y1 - ny * half),
            ]

        @staticmethod
        def _line_intersection(a, direction_a, b, direction_b):
            """Intersection of infinite 2D lines, or None when parallel."""
            ax, ay = a
            adx, ady = direction_a
            bx, by = b
            bdx, bdy = direction_b
            denominator = adx * bdy - ady * bdx
            if abs(denominator) <= 1e-9:
                return None
            cross = (bx - ax) * bdy - (by - ay) * bdx
            scale = cross / denominator
            return ax + scale * adx, ay + scale * ady

        def _wall_footprint_path(self, zone, zones):
            """Create one continuous wall footprint with closed, square/mitered corners."""
            points = list(getattr(zone, "points", []) or [])
            thickness = max(0.0, float(getattr(zone, "thickness_m", 0.1)))
            if len(points) < 2 or thickness <= 0.0:
                return QPainterPath()

            mode = getattr(zone, "wall_mode", "free_standing")
            if mode != "boundary_outside" or self._room_by_id(getattr(zone, "host_room_id", None), zones) is None:
                from PyQt6.QtGui import QPainterPathStroker
                centerline = QPainterPath(QPointF(points[0][0], points[0][1]))
                for x, y in points[1:]:
                    centerline.lineTo(x, y)
                stroker = QPainterPathStroker()
                stroker.setWidth(thickness)
                stroker.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
                stroker.setCapStyle(Qt.PenCapStyle.SquareCap)
                return stroker.createStroke(centerline)

            combined = QPainterPath()
            for idx in range(len(points) - 1):
                footprint = self._wall_segment_footprint(zone, points[idx], points[idx + 1], zones)
                if len(footprint) != 4:
                    continue
                segment_path = QPainterPath(QPointF(footprint[0][0], footprint[0][1]))
                for x, y in footprint[1:]:
                    segment_path.lineTo(x, y)
                segment_path.closeSubpath()
                combined = segment_path if combined.isEmpty() else combined.united(segment_path)

            for idx in range(1, len(points) - 1):
                previous_point = points[idx - 1]
                vertex = points[idx]
                next_point = points[idx + 1]
                normal_a = self._wall_outward_normal(zone, previous_point, vertex, zones)
                normal_b = self._wall_outward_normal(zone, vertex, next_point, zones)
                if normal_a is None or normal_b is None:
                    continue
                dot = normal_a[0] * normal_b[0] + normal_a[1] * normal_b[1]
                if dot > 0.999:
                    continue
                direction_a = (vertex[0] - previous_point[0], vertex[1] - previous_point[1])
                direction_b = (next_point[0] - vertex[0], next_point[1] - vertex[1])
                a = (vertex[0] + normal_a[0] * thickness, vertex[1] + normal_a[1] * thickness)
                b = (vertex[0] + normal_b[0] * thickness, vertex[1] + normal_b[1] * thickness)
                intersection = self._line_intersection(a, direction_a, b, direction_b)
                if intersection is None:
                    intersection = (
                        vertex[0] + (normal_a[0] + normal_b[0]) * thickness,
                        vertex[1] + (normal_a[1] + normal_b[1]) * thickness,
                    )
                join_path = QPainterPath(QPointF(vertex[0], vertex[1]))
                join_path.lineTo(a[0], a[1])
                join_path.lineTo(intersection[0], intersection[1])
                join_path.lineTo(b[0], b[1])
                join_path.closeSubpath()
                combined = combined.united(join_path)
            return combined.simplified()

        # ──────────────────────────────────────────────────────────────────────
        # Anchor 3D geometry helpers
        # ──────────────────────────────────────────────────────────────────────
        def _make_anchor_octahedron(self, x, y, z, r=0.12):
            """Simple diamond shape centered at anchor position."""
            verts = np.array([
                [x,     y,     z + r],
                [x + r, y,     z    ],
                [x,     y + r, z    ],
                [x - r, y,     z    ],
                [x,     y - r, z    ],
                [x,     y,     z - r],
            ])
            faces = np.array([
                [0, 1, 2], [0, 2, 3], [0, 3, 4], [0, 4, 1],
                [5, 2, 1], [5, 3, 2], [5, 4, 3], [5, 1, 4],
            ])
            return verts, faces

        def _clear_anchor_items(self):
            for item in self._anchor_items:
                self.removeItem(item)
            self._anchor_items.clear()

        def set_anchors(self, anchors):
            self._clear_anchor_items()
            if not anchors:
                return

            from OpenGL import GL
            # Disable depth test so anchors are always visible through walls
            no_depth = {
                GL.GL_DEPTH_TEST: False,
                GL.GL_BLEND: True,
                "glBlendFunc": (GL.GL_SRC_ALPHA, GL.GL_ONE_MINUS_SRC_ALPHA),
            }

            body_color = (251/255, 191/255,  36/255, 0.95)   # amber-400
            edge_color = (255/255, 251/255, 235/255, 1.00)   # near-white edge

            for a in anchors:
                if not a.get("placed", True):
                    continue
                ax = float(a.get("x", a.get("x_m", 0.0)))
                ay = float(a.get("y", a.get("y_m", 0.0)))
                az = float(a.get("z", a.get("z_m", 0.0)))

                # Diamond (octahedron)
                verts, faces = self._make_anchor_octahedron(ax, ay, az, r=0.10)
                mesh = gl.GLMeshItem(
                    vertexes=verts, faces=faces,
                    color=body_color, smooth=False,
                    shader="shaded", drawEdges=True,
                    edgeColor=edge_color
                )
                mesh.setGLOptions(no_depth)
                self.addItem(mesh)
                self._anchor_items.append(mesh)


        def set_geofences(self, zones):
            self._draw_zones_3d(zones or [])

        def _draw_zones_3d(self, zones):
            for item in self._mesh_items:
                self.removeItem(item)
            self._mesh_items.clear()

            for zone in zones:
                if len(zone.points) < 2:
                    continue

                object_type = getattr(zone, "object_type", "zone")
                base_color_hex = zone.color if getattr(zone, "color", "").startswith("#") else None

                # 1. Color assignments matching 2.5D visual styles (RGBA float tuples)
                if object_type == "room":
                    hex_color = base_color_hex if base_color_hex else "#1D4ED8"
                    color = self._hex_to_rgba(hex_color, 0.12)
                    edge_color = self._hex_to_rgba("#7DD3FC", 1.0)
                elif object_type == "wall":
                    hex_color = base_color_hex if base_color_hex else "#111827"
                    color = self._hex_to_rgba(hex_color, 1.0)
                    r, g, b, a = color
                    factor = 0.05  # Make it 5% lighter towards white
                    edge_color = (
                        min(1.0, r + (1.0 - r) * factor),
                        min(1.0, g + (1.0 - g) * factor),
                        min(1.0, b + (1.0 - b) * factor),
                        1.0
                    )
                elif object_type == "object":
                    hex_color = base_color_hex if base_color_hex else "#F59E0B"
                    color = self._hex_to_rgba(hex_color, 1.0)
                    r, g, b, a = color
                    factor = 0.20  # Make it 20% lighter towards white
                    edge_color = (
                        min(1.0, r + (1.0 - r) * factor),
                        min(1.0, g + (1.0 - g) * factor),
                        min(1.0, b + (1.0 - b) * factor),
                        1.0
                    )
                elif zone.zone_type == "forbidden":
                    hex_color = base_color_hex if base_color_hex else "#EF4444"
                    color = self._hex_to_rgba(hex_color, 0.30)
                    edge_color = self._hex_to_rgba("#F87171", 1.0)
                else:
                    hex_color = base_color_hex if base_color_hex else "#22C55E"
                    color = self._hex_to_rgba(hex_color, 0.25)
                    edge_color = self._hex_to_rgba("#4ADE80", 1.0)

                # 2. Render geometries based on type
                if object_type == "room":
                    # Rooms: drawn as 3D slab with 0.05m thickness
                    self._draw_prism_mesh(zone.points, 0.0, 0.05, color, edge_color, draw_outlines=False)
                    
                elif object_type == "wall":
                    # Walls: drawn segment-by-segment and join-by-join to prevent hollow-room filling (roof/ceiling bug)
                    points = list(getattr(zone, "points", []) or [])
                    thickness = max(0.0, float(getattr(zone, "thickness_m", 0.1)))
                    if len(points) >= 2 and thickness > 0.0:
                        # 1. Draw segment footprints
                        for idx in range(len(points) - 1):
                            footprint = self._wall_segment_footprint(zone, points[idx], points[idx + 1], zones)
                            if len(footprint) == 4:
                                self._draw_prism_mesh(footprint, zone.min_z, zone.max_z, color, edge_color, gl_options='opaque', draw_outlines=True)
                        
                        # 2. Draw corner join footprints
                        for idx in range(1, len(points) - 1):
                            previous_point = points[idx - 1]
                            vertex = points[idx]
                            next_point = points[idx + 1]
                            normal_a = self._wall_outward_normal(zone, previous_point, vertex, zones)
                            normal_b = self._wall_outward_normal(zone, vertex, next_point, zones)
                            if normal_a is None or normal_b is None:
                                continue
                            dot = normal_a[0] * normal_b[0] + normal_a[1] * normal_b[1]
                            if dot > 0.999:
                                continue
                            direction_a = (vertex[0] - previous_point[0], vertex[1] - previous_point[1])
                            direction_b = (next_point[0] - vertex[0], next_point[1] - vertex[1])
                            a = (vertex[0] + normal_a[0] * thickness, vertex[1] + normal_a[1] * thickness)
                            b = (vertex[0] + normal_b[0] * thickness, vertex[1] + normal_b[1] * thickness)
                            intersection = self._line_intersection(a, direction_a, b, direction_b)
                            if intersection is None:
                                intersection = (
                                    vertex[0] + (normal_a[0] + normal_b[0]) * thickness,
                                    vertex[1] + (normal_a[1] + normal_b[1]) * thickness,
                                )
                            join_pts = [vertex, a, intersection, b]
                            self._draw_prism_mesh(join_pts, zone.min_z, zone.max_z, color, edge_color, gl_options='opaque', draw_outlines=True)
                            
                elif object_type == "object":
                    # Objects (pillars/columns): drawn as opaque solid 3D prisms
                    if len(zone.points) >= 3:
                        self._draw_prism_mesh(zone.points, zone.min_z, zone.max_z, color, edge_color, gl_options='opaque', draw_outlines=True)
                        
                else:
                    # Rule Zones: translucent volume 0.5m tall, colour matches 2D zone
                    if len(zone.points) >= 3:
                        zone_min_z = float(getattr(zone, "min_z", 0.0))
                        zone_max_z = zone_min_z + 0.50  # fixed 0.5m height for visibility
                        self._draw_prism_mesh(
                            zone.points, zone_min_z, zone_max_z,
                            color, edge_color,
                            gl_options='translucent', draw_outlines=True
                        )

        def _connect_signals(self):
            if self._vm:
                try:
                    self._vm.position_updated.disconnect(self._on_position_updated)
                except TypeError:
                    pass
                try:
                    self._vm.sensor_fusion_updated.disconnect(self._on_sensor_fusion_updated)
                except TypeError:
                    pass
                try:
                    self._vm.geofence_layout_updated.disconnect(self.set_geofences)
                except TypeError:
                    pass

                self._vm.position_updated.connect(self._on_position_updated)
                self._vm.sensor_fusion_updated.connect(self._on_sensor_fusion_updated)
                self._vm.geofence_layout_updated.connect(self.set_geofences)

        def _on_position_updated(self, x, y, z, rms):
            self._tag_pos = [x, y, z]
            self._update_tag_mesh(x, y, z, self._tag_yaw)

            self._trail_points.append([x, y, z])
            if len(self._trail_points) > 50:
                self._trail_points.pop(0)

            self.tag_trail.setData(pos=np.array(self._trail_points))
            self.update()

        def _on_sensor_fusion_updated(self, data: dict):
            x = float(data.get("ukf_x_m", 0.0))
            y = float(data.get("ukf_y_m", 0.0))
            z = self._tag_pos[2]
            yaw = float(data.get("ukf_yaw_deg", 0.0))
            self._tag_pos = [x, y, z]
            self._tag_yaw = yaw
            self._update_tag_mesh(x, y, z, yaw)

            self._trail_points.append([x, y, z])
            if len(self._trail_points) > 50:
                self._trail_points.pop(0)

            self.tag_trail.setData(pos=np.array(self._trail_points))
            self.update()
else:
    class Geofence3DWidget:
        def __init__(self, viewmodel, parent=None):
            pass
        def set_viewmodel(self, vm):
            pass
        def set_anchors(self, anchors):
            pass
        def set_geofences(self, zones):
            pass
        def _clear_anchor_items(self):
            pass
