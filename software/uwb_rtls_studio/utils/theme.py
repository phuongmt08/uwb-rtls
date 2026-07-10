"""
===============================================================================
  UWB RTLS Studio — Theme (Deep Slate Dark)
===============================================================================
  Color Palette:
    Background:    #0F172A    Surface:       #1E293B
    Border:        #334155    Text Primary:  #F8FAFC
    Text Dim:      #94A3B8    Accent/Cyan:   #22D3EE
    Primary/Blue:  #2563EB    Success/Green: #10B981
    Warning/Amber: #F59E0B    Danger/Red:    #EF4444
===============================================================================
"""

# ── Color Palette ─────────────────────────────────────────────────────
COLOR_BG         = "#0F172A"
COLOR_BG_DARKER  = "#0A0F1E"
COLOR_SURFACE    = "#1E293B"
COLOR_SURFACE_2  = "#253346"
COLOR_BORDER     = "#334155"
COLOR_BORDER_LT  = "#475569"
COLOR_TEXT        = "#F8FAFC"
COLOR_TEXT_DIM    = "#94A3B8"
COLOR_TEXT_DARK   = "#64748B"
COLOR_ACCENT     = "#22D3EE"
COLOR_ACCENT_DIM = "#0E7490"
COLOR_PRIMARY    = "#2563EB"
COLOR_SUCCESS    = "#10B981"
COLOR_WARNING    = "#F59E0B"
COLOR_DANGER     = "#EF4444"
COLOR_TERMINAL_BG   = "#020617"
COLOR_TERMINAL_TEXT = "#10B981"

# ── Full QSS Stylesheet ──────────────────────────────────────────────
DARK_STYLESHEET = f"""
/* ═══════════════════ GLOBAL ═══════════════════ */
QWidget {{
    background-color: {COLOR_BG};
    color: {COLOR_TEXT};
    font-family: 'Segoe UI', 'Inter', sans-serif;
    font-size: 13px;
}}

/* ═══════════════════ MAIN WINDOW ═══════════════ */
QMainWindow {{
    background-color: {COLOR_BG};
}}

/* ═══════════════════ GROUP BOX ═════════════════ */
QGroupBox {{
    background-color: {COLOR_SURFACE};
    border: 1px solid {COLOR_BORDER};
    border-radius: 8px;
    margin-top: 14px;
    padding: 16px 12px 12px 12px;
    font-weight: bold;
    font-size: 13px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 16px;
    padding: 2px 8px;
    color: {COLOR_ACCENT};
    background-color: {COLOR_SURFACE};
    border-radius: 4px;
}}

/* ═══════════════════ TAB WIDGET ════════════════ */
LeftTabWidget {{
    background-color: #13111C;
}}
QTabWidget::pane {{
    border: 1px solid {COLOR_BORDER};
    border-radius: 8px;
    background-color: {COLOR_BG};
    top: -1px;
}}
QTabBar::tab {{
    background-color: {COLOR_SURFACE};
    color: {COLOR_TEXT_DIM};
    border: 1px solid {COLOR_BORDER};
    border-bottom: none;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
    padding: 10px 20px;
    margin-right: 2px;
    font-weight: bold;
    min-width: 120px;
}}
QTabBar::tab:selected {{
    background-color: {COLOR_BG};
    color: {COLOR_ACCENT};
    border-bottom: 3px solid {COLOR_ACCENT};
}}
QTabBar::tab:hover:!selected {{
    background-color: {COLOR_SURFACE_2};
    color: {COLOR_TEXT};
}}

/* ═══════════════════ PUSH BUTTON ═══════════════ */
QPushButton {{
    background-color: {COLOR_SURFACE};
    color: {COLOR_TEXT};
    border: 1px solid {COLOR_BORDER};
    border-radius: 6px;
    padding: 4px 8px;
    font-weight: bold;
    min-height: 20px;
}}
QPushButton:hover {{
    background-color: {COLOR_SURFACE_2};
    border-color: {COLOR_ACCENT};
}}
QPushButton:pressed {{
    background-color: {COLOR_ACCENT_DIM};
    border-color: {COLOR_ACCENT};
}}
QPushButton:disabled {{
    background-color: {COLOR_BG_DARKER};
    color: {COLOR_TEXT_DARK};
    border-color: {COLOR_BORDER};
}}

/* Accent buttons */
QPushButton[class="accent"] {{
    background-color: {COLOR_ACCENT_DIM};
    color: {COLOR_TEXT};
    border: 1px solid {COLOR_ACCENT};
}}
QPushButton[class="accent"]:hover {{
    background-color: {COLOR_ACCENT};
    color: {COLOR_BG};
}}

/* Danger buttons */
QPushButton[class="danger"] {{
    background-color: rgba(239, 68, 68, 0.15);
    color: {COLOR_DANGER};
    border: 1px solid {COLOR_DANGER};
}}
QPushButton[class="danger"]:hover {{
    background-color: {COLOR_DANGER};
    color: {COLOR_TEXT};
}}

/* ═══════════════════ LINE EDIT ═════════════════ */
QLineEdit {{
    background-color: {COLOR_BG_DARKER};
    color: {COLOR_TEXT};
    border: 1px solid {COLOR_BORDER};
    border-radius: 6px;
    padding: 0px;
    selection-background-color: {COLOR_ACCENT_DIM};
}}
QLineEdit:focus {{
    border-color: {COLOR_ACCENT};
}}
QLineEdit:read-only {{
    background-color: {COLOR_SURFACE};
    color: {COLOR_TEXT_DIM};
}}

/* ═══════════════════ SPIN BOX ═════════════════ */
QSpinBox, QDoubleSpinBox {{
    background-color: {COLOR_BG_DARKER};
    color: {COLOR_TEXT};
    border: 1px solid {COLOR_BORDER};
    border-radius: 6px;
    padding: 6px 26px 6px 10px; /* Leave space on the right for up/down buttons */
}}
QSpinBox:focus, QDoubleSpinBox:focus {{
    border-color: {COLOR_ACCENT};
}}

/* ═══════════════════ COMBO BOX ════════════════ */
QComboBox {{
    background-color: {COLOR_BG_DARKER};
    color: {COLOR_TEXT};
    border: 1px solid {COLOR_BORDER};
    border-radius: 6px;
    padding: 8px 12px;
    min-width: 100px;
}}
QComboBox:focus {{
    border-color: {COLOR_ACCENT};
}}
QComboBox::drop-down {{
    border: none;
    width: 30px;
}}
QComboBox QAbstractItemView {{
    background-color: {COLOR_SURFACE};
    color: {COLOR_TEXT};
    border: 1px solid {COLOR_BORDER};
    selection-background-color: {COLOR_ACCENT_DIM};
    selection-color: {COLOR_TEXT};
}}

/* ═══════════════════ TABLE ════════════════════ */
QTableWidget, QTableView {{
    background-color: {COLOR_BG_DARKER};
    color: {COLOR_TEXT};
    border: 1px solid {COLOR_BORDER};
    border-radius: 6px;
    gridline-color: {COLOR_BORDER};
    selection-background-color: rgba(34, 211, 238, 0.15);
    selection-color: {COLOR_ACCENT};
    alternate-background-color: {COLOR_SURFACE};
}}
QHeaderView::section {{
    background-color: {COLOR_SURFACE};
    color: {COLOR_ACCENT};
    border: 1px solid {COLOR_BORDER};
    padding: 8px;
    font-weight: bold;
}}

/* ═══════════════════ SCROLL BAR ═══════════════ */
QScrollBar:vertical {{
    background-color: transparent;
    width: 8px;
    margin: 0px;
}}
QScrollBar::handle:vertical {{
    background-color: #475569;
    border-radius: 4px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{
    background-color: #64748B;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
    background: none;
}}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    background: none;
}}
QScrollBar:horizontal {{
    background-color: transparent;
    height: 8px;
    margin: 0px;
}}
QScrollBar::handle:horizontal {{
    background-color: #475569;
    border-radius: 4px;
    min-width: 30px;
}}
QScrollBar::handle:horizontal:hover {{
    background-color: #64748B;
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0px;
    background: none;
}}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
    background: none;
}}

/* ═══════════════════ PROGRESS BAR ═════════════ */
QProgressBar {{
    background-color: {COLOR_BG_DARKER};
    border: 1px solid {COLOR_BORDER};
    border-radius: 6px;
    text-align: center;
    color: {COLOR_TEXT};
    min-height: 20px;
}}
QProgressBar::chunk {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 {COLOR_ACCENT_DIM}, stop:1 {COLOR_ACCENT});
    border-radius: 5px;
}}

/* ═══════════════════ TEXT EDIT / PLAIN TEXT ════ */
QTextEdit, QPlainTextEdit {{
    background-color: {COLOR_TERMINAL_BG};
    color: {COLOR_TERMINAL_TEXT};
    border: 1px solid {COLOR_BORDER};
    border-radius: 6px;
    padding: 8px;
    font-family: 'Cascadia Code', 'Consolas', monospace;
    font-size: 12px;
}}

/* ═══════════════════ LABEL ═════════════════════ */
QLabel {{
    color: {COLOR_TEXT};
    background: transparent;
}}

/* ═══════════════════ CHECK BOX ════════════════ */
QCheckBox {{
    color: {COLOR_TEXT};
    spacing: 8px;
}}
QCheckBox::indicator {{
    width: 18px;
    height: 18px;
    border: 1px solid {COLOR_BORDER};
    border-radius: 4px;
    background-color: {COLOR_BG_DARKER};
}}
QCheckBox::indicator:checked {{
    background-color: {COLOR_ACCENT};
    border-color: {COLOR_ACCENT};
}}

/* ═══════════════════ SLIDER ═══════════════════ */
QSlider::groove:horizontal {{
    background-color: {COLOR_BORDER};
    height: 6px;
    border-radius: 3px;
}}
QSlider::handle:horizontal {{
    background-color: {COLOR_ACCENT};
    width: 16px;
    height: 16px;
    margin: -5px 0;
    border-radius: 8px;
}}
QSlider::sub-page:horizontal {{
    background-color: {COLOR_ACCENT};
    border-radius: 3px;
}}

/* ═══════════════════ TOOL TIP ═════════════════ */
QToolTip {{
    background-color: {COLOR_SURFACE};
    color: {COLOR_TEXT};
    border: 1px solid {COLOR_ACCENT};
    border-radius: 4px;
    padding: 6px;
}}

/* ═══════════════════ DIALOG ═══════════════════ */
QDialog {{
    background-color: {COLOR_BG};
}}

/* ═══════════════════ STATUS BAR ═══════════════ */
QStatusBar {{
    background-color: {COLOR_SURFACE};
    color: {COLOR_TEXT_DIM};
    border-top: 1px solid {COLOR_BORDER};
}}

/* ═══════════════════ SCROLL AREA ═════════════ */
QScrollArea {{
    border: none;
    background-color: transparent;
}}

/* ═══════════════════ FRAME ═══════════════════ */
QFrame[class="card"] {{
    background-color: {COLOR_SURFACE};
    border: 1px solid {COLOR_BORDER};
    border-radius: 8px;
    padding: 12px;
}}

QFrame[class="separator"] {{
    background-color: {COLOR_BORDER};
    max-height: 1px;
}}
"""