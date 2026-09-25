"""Visualize a GraphML track/ground-truth map (e.g. custom_track_modified.xml).

Usage:
    python visualize_track_map.py [path/to/track.xml] [--save output.png] [--ids]

If no path is given, defaults to custom_track_modified.xml next to this script.
Each edge's 'dotted' attribute controls the lane width:
    True  -> single lane (22 cm)
    False -> double lane (44 cm)

Graph nodes sit on the lane *centerline*, so for each edge the two physical
lane boundaries are drawn as lines offset perpendicular to the centerline by
half the lane width. Edges are directed (source -> target), so an arrowhead
is drawn at the midpoint of every segment to show travel direction.
"""

import argparse
import math
import os
import xml.etree.ElementTree as ET

import matplotlib.pyplot as plt

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UWB_STUDIO_DIR = os.path.normpath(os.path.join(BASE_DIR, '..', 'uwb_rtls_studio'))
DEFAULT_GRAPH_PATH = os.path.join(UWB_STUDIO_DIR, 'graph.hml')

# Physical widths (meters) used just for a rough visual cue in the plot;
# actual line thickness on screen is scaled from these.
SINGLE_LANE_WIDTH_M = 0.22
DOUBLE_LANE_WIDTH_M = 0.44

# Anchor coordinates, mirrored from firmware/uwb/sys/positioning_config.h
# (Zone 1 Defaults). Z is the mounting height and is not shown on this 2D map.
ZONE_1_ANCHORS = {
    1: (-0.5, -0.5),
    2: (10.026, -0.5),
    3: (-0.5, 10.026),
    4: (10.026, 10.026),
}


def parse_graphml_track(filepath):
    """Parse a GraphML track file into nodes + segments.

    Returns (nodes, segments) where nodes is {id: (x, y)} and segments is a
    list of (x1, y1, x2, y2, is_dotted) tuples.
    """
    ns = {'g': 'http://graphml.graphdrawing.org/xmlns'}
    tree = ET.parse(filepath)
    root = tree.getroot()

    key_names = {}
    for key in root.findall('g:key', ns):
        key_id = key.get('id')
        attr_name = key.get('attr.name')
        if key_id and attr_name:
            key_names[key_id] = attr_name

    graph = root.find('g:graph', ns)
    if graph is None:
        raise ValueError(f"No <graph> element found in {filepath}")

    nodes = {}
    for node in graph.findall('g:node', ns):
        values = {}
        for data in node.findall('g:data', ns):
            values[key_names.get(data.get('key'), data.get('key'))] = data.text
        try:
            nodes[node.get('id')] = (float(values['x']), float(values['y']))
        except (KeyError, TypeError, ValueError):
            continue

    segments = []
    for edge in graph.findall('g:edge', ns):
        src = nodes.get(edge.get('source'))
        dst = nodes.get(edge.get('target'))
        if src is None or dst is None:
            continue
        is_dotted = False
        for data in edge.findall('g:data', ns):
            attr_name = key_names.get(data.get('key'), data.get('key'))
            if attr_name == 'dotted' and data.text:
                is_dotted = data.text.strip().lower() == 'true'
        segments.append((src[0], src[1], dst[0], dst[1], is_dotted))

    return nodes, segments


def lane_boundaries(x1, y1, x2, y2, width):
    """Return the two boundary lines offset perpendicular to a centerline
    segment by half the lane width, as ((bx1, by1, bx2, by2), (...))."""
    dx, dy = x2 - x1, y2 - y1
    length = math.hypot(dx, dy)
    if length == 0:
        return None
    # Unit vector perpendicular to the segment direction.
    px, py = -dy / length, dx / length
    half = width / 2.0
    left = (x1 + px * half, y1 + py * half, x2 + px * half, y2 + py * half)
    right = (x1 - px * half, y1 - py * half, x2 - px * half, y2 - py * half)
    return left, right


def plot_track(nodes, segments, title, anchors=None, show_ids=False, arrow_stride=1):
    fig, ax = plt.subplots(figsize=(12, 10))

    single_label_used = False
    double_label_used = False
    for i, (x1, y1, x2, y2, is_dotted) in enumerate(segments):
        width = SINGLE_LANE_WIDTH_M if is_dotted else DOUBLE_LANE_WIDTH_M
        color = 'tab:orange' if is_dotted else 'tab:blue'

        # Physical lane boundaries, offset from the centerline.
        boundaries = lane_boundaries(x1, y1, x2, y2, width)
        label = None
        if boundaries:
            if is_dotted and not single_label_used:
                label = f'Single lane boundary ({SINGLE_LANE_WIDTH_M * 100:.0f} cm)'
                single_label_used = True
            elif not is_dotted and not double_label_used:
                label = f'Double lane boundary ({DOUBLE_LANE_WIDTH_M * 100:.0f} cm)'
                double_label_used = True
            for bx1, by1, bx2, by2 in boundaries:
                ax.plot([bx1, bx2], [by1, by2], color=color, linewidth=1.1,
                        label=label, zorder=2)
                label = None

        # Thin centerline through the nodes (the actual graph path).
        ax.plot([x1, x2], [y1, y2], color=color, linewidth=0.6,
                linestyle=(0, (4, 3)), alpha=0.6, zorder=2)

        # Direction arrow (source -> target) at the segment midpoint.
        if i % arrow_stride == 0:
            mx, my = (x1 + x2) / 2.0, (y1 + y2) / 2.0
            dx, dy = x2 - x1, y2 - y1
            length = math.hypot(dx, dy)
            if length > 0:
                ux, uy = dx / length, dy / length
                arrow_len = min(0.12, length * 0.5)
                start = (mx - ux * arrow_len / 2.0, my - uy * arrow_len / 2.0)
                end = (mx + ux * arrow_len / 2.0, my + uy * arrow_len / 2.0)
                ax.annotate('', xy=end, xytext=start,
                             arrowprops=dict(arrowstyle='-|>', color='dimgray',
                                              lw=0.9, mutation_scale=9),
                             zorder=4)

    # Nodes are the lane centerline points.
    xs = [p[0] for p in nodes.values()]
    ys = [p[1] for p in nodes.values()]
    ax.scatter(xs, ys, s=10, color='black', zorder=3, label='Node (lane center)')

    if show_ids:
        for node_id, (x, y) in nodes.items():
            ax.annotate(node_id, (x, y), fontsize=6, xytext=(2, 2),
                        textcoords='offset points', zorder=5)

    if anchors:
        ax_x = [p[0] for p in anchors.values()]
        ax_y = [p[1] for p in anchors.values()]
        ax.scatter(ax_x, ax_y, s=140, marker='^', color='red',
                   edgecolors='black', linewidths=0.8, zorder=6, label='Anchor')
        for anchor_id, (x, y) in anchors.items():
            ax.annotate(f'A{anchor_id}', (x, y), fontsize=9, fontweight='bold',
                        color='red', xytext=(6, 6), textcoords='offset points',
                        zorder=7)

    ax.set_title(title)
    ax.set_xlabel('X (m)')
    ax.set_ylabel('Y (m)')
    ax.set_aspect('equal', adjustable='datalim')
    ax.grid(True, linestyle='--', alpha=0.4)
    ax.legend(loc='upper right', fontsize=8)

    n_nodes = len(nodes)
    n_edges = len(segments)
    fig.text(0.01, 0.01, f'{n_nodes} nodes, {n_edges} edges (arrows show source -> target direction)',
              fontsize=8, color='gray')

    fig.tight_layout()
    return fig


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('path', nargs='?',
                         default=DEFAULT_GRAPH_PATH,
                         help='Path to GraphML track XML file (default: uwb_rtls_studio/graph.hml)')
    parser.add_argument('--save', help='Save the figure to this file instead of/in addition to showing it')
    parser.add_argument('--ids', action='store_true', help='Annotate node IDs on the plot')
    parser.add_argument('--arrow-stride', type=int, default=1,
                         help='Draw a direction arrow on every Nth edge (default: 1, every edge)')
    parser.add_argument('--no-anchors', action='store_true',
                         help='Do not overlay the Zone 1 anchor positions')
    parser.add_argument('--no-show', action='store_true', help='Do not open an interactive window')
    args = parser.parse_args()

    nodes, segments = parse_graphml_track(args.path)
    title = os.path.basename(args.path)
    anchors = None if args.no_anchors else ZONE_1_ANCHORS
    fig = plot_track(nodes, segments, title, anchors=anchors, show_ids=args.ids,
                      arrow_stride=max(1, args.arrow_stride))

    if args.save:
        fig.savefig(args.save, dpi=150)
        print(f'Saved figure to {args.save}')

    if not args.no_show:
        plt.show()


if __name__ == '__main__':
    main()
