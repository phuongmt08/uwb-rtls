import csv
import html
import math
import os
import re
from collections import OrderedDict

from .module_csv import find_latest_csv_file


ANSI_RESET = "\033[0m"
ANSI_DIM = "\033[2m"
ANSI_BOLD = "\033[1m"
ANSI_CYAN = "\033[36m"
ANSI_GREEN = "\033[32m"
ANSI_YELLOW = "\033[33m"
ANSI_MAGENTA = "\033[35m"
ANSI_BLUE = "\033[34m"
ANSI_RED = "\033[31m"
ANSI_GRAY = "\033[90m"

STATUS_COLORS = {
    "Init": ANSI_MAGENTA,
    "Update": ANSI_GREEN,
    "Predict": ANSI_YELLOW,
}

FIELD_COLORS = {
    "frame": ANSI_GRAY,
    "tx": ANSI_GRAY,
    "status": ANSI_BOLD,
    "ax": ANSI_CYAN,
    "ay": ANSI_CYAN,
    "gz": ANSI_CYAN,
    "px": ANSI_GREEN,
    "py": ANSI_GREEN,
    "dt": ANSI_BLUE,
    "mask": ANSI_MAGENTA,
    "err": ANSI_RED,
}

for _name in ("d1", "d2", "d3", "d4"):
    FIELD_COLORS[_name] = ANSI_YELLOW
for _name in ("amp1", "amp2", "amp3", "amp4"):
    FIELD_COLORS[_name] = ANSI_MAGENTA
for _name in ("snr1", "snr2", "snr3", "snr4"):
    FIELD_COLORS[_name] = ANSI_BLUE


FRAME_RE = re.compile(r"^\(\s*(?P<frame>\d+)\s*/\s*(?P<tx>\d+)\s*\)\s*(?P<rest>.*)$")
TOKEN_RE = re.compile(r"(?P<key>[A-Za-z_]\w*)\s*:\s*(?P<value>nan|[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)")
STATUS_RE = re.compile(r"^(?P<status>Init|Update|Predict)\b\s*(?P<rest>.*)$")

PREFERRED_FIELDS = [
    "frame", "tx", "status",
    "ax", "ay", "gz", "px", "py", "dt", "mask",
    "d1", "d2", "d3", "d4",
    "err",
    "amp1", "amp2", "amp3", "amp4",
    "snr1", "snr2", "snr3", "snr4",
]


def resolve_source_csv(source_path=None):
    if source_path:
        path = os.path.abspath(os.path.expanduser(source_path.strip()))
        if not os.path.isfile(path):
            raise FileNotFoundError(f"Khong tim thay file CSV: {path}")
        return path
    return find_latest_csv_file()


def generate_log_from_csv(source_path=None, output_path=None):
    csv_path = resolve_source_csv(source_path)
    rows = _read_rows(csv_path)
    if not rows:
        raise ValueError(f"File CSV khong co dong hop le: {csv_path}")

    fields = _collect_fields(rows)
    widths = _compute_widths(rows, fields)
    log_path = output_path or os.path.splitext(csv_path)[0] + ".log"
    html_path = os.path.splitext(log_path)[0] + ".html"

    with open(log_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(_build_header(csv_path, fields, widths, colorize=False))
        for row in rows:
            f.write(_format_row(row, fields, widths, colorize=False) + "\n")

    with open(html_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(_build_html(csv_path, rows, fields))

    return log_path, html_path


def _read_rows(csv_path):
    with open(csv_path, "r", encoding="utf-8", newline="") as f:
        sample = f.readline()
        f.seek(0)

        if "frame_counter" in sample and "," in sample:
            return _read_standard_csv(f)

        rows = []
        reader = csv.reader(f)
        for raw_row in reader:
            if not raw_row:
                continue
            line = ",".join(raw_row).strip()
            parsed = _parse_text_line(line)
            if parsed:
                rows.append(parsed)
        return rows


def _read_standard_csv(file_obj):
    rows = []
    reader = csv.DictReader(file_obj)
    for raw in reader:
        row = OrderedDict()
        for key, value in raw.items():
            if key is None:
                continue
            clean_key = key.strip()
            clean_value = "" if value is None else str(value).strip()
            if clean_key and clean_value != "":
                row[clean_key] = clean_value
        if row:
            _normalize_standard_keys(row)
            rows.append(row)
    return rows


def _normalize_standard_keys(row):
    aliases = {
        "frame_counter": "frame",
        "tx_frame_cnt": "tx",
        "anchor_mask": "mask",
    }
    for old, new in aliases.items():
        if old in row and new not in row:
            row[new] = row.pop(old)


def _parse_text_line(line):
    match = FRAME_RE.match(line)
    if not match:
        return None

    row = OrderedDict()
    row["frame"] = match.group("frame")
    row["tx"] = match.group("tx")

    rest = match.group("rest").strip()
    status_match = STATUS_RE.match(rest)
    if status_match:
        row["status"] = status_match.group("status")
        rest = status_match.group("rest")

    for token in TOKEN_RE.finditer(rest):
        key = token.group("key")
        row[key] = _normalize_number(token.group("value"), key)

    return row


def _normalize_number(value, key=None):
    if value.lower() == "nan":
        return "nan"
    try:
        number = float(value)
    except ValueError:
        return value
    if math.isfinite(number):
        if key in ("mask", "err"):
            return str(int(number))
        abs_number = abs(number)
        if abs_number >= 1_000_000 or (0 < abs_number < 0.0001):
            return f"{number:.6e}"
        return f"{number:.6f}"
    return value


def _collect_fields(rows):
    seen = set()
    fields = []

    for field in PREFERRED_FIELDS:
        if any(field in row for row in rows):
            fields.append(field)
            seen.add(field)

    for row in rows:
        for field in row:
            if field not in seen:
                fields.append(field)
                seen.add(field)

    return fields


def _compute_widths(rows, fields):
    widths = {}
    for field in fields:
        values = [str(row.get(field, "")) for row in rows]
        widths[field] = max(len(field), *(len(value) for value in values))
    return widths


def _build_header(csv_path, fields, widths, colorize=False):
    title = f"LOG VIEW: {os.path.basename(csv_path)}"
    separator_len = sum(widths[field] + 3 for field in fields) + 1
    separator = "=" * max(separator_len, len(title))
    header_cells = [
        _color(field.upper().ljust(widths[field]), FIELD_COLORS.get(field, ANSI_BOLD), colorize)
        for field in fields
    ]
    return (
        _color(separator, ANSI_DIM, colorize) + "\n"
        + _color(title, ANSI_BOLD, colorize) + "\n"
        + _color(separator, ANSI_DIM, colorize) + "\n"
        + " | ".join(header_cells) + "\n"
        + _color("-" * max(separator_len, len(title)), ANSI_DIM, colorize) + "\n"
    )


def _format_row(row, fields, widths, colorize=False):
    cells = []
    for field in fields:
        value = str(row.get(field, ""))
        if field in ("frame", "tx", "mask", "err"):
            text = value.rjust(widths[field])
        elif _looks_numeric(value):
            text = value.rjust(widths[field])
        else:
            text = value.ljust(widths[field])

        color = STATUS_COLORS.get(value) if field == "status" else FIELD_COLORS.get(field)
        cells.append(_color(text, color, colorize))
    return " | ".join(cells)


def _build_html(csv_path, rows, fields):
    title = f"LOG VIEW: {os.path.basename(csv_path)}"
    parts = [
        "<!doctype html>",
        "<html>",
        "<head>",
        '<meta charset="utf-8">',
        f"<title>{html.escape(title)}</title>",
        "<style>",
        "body{margin:0;background:#101214;color:#e7e9ee;font-family:Consolas,'Courier New',monospace;font-size:13px;}",
        ".wrap{padding:18px;}",
        "h1{font-size:18px;margin:0 0 14px;color:#ffffff;}",
        "table{border-collapse:collapse;white-space:nowrap;}",
        "th,td{padding:3px 9px;border-bottom:1px solid #252a31;text-align:right;}",
        "th{position:sticky;top:0;background:#171b21;color:#ffffff;z-index:1;}",
        "td.text,th.text{text-align:left;}",
        ".frame,.tx{color:#9ca3af}.status{font-weight:700}.Init{color:#d946ef}.Update{color:#22c55e}.Predict{color:#facc15}",
        ".imu{color:#22d3ee}.pos{color:#4ade80}.dt{color:#60a5fa}.mask{color:#e879f9}.dist{color:#facc15}.err{color:#f87171}.amp{color:#e879f9}.snr{color:#60a5fa}",
        "</style>",
        "</head>",
        "<body><div class=\"wrap\">",
        f"<h1>{html.escape(title)}</h1>",
        "<table>",
        "<thead><tr>",
    ]

    for field in fields:
        text_class = " text" if field == "status" else ""
        parts.append(f'<th class="{_html_class(field)}{text_class}">{html.escape(field.upper())}</th>')
    parts.append("</tr></thead><tbody>")

    for row in rows:
        parts.append("<tr>")
        for field in fields:
            value = str(row.get(field, ""))
            text_class = " text" if field == "status" else ""
            status_class = f" {value}" if field == "status" and value in STATUS_COLORS else ""
            parts.append(
                f'<td class="{_html_class(field)}{text_class}{status_class}">{html.escape(value)}</td>'
            )
        parts.append("</tr>")

    parts.extend(["</tbody></table>", "</div></body>", "</html>"])
    return "\n".join(parts) + "\n"


def _html_class(field):
    if field in ("frame", "tx"):
        return field
    if field in ("ax", "ay", "gz"):
        return "imu"
    if field in ("px", "py"):
        return "pos"
    if field == "dt":
        return "dt"
    if field == "mask":
        return "mask"
    if field.startswith("d"):
        return "dist"
    if field == "err":
        return "err"
    if field.startswith("amp"):
        return "amp"
    if field.startswith("snr"):
        return "snr"
    return field


def _looks_numeric(value):
    if value.lower() == "nan":
        return True
    try:
        float(value)
    except ValueError:
        return False
    return True


def _color(text, color, colorize=False):
    if not colorize or not color:
        return text
    return f"{color}{text}{ANSI_RESET}"
