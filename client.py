#!/usr/bin/env python3
"""InkSync - Miro-style PySide6 client for the LAN collaborative whiteboard.

Run:  python whiteboard_client_expanded.py
Needs: pip install PySide6        (server: whiteboard_server_expanded.py, updated version)
"""
import sys, socket, threading, queue, time, os, base64, html, math, copy, zlib, secrets
from PySide6.QtCore import (Qt, Signal, QObject, QPointF, QRectF, QSizeF, QPoint, QTimer, QSize, QEvent,
                            QBuffer, QIODevice, QSettings, QByteArray)
from PySide6.QtGui import (QPainter, QPen, QColor, QFont, QPixmap, QImage, QIcon, QPainterPath,
                           QBrush, QPolygonF, QFontMetricsF, QGuiApplication, QTextOption)
from PySide6.QtPrintSupport import QPrinter
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLineEdit, QLabel, QColorDialog, QTextEdit, QPlainTextEdit, QMessageBox, QFileDialog, QDialog,
    QFormLayout, QToolButton, QFrame, QMenu, QGraphicsDropShadowEffect, QScrollArea, QStackedWidget,
    QSizePolicy, QGridLayout)

from whiteboard.protocol import encode, decode_line, MAX_MESSAGE

# ───────────────────────────── world / theme ─────────────────────────────
WORLD_W, WORLD_H = 10000.0, 6250.0          # board size in world units (server stores 0..1 of this)
INK = "#050038"; ACCENT = "#4262ff"; ACCENT_SOFT = "#e7ebff"; BORDER = "#e0e2e8"
BOARD_BG = "#f7f8fa"; OUTSIDE_BG = "#eceef2"; GRID = "#e6e8ee"
UI_FONT = "Segoe UI, SF Pro Text, Helvetica Neue, Arial"
STICKY_COLORS = ["#F9E163", "#A9A0F5", "#D5F57A", "#F5A862", "#F4A6EE", "#7CB7F7", "#8EE5C0", "#FF8A8A", "#FFFFFF"]
FILL_COLORS = ["", "#FFFFFF", "#7CB7F7", "#F9E163", "#D5F57A", "#F5A862", "#A9A0F5", "#F4A6EE", "#8EE5C0", "#FF8A8A", "#CBD0DB", "#1A1A2E"]
STROKE_COLORS = ["#1A1A2E", "#E5484D", "#F5A623", "#2AB673", "#4262FF", "#8E44EF", "#FF66B3", "#808797", "#FFFFFF"]
USER_COLORS = ["#4262ff", "#e5446d", "#18a96b", "#f59e0b", "#8b5cf6", "#0ea5c6", "#ef4444", "#d946ef"]
REACTIONS = ["🎉", "👍", "❤️", "😂", "👏", "🔥", "🤔", "✅"]
EMOJI_STICKERS = ["⭐", "❤️", "💡", "📌", "🎯", "🚀", "🌱", "☀️", "☁️", "🏠", "📷", "🎨", "💬", "✅", "⚠️", "🎉", "📍", "🔔"]
SHAPE_KINDS = ("rect", "rounded", "ellipse", "triangle", "diamond", "frame")
LINE_KINDS = ("line", "arrow")
STROKE_KINDS = ("stroke", "highlighter")
# raw Qt text flags as ints (avoids enum-mixing differences between PySide6 versions)
F_CENTER_WRAP = 0x0004 | 0x0080 | 0x1000 | 0x0200
F_LEFT_WRAP = 0x0001 | 0x0020 | 0x1000 | 0x0200
F_LEFT = 0x0001 | 0x0020 | 0x0200

def user_color(name):
    return USER_COLORS[zlib.crc32(name.encode("utf-8")) % len(USER_COLORS)]
def pastel(hexcolor, k=0.74):
    c = QColor(hexcolor)
    return QColor(int(c.red() + (255 - c.red()) * k), int(c.green() + (255 - c.green()) * k), int(c.blue() + (255 - c.blue()) * k))
def initials(name):
    parts = [p for p in name.replace("_", " ").split() if p]
    if not parts: return "?"
    return (parts[0][0] + (parts[1][0] if len(parts) > 1 else "")).upper()
def ink_for(fill):
    c = QColor(fill) if fill else QColor("#ffffff")
    return "#ffffff" if (0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()) < 120 else "#1A1A2E"
def ui_font(px, weight=QFont.Medium):
    f = QFont(); f.setFamilies([s.strip() for s in UI_FONT.split(",")]); f.setPixelSize(px); f.setWeight(weight); return f

# ───────────────────────────── vector icons ─────────────────────────────
def _pen(p, c, w=1.8):
    pen = QPen(QColor(c), w, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin); p.setPen(pen); p.setBrush(Qt.NoBrush)
def _arrowhead(p, tip, ang, size=5.0):
    a = QPointF(tip.x() - size * math.cos(ang - 0.5), tip.y() - size * math.sin(ang - 0.5))
    b = QPointF(tip.x() - size * math.cos(ang + 0.5), tip.y() - size * math.sin(ang + 0.5))
    p.drawLine(tip, a); p.drawLine(tip, b)
def _poly(pts): return QPolygonF([QPointF(x, y) for x, y in pts])
def _text(p, c, s, px=16):
    p.setPen(QColor(c)); f = ui_font(px, QFont.Bold); p.setFont(f); p.drawText(QRectF(0, 0, 24, 24), 0x0084, s)

def _i_select(p, c): p.setPen(QPen(QColor(c), 1.4, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)); p.setBrush(QColor(c)); p.drawPolygon(_poly([(6, 4), (6, 19), (10, 15), (13, 21), (15.5, 19.8), (12.6, 14), (18, 14)]))
def _i_hand(p, c):
    _pen(p, c); path = QPainterPath(); path.moveTo(8, 12); path.lineTo(8, 6.5); path.moveTo(11, 11); path.lineTo(11, 4.5); path.moveTo(14, 11); path.lineTo(14, 5.5)
    path.moveTo(17, 12); path.lineTo(17, 8); path.moveTo(8, 12); path.lineTo(6.5, 10.5); path.moveTo(8, 12); path.lineTo(8, 14.5)
    path.cubicTo(8, 19, 11, 21, 13, 21); path.cubicTo(16, 21, 17, 18, 17, 15); path.lineTo(17, 12); p.drawPath(path)
def _i_text(p, c): _pen(p, c, 2); p.drawLine(QPointF(6, 6.5), QPointF(18, 6.5)); p.drawLine(QPointF(12, 6.5), QPointF(12, 18.5)); p.drawLine(QPointF(9, 18.5), QPointF(15, 18.5))
def _i_sticky(p, c):
    _pen(p, c); path = QPainterPath(); path.moveTo(5, 5); path.lineTo(19, 5); path.lineTo(19, 14); path.lineTo(14, 19); path.lineTo(5, 19); path.closeSubpath(); p.drawPath(path)
    p.drawLine(QPointF(19, 14), QPointF(14, 14)); p.drawLine(QPointF(14, 14), QPointF(14, 19))
def _i_shape(p, c): _pen(p, c); p.drawRoundedRect(QRectF(4.5, 10, 9.5, 9.5), 1.5, 1.5); p.drawEllipse(QPointF(15, 9), 5, 5)
def _i_rect(p, c): _pen(p, c); p.drawRect(QRectF(4.5, 6.5, 15, 11))
def _i_rounded(p, c): _pen(p, c); p.drawRoundedRect(QRectF(4.5, 6.5, 15, 11), 4, 4)
def _i_ellipse(p, c): _pen(p, c); p.drawEllipse(QRectF(4.5, 5.5, 15, 13))
def _i_triangle(p, c): _pen(p, c); p.drawPolygon(_poly([(12, 5), (19.5, 18.5), (4.5, 18.5)]))
def _i_diamond(p, c): _pen(p, c); p.drawPolygon(_poly([(12, 4), (20, 12), (12, 20), (4, 12)]))
def _i_arrow(p, c): _pen(p, c); p.drawLine(QPointF(5, 19), QPointF(19, 5)); _arrowhead(p, QPointF(19, 5), -math.pi / 4, 6)
def _i_line(p, c): _pen(p, c); p.drawLine(QPointF(5, 19), QPointF(19, 5))
def _i_curve(p, c):
    _pen(p, c); path = QPainterPath(); path.moveTo(5, 18); path.quadTo(6, 7, 18, 7); p.drawPath(path); _arrowhead(p, QPointF(18, 7), -0.1, 5.5)
def _i_pen(p, c):
    _pen(p, c); p.drawPolygon(_poly([(5, 19.5), (6, 15), (15.5, 5.5), (18.5, 8.5), (9, 18)])); p.drawLine(QPointF(13.5, 7.5), QPointF(16.5, 10.5))
def _i_marker(p, c):
    _pen(p, c); p.drawPolygon(_poly([(8, 15), (15, 5.5), (18.5, 8.5), (11, 17)])); p.drawLine(QPointF(8, 15), QPointF(5.5, 19)); p.drawLine(QPointF(5.5, 19), QPointF(11, 17)); p.drawLine(QPointF(10, 20), QPointF(19, 20))
def _i_eraser(p, c):
    _pen(p, c); p.save(); p.translate(12, 12); p.rotate(-45); p.drawRoundedRect(QRectF(-8, -4.5, 16, 9), 2, 2); p.drawLine(QPointF(-1.5, -4.5), QPointF(-1.5, 4.5)); p.restore()
    p.drawLine(QPointF(10, 19.5), QPointF(19.5, 19.5))
def _i_frame(p, c): _pen(p, c); [p.drawLine(QPointF(*a), QPointF(*b)) for a, b in (((8, 4), (8, 20)), ((16, 4), (16, 20)), ((4, 8), (20, 8)), ((4, 16), (20, 16)))]
def _i_plus(p, c): _pen(p, c, 2.2); p.drawLine(QPointF(12, 5.5), QPointF(12, 18.5)); p.drawLine(QPointF(5.5, 12), QPointF(18.5, 12))
def _i_minus(p, c): _pen(p, c, 2.2); p.drawLine(QPointF(5.5, 12), QPointF(18.5, 12))
def _i_undo(p, c):
    _pen(p, c, 2); path = QPainterPath(); path.moveTo(7, 9.5); path.lineTo(15, 9.5); path.cubicTo(19, 9.5, 19, 18, 15, 18); path.lineTo(8, 18); p.drawPath(path)
    p.drawLine(QPointF(7, 9.5), QPointF(10.5, 6)); p.drawLine(QPointF(7, 9.5), QPointF(10.5, 13))
def _i_redo(p, c):
    _pen(p, c, 2); path = QPainterPath(); path.moveTo(17, 9.5); path.lineTo(9, 9.5); path.cubicTo(5, 9.5, 5, 18, 9, 18); path.lineTo(16, 18); p.drawPath(path)
    p.drawLine(QPointF(17, 9.5), QPointF(13.5, 6)); p.drawLine(QPointF(17, 9.5), QPointF(13.5, 13))
def _i_gear(p, c):
    _pen(p, c, 1.8); p.drawEllipse(QPointF(12, 12), 3, 3); p.drawEllipse(QPointF(12, 12), 6.6, 6.6)
    for k in range(8):
        a = k * math.pi / 4; p.drawLine(QPointF(12 + 6.6 * math.cos(a), 12 + 6.6 * math.sin(a)), QPointF(12 + 9 * math.cos(a), 12 + 9 * math.sin(a)))
def _i_export(p, c):
    _pen(p, c, 1.9); p.drawLine(QPointF(12, 15), QPointF(12, 4.5)); p.drawLine(QPointF(12, 4.5), QPointF(8, 8.5)); p.drawLine(QPointF(12, 4.5), QPointF(16, 8.5))
    path = QPainterPath(); path.moveTo(5, 12); path.lineTo(5, 19.5); path.lineTo(19, 19.5); path.lineTo(19, 12); p.drawPath(path)
def _i_search(p, c): _pen(p, c, 2); p.drawEllipse(QPointF(10.5, 10.5), 5.8, 5.8); p.drawLine(QPointF(15, 15), QPointF(19.5, 19.5))
def _i_chat(p, c):
    _pen(p, c); path = QPainterPath(); path.addRoundedRect(QRectF(4.5, 5, 15, 11.5), 3, 3); p.drawPath(path)
    p.drawPolyline(_poly([(9, 16.5), (9, 20), (13, 16.5)])); p.drawLine(QPointF(8.5, 9.5), QPointF(15.5, 9.5)); p.drawLine(QPointF(8.5, 12.5), QPointF(13, 12.5))
def _i_apps(p, c): _pen(p, c); p.drawRoundedRect(QRectF(4.5, 4.5, 6, 6), 1.2, 1.2); p.drawRoundedRect(QRectF(4.5, 13.5, 6, 6), 1.2, 1.2); p.drawRoundedRect(QRectF(13.5, 13.5, 6, 6), 1.2, 1.2); p.drawLine(QPointF(16.5, 4.5), QPointF(16.5, 10.5)); p.drawLine(QPointF(13.5, 7.5), QPointF(19.5, 7.5))
def _i_laser(p, c):
    _pen(p, c); p.drawLine(QPointF(5, 5), QPointF(5, 15)); p.drawLine(QPointF(5, 5), QPointF(7.2, 7.2))
    p.setBrush(QColor(c)); p.drawEllipse(QPointF(15, 15), 2.4, 2.4); _pen(p, c, 1.5)
    for a in (0, 60, 120, 180, 240, 300):
        r = math.radians(a); p.drawLine(QPointF(15 + 4.4 * math.cos(r), 15 + 4.4 * math.sin(r)), QPointF(15 + 6.4 * math.cos(r), 15 + 6.4 * math.sin(r)))
def _i_party(p, c):
    _pen(p, c); p.drawPolygon(_poly([(4.5, 19.5), (8, 8), (16, 16)])); p.drawLine(QPointF(13, 5), QPointF(13.5, 7)); p.drawLine(QPointF(17.5, 9.5), QPointF(19.5, 9)); p.drawLine(QPointF(16, 4.5), QPointF(18, 6.5)); p.drawEllipse(QPointF(19, 14), 1, 1)
def _i_panel(p, c): _pen(p, c); p.drawRoundedRect(QRectF(4.5, 5, 15, 14), 2, 2); p.drawLine(QPointF(10, 5), QPointF(10, 19))
def _i_chevron(p, c): _pen(p, c, 2); p.drawPolyline(_poly([(7, 10), (12, 15), (17, 10)]))
def _i_image(p, c): _pen(p, c); p.drawRoundedRect(QRectF(4.5, 5.5, 15, 13), 2, 2); p.drawEllipse(QPointF(9.5, 10), 1.5, 1.5); p.drawPolyline(_poly([(5, 17.5), (10.5, 13), (14, 16), (16.5, 13.5), (19, 16)]))
def _i_emoji(p, c): _pen(p, c); p.drawEllipse(QPointF(12, 12), 7.5, 7.5); p.drawPoint(QPointF(9.5, 10)); p.drawPoint(QPointF(14.5, 10)); path = QPainterPath(); path.moveTo(8.5, 14); path.quadTo(12, 18, 15.5, 14); p.drawPath(path)
def _i_trash(p, c): _pen(p, c); p.drawLine(QPointF(5, 7), QPointF(19, 7)); p.drawPolyline(_poly([(9.5, 7), (9.5, 5), (14.5, 5), (14.5, 7)])); p.drawPolygon(_poly([(7, 7), (8, 19.5), (16, 19.5), (17, 7)]))
def _i_copy(p, c): _pen(p, c); p.drawRoundedRect(QRectF(8.5, 8.5, 10, 10), 1.8, 1.8); path = QPainterPath(); path.moveTo(5.5, 14.5); path.lineTo(5.5, 6.5); path.quadTo(5.5, 5.5, 6.5, 5.5); path.lineTo(14.5, 5.5); p.drawPath(path)
def _i_dash(p, c): _pen(p, c, 2); p.drawLine(QPointF(4, 12), QPointF(8, 12)); p.drawLine(QPointF(10, 12), QPointF(14, 12)); p.drawLine(QPointF(16, 12), QPointF(20, 12))
def _i_fit(p, c):
    _pen(p, c, 1.9)
    for (x, y, dx, dy) in ((5, 5, 1, 1), (19, 5, -1, 1), (5, 19, 1, -1), (19, 19, -1, -1)):
        p.drawLine(QPointF(x, y), QPointF(x + 5 * dx, y)); p.drawLine(QPointF(x, y), QPointF(x, y + 5 * dy))
def _i_comment(p, c): _i_chat(p, c)
def _i_help(p, c): _text(p, c, "?", 17)
def _i_close(p, c): _pen(p, c, 2); p.drawLine(QPointF(7, 7), QPointF(17, 17)); p.drawLine(QPointF(17, 7), QPointF(7, 17))
def _i_send(p, c): _pen(p, c, 1.8); p.drawPolygon(_poly([(4.5, 5.5), (20, 12), (4.5, 18.5), (7.5, 12)])); p.drawLine(QPointF(7.5, 12), QPointF(20, 12))
def _i_people(p, c): _pen(p, c); p.drawEllipse(QPointF(9.5, 9), 3.2, 3.2); p.drawEllipse(QPointF(16.5, 10), 2.4, 2.4); path = QPainterPath(); path.moveTo(4, 19); path.quadTo(4, 13.5, 9.5, 13.5); path.quadTo(15, 13.5, 15, 19); p.drawPath(path)
def _i_present(p, c): _pen(p, c); p.drawRoundedRect(QRectF(4, 5, 16, 11), 1.5, 1.5); p.drawLine(QPointF(12, 16), QPointF(12, 20)); p.drawLine(QPointF(8, 20), QPointF(16, 20))
ICONS = {k[3:]: v for k, v in list(globals().items()) if k.startswith("_i_")}
_ICON_CACHE = {}
def make_icon(name, color=INK, on=ACCENT, size=22):
    key = (name, color, on, size)
    if key in _ICON_CACHE: return _ICON_CACHE[key]
    ic = QIcon()
    for col, mode, state in ((color, QIcon.Normal, QIcon.Off), (on, QIcon.Normal, QIcon.On), ("#b6bac7", QIcon.Disabled, QIcon.Off)):
        pm = QPixmap(size * 2, size * 2); pm.setDevicePixelRatio(2); pm.fill(Qt.transparent)
        p = QPainter(pm); p.setRenderHint(QPainter.Antialiasing); p.scale(size / 24.0, size / 24.0); ICONS[name](p, col); p.end()
        ic.addPixmap(pm, mode, state)
    _ICON_CACHE[key] = ic
    return ic

# ───────────────────────────── networking (unchanged logic) ─────────────────────────────
class NetSignals(QObject):
    message = Signal(dict)
    status = Signal(str)
    disconnected = Signal()

class Network:
    """Reconnects with bounded exponential backoff; the server snapshot repairs local state."""
    def __init__(self, host, port, rejoin_message=None):
        self.host, self.port = host, port
        self.rejoin_message = rejoin_message
        self.initial_message = None
        self.signals = NetSignals()
        self.sock = None
        self.out = queue.Queue()
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()
    def send(self, obj):
        if self.stop_event.is_set(): return
        # Retain the first create/join request so a connection failure before
        # the initial acknowledgement does not leave the client unable to retry.
        if self.initial_message is None and obj.get("type") in ("create", "join"):
            self.initial_message = dict(obj)
        self.out.put(obj)
    def _drain(self):
        while True:
            try: self.out.get_nowait()
            except queue.Empty: break
    def run(self):
        delay = 1.0
        while not self.stop_event.is_set():
            try:
                self.sock = socket.create_connection((self.host, self.port), timeout=5)
                self.sock.settimeout(.5)
                self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
                self.signals.status.emit("Connected to server")
                last_rx = time.monotonic(); last_ping = last_rx
                first = self.rejoin_message or self.initial_message
                if first:
                    self.sock.sendall(encode(first))
                    # The first request is also queued by send(); consume that
                    # queued copy so it is not transmitted twice on one socket.
                    if not self.rejoin_message:
                        try:
                            queued = self.out.get_nowait()
                            if queued != first: self.out.put(queued)
                        except queue.Empty:
                            pass
                else:
                    try:
                        first = self.out.get(timeout=3)
                        self.initial_message = dict(first)
                        self.sock.sendall(encode(first))
                    except queue.Empty:
                        raise ConnectionError("No initial room request was supplied")
                delay = 1.0
                buf = b""
                while not self.stop_event.is_set():
                    try:
                        while True:
                            item = self.out.get_nowait()
                            self.sock.sendall(encode(item))
                    except queue.Empty: pass
                    try: data = self.sock.recv(65536)
                    except socket.timeout:
                        now = time.monotonic()
                        if now - last_ping > 15:
                            self.sock.sendall(encode({"type": "ping"})); last_ping = now
                        if now - last_rx > 45: raise ConnectionError("Server heartbeat timed out")
                        continue
                    if not data: raise ConnectionError("Server closed the connection")
                    last_rx = time.monotonic()
                    buf += data
                    if len(buf) > MAX_MESSAGE and b"\n" not in buf: raise ValueError("Oversized server frame")
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        if line: self.signals.message.emit(decode_line(line))
            except Exception as exc:
                if not self.stop_event.is_set():
                    self.signals.status.emit(f"Disconnected: {exc}. Reconnecting in {delay:.0f}s…")
                    self._drain()
                    self.stop_event.wait(delay)
                    delay = min(delay * 2, 15.0)
            finally:
                try:
                    if self.sock: self.sock.close()
                except OSError: pass
                self.sock = None
        self.signals.disconnected.emit()
    def close(self):
        self.stop_event.set()
        try:
            if self.sock: self.sock.shutdown(socket.SHUT_RDWR)
        except OSError: pass

# ───────────────────────────── shared UI pieces ─────────────────────────────
def shadow(widget, blur=22, dy=4, alpha=38):
    e = QGraphicsDropShadowEffect(widget); e.setBlurRadius(blur); e.setOffset(0, dy); e.setColor(QColor(5, 0, 56, alpha)); widget.setGraphicsEffect(e)

class Card(QFrame):
    """White rounded floating panel (the Miro look)."""
    def __init__(self, parent=None, radius=10, shadowed=True):
        super().__init__(parent)
        self.setObjectName("card")
        self.setStyleSheet(f"#card{{background:#ffffff;border:1px solid {BORDER};border-radius:{radius}px;}}")
        if shadowed: shadow(self)

BTN_QSS = f"""
QToolButton{{background:transparent;border:0;border-radius:8px;color:{INK};}}
QToolButton:hover{{background:#f0f1f5;}}
QToolButton:checked{{background:{ACCENT_SOFT};}}
QToolButton:disabled{{color:#b6bac7;}}
QToolButton::menu-indicator{{image:none;}}
"""
def tbtn(icon=None, tip="", size=40, isize=22, checkable=False, text=None, cb=None):
    b = QToolButton(); b.setFixedSize(size, size) if size else None; b.setCursor(Qt.PointingHandCursor)
    if icon: b.setIcon(make_icon(icon, size=isize)); b.setIconSize(QSize(isize, isize))
    if text: b.setText(text)
    b.setToolTip(tip); b.setCheckable(checkable); b.setStyleSheet(BTN_QSS); b.setFocusPolicy(Qt.NoFocus)
    if cb: b.clicked.connect(cb)
    return b

class Popup(QFrame):
    """Small floating popup (tool flyouts, colour palettes)."""
    def __init__(self, parent=None):
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint)
        self.setStyleSheet(f"Popup{{background:#ffffff;border:1px solid {BORDER};border-radius:10px;}}")
        self.lay = QGridLayout(self); self.lay.setContentsMargins(8, 8, 8, 8); self.lay.setSpacing(4)
    def show_at(self, anchor, side="right"):
        self.adjustSize()
        if side == "right": pos = anchor.mapToGlobal(QPoint(anchor.width() + 10, (anchor.height() - self.height()) // 2))
        else: pos = anchor.mapToGlobal(QPoint((anchor.width() - self.width()) // 2, -self.height() - 10))
        self.move(pos); self.show()

class Swatch(QToolButton):
    def __init__(self, color, size=26, ring=False):
        super().__init__(); self.setFixedSize(size, size); self.setCursor(Qt.PointingHandCursor); self.setFocusPolicy(Qt.NoFocus)
        self.color = color; self.ring = ring; self.size_ = size
    def paintEvent(self, e):
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(3, 3, self.size_ - 6, self.size_ - 6)
        if self.color == "":
            p.setPen(QPen(QColor("#b6bac7"), 1.5)); p.setBrush(QColor("#ffffff")); p.drawEllipse(r); p.setPen(QPen(QColor("#e5484d"), 1.6)); p.drawLine(r.bottomLeft() + QPointF(2, -2), r.topRight() + QPointF(-2, 2))
        else:
            p.setPen(QPen(QColor("#c9ccd6"), 1) if QColor(self.color).lightness() > 235 else Qt.NoPen); p.setBrush(QColor(self.color)); p.drawEllipse(r)
        if self.ring:
            p.setBrush(Qt.NoBrush); p.setPen(QPen(QColor(ACCENT), 2)); p.drawEllipse(QRectF(1, 1, self.size_ - 2, self.size_ - 2))

class ColorPopup(Popup):
    def __init__(self, colors, current, callback, allow_custom=True, parent=None):
        super().__init__(parent)
        for i, c in enumerate(colors):
            s = Swatch(c, ring=(c.lower() == (current or "").lower())); s.clicked.connect(lambda _=False, col=c: (callback(col), self.close()))
            self.lay.addWidget(s, i // 6, i % 6)
        if allow_custom:
            more = QToolButton(); more.setText("Custom…"); more.setStyleSheet(f"QToolButton{{border:0;color:{ACCENT};font-weight:600;padding:4px;}}"); more.setCursor(Qt.PointingHandCursor)
            more.clicked.connect(lambda: self._custom(current, callback)); self.lay.addWidget(more, (len(colors) + 5) // 6, 0, 1, 6)
    def _custom(self, current, callback):
        self.close(); c = QColorDialog.getColor(QColor(current or "#ffffff"), None, "Pick a colour")
        if c.isValid(): callback(c.name())

class Avatar(QWidget):
    def __init__(self, name, size=34, ring="#ffffff", text=None, bg=None, fg="#ffffff"):
        super().__init__(); self.setFixedSize(size, size); self.name, self.size_, self.ring, self.text_, self.bg, self.fg = name, size, ring, text, bg, fg
        self.setToolTip(name)
    def paintEvent(self, e):
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        s = self.size_; col = QColor(self.bg or user_color(self.name))
        p.setPen(QPen(QColor(self.ring), 2.5)); p.setBrush(col); p.drawEllipse(QRectF(1.25, 1.25, s - 2.5, s - 2.5))
        p.setPen(QColor(self.fg)); p.setFont(ui_font(int(s * 0.38), QFont.DemiBold)); p.drawText(QRectF(0, 0, s, s), 0x0084, self.text_ or initials(self.name))

class AvatarStack(QWidget):
    """Overlapping avatars of the people currently in the room, plus the '+N' overflow bubble."""
    def __init__(self):
        super().__init__(); self.lay = QHBoxLayout(self); self.lay.setContentsMargins(0, 0, 0, 0); self.lay.setSpacing(-9)
    def set_people(self, names, limit=3):
        while self.lay.count():
            w = self.lay.takeAt(0).widget()
            if w: w.deleteLater()
        for n in names[:limit]: self.lay.addWidget(Avatar(n))
        if len(names) > limit: self.lay.addWidget(Avatar("+", text=str(len(names) - limit), bg="#ffffff", fg=INK, ring="#c9ccd6"))
        self.adjustSize()

def vsep(h=22):
    s = QFrame(); s.setFixedSize(1, h); s.setStyleSheet(f"background:{BORDER};"); return s

class InlineEditor(QPlainTextEdit):
    committed = Signal(str, bool)          # text, cancelled
    def __init__(self, parent, text, font_px, fg, bg, border=True):
        super().__init__(parent)
        self.setPlainText(text); self.setFrameShape(QFrame.NoFrame); self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff); self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        f = ui_font(max(8, int(font_px)), QFont.Medium); self.setFont(f)
        self.setStyleSheet(f"QPlainTextEdit{{background:{bg};color:{fg};border:{'2px solid ' + ACCENT if border else '0'};border-radius:4px;padding:2px;selection-background-color:{ACCENT};}}")
        self._done = False; self.setFocus(); self.selectAll()
    def finish(self, cancelled=False):
        if self._done: return
        self._done = True; self.committed.emit(self.toPlainText(), cancelled)
    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape: self.finish(True); return
        if e.key() in (Qt.Key_Return, Qt.Key_Enter) and (e.modifiers() & (Qt.ControlModifier | Qt.ShiftModifier)): self.finish(False); return
        super().keyPressEvent(e)
    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        QTimer.singleShot(0, lambda: self.finish(False))

# ───────────────────────────── connect screen ─────────────────────────────
class ConnectDialog(QDialog):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("InkSync")
        self.data = None; self.creating = True
        self.settings = QSettings("InkSync", "client")
        self.setFixedWidth(430)
        self.setStyleSheet(f"""QDialog{{background:#ffffff;}} QLabel{{color:{INK};}}
            QLineEdit{{border:1px solid #d3d6e0;border-radius:8px;padding:9px 11px;font-size:14px;color:{INK};background:#fff;}}
            QLineEdit:focus{{border:2px solid {ACCENT};padding:8px 10px;}} QLineEdit:disabled{{background:#f3f4f7;color:#a2a7b8;}}""")
        v = QVBoxLayout(self); v.setContentsMargins(30, 28, 30, 26); v.setSpacing(10)
        logo = QLabel("InkSync"); logo.setFont(ui_font(30, QFont.Black)); logo.setStyleSheet(f"color:{INK};"); v.addWidget(logo)
        sub = QLabel("Collaborative whiteboard for your local network"); sub.setStyleSheet("color:#6b7089;font-size:13px;"); v.addWidget(sub); v.addSpacing(8)
        seg = QHBoxLayout(); seg.setSpacing(0)
        self.b_create = QPushButton("Create board"); self.b_join = QPushButton("Join board")
        for b, left in ((self.b_create, True), (self.b_join, False)):
            b.setCheckable(True); b.setCursor(Qt.PointingHandCursor); b.setFixedHeight(36)
            r = "8px 0 0 8px" if left else "0 8px 8px 0"
            b.setStyleSheet(f"QPushButton{{border:1px solid #d3d6e0;border-radius:0;background:#fff;color:{INK};font-weight:600;border-top-left-radius:{8 if left else 0}px;border-bottom-left-radius:{8 if left else 0}px;border-top-right-radius:{0 if left else 8}px;border-bottom-right-radius:{0 if left else 8}px;}} QPushButton:checked{{background:{ACCENT_SOFT};color:{ACCENT};border-color:{ACCENT};}}")
            seg.addWidget(b)
        self.b_create.clicked.connect(lambda: self.set_mode(True)); self.b_join.clicked.connect(lambda: self.set_mode(False)); v.addLayout(seg)
        def field(label, widget, ph=""):
            l = QLabel(label); l.setStyleSheet("font-size:12px;font-weight:600;color:#4a4f69;margin-top:4px;"); v.addWidget(l)
            if ph: widget.setPlaceholderText(ph)
            v.addWidget(widget); return l
        row = QHBoxLayout(); self.host = QLineEdit(self.settings.value("host", "127.0.0.1")); self.port = QLineEdit(self.settings.value("port", "8765")); self.port.setFixedWidth(90)
        hv = QVBoxLayout(); l1 = QLabel("Server IP / hostname"); l1.setStyleSheet("font-size:12px;font-weight:600;color:#4a4f69;"); hv.addWidget(l1); hv.addWidget(self.host)
        pv = QVBoxLayout(); l2 = QLabel("Port"); l2.setStyleSheet("font-size:12px;font-weight:600;color:#4a4f69;"); pv.addWidget(l2); pv.addWidget(self.port)
        row.addLayout(hv, 1); row.addLayout(pv); v.addLayout(row)
        self.username = QLineEdit(self.settings.value("username", "Guest")); field("Your name", self.username)
        self.name = QLineEdit("Team Whiteboard"); self.name_l = field("Board name", self.name)
        self.room = QLineEdit(self.settings.value("room", "")); self.room_l = field("Board code", self.room, "e.g. 9F3A1C7B")
        self.password = QLineEdit(); self.password.setEchoMode(QLineEdit.Password); field("Board password", self.password, "4–128 characters")
        self.owner = QLineEdit(); self.owner_l = field("Host token (optional)", self.owner, "Only if you are rejoining as host")
        v.addSpacing(8)
        go = QPushButton("Open board"); go.setCursor(Qt.PointingHandCursor); go.setFixedHeight(42); go.setDefault(True)
        go.setStyleSheet(f"QPushButton{{background:{ACCENT};color:white;border:0;border-radius:9px;font-size:14px;font-weight:700;}} QPushButton:hover{{background:#3350e6;}}")
        go.clicked.connect(self.accept); v.addWidget(go)
        self.set_mode(self.settings.value("mode", "create") == "create")
    def set_mode(self, creating):
        self.creating = creating; self.b_create.setChecked(creating); self.b_join.setChecked(not creating)
        for w in (self.name, self.name_l): w.setVisible(creating)
        for w in (self.room, self.room_l, self.owner, self.owner_l): w.setVisible(not creating)
        self.adjustSize()
    def accept(self):
        try: port = int(self.port.text())
        except ValueError:
            QMessageBox.warning(self, "Invalid port", "Port must be a number."); return
        if not self.host.text().strip() or not 1 <= port <= 65535 or not self.username.text().strip():
            QMessageBox.warning(self, "Missing details", "Enter a valid server, port and name."); return
        self.data = {"host": self.host.text().strip(), "port": port, "username": self.username.text().strip(),
                     "password": self.password.text(), "create": self.creating, "room": self.room.text().strip(),
                     "name": self.name.text().strip(), "owner_token": self.owner.text().strip()}
        if self.creating:
            if not 4 <= len(self.data["password"]) <= 128:
                QMessageBox.warning(self, "Invalid board password", "Choose a password containing 4–128 characters."); return
            if not self.data["name"]:
                QMessageBox.warning(self, "Missing board name", "Enter a name for the new board."); return
        elif not self.data["room"]:
            QMessageBox.warning(self, "Missing board code", "Enter the board code."); return
        self.settings.setValue("host", self.data["host"]); self.settings.setValue("port", str(port)); self.settings.setValue("username", self.data["username"])
        self.settings.setValue("room", self.data["room"]); self.settings.setValue("mode", "create" if self.creating else "join")
        super().accept()

# ───────────────────────────── canvas ─────────────────────────────
GRID_STEP = 20.0
TEXT_KINDS = ("text", "sticky", "comment") + SHAPE_KINDS
FILE_IMG_EXT = (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp")

def seg_dist(p, a, b):
    dx, dy = b.x() - a.x(), b.y() - a.y(); L = dx * dx + dy * dy
    if L == 0: return math.hypot(p.x() - a.x(), p.y() - a.y())
    t = max(0.0, min(1.0, ((p.x() - a.x()) * dx + (p.y() - a.y()) * dy) / L))
    return math.hypot(p.x() - (a.x() + t * dx), p.y() - (a.y() + t * dy))

def encode_qimage(img):
    """PNG/JPEG bytes (base64) small enough for the server's 1.3 MB base64 limit, downscaling if needed."""
    if max(img.width(), img.height()) > 2000: img = img.scaled(2000, 2000, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    for scale in (1.0, .75, .55, .4, .3, .2):
        im = img if scale == 1.0 else img.scaled(max(1, int(img.width() * scale)), max(1, int(img.height() * scale)), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        for fmt, q in (("PNG", -1), ("JPG", 85)):
            ba = QByteArray(); buf = QBuffer(ba); buf.open(QIODevice.WriteOnly); im.save(buf, fmt, q); buf.close()
            raw = bytes(ba.data())
            if len(raw) * 4 // 3 + 8 <= 1_250_000: return base64.b64encode(raw).decode("ascii")
    return None

class Canvas(QWidget):
    send_op = Signal(dict); send_update = Signal(str, dict); send_delete = Signal(str)
    send_cursor = Signal(list, dict); sync_needed = Signal()
    selection_changed = Signal(); view_changed = Signal(); tool_changed = Signal(str); tool_request = Signal(str)
    toast = Signal(str); undo_req = Signal(); redo_req = Signal(); search_req = Signal(); content_changed = Signal()

    def __init__(self):
        super().__init__()
        self.setMouseTracking(True); self.setFocusPolicy(Qt.StrongFocus); self.setAcceptDrops(True); self.setMinimumSize(560, 420)
        self.items = []; self.by_id = {}; self.seq = 0
        self.zoom = 1.0; self.origin = QPointF(WORLD_W / 2 - 600, WORLD_H / 2 - 350); self._centered = False
        self.tool = "select"; self.icon_text = "⭐"
        self.style = {"color": "#1A1A2E", "hl": "#F5D90A", "fill": "#FFFFFF", "sticky": STICKY_COLORS[0], "width": 4, "fs": 26, "dash": False}
        self.me = ""; self.role = "editor"; self.open_editing = False
        self.grid_style = "lines"; self.snap = False; self.show_remote = True
        self.selected = set(); self.drag = None; self.current = None; self.pending = []
        self.editor = None; self.edit_ctx = None; self.editing_id = None; self.auto_select = 0
        self.hover_id = None; self.space_down = False
        self.clipboard_items = []; self.img_cache = {}; self.fit_cache = {}
        self.remote = {}; self.local_emojis = []; self.laser_trail = []; self.laser_on = False
        self.last_cursor_time = 0.0; self.last_world = QPointF(WORLD_W / 2, WORLD_H / 2); self.last_screen = QPointF(300, 300)
        self._tick = 0; self.eraser_pos = None
        self.timer = QTimer(self); self.timer.timeout.connect(self._on_tick); self.timer.start(40)

    # ── coordinate helpers
    def w2s(self, pt): return QPointF((pt.x() - self.origin.x()) * self.zoom, (pt.y() - self.origin.y()) * self.zoom)
    def s2w(self, pt): return QPointF(pt.x() / self.zoom + self.origin.x(), pt.y() / self.zoom + self.origin.y())
    def n2w(self, p): return QPointF(p[0] * WORLD_W, p[1] * WORLD_H)
    def w2n(self, pt): return [min(1.0, max(0.0, pt.x() / WORLD_W)), min(1.0, max(0.0, pt.y() / WORLD_H))]
    def snapv(self, v): return round(v / GRID_STEP) * GRID_STEP if self.snap else v
    def snapp(self, pt): return QPointF(self.snapv(pt.x()), self.snapv(pt.y()))
    def view_rect(self): return QRectF(self.origin.x(), self.origin.y(), self.width() / self.zoom, self.height() / self.zoom)
    def can_edit(self, op): return self.open_editing or op.get("author") == self.me or self.role == "host"
    def active_items(self): return [o for o in self.items if o.get("active", True)]

    # ── view control
    def _clamp(self):
        vw, vh = self.width() / self.zoom, self.height() / self.zoom
        self.origin.setX(max(-vw * 0.6, min(WORLD_W - vw * 0.4, self.origin.x())))
        self.origin.setY(max(-vh * 0.6, min(WORLD_H - vh * 0.4, self.origin.y())))
    def _view_changed(self): self._clamp(); self.update(); self.view_changed.emit()
    def zoom_at(self, factor, pos=None):
        pos = pos or QPointF(self.width() / 2, self.height() / 2)
        z = max(0.05, min(4.0, self.zoom * factor)); wp = self.s2w(pos); self.zoom = z
        self.origin = QPointF(wp.x() - pos.x() / z, wp.y() - pos.y() / z); self._view_changed()
    def set_zoom(self, z): self.zoom_at(z / self.zoom)
    def center_on(self, wpt):
        self.origin = QPointF(wpt.x() - self.width() / 2 / self.zoom, wpt.y() - self.height() / 2 / self.zoom); self._view_changed()
    def content_rect(self, ids=None):
        r = None
        for o in self.active_items():
            if ids is not None and o.get("id") not in ids: continue
            b = self.bbox(o)
            if o["kind"] == "frame": b = b.adjusted(0, -50, 0, 0)
            r = b if r is None else r.united(b)
        return r
    def fit_content(self, ids=None):
        r = self.content_rect(ids)
        if r is None: self.zoom = 1.0; self.center_on(QPointF(WORLD_W / 2, WORLD_H / 2)); return
        z = min((self.width() - 220) / max(r.width(), 50), (self.height() - 220) / max(r.height(), 50), 2.0)
        self.zoom = max(0.05, z); self.center_on(r.center())
    def resizeEvent(self, e):
        if not self._centered and self.width() > 50:
            self._centered = True; self.center_on(QPointF(WORLD_W / 2, WORLD_H / 2))
        self._clamp(); self.view_changed.emit()
    def wheelEvent(self, e):
        mods = e.modifiers(); pd = e.pixelDelta(); ad = e.angleDelta()
        if mods & (Qt.ControlModifier | Qt.MetaModifier) or (pd.isNull() and not (mods & Qt.ShiftModifier)):
            dy = pd.y() if not pd.isNull() else ad.y()
            self.zoom_at(math.pow(1.0015 if not pd.isNull() else 1.0011, dy), e.position())
        else:
            dx, dy = (pd.x(), pd.y()) if not pd.isNull() else (ad.x(), ad.y())
            if mods & Qt.ShiftModifier and dx == 0: dx, dy = dy, 0
            self.origin += QPointF(-dx / self.zoom, -dy / self.zoom); self._view_changed()
    def event(self, ev):
        if ev.type() == QEvent.NativeGesture:
            try:
                if ev.gestureType() == Qt.ZoomNativeGesture: self.zoom_at(1.0 + ev.value(), ev.position()); return True
            except Exception: pass
        return super().event(ev)

    # ── model updates from the server
    def apply_event(self, ev):
        incoming = ev.get("seq", 0)
        if incoming <= self.seq: return
        if incoming != self.seq + 1: self.sync_needed.emit(); return
        self.seq = incoming; action = ev.get("action")
        if action == "add":
            op = ev["op"]; self.items.append(op); self.by_id[op["id"]] = op
            if op.get("author") == self.me:
                if self.pending: self.pending.pop(0)
                if self.auto_select > 0:
                    self.selected.add(op["id"]); self.auto_select -= 1; self.selection_changed.emit()
        elif action in ("undo", "redo"):
            op = self.by_id.get(ev.get("id"))
            if op is not None:
                op["active"] = (action == "redo")
                if action == "undo" and op["id"] in self.selected: self.selected.discard(op["id"]); self.selection_changed.emit()
        elif action == "update":
            op = self.by_id.get(ev.get("id"))
            if op is not None and not (self.drag and self.drag.get("mode") in ("move", "resize") and op["id"] in self.selected):
                op.update(ev.get("changes", {})); self.fit_cache.clear()
        elif action == "clear":
            self.items.clear(); self.by_id.clear(); self.selected.clear(); self.selection_changed.emit()
        self.content_changed.emit(); self.update()
    def load_snapshot(self, snap):
        # Ignore stale snapshots: a delayed sync response must never roll the
        # canvas backwards after newer events have already been applied.
        snapshot_seq = int(snap.get("seq", 0))
        if snapshot_seq < self.seq: return
        self.seq = snapshot_seq; self.remote.clear(); self.pending.clear(); self.drag = None; self.current = None
        self.items = [dict(op, active=True) for op in snap.get("items", [])]
        self.by_id = {o["id"]: o for o in self.items}; self.fit_cache.clear()
        self.selected &= set(self.by_id); self.selection_changed.emit(); self.content_changed.emit(); self.update()
    def set_remote_cursor(self, m):
        cid = m.get("id", ""); now = time.monotonic()
        c = self.remote.setdefault(cid, {"trail": []})
        c.update({"username": m.get("username", "?"), "point": m.get("point", [0, 0]), "seen": now})
        if m.get("laser"): c["trail"].append((self.n2w(m["point"]), now)); c["trail"] = c["trail"][-40:]; c["laser_t"] = now
        if m.get("emoji"): c["emoji"] = m["emoji"]; c["emoji_t"] = now
        self.update()
    def remove_remote(self, cid): self.remote.pop(cid, None); self.update()
    def react(self, emoji):
        self.send_cursor.emit(self.w2n(self.last_world), {"emoji": emoji}); self.local_emojis.append((emoji, QPointF(self.last_screen), time.monotonic()))

    # ── geometry
    def make_font(self, px, weight=QFont.Medium):
        f = QFont(); f.setFamilies([s.strip() for s in UI_FONT.split(",")]); f.setPixelSize(max(6, int(px))); f.setWeight(weight); return f
    def default_fs(self, op): return op.get("fs") or {"text": 28, "sticky": 24, "comment": 16, "frame": 22}.get(op["kind"], 20)
    def image_of(self, op):
        key = op.get("id") or id(op)
        if key not in self.img_cache:
            try: img = QImage.fromData(base64.b64decode(op.get("data", ""), validate=False))
            except Exception: img = QImage()
            self.img_cache[key] = img
        return self.img_cache[key]
    def image_dims(self, op):
        if op.get("w") and op.get("h"): return float(op["w"]), float(op["h"])
        img = self.image_of(op)
        if img.isNull(): return 200.0, 150.0
        s = 420.0 / max(img.width(), img.height()); return img.width() * s, img.height() * s
    def bbox(self, op):
        k = op["kind"]; pts = [self.n2w(p) for p in op.get("points", [])]
        if not pts: return QRectF()
        if k in STROKE_KINDS or k == "erase":
            xs = [q.x() for q in pts]; ys = [q.y() for q in pts]; pad = max(float(op.get("width", 3)), 14 if k == "highlighter" else 0) / 2 + 2
            return QRectF(min(xs) - pad, min(ys) - pad, max(xs) - min(xs) + 2 * pad, max(ys) - min(ys) + 2 * pad)
        if k in LINE_KINDS or k in SHAPE_KINDS:
            r = QRectF(pts[0], pts[1]).normalized()
            if k in LINE_KINDS:
                if op.get("curve"): r = r.united(QRectF(self.curve_ctrl(pts[0], pts[1]) - QPointF(2, 2), QSizeF(4, 4)))
                return r.adjusted(-8, -8, 8, 8)
            return r
        if k == "sticky": return QRectF(pts[0].x(), pts[0].y(), float(op.get("w", 190)), float(op.get("h", 190)))
        if k == "image":
            w, h = self.image_dims(op); return QRectF(pts[0].x(), pts[0].y(), w, h)
        if k == "comment": return QRectF(pts[0].x(), pts[0].y(), 46, 38)
        if k == "text":
            fm = QFontMetricsF(self.make_font(self.default_fs(op))); r = fm.boundingRect(QRectF(0, 0, 4000, 4000), F_LEFT, op.get("text", "") or " ")
            return QRectF(pts[0].x(), pts[0].y(), max(r.width(), 24) + 6, max(r.height(), self.default_fs(op)) + 6)
        return QRectF(pts[0], pts[0])
    def curve_ctrl(self, a, b):
        dx, dy = b.x() - a.x(), b.y() - a.y(); L = math.hypot(dx, dy) or 1.0
        return QPointF((a.x() + b.x()) / 2 - dy / L * L * 0.25, (a.y() + b.y()) / 2 + dx / L * L * 0.25)
    def polyline_of(self, op):
        pts = [self.n2w(p) for p in op["points"]]
        if op["kind"] in LINE_KINDS and op.get("curve") and len(pts) == 2:
            c = self.curve_ctrl(pts[0], pts[1]); return [QPointF((1 - t) ** 2 * pts[0].x() + 2 * (1 - t) * t * c.x() + t * t * pts[1].x(),
                                                                (1 - t) ** 2 * pts[0].y() + 2 * (1 - t) * t * c.y() + t * t * pts[1].y()) for t in [i / 16 for i in range(17)]]
        return pts
    def hit(self, op, wp, tol):
        k = op["kind"]
        if k in STROKE_KINDS or k in LINE_KINDS:
            pl = self.polyline_of(op); lim = max(float(op.get("width", 3)) / 2, 7 if k == "highlighter" else 0) + tol
            if len(pl) == 1: return math.hypot(wp.x() - pl[0].x(), wp.y() - pl[0].y()) <= lim
            return any(seg_dist(wp, a, b) <= lim for a, b in zip(pl, pl[1:]))
        r = self.bbox(op)
        if k == "frame":
            title = QRectF(r.left(), r.top() - 46, min(r.width(), 520), 46)
            return title.contains(wp) or (r.adjusted(-tol, -tol, tol, tol).contains(wp) and not r.adjusted(tol, tol, -tol, -tol).contains(wp))
        if k in SHAPE_KINDS and not op.get("fill") and not op.get("text"):
            return r.adjusted(-tol, -tol, tol, tol).contains(wp) and not r.adjusted(tol, tol, -tol, -tol).contains(wp)
        return r.adjusted(-tol, -tol, tol, tol).contains(wp)
    def hit_item(self, wp):
        tol = 6 / self.zoom
        for pass_frames in (False, True):
            for op in reversed(self.items):
                if not op.get("active", True) or (op["kind"] == "frame") != pass_frames: continue
                if self.hit(op, wp, tol): return op
        return None
    def handles(self, op):
        k = op["kind"]
        if k in SHAPE_KINDS:
            r = self.bbox(op); return {"tl": r.topLeft(), "tr": r.topRight(), "bl": r.bottomLeft(), "br": r.bottomRight()}
        if k in LINE_KINDS: pts = [self.n2w(p) for p in op["points"]]; return {"p0": pts[0], "p1": pts[1]}
        if k in ("sticky", "image", "text"): return {"br": self.bbox(op).bottomRight()}
        return {}
    def sel_single(self):
        ops = [self.by_id[i] for i in self.selected if i in self.by_id and self.by_id[i].get("active", True)]
        return ops[0] if len(ops) == 1 else None
    def handle_at(self, pos):
        op = self.sel_single()
        if not op or not self.can_edit(op): return None
        for name, wp in self.handles(op).items():
            sp = self.w2s(wp)
            if abs(sp.x() - pos.x()) <= 9 and abs(sp.y() - pos.y()) <= 9: return name
        return None

    # ── painting
    def paintEvent(self, ev):
        p = QPainter(self); p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing | QPainter.SmoothPixmapTransform)
        p.fillRect(self.rect(), QColor(OUTSIDE_BG))
        board = QRectF(self.w2s(QPointF(0, 0)), self.w2s(QPointF(WORLD_W, WORLD_H)))
        p.fillRect(board, QColor(BOARD_BG)); self.draw_grid(p, board)
        p.setPen(QPen(QColor("#d5d8e1"), 1)); p.setBrush(Qt.NoBrush); p.drawRect(board)
        p.save(); p.scale(self.zoom, self.zoom); p.translate(-self.origin.x(), -self.origin.y())
        self.paint_world(p, self.view_rect().adjusted(-60, -60, 60, 60), live=True)
        p.restore()
        self.draw_selection(p)
        if self.drag and self.drag.get("mode") == "marquee":
            r = self.drag["rect"]; p.setPen(QPen(QColor(ACCENT), 1)); p.setBrush(QColor(66, 98, 255, 28)); p.drawRect(r)
        if self.tool == "erase" and self.eraser_pos is not None:
            p.setPen(QPen(QColor("#e5484d"), 1.5)); p.setBrush(QColor(229, 72, 77, 30)); p.drawEllipse(self.eraser_pos, 14, 14)
        self.draw_overlays(p); p.end()
    def draw_grid(self, p, board):
        if self.grid_style == "none": return
        step = 40.0; sp = step * self.zoom
        if sp < 12: step *= 5; sp = step * self.zoom
        clip = board.intersected(QRectF(self.rect()))
        if clip.isEmpty(): return
        p.save(); p.setClipRect(clip)
        k0 = math.floor(self.origin.x() / step); k1 = math.floor(self.origin.y() / step)
        xs = []; x = (k0 * step - self.origin.x()) * self.zoom
        while x < self.width(): xs.append(x); x += sp
        ys = []; y = (k1 * step - self.origin.y()) * self.zoom
        while y < self.height(): ys.append(y); y += sp
        if self.grid_style == "dots":
            p.setPen(QPen(QColor("#c4c8d4"), max(1.5, 2 * min(1.0, self.zoom)), Qt.SolidLine, Qt.RoundCap))
            for xx in xs:
                for yy in ys: p.drawPoint(QPointF(xx, yy))
        else:
            p.setPen(QPen(QColor(GRID), 1))
            for xx in xs: p.drawLine(QPointF(xx, 0), QPointF(xx, self.height()))
            for yy in ys: p.drawLine(QPointF(0, yy), QPointF(self.width(), yy))
        p.restore()
    def paint_world(self, p, vis, live=False):
        ordered = [o for o in self.items if o.get("active", True) and o["kind"] == "frame"] + [o for o in self.items if o.get("active", True) and o["kind"] != "frame"]
        for op in ordered:
            if live and op.get("id") == self.editing_id: continue
            b = self.bbox(op)
            if op["kind"] == "frame": b = b.adjusted(0, -60, 0, 0)
            if not vis.intersects(b.adjusted(-30, -30, 30, 60)): continue
            self.draw_op(p, op, live)
        if live:
            for op in self.pending: self.draw_op(p, op, True)
            if self.current: self.draw_op(p, self.current, True)
    def shape_path(self, kind, r):
        path = QPainterPath()
        if kind in ("rect", "frame"): path.addRect(r)
        elif kind == "rounded": rad = min(r.width(), r.height()) * 0.22; path.addRoundedRect(r, rad, rad)
        elif kind == "ellipse": path.addEllipse(r)
        elif kind == "triangle": path.addPolygon(_poly([(r.center().x(), r.top()), (r.right(), r.bottom()), (r.left(), r.bottom())])); path.closeSubpath()
        elif kind == "diamond": path.addPolygon(_poly([(r.center().x(), r.top()), (r.right(), r.center().y()), (r.center().x(), r.bottom()), (r.left(), r.center().y())])); path.closeSubpath()
        return path
    def fitted_px(self, op, inner, base):
        text = op.get("text", "")
        if not text or inner.width() < 20: return base
        key = (text, round(inner.width()), round(inner.height()), base); v = self.fit_cache.get(key)
        if v: return v
        px = base
        while px > 9:
            r = QFontMetricsF(self.make_font(px)).boundingRect(QRectF(0, 0, inner.width(), 20000), F_CENTER_WRAP, text)
            if r.height() <= inner.height(): break
            px -= 2
        if len(self.fit_cache) > 3000: self.fit_cache.clear()
        self.fit_cache[key] = px; return px
    def draw_op(self, p, op, live=False):
        k = op["kind"]; pts = [self.n2w(q) for q in op.get("points", [])]
        if not pts: return
        col = QColor(op.get("color", "#1A1A2E")); width = float(op.get("width", 3))
        if k in STROKE_KINDS:
            if k == "highlighter": col.setAlpha(95); width = max(width * 2.5, 16)
            cap = Qt.FlatCap if k == "highlighter" else Qt.RoundCap
            p.setPen(QPen(col, width, Qt.SolidLine, cap, Qt.RoundJoin)); p.setBrush(Qt.NoBrush)
            if len(pts) == 1: p.setBrush(col); p.setPen(Qt.NoPen); p.drawEllipse(pts[0], width / 2, width / 2); return
            path = QPainterPath(pts[0])
            for i in range(1, len(pts) - 1): path.quadTo(pts[i], QPointF((pts[i].x() + pts[i + 1].x()) / 2, (pts[i].y() + pts[i + 1].y()) / 2))
            path.lineTo(pts[-1]); p.drawPath(path)
        elif k in LINE_KINDS and len(pts) == 2:
            a, b = pts; pen = QPen(col, width, Qt.DashLine if op.get("dash") else Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin); p.setPen(pen); p.setBrush(Qt.NoBrush)
            path = QPainterPath(a); src = a
            if op.get("curve"): c = self.curve_ctrl(a, b); path.quadTo(c, b); src = c
            else: path.lineTo(b)
            p.drawPath(path)
            if k == "arrow":
                ang = math.atan2(b.y() - src.y(), b.x() - src.x()); size = max(16, width * 4.5)
                p.setPen(QPen(col, max(1.0, width * 0.6), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)); p.setBrush(col)
                p.drawPolygon(_poly([(b.x(), b.y()), (b.x() - size * math.cos(ang - 0.42), b.y() - size * math.sin(ang - 0.42)), (b.x() - size * math.cos(ang + 0.42), b.y() - size * math.sin(ang + 0.42))]))
        elif k in SHAPE_KINDS and len(pts) == 2:
            r = QRectF(pts[0], pts[1]).normalized(); fill = op.get("fill", "")
            if k == "frame":
                p.setPen(QPen(QColor("#cfd3de"), 2)); p.setBrush(QColor(fill) if fill else QColor("#ffffff")); p.drawRect(r)
                p.setPen(QColor("#6b7089")); p.setFont(self.make_font(self.default_fs(op), QFont.DemiBold))
                p.drawText(QRectF(r.left(), r.top() - self.default_fs(op) * 1.9, max(r.width(), 100), self.default_fs(op) * 1.7), F_LEFT, op.get("text") or "Frame"); return
            p.setPen(QPen(col, width, Qt.DashLine if op.get("dash") else Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)); p.setBrush(QColor(fill) if fill else Qt.NoBrush)
            p.drawPath(self.shape_path(k, r))
            if op.get("text"):
                inner = r.adjusted(10, 10, -10, -10)
                if k == "triangle": inner = QRectF(r.left() + r.width() * 0.25, r.top() + r.height() * 0.45, r.width() * 0.5, r.height() * 0.5)
                elif k in ("ellipse", "diamond"): inner = r.adjusted(r.width() * 0.16, r.height() * 0.16, -r.width() * 0.16, -r.height() * 0.16)
                px = self.fitted_px(op, inner, self.default_fs(op)); p.setFont(self.make_font(px, QFont.DemiBold)); p.setPen(QColor(ink_for(fill) if fill else op.get("color", "#1A1A2E")))
                p.drawText(inner, F_CENTER_WRAP, op["text"])
        elif k == "sticky":
            r = self.bbox(op); fill = QColor(op.get("fill") or STICKY_COLORS[0])
            p.setPen(Qt.NoPen); p.setBrush(QColor(20, 20, 60, 26)); p.drawRect(r.translated(2, 10)); p.setBrush(QColor(20, 20, 60, 24)); p.drawRect(r.translated(0, 4))
            p.setBrush(fill); p.drawRect(r)
            if op.get("text"):
                inner = r.adjusted(14, 14, -14, -14); px = self.fitted_px(op, inner, self.default_fs(op))
                p.setFont(self.make_font(px)); p.setPen(QColor(ink_for(op.get("fill") or STICKY_COLORS[0]))); p.drawText(inner, F_CENTER_WRAP, op["text"])
        elif k == "text":
            p.setFont(self.make_font(self.default_fs(op))); p.setPen(col); p.drawText(QRectF(pts[0].x() + 3, pts[0].y() + 3, 4000, 4000), F_LEFT, op.get("text", ""))
        elif k == "image":
            img = self.image_of(op)
            if not img.isNull(): w, h = self.image_dims(op); p.drawImage(QRectF(pts[0].x(), pts[0].y(), w, h), img)
        elif k == "comment":
            a = op.get("author") or self.me; strong = QColor(user_color(a)); r = self.bbox(op)
            body = QRectF(r.left(), r.top(), 46, 32); p.setPen(QPen(strong, 3)); p.setBrush(pastel(strong.name(), 0.7)); p.drawRoundedRect(body, 9, 9)
            p.drawPolygon(_poly([(r.left() + 10, body.bottom() - 1), (r.left() + 10, r.bottom()), (r.left() + 20, body.bottom() - 1)]))
            p.setPen(QPen(strong, 3, Qt.SolidLine, Qt.RoundCap)); p.drawLine(QPointF(r.left() + 12, r.top() + 12), QPointF(r.left() + 34, r.top() + 12)); p.drawLine(QPointF(r.left() + 12, r.top() + 22), QPointF(r.left() + 28, r.top() + 22))
            if op.get("id") and (op["id"] in self.selected or op["id"] == self.hover_id):
                tr = QFontMetricsF(self.make_font(16)).boundingRect(QRectF(0, 0, 300, 4000), F_LEFT_WRAP, op.get("text", ""))
                card = QRectF(r.right() + 10, r.top(), 340, tr.height() + 62)
                p.setPen(QPen(QColor(BORDER), 1.5)); p.setBrush(QColor("#ffffff")); p.drawRoundedRect(card, 10, 10)
                p.setPen(strong); p.setFont(self.make_font(15, QFont.Bold)); p.drawText(QRectF(card.left() + 16, card.top() + 10, 300, 22), F_LEFT, a)
                p.setPen(QColor(INK)); p.setFont(self.make_font(16)); p.drawText(QRectF(card.left() + 16, card.top() + 38, 300, tr.height() + 4), F_LEFT_WRAP, op.get("text", ""))
    def draw_selection(self, p):
        ops = [self.by_id[i] for i in self.selected if i in self.by_id and self.by_id[i].get("active", True) and i != self.editing_id]
        if not ops: return
        union = None
        for op in ops:
            b = self.bbox(op); sr = QRectF(self.w2s(b.topLeft()), self.w2s(b.bottomRight())).adjusted(-3, -3, 3, 3)
            p.setBrush(Qt.NoBrush); p.setPen(QPen(QColor(ACCENT), 1.6)); p.drawRect(sr); union = sr if union is None else union.united(sr)
        if len(ops) > 1: p.setPen(QPen(QColor(ACCENT), 1, Qt.DashLine)); p.drawRect(union.adjusted(-5, -5, 5, 5))
        else:
            op = ops[0]
            if self.can_edit(op):
                for name, wp in self.handles(op).items():
                    sp = self.w2s(wp); p.setPen(QPen(QColor(ACCENT), 1.8)); p.setBrush(QColor("#ffffff")); p.drawEllipse(sp, 5.5, 5.5)
    def draw_overlays(self, p):
        now = time.monotonic()
        # my laser
        if self.laser_trail:
            self.laser_trail = [(pt, t) for pt, t in self.laser_trail if now - t < 1.3]; self._draw_laser(p, self.laser_trail, now)
        if self.show_remote:
            for cid, c in list(self.remote.items()):
                age = now - c["seen"]
                if age > 25: self.remote.pop(cid, None); continue
                name = c["username"]; strong = QColor(user_color(name)); soft = pastel(strong.name(), 0.72)
                c["trail"] = [(pt, t) for pt, t in c.get("trail", []) if now - t < 1.3]
                if c["trail"]: self._draw_laser(p, c["trail"], now)
                pt = self.w2s(self.n2w(c["point"]))
                if not QRectF(-120, -60, self.width() + 240, self.height() + 120).contains(pt): continue
                p.setOpacity(1.0 if age < 10 else max(0.35, 1 - (age - 10) / 15.0))
                p.setPen(QPen(strong, 1.7, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)); p.setBrush(soft)
                p.drawPolygon(_poly([(pt.x() + x, pt.y() + y) for x, y in ((0, 0), (0, 17), (4.6, 13), (7.8, 20.5), (11, 19.2), (7.9, 12), (13.5, 11.6))]))
                font = self.make_font(14, QFont.DemiBold); p.setFont(font); tw = QFontMetricsF(font).horizontalAdvance(name)
                rect = QRectF(pt.x() + 15, pt.y() + 18, tw + 24, 27); p.setPen(QPen(strong, 1.7)); p.setBrush(soft); p.drawRoundedRect(rect, 13.5, 13.5)
                p.setPen(QColor(INK)); p.drawText(rect, 0x0084, name)
                if now - c.get("emoji_t", -99) < 2.8:
                    k = (now - c["emoji_t"]) / 2.8; p.setOpacity(max(0.0, 1 - k)); p.setFont(QFont("Arial", 26)); p.drawText(QPointF(pt.x() + 8, pt.y() - 8 - 70 * k), c["emoji"])
                p.setOpacity(1.0)
        self.local_emojis = [(e, pos, t) for e, pos, t in self.local_emojis if now - t < 2.8]
        for e, pos, t in self.local_emojis:
            k = (now - t) / 2.8; p.setOpacity(max(0.0, 1 - k)); p.setFont(QFont("Arial", 26)); p.drawText(QPointF(pos.x() + 8, pos.y() - 8 - 70 * k), e)
        p.setOpacity(1.0)
    def _draw_laser(self, p, trail, now):
        pts = [(self.w2s(pt), t) for pt, t in trail]
        for (a, ta), (b, tb) in zip(pts, pts[1:]):
            al = max(0.0, 1 - (now - tb) / 1.3); p.setPen(QPen(QColor(255, 40, 70, int(210 * al)), 5 * al + 1, Qt.SolidLine, Qt.RoundCap)); p.drawLine(a, b)
        if pts:
            tip = pts[-1][0]; p.setPen(Qt.NoPen); p.setBrush(QColor(255, 40, 70, 70)); p.drawEllipse(tip, 13, 13); p.setBrush(QColor(255, 40, 70)); p.drawEllipse(tip, 6, 6)
    def _on_tick(self):
        now = time.monotonic(); self._tick += 1
        anim = bool(self.laser_trail) or bool(self.local_emojis) or any(c.get("trail") or now - c.get("emoji_t", -99) < 3 for c in self.remote.values())
        if anim or self._tick % 25 == 0: self.update()

    # ── tools / creation
    def set_tool(self, tool):
        if self.editor: self.editor.finish(False)
        self.tool = tool
        if tool != "select" and self.selected: self.selected = set(); self.selection_changed.emit()
        self.drag = None; self.current = None; self.eraser_pos = None; self._update_cursor(None); self.update()
    def revert_to_select(self): self.tool = "select"; self.tool_changed.emit("select"); self._update_cursor(None)
    def new_op(self, tool, pts):
        s = self.style; kind, extra = tool, {}
        if tool == "curve": kind = "arrow"; extra["curve"] = True
        op = {"kind": kind, "color": s["hl"] if kind == "highlighter" else s["color"], "width": s["width"], "points": pts, "text": ""}
        if kind in SHAPE_KINDS: op["fill"] = "#FFFFFF" if kind == "frame" else s["fill"]; op["fs"] = s["fs"] if kind != "frame" else 22
        if kind in LINE_KINDS: op["dash"] = s["dash"]
        if kind == "sticky": op.update({"fill": s["sticky"], "w": 190, "h": 190, "fs": 24})
        if kind == "text": op["fs"] = s["fs"] + 4
        op.update(extra); return op
    def emit_new(self, op, select=False):
        op = {k: v for k, v in op.items() if k not in ("id", "author", "active")}
        if select: self.auto_select = max(self.auto_select, 0) + 1
        self.pending.append(dict(op, active=True)); self.send_op.emit(op); self.update()
    def create_item(self, op, select=True):
        self.selected = set(); self.selection_changed.emit(); self.auto_select = 0; self.emit_new(op, select)
    def default_shape(self, tool, wp):
        if tool == "frame": a, b = QPointF(wp.x(), wp.y()), QPointF(wp.x() + 520, wp.y() + 340)
        else: a, b = QPointF(wp.x() - 100, wp.y() - 70), QPointF(wp.x() + 100, wp.y() + 70)
        return [self.w2n(self.snapp(a)), self.w2n(self.snapp(b))]

    def mousePressEvent(self, e):
        self.setFocus()
        if self.editor: self.editor.finish(False)
        pos = e.position(); wp = self.s2w(pos); self.last_world = wp; self.last_screen = pos
        if e.button() == Qt.MiddleButton or (e.button() == Qt.LeftButton and (self.space_down or self.tool == "hand")):
            self.drag = {"mode": "pan", "start": pos, "origin": QPointF(self.origin)}; self.setCursor(Qt.ClosedHandCursor); return
        if e.button() != Qt.LeftButton: return
        t = self.tool
        if t == "select": self._press_select(e, pos, wp)
        elif t == "erase": self.drag = {"mode": "erase", "done": set()}; self.eraser_pos = pos; self.erase_at(wp)
        elif t in ("stroke", "highlighter"): self.current = self.new_op(t, [self.w2n(wp)]); self.drag = {"mode": "draw"}
        elif t in ("rect", "rounded", "ellipse", "triangle", "diamond", "frame", "line", "arrow", "curve"):
            n = self.w2n(self.snapp(wp)); self.current = self.new_op(t, [n, list(n)]); self.drag = {"mode": "shape", "start": pos}
        elif t == "sticky":
            op = self.new_op("sticky", [self.w2n(self.snapp(QPointF(wp.x() - 95, wp.y() - 95)))]); self.begin_editor(op, True)
        elif t in ("text", "comment"): self.begin_editor(self.new_op(t, [self.w2n(wp)]), True)
        elif t == "icon":
            op = {"kind": "text", "color": "#1A1A2E", "width": 3, "points": [self.w2n(QPointF(wp.x() - 30, wp.y() - 30))], "text": self.icon_text, "fs": 64}
            self.create_item(op); self.revert_to_select()
        elif t == "laser": self.laser_on = True; self.drag = {"mode": "laser"}; self.laser_trail.append((wp, time.monotonic())); self._send_cursor(wp, True)
    def _press_select(self, e, pos, wp):
        shift = bool(e.modifiers() & Qt.ShiftModifier); h = self.handle_at(pos)
        if h:
            op = self.sel_single(); self.drag = {"mode": "resize", "handle": h, "op": op, "orig": copy.deepcopy({k: op.get(k) for k in ("points", "w", "h", "fs")}), "dims": self.image_dims(op) if op["kind"] == "image" else None, "box": self.bbox(op)}; return
        op = self.hit_item(wp)
        if op:
            if shift:
                (self.selected.discard if op["id"] in self.selected else self.selected.add)(op["id"]); self.selection_changed.emit()
            elif op["id"] not in self.selected: self.selected = {op["id"]}; self.selection_changed.emit()
            if op["id"] in self.selected:
                orig = {i: copy.deepcopy(self.by_id[i]["points"]) for i in self.selected if i in self.by_id and self.can_edit(self.by_id[i])}
                self.drag = {"mode": "move", "start": wp, "orig": orig, "moved": False, "denied": len(orig) < len(self.selected)}
        else:
            if not shift and self.selected: self.selected = set(); self.selection_changed.emit()
            self.drag = {"mode": "marquee", "start": pos, "base": set(self.selected) if shift else set(), "rect": QRectF(pos, pos)}
    def mouseMoveEvent(self, e):
        pos = e.position(); wp = self.s2w(pos); self.last_world = wp; self.last_screen = pos; d = self.drag; mode = d["mode"] if d else None
        if mode == "pan":
            self.origin = QPointF(d["origin"].x() - (pos.x() - d["start"].x()) / self.zoom, d["origin"].y() - (pos.y() - d["start"].y()) / self.zoom); self._view_changed()
        elif mode == "draw": self.current["points"].append(self.w2n(wp)); self.update()
        elif mode == "shape":
            n = self.w2n(self.snapp(wp))
            if e.modifiers() & Qt.ShiftModifier and self.current["kind"] in SHAPE_KINDS + LINE_KINDS:
                a = self.n2w(self.current["points"][0]); dx, dy = wp.x() - a.x(), wp.y() - a.y()
                if self.current["kind"] in LINE_KINDS:
                    if abs(dx) > abs(dy) * 2: dy = 0
                    elif abs(dy) > abs(dx) * 2: dx = 0
                else: m = max(abs(dx), abs(dy)); dx, dy = math.copysign(m, dx or 1), math.copysign(m, dy or 1)
                n = self.w2n(QPointF(a.x() + dx, a.y() + dy))
            self.current["points"][1] = n; self.update()
        elif mode == "move": self._do_move(wp)
        elif mode == "resize": self._do_resize(wp)
        elif mode == "marquee": self._do_marquee(pos)
        elif mode == "erase": self.eraser_pos = pos; self.erase_at(wp); self.update()
        elif mode == "laser": self.laser_trail.append((wp, time.monotonic())); self.laser_trail = self.laser_trail[-60:]
        else:
            if self.tool == "erase": self.eraser_pos = pos; self.update()
            if self.tool == "select":
                hid = None; op = self.hit_item(wp)
                if op and op["kind"] == "comment": hid = op["id"]
                if hid != self.hover_id: self.hover_id = hid; self.update()
            self._update_cursor(pos)
        now = time.monotonic()
        if now - self.last_cursor_time > .04: self._send_cursor(wp, mode == "laser"); self.last_cursor_time = now
    def _send_cursor(self, wp, laser=False): self.send_cursor.emit(self.w2n(wp), {"laser": True} if laser else {})
    def _do_move(self, wp):
        d = self.drag; dx, dy = wp.x() - d["start"].x(), wp.y() - d["start"].y()
        if not d["moved"]:
            if math.hypot(dx, dy) * self.zoom < 4: return
            if d["denied"]: self.toast.emit("You can only move objects you created (ask the host or enable --open-editing).")
            d["moved"] = True
        if not d["orig"]: return
        dx, dy = self.snapv(dx), self.snapv(dy)
        allp = [pt for pts in d["orig"].values() for pt in pts]
        dx = max(-min(q[0] for q in allp) * WORLD_W, min((1 - max(q[0] for q in allp)) * WORLD_W, dx)); dy = max(-min(q[1] for q in allp) * WORLD_H, min((1 - max(q[1] for q in allp)) * WORLD_H, dy))
        for i, pts in d["orig"].items(): self.by_id[i]["points"] = [[q[0] + dx / WORLD_W, q[1] + dy / WORLD_H] for q in pts]
        self.update()
    def _do_resize(self, wp):
        d = self.drag; op = d["op"]; k = op["kind"]; h = d["handle"]; o = d["orig"]; wp = self.snapp(wp)
        if k in SHAPE_KINDS:
            r = QRectF(self.n2w(o["points"][0]), self.n2w(o["points"][1])).normalized()
            fixed = {"tl": r.bottomRight(), "tr": r.bottomLeft(), "bl": r.topRight(), "br": r.topLeft()}[h]
            op["points"] = [self.w2n(fixed), self.w2n(wp)]
        elif k in LINE_KINDS:
            pts = copy.deepcopy(o["points"]); pts[0 if h == "p0" else 1] = self.w2n(wp); op["points"] = pts
        elif k in ("sticky", "image"):
            a = self.n2w(o["points"][0]); w = max(40.0, wp.x() - a.x()); hh = max(40.0, wp.y() - a.y())
            if k == "image": ow, oh = d["dims"]; hh = w * oh / ow
            op["w"], op["h"] = min(w, 6000), min(hh, 6000)
        elif k == "text":
            a = self.n2w(o["points"][0]); box = d["box"]; op["fs"] = max(8, min(300, int((o["fs"] or 28) * max(10.0, wp.y() - a.y()) / max(10.0, box.height()))))
        self.fit_cache.clear(); self.update()
    def _do_marquee(self, pos):
        d = self.drag; r = QRectF(d["start"], pos).normalized(); d["rect"] = r; wr = QRectF(self.s2w(r.topLeft()), self.s2w(r.bottomRight())); sel = set(d["base"])
        for op in self.active_items():
            b = self.bbox(op)
            if (op["kind"] == "frame" and wr.contains(b)) or (op["kind"] != "frame" and wr.intersects(b)): sel.add(op["id"])
        if sel != self.selected: self.selected = sel; self.selection_changed.emit()
        self.update()
    def mouseReleaseEvent(self, e):
        d = self.drag; self.drag = None
        if not d: return
        mode = d["mode"]; pos = e.position(); wp = self.s2w(pos)
        if mode == "pan": self._update_cursor(pos)
        elif mode == "draw":
            op = self.current; self.current = None
            if len(op["points"]) > 5000: op["points"] = op["points"][::2][:5000]
            if len(op["points"]) > 2:   # light simplification keeps messages small
                pts = [op["points"][0]] + [q for a, q in zip(op["points"], op["points"][1:]) if abs(q[0] - a[0]) * WORLD_W + abs(q[1] - a[1]) * WORLD_H > 1.2 / max(self.zoom, 0.2)]
                op["points"] = pts if len(pts) > 1 else op["points"][:2]
            self.emit_new(op)
        elif mode == "shape":
            op = self.current; self.current = None; a = self.n2w(op["points"][0]); b = self.n2w(op["points"][1])
            tiny = math.hypot(b.x() - a.x(), b.y() - a.y()) * self.zoom < 8
            if tiny and op["kind"] in LINE_KINDS: self.update(); return
            if tiny: op["points"] = self.default_shape(self.tool, a)
            tool = self.tool; self.create_item(op); self.revert_to_select()
        elif mode == "move":
            if d["moved"]:
                for i in d["orig"]: self.send_update.emit(i, {"points": self.by_id[i]["points"]})
            self.update()
        elif mode == "resize":
            op = d["op"]; k = op["kind"]; ch = {"points": op["points"]} if k in SHAPE_KINDS + LINE_KINDS else ({"w": op["w"], "h": op["h"]} if k in ("sticky", "image") else {"fs": op["fs"]})
            if any(op.get(x) != d["orig"].get(x) for x in ch): self.send_update.emit(op["id"], ch)
        elif mode == "marquee": self.update()
        elif mode == "laser": self._send_cursor(wp, False)
        elif mode == "erase": self.eraser_pos = pos; self.update()
    def mouseDoubleClickEvent(self, e):
        if e.button() != Qt.LeftButton or self.tool != "select": return
        wp = self.s2w(e.position()); op = self.hit_item(wp)
        if op and op["kind"] in TEXT_KINDS: self.edit_item(op)
        elif not op: self.begin_editor(self.new_op("text", [self.w2n(wp)]), True)
    def edit_item(self, op):
        if not self.can_edit(op): self.toast.emit("You can only edit objects you created."); return
        self.begin_editor(op, False)

    # ── inline editor
    def begin_editor(self, op, new):
        if self.editor: self.editor.finish(False)
        k = op["kind"]; r = self.bbox(op); sr = QRectF(self.w2s(r.topLeft()), self.w2s(r.bottomRight()))
        fs = self.default_fs(op) * self.zoom; fill = op.get("fill", "")
        if k == "frame": sr = QRectF(sr.left(), sr.top() - fs * 1.9, max(sr.width(), 200), fs * 1.9)
        elif k in ("text", "comment"): sr = QRectF(sr.left(), sr.top(), max(sr.width() + 40, 240), max(sr.height() + 20, 70)); fill = ""
        elif k in SHAPE_KINDS: sr = sr.adjusted(8, sr.height() * 0.3, -8, -sr.height() * 0.3) if sr.height() > 80 else sr
        bg = fill if (fill and k in ("sticky",) + SHAPE_KINDS) else "#ffffff"
        fg = ink_for(fill) if fill and k in ("sticky",) + SHAPE_KINDS else ("#1A1A2E" if k in ("text", "comment") else INK)
        ed = InlineEditor(self, op.get("text", ""), fs, fg, bg); ed.setGeometry(sr.toRect()); ed.setPlaceholderText("Type here…")
        if k in ("sticky",) + SHAPE_KINDS and k != "frame": ed.document().setDefaultTextOption(QTextOption(Qt.AlignCenter))
        ed.committed.connect(self._editor_done); ed.show(); ed.setFocus(); self.editor = ed; self.edit_ctx = {"op": op, "new": new}
        if not new: self.editing_id = op.get("id")
        self.update()
    def _editor_done(self, text, cancelled):
        ed, ctx = self.editor, self.edit_ctx; self.editor = None; self.edit_ctx = None; self.editing_id = None
        if ed: ed.hide(); ed.deleteLater()
        self.setFocus()
        if not ctx: self.update(); return
        if cancelled:
            self.update(); return
        op = ctx["op"]; text = text.strip()[:2000]
        if ctx["new"]:
            if op["kind"] in ("text", "comment") and not text: self.update(); return
            op["text"] = text; self.create_item(op); self.revert_to_select()
        elif text != op.get("text", ""):
            if op["kind"] in ("text", "comment") and not text: self.delete_ids([op["id"]])
            else: op["text"] = text; self.fit_cache.clear(); self.send_update.emit(op["id"], {"text": text})
        self.update()

    # ── editing actions
    def delete_ids(self, ids):
        denied = 0
        for i in list(ids):
            op = self.by_id.get(i)
            if not op: continue
            if not self.can_edit(op): denied += 1; continue
            op["active"] = False; self.send_delete.emit(i); self.selected.discard(i)
        if denied: self.toast.emit(f"{denied} object(s) belong to someone else — only the host can delete them.")
        self.selection_changed.emit(); self.update()
    def delete_selected(self): self.delete_ids(list(self.selected))
    def erase_at(self, wp):
        tol = 14 / self.zoom
        for op in reversed(self.items):
            if not op.get("active", True) or op["kind"] not in STROKE_KINDS + LINE_KINDS or op["id"] in self.drag["done"]: continue
            if self.hit(op, wp, tol):
                self.drag["done"].add(op["id"])
                if self.can_edit(op): op["active"] = False; self.send_delete.emit(op["id"])
    def update_selected(self, changes_for):
        """changes_for(op) -> dict of changes (or {}); applied locally and sent to the server."""
        for i in list(self.selected):
            op = self.by_id.get(i)
            if not op or not self.can_edit(op): continue
            ch = changes_for(op)
            if ch: op.update(ch); self.send_update.emit(i, ch)
        self.fit_cache.clear(); self.update()
    def select_all(self): self.selected = {o["id"] for o in self.active_items()}; self.selection_changed.emit(); self.update()
    def copy_selected(self):
        self.clipboard_items = [copy.deepcopy({k: v for k, v in self.by_id[i].items() if k not in ("id", "author", "active")}) for i in sorted(self.selected) if i in self.by_id]
        return len(self.clipboard_items)
    def paste_items(self, at=None, offset=None):
        items = copy.deepcopy(self.clipboard_items)
        if not items: return
        tl = None
        for op in items:
            pts = [self.n2w(q) for q in op["points"]]; tl = QPointF(min(q.x() for q in pts), min(q.y() for q in pts)) if tl is None else QPointF(min(tl.x(), min(q.x() for q in pts)), min(tl.y(), min(q.y() for q in pts)))
        dx, dy = (at.x() - tl.x(), at.y() - tl.y()) if at else (offset or (40, 40))
        self.selected = set(); self.selection_changed.emit(); self.auto_select = len(items)
        for op in items:
            op["points"] = [self.w2n(QPointF(self.n2w(q).x() + dx, self.n2w(q).y() + dy)) for q in op["points"]]
            self.pending.append(dict(op, active=True)); self.send_op.emit(op)
        self.update()
    def duplicate_selected(self):
        if self.copy_selected(): self.paste_items(offset=(36, 36))
    def nudge(self, dx, dy):
        def ch(op): return {"points": [[min(1, max(0, q[0] + dx / WORLD_W)), min(1, max(0, q[1] + dy / WORLD_H))] for q in op["points"]]}
        self.update_selected(ch)
    def insert_image(self, img, at=None):
        if img.isNull(): self.toast.emit("That image could not be read."); return
        b64 = encode_qimage(img)
        if not b64: self.toast.emit("Image is too large to send."); return
        s = 420.0 / max(img.width(), img.height()); w, h = max(40, img.width() * s), max(40, img.height() * s)
        c = at or self.s2w(QPointF(self.width() / 2, self.height() / 2))
        self.create_item({"kind": "image", "color": "#202020", "width": 3, "points": [self.w2n(QPointF(c.x() - w / 2, c.y() - h / 2))], "data": b64, "w": w, "h": h, "text": ""})
    def paste_from_clipboard(self):
        md = QGuiApplication.clipboard().mimeData()
        if md and md.hasImage():
            img = QGuiApplication.clipboard().image(); self.insert_image(img, self.last_world); return
        if self.clipboard_items: self.paste_items(self.last_world); return
        if md and md.hasText() and md.text().strip():
            op = self.new_op("sticky", [self.w2n(QPointF(self.last_world.x() - 95, self.last_world.y() - 95))]); op["text"] = md.text().strip()[:2000]; self.create_item(op)
    def add_template(self, ops):
        """ops are built in world coordinates relative to the viewport centre; send them all."""
        self.selected = set(); self.selection_changed.emit(); self.auto_select = 0
        for op in ops: self.emit_new(op)

    # ── keyboard / menus / drops
    KEYMAP = {Qt.Key_V: "select", Qt.Key_H: "hand", Qt.Key_P: "stroke", Qt.Key_T: "text", Qt.Key_N: "sticky", Qt.Key_R: "rect", Qt.Key_O: "ellipse",
              Qt.Key_L: "line", Qt.Key_A: "arrow", Qt.Key_F: "frame", Qt.Key_E: "erase", Qt.Key_M: "highlighter", Qt.Key_C: "comment", Qt.Key_K: "laser"}
    def keyPressEvent(self, e):
        k = e.key(); mods = e.modifiers(); ctrl = bool(mods & Qt.ControlModifier); shift = bool(mods & Qt.ShiftModifier)
        if k == Qt.Key_Space and not e.isAutoRepeat(): self.space_down = True; self.setCursor(Qt.OpenHandCursor); return
        if ctrl:
            if k == Qt.Key_Z: (self.redo_req if shift else self.undo_req).emit()
            elif k == Qt.Key_Y: self.redo_req.emit()
            elif k == Qt.Key_A: self.select_all()
            elif k == Qt.Key_C: self.copy_selected()
            elif k == Qt.Key_X: (self.copy_selected(), self.delete_selected())
            elif k == Qt.Key_V: self.paste_from_clipboard()
            elif k == Qt.Key_D: self.duplicate_selected()
            elif k == Qt.Key_F: self.search_req.emit()
            elif k == Qt.Key_0: self.set_zoom(1.0)
            elif k in (Qt.Key_Equal, Qt.Key_Plus): self.zoom_at(1.25)
            elif k == Qt.Key_Minus: self.zoom_at(0.8)
            else: super().keyPressEvent(e)
            return
        if k in (Qt.Key_Delete, Qt.Key_Backspace): self.delete_selected()
        elif k == Qt.Key_Escape:
            if self.selected: self.selected = set(); self.selection_changed.emit(); self.update()
            else: self.tool_request.emit("select")
        elif k in (Qt.Key_Return, Qt.Key_Enter):
            op = self.sel_single()
            if op and op["kind"] in TEXT_KINDS: self.edit_item(op)
        elif k in (Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down) and self.selected:
            st = 10 if shift else 1; self.nudge({Qt.Key_Left: -st, Qt.Key_Right: st}.get(k, 0), {Qt.Key_Up: -st, Qt.Key_Down: st}.get(k, 0))
        elif k == Qt.Key_1 and shift: self.fit_content()
        elif k in self.KEYMAP: self.tool_request.emit(self.KEYMAP[k])
        else: super().keyPressEvent(e)
    def keyReleaseEvent(self, e):
        if e.key() == Qt.Key_Space and not e.isAutoRepeat(): self.space_down = False; self._update_cursor(None)
        else: super().keyReleaseEvent(e)
    def _update_cursor(self, pos):
        t = self.tool
        if self.space_down or t == "hand": self.setCursor(Qt.OpenHandCursor)
        elif t in ("text", "comment"): self.setCursor(Qt.IBeamCursor)
        elif t == "erase": self.setCursor(Qt.BlankCursor)
        elif t == "select":
            h = self.handle_at(pos) if pos is not None else None
            self.setCursor({"tl": Qt.SizeFDiagCursor, "br": Qt.SizeFDiagCursor, "tr": Qt.SizeBDiagCursor, "bl": Qt.SizeBDiagCursor}.get(h, Qt.SizeAllCursor if h else Qt.ArrowCursor))
        else: self.setCursor(Qt.CrossCursor)
    def contextMenuEvent(self, e):
        wp = self.s2w(QPointF(e.pos())); op = self.hit_item(wp)
        if op and op["id"] not in self.selected: self.selected = {op["id"]}; self.selection_changed.emit(); self.update()
        m = QMenu(self); m.setStyleSheet(f"QMenu{{background:#fff;border:1px solid {BORDER};border-radius:8px;padding:6px;}} QMenu::item{{padding:7px 22px;border-radius:5px;}} QMenu::item:selected{{background:{ACCENT_SOFT};color:{ACCENT};}}")
        if self.selected:
            sel = self.sel_single()
            if sel and sel["kind"] in TEXT_KINDS: m.addAction("Edit text", lambda: self.edit_item(sel))
            m.addAction("Duplicate\tCtrl+D", self.duplicate_selected); m.addAction("Copy\tCtrl+C", self.copy_selected); m.addAction("Delete\tDel", self.delete_selected)
        else:
            self.last_world = wp; m.addAction("Paste\tCtrl+V", self.paste_from_clipboard); m.addAction("Select all\tCtrl+A", self.select_all); m.addAction("Zoom to fit\tShift+1", self.fit_content)
        m.exec(e.globalPos())
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() or e.mimeData().hasImage(): e.acceptProposedAction()
    def dropEvent(self, e):
        at = self.s2w(e.position())
        for url in e.mimeData().urls():
            path = url.toLocalFile()
            if path.lower().endswith(FILE_IMG_EXT): self.insert_image(QImage(path), at)
        e.acceptProposedAction()

    # ── export
    def export_image(self, path, scale=2.0):
        r = self.content_rect(); r = (r.adjusted(-60, -110, 60, 60) if r else QRectF(WORLD_W / 2 - 400, WORLD_H / 2 - 250, 800, 500))
        while r.width() * scale > 9000 or r.height() * scale > 9000: scale *= 0.8
        img = QImage(int(r.width() * scale), int(r.height() * scale), QImage.Format_ARGB32); img.fill(QColor("#ffffff"))
        p = QPainter(img); p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing | QPainter.SmoothPixmapTransform); p.scale(scale, scale); p.translate(-r.x(), -r.y())
        self.paint_world(p, r); p.end(); return img.save(path)
    def export_pdf(self, path):
        from PySide6.QtGui import QPageLayout
        r = self.content_rect(); r = (r.adjusted(-60, -110, 60, 60) if r else QRectF(WORLD_W / 2 - 400, WORLD_H / 2 - 250, 800, 500))
        printer = QPrinter(QPrinter.HighResolution); printer.setOutputFormat(QPrinter.PdfFormat); printer.setOutputFileName(path)
        try: printer.setPageOrientation(QPageLayout.Landscape if r.width() >= r.height() else QPageLayout.Portrait)
        except Exception: pass
        p = QPainter()
        if not p.begin(printer): return False
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing | QPainter.SmoothPixmapTransform)
        t = printer.pageRect(QPrinter.DevicePixel); s = min(t.width() / r.width(), t.height() / r.height())
        p.translate(t.x() + (t.width() - r.width() * s) / 2, t.y() + (t.height() - r.height() * s) / 2); p.scale(s, s); p.translate(-r.x(), -r.y())
        p.setClipRect(r); self.paint_world(p, r); p.end(); return True

# ───────────────────────────── extra widgets ─────────────────────────────
class WidthBtn(QToolButton):
    def __init__(self, w):
        super().__init__(); self.w = w; self.setCheckable(True); self.setFixedSize(34, 34); self.setCursor(Qt.PointingHandCursor); self.setFocusPolicy(Qt.NoFocus); self.setToolTip(f"Thickness {w}")
        self.setStyleSheet(BTN_QSS)
    def paintEvent(self, e):
        super().paintEvent(e); p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor(ACCENT if self.isChecked() else INK), min(12, 1.5 + self.w * 0.6), Qt.SolidLine, Qt.RoundCap)); p.drawLine(QPointF(9, 17), QPointF(25, 17))

class ColorBtn(QToolButton):
    def __init__(self, tip):
        super().__init__(); self.setFixedSize(34, 34); self.setToolTip(tip); self.setCursor(Qt.PointingHandCursor); self.setFocusPolicy(Qt.NoFocus); self.color = "#1A1A2E"; self.setStyleSheet(BTN_QSS)
    def paintEvent(self, e):
        super().paintEvent(e); p = QPainter(self); p.setRenderHint(QPainter.Antialiasing); r = QRectF(7, 7, 20, 20)
        if self.color == "": p.setPen(QPen(QColor("#b6bac7"), 1.5)); p.setBrush(QColor("#fff")); p.drawEllipse(r); p.setPen(QPen(QColor("#e5484d"), 1.6)); p.drawLine(QPointF(10, 24), QPointF(24, 10))
        else: p.setPen(QPen(QColor("#c9ccd6"), 1.2)); p.setBrush(QColor(self.color)); p.drawEllipse(r)

class MiniMap(QWidget):
    def __init__(self, canvas):
        super().__init__(); self.cv = canvas; self.setFixedSize(220, 140); self.setCursor(Qt.PointingHandCursor); self.region = QRectF()
    def _region(self):
        r = self.cv.content_rect() or QRectF(WORLD_W / 2 - 800, WORLD_H / 2 - 500, 1600, 1000); r = r.united(self.cv.view_rect())
        r = r.adjusted(-r.width() * .08, -r.height() * .08, r.width() * .08, r.height() * .08); ar = self.width() / self.height()
        if r.width() / r.height() > ar: h = r.width() / ar; r = QRectF(r.left(), r.center().y() - h / 2, r.width(), h)
        else: w = r.height() * ar; r = QRectF(r.center().x() - w / 2, r.top(), w, r.height())
        return r
    def paintEvent(self, e):
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing); self.region = r = self._region(); k = self.width() / r.width()
        p.fillRect(self.rect(), QColor("#ffffff")); f = lambda wp: QPointF((wp.x() - r.left()) * k, (wp.y() - r.top()) * k)
        for op in self.cv.active_items():
            b = self.cv.bbox(op); rr = QRectF(f(b.topLeft()), f(b.bottomRight())); c = QColor(op.get("fill") or op.get("color") or "#999")
            if op["kind"] in STROKE_KINDS + LINE_KINDS: p.setPen(QPen(QColor(op.get("color", "#999")), 1)); p.setBrush(Qt.NoBrush)
            else: p.setPen(Qt.NoPen); p.setBrush(c if op["kind"] != "frame" else QColor("#e6e8ee"))
            p.drawRect(rr if rr.width() > 1 or rr.height() > 1 else rr.adjusted(-1, -1, 1, 1))
        vr = self.cv.view_rect(); v = QRectF(f(vr.topLeft()), f(vr.bottomRight())); p.setPen(QPen(QColor(ACCENT), 1.6)); p.setBrush(QColor(66, 98, 255, 30)); p.drawRect(v)
    def _go(self, e):
        r = self.region; k = self.width() / r.width(); self.cv.center_on(QPointF(r.left() + e.position().x() / k, r.top() + e.position().y() / k))
    def mousePressEvent(self, e): self._go(e)
    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.LeftButton: self._go(e)

class ShareDialog(QDialog):
    def __init__(self, parent, host, port, room, token, creating):
        super().__init__(parent); self.setWindowTitle("Share board"); self.setFixedWidth(440)
        self.setStyleSheet(f"QDialog{{background:#fff;}} QLabel{{color:{INK};}} QLineEdit{{border:1px solid #d3d6e0;border-radius:8px;padding:8px 10px;background:#f7f8fa;color:{INK};}}")
        v = QVBoxLayout(self); v.setContentsMargins(26, 24, 26, 22); v.setSpacing(8)
        t = QLabel("Board created" if creating else "Invite people"); t.setFont(ui_font(20, QFont.Bold)); v.addWidget(t)
        s = QLabel("Anyone on your network can join with these details and the board password."); s.setWordWrap(True); s.setStyleSheet("color:#6b7089;"); v.addWidget(s)
        def row(label, value, secret=False):
            l = QLabel(label); l.setStyleSheet("font-size:12px;font-weight:600;color:#4a4f69;margin-top:6px;"); v.addWidget(l)
            h = QHBoxLayout(); e = QLineEdit(value); e.setReadOnly(True); h.addWidget(e, 1)
            b = QPushButton("Copy"); b.setCursor(Qt.PointingHandCursor); b.setStyleSheet(f"QPushButton{{background:{ACCENT_SOFT};color:{ACCENT};border:0;border-radius:8px;padding:9px 14px;font-weight:700;}} QPushButton:hover{{background:#d9e0ff;}}")
            b.clicked.connect(lambda: (QGuiApplication.clipboard().setText(value), b.setText("Copied"))); h.addWidget(b); v.addLayout(h)
        row("Server address", f"{host}:{port}"); row("Board code", room)
        if token: row("Host token (keep private – lets you rejoin as host)", token)
        ok = QPushButton("Done"); ok.setCursor(Qt.PointingHandCursor); ok.setFixedHeight(40); ok.clicked.connect(self.accept)
        ok.setStyleSheet(f"QPushButton{{background:{ACCENT};color:#fff;border:0;border-radius:9px;font-weight:700;margin-top:10px;}} QPushButton:hover{{background:#3350e6;}}"); v.addWidget(ok)

class ShortcutsDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent); self.setWindowTitle("Keyboard shortcuts"); self.setFixedWidth(560)
        self.setStyleSheet(f"QDialog{{background:#fff;}} QLabel{{color:{INK};font-size:13px;}}")
        g = QGridLayout(self); g.setContentsMargins(26, 22, 26, 22); g.setHorizontalSpacing(18); g.setVerticalSpacing(7)
        rows = [("V", "Select"), ("H / Space+drag", "Pan"), ("T", "Text"), ("N", "Sticky note"), ("R / O", "Rectangle / Circle"), ("L / A", "Line / Arrow"),
                ("P / M / E", "Pen / Highlighter / Eraser"), ("F", "Frame"), ("C", "Comment"), ("K", "Laser pointer"), ("Ctrl+Z / Ctrl+Y", "Undo / Redo"),
                ("Ctrl+C / V / D", "Copy / Paste / Duplicate"), ("Del", "Delete selection"), ("Ctrl+A", "Select all"), ("Ctrl+F", "Search board"),
                ("Ctrl+Wheel / Pinch", "Zoom"), ("Shift+1", "Zoom to fit"), ("Ctrl+0", "Zoom 100%"), ("Arrows (+Shift)", "Nudge selection"), ("Enter / Double-click", "Edit text"),
                ("Shift (while drawing)", "Straight line / square"), ("Drop / Paste image", "Add picture")]
        for i, (k, d) in enumerate(rows):
            kk = QLabel(k); kk.setStyleSheet(f"background:#f0f1f5;border-radius:5px;padding:3px 8px;font-weight:600;"); g.addWidget(kk, i // 2, (i % 2) * 2, Qt.AlignLeft); g.addWidget(QLabel(d), i // 2, (i % 2) * 2 + 1)

# ───────────────────────────── main window ─────────────────────────────
TOOL_GROUP = {"select": "select", "text": "text", "sticky": "sticky", "rect": "shape", "rounded": "shape", "ellipse": "shape", "triangle": "shape", "diamond": "shape",
              "line": "line", "arrow": "line", "curve": "line", "stroke": "pen", "highlighter": "pen", "erase": "pen", "frame": "frame"}
TOOL_ICON = {"stroke": "pen", "highlighter": "marker", "erase": "eraser"}
TOOL_NAME = {"rect": "Rectangle", "rounded": "Rounded rectangle", "ellipse": "Circle", "triangle": "Triangle", "diamond": "Diamond", "arrow": "Arrow", "line": "Line",
             "curve": "Curved arrow", "stroke": "Pen", "highlighter": "Highlighter", "erase": "Eraser"}
GROUPS = {"shape": ["rect", "rounded", "ellipse", "triangle", "diamond"], "line": ["arrow", "line", "curve"], "pen": ["stroke", "highlighter", "erase"]}
GLOBAL_QSS = f"""
QMenu{{background:#fff;border:1px solid {BORDER};border-radius:10px;padding:6px;}}
QMenu::item{{padding:8px 26px 8px 12px;border-radius:6px;color:{INK};}}
QMenu::item:selected{{background:{ACCENT_SOFT};color:{ACCENT};}}
QMenu::separator{{height:1px;background:{BORDER};margin:5px 8px;}}
QToolTip{{background:{INK};color:#fff;border:0;padding:5px 8px;border-radius:5px;}}
QScrollBar:vertical{{width:8px;background:transparent;}} QScrollBar::handle:vertical{{background:#d3d6e0;border-radius:4px;min-height:30px;}} QScrollBar::add-line,QScrollBar::sub-line{{height:0;}}
"""

class Stage(QWidget):
    resized = Signal()
    def resizeEvent(self, e): self.resized.emit()

class MainWindow(QMainWindow):
    def __init__(self, data):
        super().__init__()
        self.data = data; rejoin = None
        if not data["create"]:
            rejoin = {"type": "join", "room": data["room"], "username": data["username"], "password": data["password"], "owner_token": data["owner_token"]}
        self.net = Network(data["host"], data["port"], rejoin)
        self.role = "editor"; self.owner_token = data["owner_token"]; self.room_code = data.get("room", "")
        self.initial_create_pending = bool(data["create"]); self.room_created = not self.initial_create_pending
        self.joined_successfully = False
        self.participant_map = {}; self.unread = 0; self.presenting = False; self.search_hits = []; self.search_i = -1
        self.group_tool = {"shape": "rect", "line": "arrow", "pen": "stroke"}
        self.build_ui()
        self.net.signals.message.connect(self.on_message); self.net.signals.status.connect(self.show_toast)
        self.connect_server()
        self.ctx_timer = QTimer(self); self.ctx_timer.timeout.connect(self.position_context); self.ctx_timer.start(120)

    # ── UI construction
    def build_ui(self):
        self.setWindowTitle("InkSync"); self.resize(1440, 860)
        self.stage = Stage(); self.setCentralWidget(self.stage); self.stage.resized.connect(self.relayout)
        self.canvas = cv = Canvas(); cv.setParent(self.stage); cv.me = self.data["username"]
        cv.send_op.connect(lambda op: self.net.send({"type": "op", "op": op}))
        cv.send_update.connect(lambda i, ch: self.net.send({"type": "update", "id": i, "changes": ch}))
        cv.send_delete.connect(lambda i: self.net.send({"type": "delete", "id": i}))
        cv.send_cursor.connect(lambda pt, extra: self.net.send(dict({"type": "cursor", "point": pt}, **extra)))
        cv.sync_needed.connect(lambda: self.net.send({"type": "sync"}))
        cv.selection_changed.connect(self.update_context); cv.view_changed.connect(self.on_view_changed); cv.content_changed.connect(lambda: self.minimap.update() if self.minimap.isVisible() else None)
        cv.tool_changed.connect(self.sync_tool_buttons); cv.tool_request.connect(self.on_tool_request); cv.toast.connect(self.show_toast)
        cv.undo_req.connect(lambda: self.net.send({"type": "undo"})); cv.redo_req.connect(lambda: self.net.send({"type": "redo"})); cv.search_req.connect(self.toggle_search)
        self._build_top_left(); self._build_top_right(); self._build_toolbar(); self._build_bottom(); self._build_context(); self._build_panel(); self._build_search()
        self.toast_lbl = QLabel(self.stage); self.toast_lbl.setStyleSheet(f"background:{INK};color:#fff;border-radius:18px;padding:10px 18px;font-size:13px;font-weight:600;"); self.toast_lbl.hide()
        self.toast_timer = QTimer(self); self.toast_timer.setSingleShot(True); self.toast_timer.timeout.connect(self.toast_lbl.hide)
        self.exit_present = QPushButton("Exit presentation  (Esc)", self.stage); self.exit_present.setCursor(Qt.PointingHandCursor); self.exit_present.hide(); self.exit_present.setFocusPolicy(Qt.NoFocus)
        self.exit_present.setStyleSheet(f"QPushButton{{background:{INK};color:#fff;border:0;border-radius:17px;padding:9px 18px;font-weight:600;}}"); self.exit_present.clicked.connect(self.toggle_present)
        self.set_tool("select"); self.update_context(); QTimer.singleShot(0, self.relayout)
    def _build_top_left(self):
        c = self.top_left = Card(self.stage); h = QHBoxLayout(c); h.setContentsMargins(16, 5, 8, 5); h.setSpacing(8)
        logo = QLabel("InkSync"); logo.setFont(ui_font(21, QFont.Black)); logo.setStyleSheet(f"color:{INK};"); h.addWidget(logo); h.addWidget(vsep(26))
        self.board_name = QLabel(self.data.get("name") or "Board"); self.board_name.setFont(ui_font(15, QFont.Medium)); self.board_name.setStyleSheet(f"color:{INK};padding:0 4px;"); h.addWidget(self.board_name); h.addWidget(vsep(26))
        g = tbtn("gear", "Board settings"); m = QMenu(g)
        gm = m.addMenu("Grid"); self.grid_actions = {}
        for key, label in (("lines", "Lines"), ("dots", "Dots"), ("none", "None")):
            a = gm.addAction(label); a.setCheckable(True); a.setChecked(key == "lines"); a.triggered.connect(lambda _=False, k=key: self.set_grid(k)); self.grid_actions[key] = a
        a = m.addAction("Snap to grid"); a.setCheckable(True); a.triggered.connect(lambda v: setattr(self.canvas, "snap", v))
        a = m.addAction("Show collaborator cursors"); a.setCheckable(True); a.setChecked(True); a.triggered.connect(lambda v: (setattr(self.canvas, "show_remote", v), self.canvas.update()))
        m.addSeparator(); self.clear_action = m.addAction("Clear board…"); self.clear_action.triggered.connect(self.clear_board)
        g.setMenu(m); g.setPopupMode(QToolButton.InstantPopup); h.addWidget(g)
        e = tbtn("export", "Export"); m2 = QMenu(e); m2.addAction("Export as PNG…", self.save_png); m2.addAction("Export as PDF…", self.save_pdf); m2.addSeparator(); m2.addAction("Insert image…", self.insert_image)
        e.setMenu(m2); e.setPopupMode(QToolButton.InstantPopup); h.addWidget(e)
        self.search_btn = tbtn("search", "Search board  (Ctrl+F)", cb=self.toggle_search); h.addWidget(self.search_btn)
    def _build_top_right(self):
        a = self.card_apps = Card(self.stage); ha = QHBoxLayout(a); ha.setContentsMargins(6, 5, 6, 5)
        ab = tbtn("apps", "Templates"); m = QMenu(ab); m.addAction("Retrospective  (Start / Stop / Continue)", lambda: self.add_template("retro")); m.addAction("Kanban board", lambda: self.add_template("kanban")); m.addAction("Flowchart starter", lambda: self.add_template("flow"))
        ab.setMenu(m); ab.setPopupMode(QToolButton.InstantPopup); ha.addWidget(ab)
        b = self.card_collab = Card(self.stage); hb = QHBoxLayout(b); hb.setContentsMargins(6, 5, 12, 5); hb.setSpacing(4)
        self.laser_btn = tbtn("laser", "Laser pointer  (K)", checkable=True, cb=lambda: self.set_tool("laser" if self.laser_btn.isChecked() else "select")); hb.addWidget(self.laser_btn)
        rb = tbtn("party", "Send a reaction"); rm = QMenu(rb)
        for em in REACTIONS: rm.addAction(em, lambda e=em: self.canvas.react(e))
        rb.setMenu(rm); rb.setPopupMode(QToolButton.InstantPopup); hb.addWidget(rb)
        self.chat_btn = tbtn("chat", "Chat & participants", checkable=True, cb=self.toggle_panel); hb.addWidget(self.chat_btn)
        self.badge = QLabel("", self.chat_btn); self.badge.setFixedSize(17, 17); self.badge.setAlignment(Qt.AlignCenter); self.badge.setStyleSheet("background:#e5484d;color:#fff;border-radius:8px;font-size:10px;font-weight:700;"); self.badge.move(23, 1); self.badge.hide()
        hb.addSpacing(4); self.stack = AvatarStack(); hb.addWidget(self.stack); self.me_avatar = Avatar(self.data["username"], 36); hb.addWidget(self.me_avatar)
        c = self.card_share = QWidget(self.stage); hc = QHBoxLayout(c); hc.setContentsMargins(0, 0, 0, 0); hc.setSpacing(0)
        self.present_btn = QToolButton(); self.present_btn.setText("Present"); self.present_btn.setIcon(make_icon("chevron", size=18)); self.present_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon); self.present_btn.setLayoutDirection(Qt.RightToLeft)
        self.present_btn.setFixedHeight(46); self.present_btn.setCursor(Qt.PointingHandCursor); self.present_btn.setFocusPolicy(Qt.NoFocus)
        self.present_btn.setStyleSheet(f"QToolButton{{background:#ffffff;border:1px solid {BORDER};border-radius:10px;padding:0 12px;font-size:15px;font-weight:600;color:{INK};}} QToolButton:hover{{background:#f4f5f8;}} QToolButton::menu-indicator{{image:none;}}")
        pm = QMenu(self.present_btn); pm.addAction("Present full screen", self.toggle_present); pm.addAction("Zoom to fit all content", self.canvas.fit_content); self.present_btn.setMenu(pm); self.present_btn.setPopupMode(QToolButton.InstantPopup); hc.addWidget(self.present_btn); hc.addSpacing(10)
        self.share_btn = QPushButton("  Share"); self.share_btn.setIcon(make_icon("people", "#ffffff", "#ffffff", 20)); self.share_btn.setFixedHeight(46); self.share_btn.setCursor(Qt.PointingHandCursor); self.share_btn.setFocusPolicy(Qt.NoFocus)
        self.share_btn.setStyleSheet(f"QPushButton{{background:{ACCENT};color:#fff;border:0;border-radius:10px;padding:0 20px;font-size:15px;font-weight:700;}} QPushButton:hover{{background:#3350e6;}}"); self.share_btn.clicked.connect(self.show_share); hc.addWidget(self.share_btn)
    def _build_toolbar(self):
        c = self.toolbar = Card(self.stage); v = QVBoxLayout(c); v.setContentsMargins(6, 8, 6, 8); v.setSpacing(6); self.tool_buttons = {}
        def add(key, icon, tip, cb, group=False):
            b = tbtn(icon, tip, size=44, isize=24, checkable=True, cb=cb); v.addWidget(b); self.tool_buttons[key] = b; return b
        add("select", "select", "Select  (V)", lambda: self.set_tool("select")); add("text", "text", "Text  (T)", lambda: self.set_tool("text")); add("sticky", "sticky", "Sticky note  (N)", lambda: self.set_tool("sticky"))
        add("shape", "shape", "Shapes  (R / O)", lambda: self.pick_group("shape")); add("line", "arrow", "Lines & arrows  (A / L)", lambda: self.pick_group("line"))
        add("pen", "pen", "Pen, highlighter & eraser  (P)", lambda: self.pick_group("pen")); add("frame", "frame", "Frame  (F)", lambda: self.set_tool("frame"))
        plus = QToolButton(); plus.setFixedSize(44, 44); plus.setIcon(make_icon("plus", "#ffffff", "#ffffff", 24)); plus.setIconSize(QSize(24, 24)); plus.setToolTip("More"); plus.setCursor(Qt.PointingHandCursor); plus.setFocusPolicy(Qt.NoFocus)
        plus.setStyleSheet(f"QToolButton{{background:{INK};border:0;border-radius:9px;}} QToolButton:hover{{background:#1c1a66;}} QToolButton::menu-indicator{{image:none;}}")
        pm = QMenu(plus)
        for icon, label, fn in (("image", "Image…", self.insert_image), ("emoji", "Emoji sticker…", self.choose_icon), ("comment", "Comment  (C)", lambda: self.set_tool("comment")),
                                ("hand", "Hand tool  (H)", lambda: self.set_tool("hand")), ("laser", "Laser pointer  (K)", lambda: self.set_tool("laser"))):
            a = pm.addAction(make_icon(icon, size=20), label); a.triggered.connect(fn)
        plus.setMenu(pm); plus.setPopupMode(QToolButton.InstantPopup); v.addWidget(plus)
        u = self.undo_card = Card(self.stage); uv = QVBoxLayout(u); uv.setContentsMargins(6, 6, 6, 6); uv.setSpacing(4)
        self.undo_btn = tbtn("undo", "Undo  (Ctrl+Z)", size=44, isize=24, cb=lambda: self.net.send({"type": "undo"})); self.redo_btn = tbtn("redo", "Redo  (Ctrl+Y)", size=44, isize=24, cb=lambda: self.net.send({"type": "redo"}))
        uv.addWidget(self.undo_btn); uv.addWidget(self.redo_btn)
    def _build_bottom(self):
        l = self.bl = Card(self.stage); hl = QHBoxLayout(l); hl.setContentsMargins(5, 5, 5, 5); self.map_btn = tbtn("panel", "Minimap", size=42, isize=24, checkable=True, cb=self.toggle_minimap); hl.addWidget(self.map_btn)
        self.minimap = MiniMap(self.canvas); self.minimap.setParent(self.stage); self.minimap.setStyleSheet(f"border:1px solid {BORDER};border-radius:10px;"); shadow(self.minimap); self.minimap.hide()
        r = self.br = Card(self.stage); hr = QHBoxLayout(r); hr.setContentsMargins(5, 5, 5, 5); hr.setSpacing(0)
        hr.addWidget(tbtn("minus", "Zoom out  (Ctrl -)", size=40, isize=20, cb=lambda: self.canvas.zoom_at(0.8)))
        self.zoom_btn = QToolButton(); self.zoom_btn.setText("100%"); self.zoom_btn.setFixedSize(66, 40); self.zoom_btn.setStyleSheet(BTN_QSS + "QToolButton{font-size:14px;font-weight:600;}"); self.zoom_btn.setCursor(Qt.PointingHandCursor); self.zoom_btn.setFocusPolicy(Qt.NoFocus)
        zm = QMenu(self.zoom_btn); zm.addAction("Zoom to fit  (Shift+1)", self.canvas.fit_content); zm.addSeparator()
        for z in (25, 50, 100, 150, 200): zm.addAction(f"{z}%", lambda z=z: self.canvas.set_zoom(z / 100.0))
        self.zoom_btn.setMenu(zm); self.zoom_btn.setPopupMode(QToolButton.InstantPopup); hr.addWidget(self.zoom_btn)
        hr.addWidget(tbtn("plus", "Zoom in  (Ctrl +)", size=40, isize=20, cb=lambda: self.canvas.zoom_at(1.25)))
        self.help_btn = QToolButton(self.stage); self.help_btn.setFixedSize(46, 46); self.help_btn.setIcon(make_icon("help", "#ffffff", "#ffffff", 22)); self.help_btn.setToolTip("Keyboard shortcuts"); self.help_btn.setCursor(Qt.PointingHandCursor); self.help_btn.setFocusPolicy(Qt.NoFocus)
        self.help_btn.setStyleSheet(f"QToolButton{{background:{INK};border:0;border-radius:23px;}} QToolButton:hover{{background:#1c1a66;}}"); self.help_btn.clicked.connect(lambda: ShortcutsDialog(self).exec()); shadow(self.help_btn)
        self.fit_btn = QToolButton(self.stage); self.fit_btn.setFixedSize(50, 50); self.fit_btn.setIcon(make_icon("fit", "#ffffff", "#ffffff", 24)); self.fit_btn.setToolTip("Zoom to fit all content  (Shift+1)"); self.fit_btn.setCursor(Qt.PointingHandCursor); self.fit_btn.setFocusPolicy(Qt.NoFocus)
        self.fit_btn.setStyleSheet(f"QToolButton{{background:{ACCENT};border:0;border-radius:12px;}} QToolButton:hover{{background:#3350e6;}}"); self.fit_btn.clicked.connect(lambda: self.canvas.fit_content()); shadow(self.fit_btn)
    def _build_context(self):
        c = self.ctx = Card(self.stage); h = QHBoxLayout(c); h.setContentsMargins(8, 6, 8, 6); h.setSpacing(2)
        self.ctx_stroke = ColorBtn("Colour"); self.ctx_stroke.clicked.connect(lambda: self.pick_color("stroke")); h.addWidget(self.ctx_stroke)
        self.ctx_fill = ColorBtn("Fill"); self.ctx_fill.clicked.connect(lambda: self.pick_color("fill")); h.addWidget(self.ctx_fill)
        self.ctx_widths = []
        for w in (2, 4, 8, 14):
            b = WidthBtn(w); b.clicked.connect(lambda _=False, w=w: self.set_width(w)); h.addWidget(b); self.ctx_widths.append(b)
        self.ctx_font_w = QWidget(); fh = QHBoxLayout(self.ctx_font_w); fh.setContentsMargins(0, 0, 0, 0); fh.setSpacing(0)
        fh.addWidget(tbtn("minus", "Smaller text", size=30, isize=16, cb=lambda: self.bump_font(-1))); self.ctx_font_lbl = QLabel("26"); self.ctx_font_lbl.setFixedWidth(30); self.ctx_font_lbl.setAlignment(Qt.AlignCenter); self.ctx_font_lbl.setStyleSheet(f"color:{INK};font-weight:600;"); fh.addWidget(self.ctx_font_lbl)
        fh.addWidget(tbtn("plus", "Larger text", size=30, isize=16, cb=lambda: self.bump_font(1))); h.addWidget(self.ctx_font_w)
        self.ctx_dash = tbtn("dash", "Dashed line", size=34, isize=20, checkable=True, cb=self.toggle_dash); h.addWidget(self.ctx_dash)
        self.ctx_sep = vsep(24); h.addWidget(self.ctx_sep)
        self.ctx_dup = tbtn("copy", "Duplicate  (Ctrl+D)", size=34, isize=20, cb=self.canvas.duplicate_selected); h.addWidget(self.ctx_dup)
        self.ctx_del = tbtn("trash", "Delete  (Del)", size=34, isize=20, cb=self.canvas.delete_selected); h.addWidget(self.ctx_del)
    def _build_panel(self):
        p = self.panel = Card(self.stage, radius=12); v = QVBoxLayout(p); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(0)
        head = QHBoxLayout(); head.setContentsMargins(10, 8, 8, 8); self.tab_people = QPushButton("People"); self.tab_chat = QPushButton("Chat")
        for b in (self.tab_people, self.tab_chat):
            b.setCheckable(True); b.setCursor(Qt.PointingHandCursor); b.setFocusPolicy(Qt.NoFocus); b.setFixedHeight(34)
            b.setStyleSheet(f"QPushButton{{border:0;border-radius:8px;padding:0 14px;font-weight:600;color:#6b7089;background:transparent;}} QPushButton:checked{{background:{ACCENT_SOFT};color:{ACCENT};}} QPushButton:hover{{background:#f0f1f5;}}")
            head.addWidget(b)
        head.addStretch(1); head.addWidget(tbtn("close", "Close", size=32, isize=18, cb=self.toggle_panel)); v.addLayout(head)
        self.pages = QStackedWidget(); v.addWidget(self.pages, 1)
        self.people_list = QWidget(); self.people_lay = QVBoxLayout(self.people_list); self.people_lay.setContentsMargins(14, 6, 14, 6); self.people_lay.setSpacing(8); self.people_lay.addStretch(1)
        sc = QScrollArea(); sc.setWidgetResizable(True); sc.setFrameShape(QFrame.NoFrame); sc.setWidget(self.people_list); sc.setStyleSheet("background:transparent;"); self.pages.addWidget(sc)
        chat = QWidget(); cl = QVBoxLayout(chat); cl.setContentsMargins(12, 0, 12, 12); cl.setSpacing(8)
        self.chat_view = QTextEdit(); self.chat_view.setReadOnly(True); self.chat_view.setStyleSheet(f"QTextEdit{{border:0;background:transparent;color:{INK};font-size:13px;}}"); cl.addWidget(self.chat_view, 1)
        row = QHBoxLayout(); self.chat_input = QLineEdit(); self.chat_input.setPlaceholderText("Message the board…"); self.chat_input.setStyleSheet(f"QLineEdit{{border:1px solid #d3d6e0;border-radius:9px;padding:9px 11px;background:#fff;color:{INK};}} QLineEdit:focus{{border:2px solid {ACCENT};padding:8px 10px;}}")
        self.chat_input.returnPressed.connect(self.send_chat); row.addWidget(self.chat_input, 1); row.addWidget(tbtn("send", "Send", size=38, isize=20, cb=self.send_chat)); cl.addLayout(row); self.pages.addWidget(chat)
        self.tab_people.clicked.connect(lambda: self.show_tab(0)); self.tab_chat.clicked.connect(lambda: self.show_tab(1)); self.show_tab(0); p.hide()
    def _build_search(self):
        s = self.search_card = Card(self.stage); h = QHBoxLayout(s); h.setContentsMargins(10, 6, 6, 6); h.setSpacing(4)
        self.search_edit = QLineEdit(); self.search_edit.setPlaceholderText("Search text on the board…"); self.search_edit.setFixedWidth(250)
        self.search_edit.setStyleSheet(f"QLineEdit{{border:0;background:transparent;font-size:14px;color:{INK};}}"); self.search_edit.returnPressed.connect(lambda: self.search_step(1)); self.search_edit.textChanged.connect(self.search_run); h.addWidget(self.search_edit)
        self.search_count = QLabel("0/0"); self.search_count.setStyleSheet("color:#6b7089;font-size:12px;"); h.addWidget(self.search_count)
        h.addWidget(tbtn("chevron", "Next", size=30, isize=18, cb=lambda: self.search_step(1))); h.addWidget(tbtn("close", "Close", size=30, isize=16, cb=self.toggle_search)); s.hide()

    # ── layout
    def relayout(self):
        W, H = self.stage.width(), self.stage.height(); self.canvas.setGeometry(0, 0, W, H); pres = self.presenting
        for c in (self.top_left, self.card_apps, self.card_collab, self.card_share, self.toolbar, self.undo_card, self.bl, self.help_btn, self.fit_btn):
            c.setVisible(not pres) if c not in (self.card_collab,) else None
        self.card_collab.setVisible(True); self.exit_present.setVisible(pres); self.br.setVisible(True)
        for c in (self.top_left, self.card_apps, self.card_collab, self.card_share, self.toolbar, self.undo_card, self.bl, self.br, self.search_card, self.ctx): c.adjustSize()
        self.top_left.move(16, 16); self.search_card.move(16, 16 + self.top_left.height() + 10)
        x = W - 16
        for c in (self.card_share, self.card_collab, self.card_apps):
            if c.isVisible(): x -= c.width(); c.move(x, 16 + (self.card_collab.height() - c.height()) // 2 if c is not self.card_collab else 16); x -= 10
        tb_h = self.toolbar.height() + 10 + self.undo_card.height(); y = max(86, (H - tb_h) // 2); self.toolbar.move(16, y); self.undo_card.move(16, y + self.toolbar.height() + 10)
        self.bl.move(16, H - 16 - self.bl.height()); self.minimap.move(16, H - 16 - self.bl.height() - 10 - self.minimap.height())
        self.fit_btn.move(W - 16 - self.fit_btn.width(), H - 16 - self.fit_btn.height()); self.help_btn.move(self.fit_btn.x() - 12 - self.help_btn.width(), H - 16 - self.help_btn.height() - 2)
        self.br.move(self.help_btn.x() - 12 - self.br.width(), H - 16 - self.br.height())
        self.panel.setGeometry(W - 16 - 340, 84, 340, max(240, H - 84 - 84)); self.exit_present.adjustSize(); self.exit_present.move((W - self.exit_present.width()) // 2, 18)
        self.position_context(); self.toast_lbl.adjustSize(); self.toast_lbl.move((W - self.toast_lbl.width()) // 2, H - 90)
        for w in (self.top_left, self.card_apps, self.card_collab, self.card_share, self.toolbar, self.undo_card, self.bl, self.br, self.help_btn, self.fit_btn, self.minimap, self.panel, self.ctx, self.search_card, self.toast_lbl, self.exit_present): w.raise_()
    # ── tools
    def pick_group(self, group):
        t = self.group_tool[group]; self.set_tool(t); b = self.tool_buttons[group]; pop = Popup(self)
        for i, name in enumerate(GROUPS[group]):
            bt = tbtn(TOOL_ICON.get(name, name), TOOL_NAME[name], size=42, isize=24, checkable=True); bt.setChecked(name == self.canvas.tool); bt.clicked.connect(lambda _=False, n=name, pp=pop: (self.set_tool(n), pp.close())); pop.lay.addWidget(bt, 0, i)
        pop.show_at(b, "right")
    def set_tool(self, t):
        self.canvas.set_tool(t); self.sync_tool_buttons(t); self.update_context(); self.canvas.setFocus()
    def sync_tool_buttons(self, t):
        g = TOOL_GROUP.get(t)
        for key, b in self.tool_buttons.items(): b.setChecked(key == g)
        if g in GROUPS: self.group_tool[g] = t; self.tool_buttons[g].setIcon(make_icon(TOOL_ICON.get(t, t), size=24)); self.tool_buttons[g].setToolTip(TOOL_NAME.get(t, ""))
        self.laser_btn.setChecked(t == "laser"); self.update_context()
    def on_tool_request(self, t):
        if t == "select" and self.presenting: self.toggle_present(); return
        self.set_tool(t)
    def choose_icon(self):
        pop = Popup(self)
        for i, em in enumerate(EMOJI_STICKERS):
            b = QToolButton(); b.setText(em); b.setFont(QFont("Arial", 18)); b.setFixedSize(42, 42); b.setStyleSheet(BTN_QSS); b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, e=em, pp=pop: (setattr(self.canvas, "icon_text", e), self.set_tool("icon"), self.show_toast(f"Click the board to place {e}"), pp.close())); pop.lay.addWidget(b, i // 6, i % 6)
        pop.adjustSize(); pop.move(self.toolbar.mapToGlobal(QPoint(self.toolbar.width() + 10, self.toolbar.height() - pop.height()))); pop.show()
    # ── context bar
    def ctx_state(self):
        cv = self.canvas; kinds = set(); sel = [cv.by_id[i] for i in cv.selected if i in cv.by_id and cv.by_id[i].get("active", True)]
        if sel: kinds = {o["kind"] for o in sel}
        elif cv.tool in ("stroke", "highlighter", "rect", "rounded", "ellipse", "triangle", "diamond", "frame", "line", "arrow", "sticky", "text"): kinds = {cv.tool}
        elif cv.tool == "curve": kinds = {"arrow"}
        return sel, kinds
    def update_context(self):
        cv = self.canvas; sel, kinds = self.ctx_state(); shp = {"rect", "rounded", "ellipse", "triangle", "diamond"}
        show = {"stroke": bool(kinds & (shp | {"stroke", "highlighter", "line", "arrow", "text"})), "fill": bool(kinds & (shp | {"sticky", "frame"})), "width": bool(kinds & (shp | {"stroke", "highlighter", "line", "arrow"})),
                "font": bool(kinds & (shp | {"text", "sticky", "frame", "comment"})), "dash": bool(kinds & (shp | {"line", "arrow"}))}
        self.ctx_stroke.setVisible(show["stroke"]); self.ctx_fill.setVisible(show["fill"]); [b.setVisible(show["width"]) for b in self.ctx_widths]; self.ctx_font_w.setVisible(show["font"]); self.ctx_dash.setVisible(show["dash"])
        self.ctx_sep.setVisible(bool(sel)); self.ctx_dup.setVisible(bool(sel)); self.ctx_del.setVisible(bool(sel))
        first = sel[0] if sel else None; s = cv.style
        hl = (not sel) and cv.tool == "highlighter"
        self.ctx_stroke.color = (first.get("color") if first else (s["hl"] if hl else s["color"])) or "#1A1A2E"; self.ctx_stroke.update()
        is_sticky = ("sticky" in kinds and not (kinds & shp))
        self.ctx_fill.color = ((first.get("fill", "") if first else (s["sticky"] if is_sticky else s["fill"]))); self.ctx_fill.update()
        wv = first.get("width", 4) if first else s["width"]
        for b in self.ctx_widths: b.setChecked(b.w == min((2, 4, 8, 14), key=lambda x: abs(x - wv)))
        self.ctx_font_lbl.setText(str(int(cv.default_fs(first) if first else s["fs"]))); self.ctx_dash.setChecked(bool(first.get("dash")) if first else s["dash"])
        any_ctx = any(show.values()) or bool(sel); self.ctx.setVisible(any_ctx and not self.presenting); self.ctx.adjustSize(); self.position_context()
    def position_context(self):
        if not self.ctx.isVisible(): return
        cv = self.canvas; W = self.stage.width(); sel = [cv.by_id[i] for i in cv.selected if i in cv.by_id and cv.by_id[i].get("active", True)]
        if sel and not (cv.drag and cv.drag.get("mode") in ("move", "resize")):
            r = cv.content_rect(set(cv.selected))
            if r is not None:
                tl = cv.w2s(r.topLeft()); br = cv.w2s(r.bottomRight()); x = (tl.x() + br.x()) / 2 - self.ctx.width() / 2; y = tl.y() - self.ctx.height() - 16
                if y < 76: y = br.y() + 16
                self.ctx.move(int(max(80, min(W - self.ctx.width() - 16, x))), int(max(76, min(self.stage.height() - self.ctx.height() - 90, y)))); return
        elif sel: return
        self.ctx.move(max(80, (W - self.ctx.width()) // 2), 82)
    def pick_color(self, which):
        cv = self.canvas; sel, kinds = self.ctx_state(); shp = {"rect", "rounded", "ellipse", "triangle", "diamond"}
        if which == "stroke":
            cur = self.ctx_stroke.color; pal = STROKE_COLORS
            def apply(c):
                if sel: cv.update_selected(lambda op: {"color": c} if op["kind"] in shp | {"stroke", "highlighter", "line", "arrow", "text"} else {})
                else: cv.style["hl" if cv.tool == "highlighter" else "color"] = c
                self.update_context()
        else:
            cur = self.ctx_fill.color; sticky = ("sticky" in kinds and not (kinds & shp)); pal = STICKY_COLORS if sticky else FILL_COLORS
            def apply(c):
                if sel: cv.update_selected(lambda op: {"fill": c or ("#FFFFFF" if op["kind"] == "sticky" else "")} if op["kind"] in shp | {"sticky", "frame"} else {})
                else: cv.style["sticky" if sticky else "fill"] = c or "#FFFFFF" if sticky else c
                self.update_context()
        pop = ColorPopup(pal, cur, apply, parent=self); pop.show_at(self.ctx_stroke if which == "stroke" else self.ctx_fill, "top")
    def set_width(self, w):
        cv = self.canvas
        if cv.selected: cv.update_selected(lambda op: {"width": w} if op["kind"] in SHAPE_KINDS + LINE_KINDS + STROKE_KINDS and op["kind"] != "frame" else {})
        cv.style["width"] = w; self.update_context()
    def bump_font(self, d):
        cv = self.canvas
        def nf(cur): return int(max(8, min(300, cur * (1.15 if d > 0 else 1 / 1.15))))
        if cv.selected: cv.update_selected(lambda op: {"fs": nf(cv.default_fs(op))} if op["kind"] in TEXT_KINDS else {})
        else: cv.style["fs"] = nf(cv.style["fs"])
        self.update_context()
    def toggle_dash(self):
        cv = self.canvas; v = self.ctx_dash.isChecked()
        if cv.selected: cv.update_selected(lambda op: {"dash": v} if op["kind"] in LINE_KINDS + SHAPE_KINDS and op["kind"] != "frame" else {})
        cv.style["dash"] = v; self.update_context()
    # ── view / panels
    def on_view_changed(self):
        self.zoom_btn.setText(f"{round(self.canvas.zoom * 100)}%"); self.position_context()
        if self.minimap.isVisible(): self.minimap.update()
    def set_grid(self, k):
        self.canvas.grid_style = k
        for key, a in self.grid_actions.items(): a.setChecked(key == k)
        self.canvas.update()
    def toggle_minimap(self): self.minimap.setVisible(self.map_btn.isChecked()); self.minimap.update()
    def toggle_panel(self):
        show = not self.panel.isVisible(); self.panel.setVisible(show); self.chat_btn.setChecked(show)
        if show:
            self.panel.raise_()
            if self.pages.currentIndex() == 1: self.clear_unread()
        self.canvas.setFocus()
    def show_tab(self, i):
        self.pages.setCurrentIndex(i); self.tab_people.setChecked(i == 0); self.tab_chat.setChecked(i == 1)
        if i == 1: self.clear_unread(); self.chat_input.setFocus()
    def clear_unread(self): self.unread = 0; self.badge.hide()
    def toggle_search(self):
        show = not self.search_card.isVisible(); self.search_card.setVisible(show); self.search_btn.setChecked(show)
        if show: self.search_card.raise_(); self.search_edit.setFocus(); self.search_edit.selectAll()
        else: self.canvas.setFocus()
    def search_run(self):
        q = self.search_edit.text().strip().lower(); self.search_hits = []; self.search_i = -1
        if q:
            hits = [o for o in self.canvas.active_items() if q in (o.get("text") or "").lower()]; hits.sort(key=lambda o: (round(o["points"][0][1], 2), o["points"][0][0])); self.search_hits = [o["id"] for o in hits]
        self.search_count.setText(f"0/{len(self.search_hits)}")
        if self.search_hits: self.search_step(1)
    def search_step(self, d):
        if not self.search_hits: return
        self.search_i = (self.search_i + d) % len(self.search_hits); op = self.canvas.by_id.get(self.search_hits[self.search_i])
        if not op: return
        self.canvas.selected = {op["id"]}; self.canvas.selection_changed.emit(); self.canvas.center_on(self.canvas.bbox(op).center()); self.search_count.setText(f"{self.search_i + 1}/{len(self.search_hits)}")
    def toggle_present(self):
        self.presenting = not self.presenting
        if self.presenting: self.showFullScreen(); self.canvas.selected = set(); self.canvas.selection_changed.emit(); self.panel.hide(); self.chat_btn.setChecked(False); self.search_card.hide()
        else: self.showNormal()
        self.relayout(); self.update_context(); self.canvas.setFocus()
    def show_toast(self, msg):
        self.toast_lbl.setText(msg); self.toast_lbl.adjustSize(); self.toast_lbl.move((self.stage.width() - self.toast_lbl.width()) // 2, self.stage.height() - 90); self.toast_lbl.show(); self.toast_lbl.raise_(); self.toast_timer.start(3800)
    def show_share(self):
        ShareDialog(self, self.data["host"], self.data["port"], self.room_code, self.owner_token if self.role == "host" else "", False).exec()
    # ── templates
    def add_template(self, name):
        cv = self.canvas; c = cv.s2w(QPointF(cv.width() / 2, cv.height() / 2)); P = lambda x, y: cv.w2n(QPointF(x, y))
        def sticky(x, y, fill, text=""): return {"kind": "sticky", "color": "#1A1A2E", "width": 3, "points": [P(x, y)], "text": text, "fill": fill, "w": 190, "h": 190, "fs": 24}
        def label(x, y, text, fs=64): return {"kind": "text", "color": "#1A1A2E", "width": 3, "points": [P(x, y)], "text": text, "fs": fs}
        def shape(kind, x, y, w, h, text, fill): return {"kind": kind, "color": "#1A1A2E", "width": 3, "points": [P(x, y), P(x + w, y + h)], "text": text, "fill": fill, "fs": 26}
        def arrow(x1, y1, x2, y2): return {"kind": "arrow", "color": "#1A1A2E", "width": 3, "points": [P(x1, y1), P(x2, y2)], "text": ""}
        ops = []
        if name == "retro":
            x0, y0 = c.x() - 700, c.y() - 330; spots = [(0, 110), (215, 90), (0, 325), (215, 305), (105, 540)]
            for i, (head, col, n) in enumerate((("Start", STICKY_COLORS[1], 5), ("Stop", STICKY_COLORS[2], 4), ("Continue", STICKY_COLORS[3], 3))):
                x = x0 + i * 480; ops.append(label(x, y0, head))
                ops += [sticky(x + sx, y0 + sy, col) for sx, sy in spots[:n]]
        elif name == "kanban":
            x0, y0 = c.x() - 700, c.y() - 380
            for i, t in enumerate(("To do", "Doing", "Done")):
                ops.append(shape("frame", x0 + i * 470, y0, 430, 760, t, "#FFFFFF"))
            ops += [sticky(x0 + 120, y0 + 40 + k * 215, STICKY_COLORS[k % 3 + 1], f"Task {k + 1}") for k in range(3)]
        else:
            x0, y0 = c.x() - 300, c.y() - 330; B = "#7CB7F7"; Y = "#F9E163"
            ops += [shape("rounded", x0, y0, 200, 80, "Start", B), shape("rounded", x0, y0 + 170, 200, 80, "Receive order", B), shape("rounded", x0, y0 + 340, 200, 80, "Check stock", B),
                    shape("ellipse", x0 + 330, y0 + 310, 190, 140, "In stock", Y), shape("rect", x0 + 330, y0 + 500, 200, 80, "Cancel order", B), shape("ellipse", x0 + 330, y0 + 640, 190, 140, "Card valid?", Y),
                    arrow(x0 + 100, y0 + 80, x0 + 100, y0 + 170), arrow(x0 + 100, y0 + 250, x0 + 100, y0 + 340), arrow(x0 + 200, y0 + 380, x0 + 330, y0 + 380), arrow(x0 + 425, y0 + 450, x0 + 430, y0 + 500), arrow(x0 + 430, y0 + 580, x0 + 425, y0 + 640)]
        cv.add_template(ops); QTimer.singleShot(250, lambda: cv.fit_content(None) if False else None)
    # ── files
    def save_png(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export board", "board.png", "PNG image (*.png)")
        if path: self.show_toast("Exported PNG" if self.canvas.export_image(path) else "Could not save the image")
    def save_pdf(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export board", "board.pdf", "PDF (*.pdf)")
        if path: self.show_toast("Exported PDF" if self.canvas.export_pdf(path) else "Could not create the PDF")
    def insert_image(self):
        path, _ = QFileDialog.getOpenFileName(self, "Insert image", "", "Images (*.png *.jpg *.jpeg *.bmp *.gif *.webp)")
        if path: self.canvas.insert_image(QImage(path))
    def clear_board(self):
        if self.role != "host": self.show_toast("Only the host can clear the board."); return
        if QMessageBox.question(self, "Clear board", "Remove everything from the board for everyone?") == QMessageBox.Yes: self.net.send({"type": "clear"})
    # ── networking
    def connect_server(self):
        if self.data["create"]:
            self.create_request_id = secrets.token_hex(16)
            self.net.send({"type": "create", "request_id": self.create_request_id,
                           "name": self.data["name"], "username": self.data["username"],
                           "password": self.data["password"]})
    def refresh_people(self):
        me = self.data["username"]; others = sorted([p["username"] for p in self.participant_map.values() if p["username"] != me], key=str.casefold)
        self.stack.set_people(others, 3); self.tab_people.setText(f"People ({len(self.participant_map)})"); QTimer.singleShot(0, self.relayout)
        while self.people_lay.count() > 1:
            w = self.people_lay.takeAt(0).widget()
            if w: w.deleteLater()
        for p in sorted(self.participant_map.values(), key=lambda x: (x["username"] != me, x["username"].casefold())):
            row = QWidget(); h = QHBoxLayout(row); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(10); h.addWidget(Avatar(p["username"], 32))
            n = QLabel(p["username"] + ("  (you)" if p["username"] == me else "")); n.setStyleSheet(f"color:{INK};font-size:14px;font-weight:600;"); h.addWidget(n, 1)
            if p.get("role") == "host":
                tag = QLabel("Host"); tag.setStyleSheet(f"background:{ACCENT_SOFT};color:{ACCENT};border-radius:9px;padding:2px 9px;font-size:11px;font-weight:700;"); h.addWidget(tag)
            self.people_lay.insertWidget(self.people_lay.count() - 1, row)
    def add_chat(self, c, live=False):
        user = str(c.get("username", "")); col = user_color(user); ts = str(c.get("ts", ""))[11:16]
        self.chat_view.append(f"<div style='margin-bottom:6px'><b style='color:{col}'>{html.escape(user)}</b> <span style='color:#9aa0b5;font-size:11px'>{html.escape(ts)}</span><br>{html.escape(str(c.get('text', ''))).replace(chr(10), '<br>')}</div>")
        if live and user != self.data["username"] and not (self.panel.isVisible() and self.pages.currentIndex() == 1):
            self.unread += 1; self.badge.setText(str(min(self.unread, 9))); self.badge.show()
    def send_chat(self):
        text = self.chat_input.text().strip()
        if text: self.net.send({"type": "chat", "text": text}); self.chat_input.clear()
    def on_message(self, m):
        typ = m.get("type"); cv = self.canvas
        if typ == "created":
            self.room_created = True; self.initial_create_pending = False; self.owner_token = m.get("owner_token", ""); self.room_code = m.get("room", ""); self.data["room"] = self.room_code; self.data["owner_token"] = self.owner_token; self.data["create"] = False
            self.net.rejoin_message = {"type": "join", "room": self.room_code, "username": self.data["username"], "password": self.data["password"], "owner_token": self.owner_token}
            QTimer.singleShot(300, lambda: ShareDialog(self, self.data["host"], self.data["port"], self.room_code, self.owner_token, True).exec())
        elif typ == "joined":
            self.joined_successfully = True
            self.role = m.get("role", "editor"); cv.role = self.role; cv.me = m.get("username", self.data["username"]); cv.open_editing = bool(m.get("open_editing")); self.room_code = m.get("room", self.room_code)
            self.board_name.setText(m.get("name") or self.data.get("name") or self.room_code); self.setWindowTitle(f"{self.board_name.text()} — InkSync ({self.role})"); self.clear_action.setEnabled(self.role == "host"); self.relayout()
        elif typ == "snapshot":
            if not self.joined_successfully:
                return
            cv.load_snapshot(m); self.participant_map = {p["id"]: p for p in m.get("participants", [])}; self.refresh_people(); self.chat_view.clear()
            for c in m.get("chat", []): self.add_chat(c)
            if not self.isVisible(): self.show()
        elif typ == "canvas_event": cv.apply_event(m)
        elif typ == "chat_event": self.add_chat(m["message"], True)
        elif typ == "participant_joined":
            p = m["participant"]; self.participant_map[p["id"]] = p; self.refresh_people(); self.show_toast(f"{p['username']} joined")
        elif typ == "participant_left":
            self.participant_map.pop(m.get("id"), None); cv.remove_remote(m.get("id")); self.refresh_people(); self.show_toast(f"{m.get('username')} left")
        elif typ == "error":
            msg = m.get("message", "Unknown error")
            if not self.joined_successfully:
                QMessageBox.warning(self, "Unable to join board", msg)
                self.net.close()
                self.close()
                return
            self.show_toast(msg)
            cv.pending.clear()
            self.net.send({"type": "sync"})
        elif typ == "cursor": cv.set_remote_cursor(m)
    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape and self.presenting: self.toggle_present(); return
        super().keyPressEvent(e)
    def closeEvent(self, e): self.net.close(); e.accept()

def main():
    app = QApplication(sys.argv); app.setFont(ui_font(13)); app.setStyleSheet(GLOBAL_QSS)
    dlg = ConnectDialog()
    if dlg.exec() != QDialog.Accepted: return
    w = MainWindow(dlg.data)
    if dlg.data["create"]:
        w.show()
    # For joining, MainWindow is shown only after "joined" and "snapshot"
    # arrive from the server. A rejected username therefore never sees a board.
    sys.exit(app.exec())
if __name__ == "__main__": main()
