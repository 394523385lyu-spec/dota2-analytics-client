from __future__ import annotations

import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict

from PIL import Image, ImageDraw, ImageFilter
from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QObject,
    QRunnable,
    QSortFilterProxyModel,
    Qt,
    QThreadPool,
    QTimer,
    Signal,
)
from PySide6.QtGui import QIcon, QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSlider,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QTableView,
    QHeaderView,
    QVBoxLayout,
    QWidget,
)

from ..reporting.ward_visualization import generate_ward_visualization_html
from ..services.excel_exporter import (
    export_hero_meta_workbook,
    export_module_workbooks,
    export_raw_data_workbook,
)
from ..services.localization import display_label, display_value
from ..services.opendota import OpenDotaService
from ..services.team_modules import analyze_team_modules
from ..paths import global_output_dir, resource_path, team_output_dir
from ..assets import load_map_image, load_ward_icon
from .ward_widget import WardMapWidget


WARD_ASSET_DIR = resource_path("assets", "wards")
APP_ICON = resource_path("assets", "app_icon.png")


class WorkerSignals(QObject):
    finished = Signal(object)
    failed = Signal(str)


class Worker(QRunnable):
    def __init__(self, func: Callable[[], Any]) -> None:
        super().__init__()
        self.func = func
        self.signals = WorkerSignals()

    def run(self) -> None:
        try:
            self.signals.finished.emit(self.func())
        except Exception as exc:
            logging.exception("后台任务失败")
            self.signals.failed.emit(str(exc))


class DataTableModel(QAbstractTableModel):
    def __init__(self, rows: list[Dict[str, Any]]) -> None:
        super().__init__()
        self.rows = rows
        self.columns: list[str] = []
        for row in rows:
            for key in row:
                if key not in self.columns:
                    self.columns.append(key)

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.columns)

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole) -> Any:
        if not index.isValid():
            return None
        value = display_value(
            self.rows[index.row()].get(self.columns[index.column()])
        )
        if role == Qt.DisplayRole:
            if value is None:
                return ""
            if isinstance(value, (dict, list, tuple)):
                return json.dumps(value, ensure_ascii=False)
            return str(value)
        if role == Qt.TextAlignmentRole and isinstance(
            value, (int, float)
        ) and not isinstance(value, bool):
            return int(Qt.AlignRight | Qt.AlignVCenter)
        if role == Qt.UserRole:
            return value
        return None

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.DisplayRole,
    ) -> Any:
        if role != Qt.DisplayRole:
            return None
        if orientation == Qt.Horizontal and section < len(self.columns):
            return display_label(self.columns[section])
        return section + 1


class NumericSortProxy(QSortFilterProxyModel):
    def lessThan(self, left: QModelIndex, right: QModelIndex) -> bool:
        left_value = left.data(Qt.UserRole)
        right_value = right.data(Qt.UserRole)
        if isinstance(left_value, (int, float)) and isinstance(
            right_value, (int, float)
        ):
            return left_value < right_value
        return str(left.data(Qt.DisplayRole) or "").lower() < str(
            right.data(Qt.DisplayRole) or ""
        ).lower()


class LegacyWardMapWidget(QWidget):
    def __init__(self, module: Dict[str, Any], team_name: str) -> None:
        super().__init__()
        self.items: list[Dict[str, Any]] = []
        for side, key in (
            ("我方", "mine_details"),
            ("对手", "opponent_details"),
        ):
            for row in module.get(key, []):
                if row.get("x") is None or row.get("y") is None:
                    continue
                self.items.append({**row, "_side": side})
        self.map_image = load_map_image()
        self.obs_icon = load_ward_icon(WARD_ASSET_DIR / "observer.png", "#58A6FF")
        self.sen_icon = load_ward_icon(WARD_ASSET_DIR / "sentry.png", "#F2C94C")
        self.obs_icon_small = self.obs_icon.resize(
            (22, 22), Image.Resampling.LANCZOS
        )
        self.sen_icon_small = self.sen_icon.resize(
            (22, 22), Image.Resampling.LANCZOS
        )
        self._coordinate_offset = bool(self.items) and all(
            40 <= float(row["x"]) <= 220 and 40 <= float(row["y"]) <= 220
            for row in self.items
        )
        self._base_cache: Dict[int, Image.Image] = {}
        self._render_cache: Dict[tuple[Any, ...], QPixmap] = {}
        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(90)
        self._render_timer.timeout.connect(self._render_now)
        self.match_meta: Dict[str, Dict[str, str]] = {}
        for row in self.items:
            match_id = str(row.get("Match ID") or "")
            if not match_id or match_id in self.match_meta:
                continue
            self.match_meta[match_id] = {
                "result": str(row.get("结果") or "未知"),
                "opponent": str(row.get("对手队伍") or "未知对手"),
                "camp": str(row.get("我方阵营") or "未知阵营"),
            }
        self._build(team_name)
        self._sync_time()

    def _build(self, team_name: str) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(12)
        sidebar = QFrame()
        sidebar.setObjectName("metricCard")
        sidebar.setMaximumWidth(330)
        sidebar.setMinimumWidth(270)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(12, 12, 12, 12)
        side.setSpacing(8)
        title = QLabel(f"{team_name} · 眼位地图")
        title.setStyleSheet("font-size:16px;font-weight:700;")
        side.addWidget(title)
        self.match_combo = QComboBox()
        self.match_combo.addItem("全部比赛", "ALL")
        match_ids = sorted(
            {str(row.get("Match ID")) for row in self.items},
            key=lambda value: int(value) if value.isdigit() else 0,
            reverse=True,
        )
        for match_id in match_ids:
            meta = self.match_meta.get(match_id, {})
            result = meta.get("result", "")
            marker = "✅" if result in {"胜", "WIN"} else "❌"
            self.match_combo.addItem(
                f"{marker} {match_id} · vs {meta.get('opponent', '未知对手')} "
                f"· {meta.get('camp', '未知阵营')}",
                match_id,
            )
        side.addWidget(QLabel("选择比赛"))
        side.addWidget(self.match_combo)
        self.mine_check = QCheckBox("我方")
        self.mine_check.setChecked(True)
        self.opponent_check = QCheckBox("对手")
        self.opponent_check.setChecked(True)
        self.obs_check = QCheckBox("假眼")
        self.obs_check.setChecked(True)
        self.sen_check = QCheckBox("真眼")
        self.sen_check.setChecked(True)
        self.status_combo = QComboBox()
        self.status_combo.addItems(
            ("全部状态", "被反掉", "自然消失", "存活结束/未知")
        )
        self.view_combo = QComboBox()
        self.view_combo.addItems(("点位图", "热力图"))
        filters = QGridLayout()
        filters.setHorizontalSpacing(12)
        filters.addWidget(self.mine_check, 0, 0)
        filters.addWidget(self.opponent_check, 0, 1)
        filters.addWidget(self.obs_check, 1, 0)
        filters.addWidget(self.sen_check, 1, 1)
        side.addLayout(filters)
        selectors = QHBoxLayout()
        selectors.addWidget(self.status_combo, 1)
        selectors.addWidget(self.view_combo, 1)
        side.addLayout(selectors)
        self.match_badge = QLabel("全部比赛 · 阵营与胜负按场次显示")
        self.match_badge.setWordWrap(True)
        self.match_badge.setStyleSheet(
            "padding:7px 11px; border-radius:6px; background:#eaf1f7;"
            "color:#26445d; font-weight:600;"
        )
        side.addWidget(self.match_badge)
        side.addWidget(QLabel("时间轴"))
        self.time_slider = QSlider(Qt.Horizontal)
        self.time_slider.setMinimum(0)
        self.time_label = QLabel("0:00")
        timeline = QHBoxLayout()
        timeline.addWidget(self.time_slider, 1)
        timeline.addWidget(self.time_label)
        side.addLayout(timeline)
        self.stats_label = QLabel()
        self.stats_label.setWordWrap(True)
        side.addWidget(self.stats_label)
        side.addStretch()
        legend = QLabel("蓝色：我方　红色外圈：对手")
        legend.setStyleSheet("color:#57606a;")
        side.addWidget(legend)
        layout.addWidget(sidebar)

        map_area = QVBoxLayout()
        self.map_label = QLabel()
        self.map_label.setAlignment(Qt.AlignCenter)
        self.map_label.setMinimumSize(300, 300)
        self.map_label.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Expanding
        )
        map_area.addWidget(self.map_label, 1)
        layout.addLayout(map_area, 1)
        self.match_combo.currentIndexChanged.connect(self._sync_time)
        for widget in (
            self.mine_check,
            self.opponent_check,
            self.obs_check,
            self.sen_check,
        ):
            widget.toggled.connect(self._schedule_render)
        self.status_combo.currentIndexChanged.connect(self._schedule_render)
        self.view_combo.currentIndexChanged.connect(self._schedule_render)
        self.time_slider.valueChanged.connect(self._schedule_render)

    @staticmethod
    def _format_time(seconds: int) -> str:
        return f"{seconds // 60}:{seconds % 60:02d}"

    def _selected_match(self) -> str:
        return str(self.match_combo.currentData() or "ALL")

    def _sync_time(self) -> None:
        match_id = self._selected_match()
        if match_id == "ALL":
            self.match_badge.setText("全部比赛 · 同时显示天辉与夜魇、胜局与败局")
            self.match_badge.setStyleSheet(
                "padding:7px 11px; border-radius:6px; background:#eaf1f7;"
                "color:#26445d; font-weight:600;"
            )
        else:
            meta = self.match_meta.get(match_id, {})
            result = meta.get("result", "未知")
            color = "#e9f7ef" if result in {"胜", "WIN"} else "#fdecec"
            text_color = "#19723d" if result in {"胜", "WIN"} else "#a73535"
            self.match_badge.setText(
                f"本场我方阵营：{meta.get('camp', '未知')}　"
                f"比赛结果：{result}　对手：{meta.get('opponent', '未知')}"
            )
            self.match_badge.setStyleSheet(
                f"padding:7px 11px; border-radius:6px; background:{color};"
                f"color:{text_color}; font-weight:700;"
            )
        source = [
            row
            for row in self.items
            if match_id == "ALL" or str(row.get("Match ID")) == match_id
        ]
        maximum = max(
            [1800]
            + [
                int(row.get("消失时间(秒)") or 0)
                or int(row.get("时间(秒)") or 0) + 420
                for row in source
            ]
        )
        self.time_slider.setMaximum(maximum)
        self.time_slider.setValue(maximum)
        self._schedule_render()

    def _filtered(self) -> list[Dict[str, Any]]:
        match_id = self._selected_match()
        current_time = self.time_slider.value()
        status = self.status_combo.currentText()
        result = []
        for row in self.items:
            if match_id != "ALL" and str(row.get("Match ID")) != match_id:
                continue
            if row["_side"] == "我方" and not self.mine_check.isChecked():
                continue
            if row["_side"] == "对手" and not self.opponent_check.isChecked():
                continue
            ward_type = str(row.get("类型") or "")
            if ward_type in {"假眼", "obs"} and not self.obs_check.isChecked():
                continue
            if ward_type in {"真眼", "sen"} and not self.sen_check.isChecked():
                continue
            if int(row.get("时间(秒)") or 0) > current_time:
                continue
            if status != "全部状态" and str(row.get("消失类型")) != status:
                continue
            result.append(row)
        return result

    def _position(self, x: Any, y: Any, size: int) -> tuple[int, int]:
        minimum, maximum = (64, 192) if self._coordinate_offset else (0, 127)
        margin = 0.06
        px = (float(x) - minimum) / (maximum - minimum)
        py = 1 - (float(y) - minimum) / (maximum - minimum)
        px = margin + px * (1 - 2 * margin)
        py = margin + py * (1 - 2 * margin)
        return int(px * size), int(py * size)

    def _schedule_render(self) -> None:
        self.time_label.setText(self._format_time(self.time_slider.value()))
        self._render_timer.start()

    def _target_size(self) -> int:
        available = min(self.map_label.width(), self.map_label.height())
        return max(220, min(720, available if available > 100 else 420))

    def _cache_key(self, size: int) -> tuple[Any, ...]:
        return (
            size,
            self._selected_match(),
            self.time_slider.value(),
            self.mine_check.isChecked(),
            self.opponent_check.isChecked(),
            self.obs_check.isChecked(),
            self.sen_check.isChecked(),
            self.status_combo.currentText(),
            self.view_combo.currentText(),
        )

    def _render_now(self) -> None:
        if not self.isVisible():
            return
        size = self._target_size()
        rows = self._filtered()
        key = self._cache_key(size)
        cached = self._render_cache.get(key)
        if cached is not None:
            self.map_label.setPixmap(cached)
            self._update_stats(rows)
            return
        if size not in self._base_cache:
            self._base_cache[size] = self.map_image.resize(
                (size, size), Image.Resampling.LANCZOS
            )
        base = self._base_cache[size].copy()
        if self.view_combo.currentText() == "热力图":
            heat = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            draw = ImageDraw.Draw(heat)
            grouped: Dict[tuple[int, int, str], int] = {}
            for row in rows:
                x, y = self._position(row.get("x"), row.get("y"), size)
                group_key = (x // 8, y // 8, row["_side"])
                grouped[group_key] = grouped.get(group_key, 0) + 1
            for (grid_x, grid_y, side), count in grouped.items():
                x, y = grid_x * 8 + 4, grid_y * 8 + 4
                radius = min(34, 16 + count)
                color = (
                    (255, 70, 40, 120)
                    if side == "对手"
                    else (20, 170, 255, 120)
                )
                draw.ellipse(
                    (x - radius, y - radius, x + radius, y + radius),
                    fill=color,
                )
            heat = heat.filter(ImageFilter.GaussianBlur(18))
            base = Image.alpha_composite(base, heat)
        else:
            overlay = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            draw = ImageDraw.Draw(overlay)
            visible_rows = rows
            if len(visible_rows) > 2500:
                step = max(1, len(visible_rows) // 2500)
                visible_rows = visible_rows[::step][:2500]
            for row in visible_rows:
                x, y = self._position(row.get("x"), row.get("y"), size)
                ward_type = str(row.get("类型") or "")
                icon = (
                    self.obs_icon_small
                    if ward_type in {"假眼", "obs"}
                    else self.sen_icon_small
                )
                if row["_side"] == "对手":
                    draw.ellipse((x - 14, y - 14, x + 14, y + 14), outline="#FF4D4D", width=3)
                overlay.alpha_composite(icon, (x - icon.width // 2, y - icon.height // 2))
            base = Image.alpha_composite(base, overlay)
        rgba = base.convert("RGBA")
        raw = rgba.tobytes("raw", "RGBA")
        image = QImage(
            raw,
            rgba.width,
            rgba.height,
            rgba.width * 4,
            QImage.Format_RGBA8888,
        )
        pixmap = QPixmap.fromImage(image.copy())
        if len(self._render_cache) > 24:
            self._render_cache.clear()
        self._render_cache[key] = pixmap
        self.map_label.setPixmap(pixmap)
        self._update_stats(rows)

    def _update_stats(self, rows: list[Dict[str, Any]]) -> None:
        obs_count = sum(str(row.get("类型")) in {"假眼", "obs"} for row in rows)
        dewarded = sum(row.get("消失类型") == "被反掉" for row in rows)
        self.time_label.setText(self._format_time(self.time_slider.value()))
        self.stats_label.setText(
            f"当前点位：{len(rows)}　假眼：{obs_count}　"
            f"真眼：{len(rows) - obs_count}　被反掉：{dewarded}"
        )

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        self._schedule_render()

    def showEvent(self, event: Any) -> None:
        super().showEvent(event)
        self._schedule_render()


class DotaAnalyticsWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Dota 2 数据分析客户端")
        self.resize(1440, 900)
        self.setMinimumSize(1120, 720)
        self.opendota = OpenDotaService()
        self.thread_pool = QThreadPool.globalInstance()
        self.thread_pool.setMaxThreadCount(3)
        self._active_workers: set[Worker] = set()
        self._task_running = False
        self.current_dataset: Dict[str, Any] = {}
        self.ward_preview_path: Path | None = None
        self.team_rows: list[Dict[str, Any]] = []
        self.hero_meta_rows: list[Dict[str, Any]] = []
        self.hero_meta_context = "版本英雄数据"
        if APP_ICON.exists():
            self.setWindowIcon(QIcon(str(APP_ICON)))
        self._build_ui()
        QTimer.singleShot(350, self._auto_load_patch_options)

    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("appRoot")
        layout = QVBoxLayout(root)
        layout.setContentsMargins(24, 20, 24, 22)
        layout.setSpacing(14)

        topbar = QFrame()
        topbar.setObjectName("topbar")
        header = QHBoxLayout(topbar)
        header.setContentsMargins(20, 15, 20, 15)
        header.setSpacing(14)
        brand = QVBoxLayout()
        brand.setSpacing(3)
        title = QLabel("DOTA 2")
        title.setObjectName("title")
        subtitle = QLabel("比赛数据与战术分析工作室")
        subtitle.setObjectName("subtitle")
        brand.addWidget(title)
        brand.addWidget(subtitle)
        header.addLayout(brand)
        self.status = QLabel("就绪")
        self.status.setObjectName("status")
        header.addStretch()
        header.addWidget(self.status)
        layout.addWidget(topbar)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()
        layout.addWidget(self.progress)

        self.tabs = QTabWidget()
        self.tabs.setObjectName("mainTabs")
        self.tabs.addTab(self._build_team_tab(), "战队中心")
        self.tabs.addTab(self._build_modules_tab(), "分析工作台")
        self.tabs.addTab(self._build_export_tab(), "导出中心")
        layout.addWidget(self.tabs, 1)
        self.setCentralWidget(root)
        self.setStyleSheet(
            """
            QMainWindow, QWidget#appRoot { background:#f6f8fa; color:#1f2328; }
            QFrame#topbar { background:#ffffff; border:1px solid #d0d7de;
                border-radius:8px; }
            QLabel#title { font-size:25px; font-weight:750; color:#1f2328;
                letter-spacing:1px; }
            QLabel#subtitle { color:#57606a; font-size:12px; }
            QLabel#status { color:#1a7f37; padding:6px 12px; background:#dafbe1;
                border:1px solid #aceebb; border-radius:14px; }
            QTabWidget#mainTabs::pane { border:1px solid #d0d7de; background:#ffffff;
                border-radius:8px; top:-1px; }
            QTabWidget#mainTabs > QTabBar::tab { padding:13px 30px; margin-right:2px;
                color:#57606a; background:transparent; border-bottom:2px solid transparent; }
            QTabWidget#mainTabs > QTabBar::tab:hover { color:#1f2328; background:#f3f4f6; }
            QTabWidget#mainTabs > QTabBar::tab:selected { color:#1f2328; background:#ffffff;
                border-bottom:2px solid #0969da; font-weight:700; }
            QTabWidget::pane { border:1px solid #d8dee4; background:#ffffff;
                border-radius:6px; top:-1px; }
            QTabBar::tab { padding:10px 18px; color:#57606a;
                background:transparent; border-bottom:2px solid transparent; }
            QTabBar::tab:hover { background:#f6f8fa; color:#1f2328; }
            QTabBar::tab:selected { color:#1f2328; background:#ffffff;
                border-bottom:2px solid #0969da; font-weight:600; }
            QPushButton { min-height:35px; padding:0 16px; border:1px solid #d0d7de;
                border-radius:6px; background:#f6f8fa; color:#24292f; font-weight:500; }
            QPushButton:hover { background:#f3f4f6; border-color:#8c959f; }
            QPushButton:pressed { background:#eaeef2; border-color:#6e7781; }
            QPushButton:disabled { color:#8c959f; background:#f6f8fa; }
            QPushButton#primary { color:#ffffff; background:#1f883d;
                border-color:#1a7f37; font-weight:600; }
            QPushButton#primary:hover { background:#1a7f37; border-color:#1a7f37; }
            QPushButton#primary:pressed { background:#116329; border-color:#116329; }
            QLineEdit, QSpinBox, QComboBox { min-height:35px; border:1px solid #d0d7de;
                border-radius:6px; padding:0 10px; background:#ffffff; }
            QLineEdit:focus, QSpinBox:focus, QComboBox:focus {
                border:1px solid #0969da; background:#ffffff;
            }
            QComboBox:hover, QSpinBox:hover, QLineEdit:hover { border-color:#8c959f; }
            QComboBox:on { background:#f6f8fa; border-color:#0969da; }
            QComboBox::drop-down { width:28px; border:0; border-left:1px solid #d8dee4; }
            QComboBox QAbstractItemView { background:#ffffff; border:1px solid #d0d7de;
                selection-background-color:#ddf4ff; selection-color:#0969da;
                outline:0; padding:4px; }
            QTextEdit, QListWidget, QTableView { background:#ffffff;
                border:1px solid #d0d7de; border-radius:6px; }
            QListWidget::item { padding:10px; border-bottom:1px solid #d8dee4; }
            QListWidget::item:hover { background:#f6f8fa; }
            QListWidget::item:selected { background:#ddf4ff; color:#0969da; }
            QTableView { gridline-color:#d8dee4; alternate-background-color:#f6f8fa;
                selection-background-color:#ddf4ff; selection-color:#1f2328; }
            QHeaderView::section { background:#f6f8fa; color:#57606a; padding:9px;
                border:0; border-right:1px solid #d8dee4;
                border-bottom:1px solid #d0d7de; font-weight:600; }
            QCheckBox { spacing:7px; color:#24292f; }
            QLabel#sectionTitle { font-size:18px; font-weight:700; color:#1f2328; }
            QFrame#metricCard { background:#ffffff; border:1px solid #d0d7de;
                border-radius:6px; }
            QFrame#metricCard:hover { border-color:#8c959f; background:#f6f8fa; }
            QLabel#metricValue { font-size:23px; font-weight:700; color:#1f2328; }
            QLabel#metricLabel { color:#57606a; }
            QProgressBar { min-height:4px; max-height:4px; border:0; background:#d8dee4; }
            QProgressBar::chunk { background:#0969da; }
            """
        )

    def _primary_button(self, text: str, callback: Callable[[], None]) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName("primary")
        button.clicked.connect(callback)
        return button

    def _build_team_tab(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        controls = QHBoxLayout()
        controls.addWidget(QLabel("战队名称或 ID"))
        self.team_query = QLineEdit()
        self.team_query.setPlaceholderText("例如：Team Spirit 或 8255888")
        self.team_query.returnPressed.connect(self._search_team)
        controls.addWidget(self.team_query, 1)
        controls.addWidget(self._primary_button("查询战队", self._search_team))
        controls.addWidget(QLabel("比赛数量"))
        self.match_limit = QSpinBox()
        self.match_limit.setRange(5, 100)
        self.match_limit.setSingleStep(5)
        self.match_limit.setValue(20)
        controls.addWidget(self.match_limit)
        outer.addLayout(controls)

        splitter = QSplitter(Qt.Horizontal)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.addWidget(QLabel("查询结果"))
        self.team_results = QListWidget()
        self.team_results.itemDoubleClicked.connect(self._load_selected_team)
        left_layout.addWidget(self.team_results, 1)
        load_button = QPushButton("载入战队概况")
        load_button.clicked.connect(self._load_selected_team)
        left_layout.addWidget(load_button)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        overview_title = QLabel("战队概况")
        overview_title.setObjectName("sectionTitle")
        right_layout.addWidget(overview_title)
        right_layout.addWidget(self._build_team_overview(), 1)
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setSizes([340, 760])
        outer.addWidget(splitter, 1)
        return page

    def _metric_card(self, label: str, key: str) -> QFrame:
        card = QFrame()
        card.setObjectName("metricCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 10, 14, 10)
        value = QLabel("—")
        value.setObjectName("metricValue")
        caption = QLabel(label)
        caption.setObjectName("metricLabel")
        layout.addWidget(value)
        layout.addWidget(caption)
        self.team_metric_values[key] = value
        return card

    def _build_team_overview(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self.team_name_label = QLabel("请选择并载入一个战队")
        self.team_name_label.setStyleSheet(
            "font-size:22px;font-weight:700;color:#1f2328;"
        )
        self.team_meta_label = QLabel("载入后显示近期表现、当前选手和比赛记录")
        self.team_meta_label.setStyleSheet("color:#57606a;")
        layout.addWidget(self.team_name_label)
        layout.addWidget(self.team_meta_label)
        self.team_metric_values: Dict[str, QLabel] = {}
        metrics = QGridLayout()
        metrics.setSpacing(8)
        for index, (label, key) in enumerate(
            (
                ("分析比赛", "matches"),
                ("胜率", "win_rate"),
                ("胜负", "record"),
                ("平均时长", "duration"),
            )
        ):
            metrics.addWidget(self._metric_card(label, key), 0, index)
        layout.addLayout(metrics)
        self.team_overview_tabs = QTabWidget()
        placeholder = QLabel("暂无战队数据")
        placeholder.setAlignment(Qt.AlignCenter)
        placeholder.setStyleSheet("color:#8c959f;")
        self.team_overview_tabs.addTab(placeholder, "概览")
        layout.addWidget(self.team_overview_tabs, 1)
        return container

    def _build_modules_tab(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(8)
        inputs = QHBoxLayout()
        inputs.setSpacing(10)
        inputs.addWidget(QLabel("战队 ID"))
        self.analysis_team_id = QLineEdit()
        self.analysis_team_id.setPlaceholderText("先在战队中心选择，或直接输入 Team ID")
        inputs.addWidget(self.analysis_team_id, 1)
        inputs.addWidget(QLabel("场数"))
        self.analysis_limit = QSpinBox()
        self.analysis_limit.setRange(5, 100)
        self.analysis_limit.setSingleStep(5)
        self.analysis_limit.setValue(20)
        inputs.addWidget(self.analysis_limit)
        inputs.addWidget(QLabel("版本"))
        self.analysis_patch = QComboBox()
        self.analysis_patch.setEditable(True)
        self.analysis_patch.addItem("全部版本", "all")
        self.analysis_patch.addItem("最近版本", "latest")
        self.analysis_patch.setToolTip("可选择全部版本、最近版本，也可以手动输入 OpenDota Patch ID。")
        inputs.addWidget(self.analysis_patch)
        self.module_checks: Dict[str, QCheckBox] = {}
        for key, label in (
            ("bp", "BP"),
            ("players", "选手档案"),
            ("cooccurrence", "阵容共现"),
            ("wards", "眼位分析"),
        ):
            checkbox = QCheckBox(label)
            checkbox.setChecked(True)
            self.module_checks[key] = checkbox
            inputs.addWidget(checkbox)
        inputs.addWidget(self._primary_button("开始分析", self._run_modules))
        outer.addLayout(inputs)

        note = QLabel(
            "公开比赛数据 · 训练赛与个人单排不纳入 · 缺失字段会在数据质量中标注"
        )
        note.setWordWrap(True)
        note.setStyleSheet("color:#57606a; padding:2px 0 4px 0; font-size:11px;")
        outer.addWidget(note)

        hero_box = QFrame()
        hero_box.setObjectName("metricCard")
        hero_layout = QVBoxLayout(hero_box)
        hero_layout.setContentsMargins(16, 14, 16, 14)
        hero_layout.setSpacing(12)
        hero_header = QHBoxLayout()
        hero_title = QLabel("版本英雄数据")
        hero_title.setObjectName("sectionTitle")
        hero_subtitle = QLabel("按小版本拆分英雄场次、胜场与胜率；职业比赛和玩家公开对局分开统计")
        hero_subtitle.setStyleSheet("color:#57606a;")
        hero_title_group = QVBoxLayout()
        hero_title_group.setSpacing(2)
        hero_title_group.addWidget(hero_title)
        hero_title_group.addWidget(hero_subtitle)
        hero_header.addLayout(hero_title_group, 1)
        refresh_button = QPushButton("刷新版本")
        refresh_button.clicked.connect(self._load_patch_options)
        hero_header.addWidget(refresh_button)
        hero_layout.addLayout(hero_header)

        hero_controls = QGridLayout()
        hero_controls.setHorizontalSpacing(12)
        hero_controls.setVerticalSpacing(8)
        hero_controls.addWidget(QLabel("数据口径"), 0, 0)
        self.hero_meta_scope = QComboBox()
        self.hero_meta_scope.addItem("职业比赛 · 小版本胜率", "pro_patch")
        self.hero_meta_scope.addItem("玩家公开对局 · 小版本胜率", "public_patch")
        self.hero_meta_scope.addItem("职业 Meta 汇总（/heroStats）", "pro_meta")
        self.hero_meta_scope.setToolTip(
            "职业比赛和玩家公开对局是两个口径；默认查询职业比赛的小版本英雄胜率。"
        )
        self.hero_meta_scope.currentIndexChanged.connect(self._sync_hero_meta_controls)
        hero_controls.addWidget(self.hero_meta_scope, 0, 1, 1, 3)
        hero_controls.addWidget(QLabel("小版本"), 0, 4)
        self.hero_meta_patch = QComboBox()
        self.hero_meta_patch.setEditable(True)
        self.hero_meta_patch.addItem("最近版本", "latest")
        self.hero_meta_patch.addItem("全部职业 Meta", "meta")
        self.hero_meta_patch.setToolTip("小版本胜率使用 match_patch 聚合；职业 Meta 汇总不按版本拆分。")
        hero_controls.addWidget(self.hero_meta_patch, 0, 5, 1, 2)
        hero_controls.addWidget(QLabel("最低场次"), 1, 0)
        self.hero_meta_min_pick = QSpinBox()
        self.hero_meta_min_pick.setRange(0, 10000)
        self.hero_meta_min_pick.setSingleStep(5)
        self.hero_meta_min_pick.setValue(0)
        hero_controls.addWidget(self.hero_meta_min_pick, 1, 1)
        hero_controls.addWidget(QLabel("排序"), 1, 2)
        self.hero_meta_sort = QComboBox()
        self.hero_meta_sort.addItems(("场次", "胜率(%)", "BP总次数", "选取次数", "禁用次数"))
        hero_controls.addWidget(self.hero_meta_sort, 1, 3)
        hero_controls.setColumnStretch(1, 1)
        hero_controls.setColumnStretch(3, 1)
        hero_controls.setColumnStretch(5, 1)
        hero_layout.addLayout(hero_controls)
        hero_actions = QHBoxLayout()
        hero_actions.addStretch()
        self.export_hero_meta_button = QPushButton("导出当前结果")
        self.export_hero_meta_button.setEnabled(False)
        self.export_hero_meta_button.clicked.connect(self._export_hero_meta)
        hero_actions.addWidget(self.export_hero_meta_button)
        hero_actions.addWidget(
            self._primary_button("查询英雄数据", self._run_hero_meta)
        )
        hero_layout.addLayout(hero_actions)
        hero_note = QLabel(
            "说明：玩家公开对局指普通玩家公开比赛；职业比赛指赛事/联赛职业比赛。"
            "小版本胜率按 OpenDota match_patch 聚合，/heroStats 仅作为不分小版本的职业 Meta 汇总保留。"
        )
        hero_note.setWordWrap(True)
        hero_note.setStyleSheet("color:#57606a; font-size:11px;")
        hero_layout.addWidget(hero_note)
        outer.addWidget(hero_box)
        self._sync_hero_meta_controls()

        self.module_result_tabs = QTabWidget()
        self.module_result_tabs.addTab(
            QLabel("完成专项分析后，数据表和眼位地图会显示在这里。"), "结果"
        )
        outer.addWidget(self.module_result_tabs, 1)
        return page

    def _build_export_tab(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        title = QLabel("按原脚本分别导出")
        title.setObjectName("sectionTitle")
        outer.addWidget(title)
        description = QLabel(
            "每个分析脚本生成一个独立 Excel，保留该脚本原有的 Sheet 结构、"
            "标题配色、筛选、冻结窗格和图表。原始抓取数据另存为单独工作簿。"
        )
        description.setWordWrap(True)
        description.setStyleSheet("color:#607086; padding:0 0 8px 0;")
        outer.addWidget(description)

        output_note = QLabel(
            "无需选择路径。文件会自动保存到桌面上的“战队名称”文件夹。"
        )
        output_note.setStyleSheet(
            "padding:12px; color:#0969da; background:#ddf4ff;"
            "border:1px solid #54aeff; border-radius:6px;"
        )
        outer.addWidget(output_note)

        module_row = QHBoxLayout()
        self.export_checks: Dict[str, QCheckBox] = {}
        for key, label in (
            ("bp", "BP 分析"),
            ("players", "选手档案"),
            ("cooccurrence", "阵容共现"),
            ("wards", "眼位分析"),
        ):
            checkbox = QCheckBox(label)
            checkbox.setChecked(True)
            self.export_checks[key] = checkbox
            module_row.addWidget(checkbox)
        module_row.addStretch()
        outer.addLayout(module_row)

        actions = QHBoxLayout()
        actions.addWidget(
            self._primary_button("导出选中分析表格", self._export_module_excels)
        )
        raw_button = QPushButton("导出原始数据 Excel")
        raw_button.clicked.connect(self._export_raw_excel)
        actions.addWidget(raw_button)
        self.export_ward_html_button = QPushButton("导出眼位可视化 HTML")
        self.export_ward_html_button.clicked.connect(self._export_ward_html)
        self.export_ward_html_button.setEnabled(False)
        actions.addWidget(self.export_ward_html_button)
        actions.addStretch()
        outer.addLayout(actions)

        self.dataset_label = QLabel("尚未载入数据")
        self.dataset_label.setStyleSheet(
            "padding:16px; color:#52627a; background:#f5f8fb;"
            "border:1px solid #dce5ef; border-radius:8px;"
        )
        outer.addWidget(self.dataset_label)
        outer.addStretch()
        return page

    def _run_task(
        self, label: str, func: Callable[[], Any], callback: Callable[[Any], None]
    ) -> None:
        if self._task_running:
            QMessageBox.information(
                self,
                "任务进行中",
                "当前任务尚未完成。为保证稳定性和响应速度，请等待本次任务结束。",
            )
            return
        self._task_running = True
        self.status.setText(label)
        self.progress.show()
        worker = Worker(func)
        worker.setAutoDelete(False)
        self._active_workers.add(worker)
        worker.signals.finished.connect(
            lambda result, current=worker: self._task_finished(
                current, callback, result
            )
        )
        worker.signals.failed.connect(
            lambda error, current=worker: self._task_failed(current, error)
        )
        self.thread_pool.start(worker)

    def _task_finished(
        self,
        worker: Worker,
        callback: Callable[[Any], None],
        result: Any,
    ) -> None:
        self.progress.hide()
        self.status.setText("完成")
        try:
            callback(result)
        finally:
            self._active_workers.discard(worker)
            self._task_running = False

    def _task_failed(self, worker: Worker, error: str) -> None:
        self.progress.hide()
        self.status.setText("失败")
        self._active_workers.discard(worker)
        self._task_running = False
        logging.error("操作失败：%s", error)
        QMessageBox.critical(self, "操作失败", error)

    def _search_team(self) -> None:
        query = self.team_query.text().strip()
        if not query:
            return
        if query.isdigit():
            self._show_team_results(
                [{"team_id": int(query), "name": f"Team {query}", "tag": "-"}]
            )
            return
        self._run_task(
            "正在查询战队…",
            lambda: self.opendota.search_teams(query),
            self._show_team_results,
        )

    def _show_team_results(self, rows: Any) -> None:
        self.team_rows = list(rows)
        self.team_results.clear()
        for row in self.team_rows:
            self.team_results.addItem(
                f"{row.get('name') or '未知'} [{row.get('tag') or '-'}] · "
                f"ID {row.get('team_id')}"
            )
        if self.team_rows:
            self.team_results.setCurrentRow(0)

    def _selected_team_id(self) -> int | None:
        row = self.team_results.currentRow()
        if row < 0 or row >= len(self.team_rows):
            return None
        return int(self.team_rows[row]["team_id"])

    def _load_selected_team(self, *_args: Any) -> None:
        team_id = self._selected_team_id()
        if team_id is None:
            return
        self.analysis_team_id.setText(str(team_id))
        self.analysis_limit.setValue(self.match_limit.value())
        self._run_task(
            "正在载入战队概况…",
            lambda: self.opendota.summarize_team(team_id, self.match_limit.value()),
            self._show_team_dataset,
        )

    def _show_team_dataset(self, dataset: Dict[str, Any]) -> None:
        self._set_current_dataset(dataset)
        team = dataset.get("team", {})
        stats = dataset.get("statistics", {})
        players = dataset.get("active_players", [])
        matches = dataset.get("matches", [])
        team_name = team.get("name") or "未知战队"
        team_id = team.get("team_id") or ""
        tag = team.get("tag") or "无简称"
        self.team_name_label.setText(team_name)
        self.team_meta_label.setText(f"{tag}　·　Team ID {team_id}")
        self.team_metric_values["matches"].setText(
            f"{stats.get('matches', stats.get('matches_analyzed', 0))} 场"
        )
        self.team_metric_values["win_rate"].setText(
            f"{stats.get('win_rate', 0)}%"
        )
        self.team_metric_values["record"].setText(
            f"{stats.get('wins', 0)} - {stats.get('losses', 0)}"
        )
        self.team_metric_values["duration"].setText(
            f"{stats.get('average_duration_minutes', 0)} 分"
        )
        while self.team_overview_tabs.count():
            page = self.team_overview_tabs.widget(0)
            self.team_overview_tabs.removeTab(0)
            if page is not None:
                page.deleteLater()
        player_rows = [
            {
                "选手": row.get("name") or "未知",
                "比赛数": row.get("games_played") or 0,
                "胜场": row.get("wins") or 0,
                "胜率(%)": round(
                    (row.get("wins") or 0)
                    / max(1, row.get("games_played") or 0)
                    * 100,
                    1,
                ),
            }
            for row in players
        ]
        match_rows = [
            {
                "比赛 ID": row.get("match_id"),
                "对手": row.get("opponent") or "未知对手",
                "结果": row.get("result"),
                "时长(分)": row.get("duration_minutes"),
                "赛事": row.get("league") or "—",
            }
            for row in matches
        ]
        self.team_overview_tabs.addTab(
            self._make_data_table(player_rows), f"当前选手 {len(player_rows)}"
        )
        self.team_overview_tabs.addTab(
            self._make_data_table(match_rows), f"最近比赛 {len(match_rows)}"
        )

    def _load_patch_options(self) -> None:
        self._run_task(
            "正在加载版本列表…",
            self.opendota.get_patches,
            self._show_patch_options,
        )

    def _auto_load_patch_options(self) -> None:
        worker = Worker(self.opendota.get_patches)
        worker.setAutoDelete(False)
        self._active_workers.add(worker)
        worker.signals.finished.connect(
            lambda result, current=worker: self._auto_patch_options_finished(
                current, result
            )
        )
        worker.signals.failed.connect(
            lambda _error, current=worker: self._active_workers.discard(current)
        )
        self.thread_pool.start(worker)

    def _auto_patch_options_finished(self, worker: Worker, patches: Any) -> None:
        self._active_workers.discard(worker)
        self._show_patch_options(list(patches or []))

    def _show_patch_options(self, patches: list[Dict[str, Any]]) -> None:
        current_text = self.analysis_patch.currentText().strip()
        self.analysis_patch.blockSignals(True)
        self.analysis_patch.clear()
        self.analysis_patch.addItem("全部版本", "all")
        self.analysis_patch.addItem("最近版本", "latest")
        for patch in patches:
            patch_id = patch.get("id")
            name = patch.get("name") or f"Patch {patch_id}"
            self.analysis_patch.addItem(f"{name} · ID {patch_id}", str(patch_id))
        index = self.analysis_patch.findText(current_text)
        if index >= 0:
            self.analysis_patch.setCurrentIndex(index)
        self.analysis_patch.blockSignals(False)
        if hasattr(self, "hero_meta_patch"):
            hero_current = self.hero_meta_patch.currentText().strip()
            self.hero_meta_patch.blockSignals(True)
            self.hero_meta_patch.clear()
            self.hero_meta_patch.addItem("最近版本", "latest")
            self.hero_meta_patch.addItem("全部职业 Meta", "meta")
            for patch in patches:
                patch_id = patch.get("id")
                name = patch.get("name") or f"Patch {patch_id}"
                self.hero_meta_patch.addItem(f"{name} · ID {patch_id}", str(patch_id))
            hero_index = self.hero_meta_patch.findText(hero_current)
            if hero_index >= 0:
                self.hero_meta_patch.setCurrentIndex(hero_index)
            self.hero_meta_patch.blockSignals(False)

    def _selected_patch_filter(self) -> str:
        data = self.analysis_patch.currentData()
        text = self.analysis_patch.currentText().strip()
        if data in {"all", "latest"}:
            return str(data)
        if data:
            return str(data)
        return text or "all"

    def _selected_hero_patch_filter(self) -> str:
        data = self.hero_meta_patch.currentData()
        text = self.hero_meta_patch.currentText().strip()
        if data in {"latest", "meta"}:
            return str(data)
        if data:
            return str(data)
        return text or "latest"

    def _sync_hero_meta_controls(self) -> None:
        if not hasattr(self, "hero_meta_scope"):
            return
        scope = str(self.hero_meta_scope.currentData() or "pro_patch")
        current_sort = self.hero_meta_sort.currentText()
        self.hero_meta_sort.blockSignals(True)
        self.hero_meta_sort.clear()
        if scope == "pro_meta":
            self.hero_meta_sort.addItems(("BP总次数", "选取次数", "禁用次数", "胜率(%)"))
            self.hero_meta_patch.setCurrentIndex(
                max(0, self.hero_meta_patch.findData("meta"))
            )
            self.hero_meta_patch.setEnabled(False)
        else:
            self.hero_meta_sort.addItems(("场次", "胜率(%)"))
            self.hero_meta_patch.setEnabled(True)
            if self.hero_meta_patch.currentData() == "meta":
                index = self.hero_meta_patch.findData("latest")
                if index >= 0:
                    self.hero_meta_patch.setCurrentIndex(index)
        sort_index = self.hero_meta_sort.findText(current_sort)
        if sort_index >= 0:
            self.hero_meta_sort.setCurrentIndex(sort_index)
        self.hero_meta_sort.blockSignals(False)

    def _run_hero_meta(self) -> None:
        min_pick = self.hero_meta_min_pick.value()
        sort_key = self.hero_meta_sort.currentText()
        scope = str(self.hero_meta_scope.currentData() or "pro_patch")
        patch_filter = self._selected_hero_patch_filter()
        scope_label = self.hero_meta_scope.currentText()
        patch_label = self.hero_meta_patch.currentText()
        self.hero_meta_context = f"{scope_label} · {patch_label}"

        def load() -> list[Dict[str, Any]]:
            if scope == "pro_meta" or patch_filter == "meta":
                rows = [
                    row
                    for row in self.opendota.get_hero_stats()
                    if int(row.get("选取次数") or 0) >= min_pick
                ]
            else:
                rows = [
                    row
                    for row in self.opendota.get_patch_hero_win_rates(
                        patch_filter,
                        "public" if scope == "public_patch" else "pro",
                    )
                    if int(row.get("场次") or 0) >= min_pick
                ]
            rows.sort(
                key=lambda row: (
                    float(row.get(sort_key) or 0),
                    float(row.get("场次") or row.get("BP总次数") or 0),
                ),
                reverse=True,
            )
            return rows

        self._run_task("正在查询版本英雄数据…", load, self._show_hero_meta)

    def _show_hero_meta(self, rows: list[Dict[str, Any]]) -> None:
        self.hero_meta_rows = list(rows)
        self.export_hero_meta_button.setEnabled(bool(self.hero_meta_rows))
        target = None
        for index in range(self.module_result_tabs.count()):
            if self.module_result_tabs.tabText(index) == "版本英雄数据":
                target = index
                break
        page = self._make_data_table(rows)
        if target is None:
            self.module_result_tabs.addTab(page, "版本英雄数据")
            self.module_result_tabs.setCurrentWidget(page)
        else:
            old = self.module_result_tabs.widget(target)
            self.module_result_tabs.removeTab(target)
            if old is not None:
                old.deleteLater()
            self.module_result_tabs.insertTab(target, page, "版本英雄数据")
            self.module_result_tabs.setCurrentIndex(target)

    def _export_hero_meta(self) -> None:
        if not self.hero_meta_rows:
            QMessageBox.information(self, "缺少数据", "请先查询版本英雄数据。")
            return
        output_dir = global_output_dir("Dota2版本英雄数据")
        safe_context = (
            self.hero_meta_context.replace("/", "_")
            .replace("（", "_")
            .replace("）", "")
            .replace(" · ", "_")
            .replace(" ", "")
        )
        path = output_dir / (
            f"{safe_context}_英雄胜率_{datetime.now():%Y%m%d_%H%M%S}.xlsx"
        )
        context = self.hero_meta_context
        rows = list(self.hero_meta_rows)
        self._run_task(
            "正在导出版本英雄数据…",
            lambda: export_hero_meta_workbook(
                rows,
                path,
                title=f"Dota2 {context}",
                note="职业比赛与玩家公开对局是不同口径；本表按当前界面所选口径导出。",
            ),
            lambda result: QMessageBox.information(
                self, "导出完成", f"文件已保存：\n{result}"
            ),
        )

    def _run_modules(self) -> None:
        team_id_text = self.analysis_team_id.text().strip()
        if not team_id_text.isdigit():
            QMessageBox.information(self, "缺少战队", "请输入有效的数字 Team ID。")
            return
        selected = [
            key for key, checkbox in self.module_checks.items() if checkbox.isChecked()
        ]
        if not selected:
            QMessageBox.information(self, "未选模块", "请至少选择一个分析模块。")
            return
        team_id = int(team_id_text)
        limit = self.analysis_limit.value()
        patch_filter = self._selected_patch_filter()
        self._run_task(
            "正在抓取比赛详情并计算专项数据…",
            lambda: analyze_team_modules(
                self.opendota, team_id, limit, selected, patch_filter
            ),
            self._show_modules_dataset,
        )

    def _show_modules_dataset(self, dataset: Dict[str, Any]) -> None:
        self._set_current_dataset(dataset)
        self._populate_module_results(dataset)
        self.tabs.setCurrentIndex(1)

    def _make_data_table(
        self, rows: list[Dict[str, Any]]
    ) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        if not rows:
            layout.addWidget(QLabel("暂无数据"))
            layout.addStretch()
            return container
        label = QLabel(f"共 {len(rows):,} 条记录 · 点击表头可排序")
        label.setStyleSheet("color:#66758b;")
        layout.addWidget(label)
        model = DataTableModel(rows)
        proxy = NumericSortProxy(container)
        proxy.setSourceModel(model)
        proxy.setDynamicSortFilter(False)
        table = QTableView()
        table.setModel(proxy)
        table.setAlternatingRowColors(True)
        table.setSortingEnabled(False)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        table.setWordWrap(False)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        table.horizontalHeader().setStretchLastSection(True)
        table.horizontalHeader().setSectionsMovable(True)
        table.horizontalHeader().sectionClicked.connect(
            lambda column, view=table, current_proxy=proxy: self._sort_table_on_demand(
                view, current_proxy, column
            )
        )
        table.setColumnWidth(0, 140)
        for index in range(1, model.columnCount()):
            table.setColumnWidth(index, 110)
        container._table_model = model
        container._table_proxy = proxy
        layout.addWidget(table, 1)
        return container

    def _sort_table_on_demand(
        self,
        table: QTableView,
        proxy: NumericSortProxy,
        column: int,
    ) -> None:
        header = table.horizontalHeader()
        current_column = header.sortIndicatorSection()
        current_order = header.sortIndicatorOrder()
        order = (
            Qt.DescendingOrder
            if current_column == column and current_order == Qt.AscendingOrder
            else Qt.AscendingOrder
        )
        header.setSortIndicatorShown(True)
        header.setSortIndicator(column, order)
        proxy.sort(column, order)

    def _add_section_tabs(
        self,
        parent: QTabWidget,
        title: str,
        sections: list[tuple[str, list[Dict[str, Any]]]],
    ) -> None:
        page = QTabWidget()
        for section_title, rows in sections:
            page.addTab(self._make_data_table(list(rows)), section_title)
        parent.addTab(page, title)

    def _populate_module_results(self, dataset: Dict[str, Any]) -> None:
        while self.module_result_tabs.count():
            page = self.module_result_tabs.widget(0)
            self.module_result_tabs.removeTab(0)
            if page is not None:
                page.deleteLater()
        modules = dataset.get("modules", {})
        bp = modules.get("bp")
        if bp:
            self._add_section_tabs(
                self.module_result_tabs,
                "BP 分析",
                [
                    ("英雄汇总", bp.get("summary", [])),
                    ("全部动作", bp.get("actions", [])),
                    ("败局时间轴", bp.get("lost_timeline", [])),
                ],
            )
        players = modules.get("players")
        if players:
            self._add_section_tabs(
                self.module_result_tabs,
                "选手档案",
                [
                    ("近期概览", players.get("overview", [])),
                    ("近期英雄", players.get("recent_heroes", [])),
                    ("公开档案", players.get("profiles", [])),
                    ("生涯英雄", players.get("career_heroes", [])),
                ],
            )
        cooccurrence = modules.get("cooccurrence")
        if cooccurrence:
            self._add_section_tabs(
                self.module_result_tabs,
                "阵容共现",
                [
                    ("逐场阵容", cooccurrence.get("match_lineups", [])),
                    ("共现组合", cooccurrence.get("combinations", [])),
                    ("英雄热度", cooccurrence.get("hero_heat", [])),
                ],
            )
        wards = modules.get("wards")
        if wards:
            ward_page = QTabWidget()
            ward_page.addTab(
                WardMapWidget(
                    wards,
                    dataset.get("team", {}).get("name") or "目标战队",
                ),
                "地图可视化",
            )
            for title, rows in (
                ("概览", wards.get("overview", [])),
                ("我方明细", wards.get("mine_details", [])),
                ("对手明细", wards.get("opponent_details", [])),
                ("热力格点", wards.get("heat_grid", [])),
                ("存活质量", wards.get("quality", [])),
            ):
                ward_page.addTab(self._make_data_table(list(rows)), title)
            self.module_result_tabs.addTab(ward_page, "眼位分析")
            self.export_ward_html_button.setEnabled(True)
        else:
            self.ward_preview_path = None
            self.export_ward_html_button.setEnabled(False)
        if self.module_result_tabs.count() == 0:
            self.module_result_tabs.addTab(QLabel("所选模块没有返回数据。"), "结果")

    def _set_current_dataset(self, dataset: Dict[str, Any]) -> None:
        self.current_dataset = dataset
        if dataset.get("sheet_count") is not None:
            rows = sum(
                sheet.get("row_count_loaded", 0)
                for sheet in dataset.get("sheets", [])
            )
            label = f"{dataset.get('sheet_count', 0)} 个工作表 · {rows} 条记录"
        else:
            stats = dataset.get("statistics", {})
            patch_text = stats.get("patch_filter")
            patch_suffix = f" · {patch_text}" if patch_text else ""
            label = (
                f"{dataset.get('team', {}).get('name', '战队数据')} · "
                f"{stats.get('matches_analyzed', stats.get('matches', 0))} 场"
                f"{patch_suffix}"
            )
        self.dataset_label.setText(label)

    def _export_raw_excel(self) -> None:
        if not self.current_dataset:
            QMessageBox.information(
                self, "缺少数据", "请先载入战队或完成专项分析。"
            )
            return
        output_dir = team_output_dir(self.current_dataset)
        team_name = self.current_dataset.get("team", {}).get("name") or "Dota2"
        path = output_dir / (
            f"{team_name}_原始数据_{datetime.now():%Y%m%d_%H%M%S}.xlsx"
        )
        self._run_task(
            "正在导出原始数据 Excel…",
            lambda: export_raw_data_workbook(self.current_dataset, str(path)),
            lambda result: QMessageBox.information(
                self, "导出完成", f"文件已保存：\n{result}"
            ),
        )

    def _export_module_excels(self) -> None:
        if not self.current_dataset.get("modules"):
            QMessageBox.information(
                self, "缺少分析数据", "请先完成至少一个专项分析模块。"
            )
            return
        selected = [
            key
            for key, checkbox in self.export_checks.items()
            if checkbox.isChecked()
        ]
        if not selected:
            QMessageBox.information(
                self, "未选模块", "请至少选择一个需要导出的分析模块。"
            )
            return
        output_dir = team_output_dir(self.current_dataset)

        def complete(paths: list[Path]) -> None:
            if not paths:
                QMessageBox.information(
                    self, "没有文件", "所选模块当前没有可导出的数据。"
                )
                return
            QMessageBox.information(
                self,
                "导出完成",
                "已生成以下独立工作簿：\n\n"
                + "\n".join(path.name for path in paths)
                + f"\n\n保存目录：\n{output_dir}",
            )

        self._run_task(
            "正在按原脚本样式生成独立工作簿…",
            lambda: export_module_workbooks(
                self.current_dataset, output_dir, selected
            ),
            complete,
        )

    def _export_ward_html(self) -> None:
        wards = self.current_dataset.get("modules", {}).get("wards")
        if not wards:
            QMessageBox.information(self, "缺少眼位数据", "请先完成眼位分析。")
            return
        default_name = (
            f"{self.current_dataset.get('team', {}).get('name', 'Dota2')}"
            f"_眼位可视化_{datetime.now():%Y%m%d_%H%M%S}.html"
        )
        path = team_output_dir(self.current_dataset) / default_name
        try:
            generate_ward_visualization_html(
                wards,
                self.current_dataset.get("team", {}).get("name") or "目标战队",
                path,
            )
            QMessageBox.information(self, "导出完成", f"文件已保存：\n{path}")
        except Exception as exc:
            QMessageBox.critical(self, "导出失败", str(exc))

    def closeEvent(self, event: Any) -> None:
        if self._task_running:
            QMessageBox.information(
                self,
                "任务仍在运行",
                "请等待当前分析或导出任务完成后再关闭客户端。",
            )
            event.ignore()
            return
        self.thread_pool.waitForDone(1500)
        event.accept()


def run() -> None:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("Dota 2 数据分析客户端")
    if APP_ICON.exists():
        app.setWindowIcon(QIcon(str(APP_ICON)))
    window = DotaAnalyticsWindow()
    window.show()
    raise SystemExit(app.exec())
