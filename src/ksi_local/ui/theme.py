"""Halite-referenced native Qt design tokens, not a second web runtime."""

from PySide6.QtGui import QColor, QPalette


def studio_palette(dark: bool) -> QPalette:
    palette = QPalette()
    values = {
        "Window": "#1e1e20" if dark else "#f5f5f7",
        "WindowText": "#f5f5f7" if dark else "#1d1d1f",
        "Base": "#26262a" if dark else "#ffffff",
        "AlternateBase": "#2a2a2e" if dark else "#ededf0",
        "Text": "#f5f5f7" if dark else "#1d1d1f",
        "Button": "#26262a" if dark else "#ffffff",
        "ButtonText": "#f5f5f7" if dark else "#1d1d1f",
        "Midlight": "#454549" if dark else "#d2d2d7",
        "Mid": "#98989d" if dark else "#6e6e73",
        "Highlight": "#8b7cff" if dark else "#6c5ce7",
        "HighlightedText": "#ffffff",
        "ToolTipBase": "#2a2a2e" if dark else "#ffffff",
        "ToolTipText": "#f5f5f7" if dark else "#1d1d1f",
        "PlaceholderText": "#98989d" if dark else "#6e6e73",
    }
    for role, value in values.items():
        palette.setColor(getattr(QPalette.ColorRole, role), QColor(value))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(values["Mid"]))
    return palette


def studio_stylesheet(dark: bool) -> str:
    text = "#f5f5f7" if dark else "#1d1d1f"
    muted = "#98989d" if dark else "#6e6e73"
    border = "#414145" if dark else "#d2d2d7"
    card = "#26262a" if dark else "#ffffff"
    active = "#353047" if dark else "#e8e4ff"
    accent = "#8b7cff" if dark else "#6c5ce7"
    return f"""
        QWidget {{ color: {text}; font-size: 14px; }}
        QWidget#studioSidebar {{ border-right: 1px solid {border}; }}
        QLabel#brandTitle {{ font-size: 18px; font-weight: 700; }}
        QLabel#brandDescription, QLabel#pageIntro, QLabel#mutedLabel {{ color: {muted}; }}
        QLabel#pageHeading {{ font-size: 26px; font-weight: 700; }}
        QLabel#sectionLabel {{ font-size: 16px; font-weight: 600; }}
        QLabel#hintLabel {{ font-size: 12px; color: {muted}; }}
        QFrame[card="true"], QLabel[helpCard="true"] {{
            background: {card}; border: 1px solid {border}; border-radius: 12px;
        }}
        QLabel[helpCard="true"] {{ padding: 18px; }}
        QGroupBox {{
            background: {card}; border: 1px solid {border}; border-radius: 12px;
            margin-top: 20px; padding: 18px 12px 12px; font-weight: 600;
        }}
        QGroupBox::title {{ subcontrol-origin: margin; left: 14px; padding: 0 4px; }}
        QPushButton {{
            background: {card}; border: 1px solid {border}; border-radius: 8px;
            padding: 10px 16px; font-weight: 600; min-height: 20px;
        }}
        QPushButton:hover {{ border-color: {accent}; }}
        QPushButton:focus {{ border: 2px solid {accent}; padding: 9px 15px; }}
        QPushButton:disabled {{ color: {muted}; border-color: {border}; }}
        QPushButton#primaryButton, QPushButton[primary="true"] {{
            background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                stop:0 #8b7cff, stop:1 #00bec9);
            border: none; color: white; padding: 12px 20px; font-weight: 700;
        }}
        QPushButton#primaryButton:disabled, QPushButton[primary="true"]:disabled {{
            background: {border}; color: {muted};
        }}
        QPushButton[nav="true"] {{
            background: transparent; border: none; border-radius: 9px;
            text-align: left; padding: 12px 14px; font-size: 15px;
        }}
        QPushButton[nav="true"]:checked {{ background: {active}; color: {accent}; }}
        QPushButton[nav="true"]:hover {{ background: {active}; }}
        QPushButton[kindCard="true"] {{ text-align: left; }}
        QPushButton[kindCard="true"]:checked {{ background: {active}; border-color: {accent}; }}
        QLineEdit, QComboBox, QPlainTextEdit, QSpinBox, QDoubleSpinBox {{
            background: {card}; color: {text}; border: 1px solid {border};
            border-radius: 8px; padding: 8px 10px; min-height: 20px;
            selection-background-color: {accent}; selection-color: white;
        }}
        QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus {{ border-color: {accent}; }}
        QComboBox QAbstractItemView {{ background: {card}; color: {text}; }}
        QFrame#dropZone {{ background: {card}; border: 2px dashed {border}; border-radius: 12px; }}
        QFrame#dropZone[dragActive="true"] {{ border-color: {accent}; }}
        QLabel#modelBadge, QLabel#localBadge {{
            background: {active}; color: {accent}; border-radius: 10px; padding: 5px 10px;
        }}
        QLabel#statusLabel {{ background: {card}; border: 1px solid {border}; border-radius: 8px; padding: 10px; }}
        QTableWidget {{ background: {card}; alternate-background-color: palette(alternate-base);
            border: 1px solid {border}; border-radius: 8px; gridline-color: {border}; }}
        QHeaderView::section {{ background: {card}; border: none; border-bottom: 1px solid {border}; padding: 8px; }}
        QProgressBar {{ background: {card}; border: 1px solid {border}; border-radius: 5px; text-align: center; }}
        QProgressBar::chunk {{ background: {accent}; border-radius: 4px; }}
        QTabWidget#mainTabs::pane {{ border: none; }}
        QScrollArea {{ border: none; background: transparent; }}
        QScrollBar:vertical {{ background: transparent; width: 8px; }}
        QScrollBar::handle:vertical {{ background: {border}; border-radius: 4px; min-height: 28px; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
    """
