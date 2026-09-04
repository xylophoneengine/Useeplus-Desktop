"""QGraphicsView showing the frame with a scale bar and measurement overlays.
Scene coordinates == image pixels, so measurements are independent of view zoom."""
from __future__ import annotations

from enum import Enum, auto

from PySide6.QtCore import Qt, QPointF, QRectF, Signal
from PySide6.QtGui import QImage, QPixmap, QPen, QColor, QFont, QPainter
from PySide6.QtWidgets import (QGraphicsView, QGraphicsScene, QGraphicsPixmapItem, QGraphicsItemGroup,
                               QGraphicsLineItem, QGraphicsRectItem, QGraphicsPathItem,
                               QGraphicsSimpleTextItem)
from PySide6.QtGui import QPainterPath

from . import measure
from .calibration import Calibration


class Tool(Enum):
    NONE = auto()
    LINE = auto()
    POLYLINE = auto()
    RECT = auto()
    ANGLE = auto()


PEN = QPen(QColor(255, 230, 0), 0)          # cosmetic width 0 = 1 device px at any zoom
PEN.setCosmetic(True)
TEXT_COLOR = QColor(255, 230, 0)


class MicroscopeView(QGraphicsView):
    measurementsChanged = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setBackgroundBrush(QColor(30, 30, 30))
        self._pix = QGraphicsPixmapItem()
        self._pix.setZValue(0)
        self._scene.addItem(self._pix)
        self._bar_group = QGraphicsItemGroup(); self._bar_group.setZValue(10); self._bar_group.setVisible(False)
        self._scene.addItem(self._bar_group)
        self._bar_label = ""
        self._items: list[QGraphicsItemGroup] = []
        self._labels: list[QGraphicsSimpleTextItem] = []   # parallel to _items
        self._meas: list[dict] = []
        self._cal: Calibration | None = None
        self.tool = Tool.NONE
        self._pts: list[tuple[float, float]] = []   # in-progress points
        self._preview: QGraphicsItemGroup | None = None
        self._fitted = False
        self._panning = False
        self._frame_size: tuple[int, int] | None = None

    # ---- frame ---------------------------------------------------------
    def set_frame(self, img: QImage) -> None:
        self._pix.setPixmap(QPixmap.fromImage(img))
        size = (img.width(), img.height())
        if size != self._frame_size:
            self._frame_size = size
            self._scene.setSceneRect(QRectF(0, 0, *size))
            self._update_scale_bar()
            self._fitted = False
        if not self._fitted:
            self.fit()

    def fit(self) -> None:
        if not self._scene.sceneRect().isEmpty():
            self.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
            self._fitted = True

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        if self._fitted:
            self.fit()

    # ---- calibration / scale bar -------------------------------------
    def set_calibration(self, cal: Calibration | None) -> None:
        self._cal = cal
        self._update_scale_bar()
        for m, lbl in zip(self._meas, self._labels):
            m["label"] = self._label_for(m["type"], m["value_px"])
            lbl.setText(m["label"])
        self.measurementsChanged.emit()

    def scale_bar_visible(self) -> bool:
        return self._bar_group.isVisible()

    def scale_bar_label(self) -> str:
        return self._bar_label

    def _update_scale_bar(self) -> None:
        for c in self._bar_group.childItems():
            self._scene.removeItem(c)
        w, h = self._scene.sceneRect().width(), self._scene.sceneRect().height()
        if self._cal is None or w == 0:
            self._bar_group.setVisible(False)
            self._bar_label = ""
            return
        bar_px, self._bar_label = measure.scale_bar(self._cal.um_per_px, int(w))
        margin = w * 0.03
        x1, y = w - margin, h - margin
        line = QGraphicsLineItem(x1 - bar_px, y, x1, y)
        pen = QPen(QColor(255, 255, 255), 3); pen.setCosmetic(True); line.setPen(pen)
        txt = QGraphicsSimpleTextItem(self._bar_label)
        txt.setBrush(QColor(255, 255, 255)); txt.setFont(QFont("Helvetica", 14))
        txt.setPos(x1 - bar_px, y - 22)
        self._bar_group.addToGroup(line); self._bar_group.addToGroup(txt)
        self._bar_group.setVisible(True)

    # ---- measurements -----------------------------------------------
    def set_tool(self, tool: Tool) -> None:
        self._cancel_in_progress()
        self.tool = tool
        self.setCursor(Qt.CursorShape.CrossCursor if tool != Tool.NONE else Qt.CursorShape.ArrowCursor)

    def measurements(self) -> list[dict]:
        return [dict(m) for m in self._meas]

    def clear_measurements(self) -> None:
        for g in self._items:
            self._scene.removeItem(g)
        self._items.clear(); self._labels.clear(); self._meas.clear()
        self._cancel_in_progress()
        self.measurementsChanged.emit()

    def _label_for(self, kind: str, value_px: float) -> str:
        upp = self._cal.um_per_px if self._cal else None
        if kind == "rect":
            return measure.format_area(value_px, upp)
        if kind == "angle":
            return f"{value_px:.1f}°"
        return measure.format_length(value_px, upp)

    def _shape_group(self, kind: str, pts: list[tuple[float, float]]) -> QGraphicsItemGroup:
        g = QGraphicsItemGroup(); g.setZValue(5)
        if kind == "rect":
            (x0, y0), (x1, y1) = pts
            item = QGraphicsRectItem(QRectF(QPointF(x0, y0), QPointF(x1, y1)).normalized())
        else:
            path = QPainterPath(QPointF(*pts[0]))
            for p in pts[1:]:
                path.lineTo(QPointF(*p))
            item = QGraphicsPathItem(path)
        item.setPen(PEN); g.addToGroup(item)
        return g

    def _value(self, kind: str, pts) -> float:
        if kind == "line":
            return measure.length(pts[0], pts[1])
        if kind == "polyline":
            return measure.polyline_length(pts)
        if kind == "rect":
            (x0, y0), (x1, y1) = pts
            return abs(x1 - x0) * abs(y1 - y0)
        return measure.angle(pts[0], pts[1], pts[2])

    def _commit(self, kind: str, pts: list[tuple[float, float]]) -> None:
        value = self._value(kind, pts)
        label = self._label_for(kind, value)
        g = self._shape_group(kind, pts)
        txt = QGraphicsSimpleTextItem(label); txt.setBrush(TEXT_COLOR); txt.setFont(QFont("Helvetica", 12))
        anchor = pts[1] if kind == "angle" else pts[-1]
        txt.setPos(anchor[0] + 4, anchor[1] + 4)
        g.addToGroup(txt)
        self._scene.addItem(g)
        self._items.append(g); self._labels.append(txt)
        self._meas.append({"type": kind, "points": [[float(x), float(y)] for x, y in pts],
                           "value_px": float(value), "label": label})
        self.measurementsChanged.emit()

    def _cancel_in_progress(self) -> None:
        self._pts = []
        if self._preview is not None:
            self._scene.removeItem(self._preview)
            self._preview = None

    def _show_preview(self, kind: str, pts) -> None:
        if self._preview is not None:
            self._scene.removeItem(self._preview)
        self._preview = self._shape_group(kind, pts)
        self._scene.addItem(self._preview)

    # tool input, image coordinates -------------------------------------
    def tool_press(self, x: float, y: float) -> None:
        if self.tool in (Tool.LINE, Tool.RECT):
            self._pts = [(x, y)]
        elif self.tool == Tool.POLYLINE:
            self._pts.append((x, y))
            if len(self._pts) > 1:
                self._show_preview("polyline", self._pts)
        elif self.tool == Tool.ANGLE:
            self._pts.append((x, y))
            if len(self._pts) == 3:
                pts, self._pts = self._pts, []
                self._cancel_in_progress()
                self._commit("angle", pts)
            elif len(self._pts) == 2:
                self._show_preview("line", self._pts)

    def tool_move(self, x: float, y: float) -> None:
        if self.tool in (Tool.LINE, Tool.RECT) and self._pts:
            self._show_preview("line" if self.tool == Tool.LINE else "rect", [self._pts[0], (x, y)])
        elif self.tool == Tool.POLYLINE and self._pts:
            self._show_preview("polyline", self._pts + [(x, y)])
        elif self.tool == Tool.ANGLE and len(self._pts) == 2:
            self._show_preview("polyline", self._pts + [(x, y)])

    def tool_release(self, x: float, y: float) -> None:
        if self.tool in (Tool.LINE, Tool.RECT) and self._pts:
            pts = [self._pts[0], (x, y)]
            self._cancel_in_progress()
            if pts[0] != pts[1]:
                self._commit("line" if self.tool == Tool.LINE else "rect", pts)

    def tool_double_click(self, x: float, y: float) -> None:
        if self.tool == Tool.POLYLINE:
            pts = list(self._pts)
            if not pts or pts[-1] != (x, y):
                pts.append((x, y))
            self._cancel_in_progress()
            if len(pts) >= 2:
                self._commit("polyline", pts)

    # ---- Qt events → tool input --------------------------------------
    def _img(self, e) -> tuple[float, float]:
        p = self.mapToScene(e.position().toPoint())
        return p.x(), p.y()

    def mousePressEvent(self, e) -> None:
        if self.tool == Tool.NONE and e.button() == Qt.MouseButton.LeftButton:   # Pan tool: left-drag pans via ScrollHandDrag
            self._panning = True
            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
            super().mousePressEvent(e)
            return
        if e.button() == Qt.MouseButton.LeftButton:
            self.tool_press(*self._img(e))
        elif e.button() == Qt.MouseButton.RightButton:
            self._cancel_in_progress()
        e.accept()

    def mouseMoveEvent(self, e) -> None:
        if self._panning:
            super().mouseMoveEvent(e); return
        self.tool_move(*self._img(e))
        e.accept()

    def mouseReleaseEvent(self, e) -> None:
        if self._panning:
            super().mouseReleaseEvent(e)
            self._panning = False
            self.setDragMode(QGraphicsView.DragMode.NoDrag)
            return
        if e.button() == Qt.MouseButton.LeftButton:
            self.tool_release(*self._img(e))
        e.accept()

    def mouseDoubleClickEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton and self.tool == Tool.POLYLINE:
            self.tool_double_click(*self._img(e))
        e.accept()

    def wheelEvent(self, e) -> None:
        f = 1.15 if e.angleDelta().y() > 0 else 1 / 1.15
        self.scale(f, f)
        self._fitted = False
        e.accept()
