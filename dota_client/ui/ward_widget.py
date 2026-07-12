from __future__ import annotations

from typing import Any, Dict

import numpy as np
from PIL import Image, ImageDraw
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ..assets import WARD_ASSET_DIR, load_map_image, load_ward_icon


class WardMapWidget(QWidget):
    def __init__(self, module: Dict[str, Any], team_name: str) -> None:
        super().__init__()
        self.items: list[Dict[str, Any]] = []
        for side, key in (("我方", "mine_details"), ("对手", "opponent_details")):
            for row in module.get(key, []):
                if row.get("x") is not None and row.get("y") is not None:
                    self.items.append({**row, "_side": side})
        self.map_image = load_map_image()
        self.obs_icon = load_ward_icon(WARD_ASSET_DIR / "observer.png", "#58A6FF")
        self.sen_icon = load_ward_icon(WARD_ASSET_DIR / "sentry.png", "#F2C94C")
        self._coordinate_offset = bool(self.items) and all(
            40 <= float(row["x"]) <= 220 and 40 <= float(row["y"]) <= 220
            for row in self.items
        )
        self._base_cache: Dict[int, Image.Image] = {}
        self._render_cache: Dict[tuple[Any, ...], QPixmap] = {}
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(90)
        self._timer.timeout.connect(self._render_now)
        self.match_meta: Dict[str, Dict[str, str]] = {}
        for row in self.items:
            match_id = str(row.get("Match ID") or "")
            if match_id and match_id not in self.match_meta:
                self.match_meta[match_id] = {
                    "result": str(row.get("结果") or "未知"),
                    "opponent": str(row.get("对手队伍") or "未知对手"),
                    "camp": str(row.get("我方阵营") or "未知阵营"),
                }
        self._build(team_name)
        if self.match_combo.count() > 1:
            self.match_combo.setCurrentIndex(1)
        self._sync_match()

    def _build(self, team_name: str) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)
        self.setObjectName("wardWorkbench")

        heading = QHBoxLayout()
        title = QLabel(f"{team_name} · 眼位分析")
        title.setObjectName("wardTitle")
        subtitle = QLabel("逐场视野分布与热力密度")
        subtitle.setObjectName("wardSubtitle")
        heading.addWidget(title)
        heading.addWidget(subtitle)
        heading.addStretch()
        self.map_context = QLabel("最近一场")
        self.map_context.setObjectName("wardContext")
        heading.addWidget(self.map_context)
        self.focus_button = QPushButton("专注地图")
        self.focus_button.setObjectName("wardFocus")
        self.focus_button.setCheckable(True)
        heading.addWidget(self.focus_button)
        root.addLayout(heading)

        toolbar = QFrame()
        self.toolbar = toolbar
        toolbar.setObjectName("wardToolbar")
        toolbar_layout = QVBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(12, 10, 12, 10)
        toolbar_layout.setSpacing(9)

        first_row = QHBoxLayout()
        first_row.setSpacing(8)
        first_row.addWidget(self._caption("比赛"))
        self.match_combo = self._combo()
        self.match_combo.addItem("全部比赛", "ALL")
        for match_id in sorted(
            {str(row.get("Match ID")) for row in self.items},
            key=lambda value: int(value) if value.isdigit() else 0,
            reverse=True,
        ):
            meta = self.match_meta.get(match_id, {})
            marker = "✓" if meta.get("result") in {"胜", "WIN"} else "×"
            self.match_combo.addItem(
                f"{marker} {match_id} · vs {meta.get('opponent', '未知')} · "
                f"{meta.get('camp', '未知')}",
                match_id,
            )
        first_row.addWidget(self.match_combo, 1)
        self.status_combo = self._combo(
            ("全部状态", "被反掉", "自然消失", "存活结束/未知")
        )
        self.view_combo = self._combo(("点位图", "热力图"))
        self.time_mode_combo = self._combo(("累计放置", "当前存活"))
        first_row.addWidget(self.status_combo)
        first_row.addWidget(self.view_combo)
        first_row.addWidget(self.time_mode_combo)
        self.advanced_button = QPushButton("显示设置")
        self.advanced_button.setObjectName("wardAdvanced")
        self.advanced_button.setCheckable(True)
        first_row.addWidget(self.advanced_button)
        toolbar_layout.addLayout(first_row)

        second_row = QHBoxLayout()
        second_row.setSpacing(12)
        self.mine_check = self._check("我方", True)
        self.opponent_check = self._check("对手", True)
        self.obs_check = self._check("假眼", True)
        self.sen_check = self._check("真眼", True)
        self.early_check = self._check("前期", True)
        self.mid_check = self._check("中期", True)
        self.late_check = self._check("后期", True)
        for widget in (
            self.mine_check,
            self.opponent_check,
            self.obs_check,
            self.sen_check,
        ):
            second_row.addWidget(widget)
        second_row.addSpacing(8)
        for widget in (self.early_check, self.mid_check, self.late_check):
            second_row.addWidget(widget)
        second_row.addSpacing(8)
        second_row.addWidget(self._caption("时间"))
        self.time_slider = QSlider(Qt.Horizontal)
        self.time_slider.setMinimum(0)
        second_row.addWidget(self.time_slider, 1)
        self.time_label = QLabel("0:00")
        self.time_label.setObjectName("wardTime")
        second_row.addWidget(self.time_label)
        toolbar_layout.addLayout(second_row)

        self.advanced_panel = QFrame()
        self.advanced_panel.setObjectName("wardAdvancedPanel")
        advanced = QHBoxLayout(self.advanced_panel)
        advanced.setContentsMargins(10, 7, 10, 7)
        advanced.addWidget(QLabel("图标大小"))
        self.icon_size_slider = QSlider(Qt.Horizontal)
        self.icon_size_slider.setRange(12, 34)
        self.icon_size_slider.setValue(19)
        advanced.addWidget(self.icon_size_slider, 1)
        self.icon_size_label = QLabel("19")
        advanced.addWidget(self.icon_size_label)
        advanced.addSpacing(24)
        advanced.addWidget(QLabel("地图边距"))
        self.margin_slider = QSlider(Qt.Horizontal)
        self.margin_slider.setRange(0, 15)
        self.margin_slider.setValue(6)
        advanced.addWidget(self.margin_slider, 1)
        self.margin_label = QLabel("6%")
        advanced.addWidget(self.margin_label)
        self.advanced_panel.hide()
        toolbar_layout.addWidget(self.advanced_panel)
        root.addWidget(toolbar)

        self.status_widget = QWidget()
        status_row = QHBoxLayout(self.status_widget)
        status_row.setContentsMargins(0, 0, 0, 0)
        self.match_badge = QLabel()
        self.match_badge.setObjectName("wardMatchBadge")
        status_row.addWidget(self.match_badge, 1)
        self.legend = QLabel("● 蓝：低密度　● 黄：中密度　● 红：高密度")
        self.legend.setObjectName("wardLegend")
        status_row.addWidget(self.legend)
        root.addWidget(self.status_widget)

        self.stats_widget = QWidget()
        stats = QHBoxLayout(self.stats_widget)
        stats.setContentsMargins(0, 0, 0, 0)
        stats.setSpacing(8)
        self.stat_values: Dict[str, QLabel] = {}
        for caption, key in (
            ("当前点位", "count"),
            ("假眼", "obs"),
            ("真眼", "sen"),
            ("被反掉", "deward"),
        ):
            stats.addWidget(self._stat_card(caption, key), 1)
        root.addWidget(self.stats_widget)

        map_panel = QFrame()
        map_panel.setObjectName("wardMapPanel")
        map_layout = QVBoxLayout(map_panel)
        map_layout.setContentsMargins(12, 12, 12, 8)
        self.map_label = QLabel()
        self.map_label.setAlignment(Qt.AlignCenter)
        self.map_label.setMinimumSize(80, 80)
        self.map_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        map_layout.addWidget(self.map_label, 1)
        root.addWidget(map_panel, 1)

        self.match_combo.currentIndexChanged.connect(self._sync_match)
        for widget in (
            self.mine_check,
            self.opponent_check,
            self.obs_check,
            self.sen_check,
            self.early_check,
            self.mid_check,
            self.late_check,
        ):
            widget.toggled.connect(self._schedule)
        for combo in (self.status_combo, self.view_combo, self.time_mode_combo):
            combo.currentIndexChanged.connect(self._schedule)
        for slider in (
            self.time_slider,
            self.icon_size_slider,
            self.margin_slider,
        ):
            slider.valueChanged.connect(self._schedule)
        self.advanced_button.toggled.connect(self.advanced_panel.setVisible)
        self.focus_button.toggled.connect(self._toggle_focus)
        self._apply_style()

    def _toggle_focus(self, enabled: bool) -> None:
        self.toolbar.setVisible(not enabled)
        self.status_widget.setVisible(not enabled)
        self.stats_widget.setVisible(not enabled)
        self.focus_button.setText("恢复筛选" if enabled else "专注地图")
        QTimer.singleShot(0, self._schedule)

    @staticmethod
    def _caption(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("wardCaption")
        return label

    @staticmethod
    def _check(text: str, checked: bool) -> QCheckBox:
        widget = QCheckBox(text)
        widget.setChecked(checked)
        return widget

    @staticmethod
    def _combo(items: tuple[str, ...] = ()) -> QComboBox:
        widget = QComboBox()
        widget.setObjectName("wardCombo")
        if items:
            widget.addItems(items)
        return widget

    def _stat_card(self, caption: str, key: str) -> QFrame:
        card = QFrame()
        card.setObjectName("wardStat")
        layout = QHBoxLayout(card)
        layout.setContentsMargins(12, 7, 12, 7)
        label = QLabel(caption)
        label.setObjectName("wardStatLabel")
        value = QLabel("0")
        value.setObjectName("wardStatValue")
        layout.addWidget(label)
        layout.addStretch()
        layout.addWidget(value)
        self.stat_values[key] = value
        return card

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            QWidget#wardWorkbench { background:#ffffff; }
            QFrame#wardToolbar, QFrame#wardMapPanel {
                background:#ffffff; border:1px solid #d0d7de; border-radius:6px;
            }
            QLabel#wardTitle { color:#1f2328; font-size:19px; font-weight:700; }
            QLabel#wardSubtitle, QLabel#wardCaption { color:#57606a; }
            QLabel#wardTime { color:#1f2328; font-weight:700; min-width:42px; }
            QLabel#wardContext { color:#0969da; padding:5px 10px; background:#ddf4ff;
                border:1px solid #b6e3ff; border-radius:12px; }
            QLabel#wardMatchBadge { color:#57606a; padding:7px 10px; background:#f6f8fa;
                border:1px solid #d8dee4; border-radius:6px; }
            QLabel#wardLegend { color:#57606a; padding:7px 10px; }
            QComboBox#wardCombo { color:#1f2328; background:#ffffff;
                border:1px solid #d0d7de; border-radius:6px; padding:0 9px; }
            QComboBox#wardCombo:hover { border-color:#0969da; }
            QWidget#wardWorkbench QCheckBox { color:#24292f; }
            QWidget#wardWorkbench QSlider::groove:horizontal {
                height:5px; border-radius:2px; background:#d8dee4;
            }
            QWidget#wardWorkbench QSlider::sub-page:horizontal {
                background:#0969da; border-radius:2px;
            }
            QWidget#wardWorkbench QSlider::handle:horizontal {
                width:16px; margin:-6px 0; border-radius:8px;
                background:#ffffff; border:2px solid #0969da;
            }
            QFrame#wardStat { background:#f6f8fa; border:1px solid #d8dee4;
                border-radius:6px; }
            QLabel#wardStatValue { color:#1f2328; font-size:18px; font-weight:700; }
            QLabel#wardStatLabel, QLabel#wardMapNote { color:#57606a; font-size:11px; }
            QPushButton#wardAdvanced { min-height:32px; background:#f6f8fa;
                border:1px solid #d0d7de; color:#57606a; }
            QPushButton#wardAdvanced:hover { border-color:#8c959f; }
            QPushButton#wardFocus { min-height:30px; padding:0 12px;
                background:#ffffff; border:1px solid #d0d7de; color:#0969da; }
            QPushButton#wardFocus:hover { background:#ddf4ff; border-color:#54aeff; }
            QPushButton#wardFocus:checked { background:#0969da; color:#ffffff;
                border-color:#0969da; }
            QFrame#wardAdvancedPanel { background:#f6f8fa; border:1px solid #d8dee4;
                border-radius:6px; }
            """
        )

    @staticmethod
    def _format_time(seconds: int) -> str:
        return f"{seconds // 60}:{seconds % 60:02d}"

    def _selected_match(self) -> str:
        return str(self.match_combo.currentData() or "ALL")

    def _sync_match(self) -> None:
        match_id = self._selected_match()
        if match_id == "ALL":
            self.match_badge.setText("全部比赛 · 同时显示天辉与夜魇、胜局与败局")
            self.map_context.setText("全部比赛")
        else:
            meta = self.match_meta.get(match_id, {})
            result = meta.get("result", "未知")
            self.match_badge.setText(
                f"我方阵营：{meta.get('camp', '未知')}　"
                f"比赛结果：{result}　对手：{meta.get('opponent', '未知')}"
            )
            self.map_context.setText(
                f"{meta.get('camp', '未知')} · {result} · vs "
                f"{meta.get('opponent', '未知')}"
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
        self.time_slider.setValue(min(maximum, 1500))
        self._schedule()

    def _filtered(self) -> list[Dict[str, Any]]:
        match_id = self._selected_match()
        current_time = self.time_slider.value()
        status = self.status_combo.currentText()
        alive_only = self.time_mode_combo.currentText() == "当前存活"
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
            placed = int(row.get("时间(秒)") or 0)
            if placed > current_time:
                continue
            expiry = int(row.get("消失时间(秒)") or 0)
            if alive_only and expiry and expiry <= current_time:
                continue
            if status != "全部状态" and str(row.get("消失类型")) != status:
                continue
            minute = placed / 60
            if minute < 15 and not self.early_check.isChecked():
                continue
            if 15 <= minute < 30 and not self.mid_check.isChecked():
                continue
            if minute >= 30 and not self.late_check.isChecked():
                continue
            result.append(row)
        return result

    def _position(self, x: Any, y: Any, size: int) -> tuple[int, int]:
        minimum, maximum = (64, 192) if self._coordinate_offset else (0, 127)
        margin = self.margin_slider.value() / 100
        px = (float(x) - minimum) / (maximum - minimum)
        py = 1 - (float(y) - minimum) / (maximum - minimum)
        return (
            int((margin + px * (1 - 2 * margin)) * size),
            int((margin + py * (1 - 2 * margin)) * size),
        )

    def _schedule(self) -> None:
        self.time_label.setText(self._format_time(self.time_slider.value()))
        self.icon_size_label.setText(str(self.icon_size_slider.value()))
        self.margin_label.setText(f"{self.margin_slider.value()}%")
        self._timer.start()

    def _target_size(self) -> int:
        width = max(1, self.map_label.contentsRect().width() - 4)
        height = max(1, self.map_label.contentsRect().height() - 4)
        return max(64, min(800, width, height))

    def _cache_key(self, size: int) -> tuple[Any, ...]:
        return (
            size,
            self._selected_match(),
            self.time_slider.value(),
            *(widget.isChecked() for widget in (
                self.mine_check, self.opponent_check, self.obs_check,
                self.sen_check, self.early_check, self.mid_check, self.late_check,
            )),
            self.status_combo.currentText(),
            self.view_combo.currentText(),
            self.time_mode_combo.currentText(),
            self.icon_size_slider.value(),
            self.margin_slider.value(),
        )

    @staticmethod
    def _gaussian_kernel(sigma: float) -> np.ndarray:
        radius = max(2, int(sigma * 3))
        values = np.arange(-radius, radius + 1, dtype=np.float32)
        kernel = np.exp(-(values * values) / (2 * sigma * sigma))
        return kernel / kernel.sum()

    def _heat_overlay(
        self, rows: list[Dict[str, Any]], size: int
    ) -> Image.Image:
        density = np.zeros((size, size), dtype=np.float32)
        for row in rows:
            x, y = self._position(row.get("x"), row.get("y"), size)
            if 0 <= x < size and 0 <= y < size:
                density[y, x] += (
                    1.25 if row.get("消失类型") == "被反掉" else 1.0
                )
        sigma = max(8.0, size / 35)
        kernel = self._gaussian_kernel(sigma)
        density = np.apply_along_axis(
            lambda line: np.convolve(line, kernel, mode="same"), 1, density
        )
        density = np.apply_along_axis(
            lambda line: np.convolve(line, kernel, mode="same"), 0, density
        )
        maximum = float(density.max())
        if maximum <= 0:
            return Image.new("RGBA", (size, size), (0, 0, 0, 0))
        t = np.clip(density / maximum, 0, 1)
        red = np.where(
            t < 0.33,
            0,
            np.where(t < 0.66, 255 * (t - 0.33) / 0.33, 255),
        )
        green = np.where(
            t < 0.33,
            100 + 400 * t,
            np.where(t < 0.66, 220, 180 * (1 - (t - 0.66) / 0.34)),
        )
        blue = np.where(t < 0.33, 255, np.where(t < 0.66, 80, 0))
        alpha = 255 * np.power(t, 0.58)
        rgba = np.stack((red, green, blue, alpha), axis=-1)
        return Image.fromarray(np.uint8(np.clip(rgba, 0, 255)), "RGBA")

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
            base = Image.alpha_composite(base, self._heat_overlay(rows, size))
        else:
            overlay = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            draw = ImageDraw.Draw(overlay)
            icon_size = self.icon_size_slider.value()
            obs = self.obs_icon.resize((icon_size, icon_size), Image.Resampling.LANCZOS)
            sen = self.sen_icon.resize((icon_size, icon_size), Image.Resampling.LANCZOS)
            visible = rows
            if len(visible) > 2500:
                visible = visible[:: max(1, len(visible) // 2500)][:2500]
            for row in visible:
                x, y = self._position(row.get("x"), row.get("y"), size)
                icon = obs if str(row.get("类型")) in {"假眼", "obs"} else sen
                radius = icon_size // 2 + 3
                color = "#FF5A52" if row["_side"] == "对手" else "#58A6FF"
                draw.ellipse(
                    (x - radius, y - radius, x + radius, y + radius),
                    outline=color,
                    width=3 if row["_side"] == "对手" else 2,
                )
                overlay.alpha_composite(
                    icon, (x - icon.width // 2, y - icon.height // 2)
                )
            base = Image.alpha_composite(base, overlay)
        rgba = base.convert("RGBA")
        image = QImage(
            rgba.tobytes("raw", "RGBA"),
            rgba.width,
            rgba.height,
            rgba.width * 4,
            QImage.Format_RGBA8888,
        )
        pixmap = QPixmap.fromImage(image.copy())
        if len(self._render_cache) > 28:
            self._render_cache.clear()
        self._render_cache[key] = pixmap
        self.map_label.setPixmap(pixmap)
        self._update_stats(rows)

    def _update_stats(self, rows: list[Dict[str, Any]]) -> None:
        obs_count = sum(str(row.get("类型")) in {"假眼", "obs"} for row in rows)
        self.stat_values["count"].setText(str(len(rows)))
        self.stat_values["obs"].setText(str(obs_count))
        self.stat_values["sen"].setText(str(len(rows) - obs_count))
        self.stat_values["deward"].setText(
            str(sum(row.get("消失类型") == "被反掉" for row in rows))
        )

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        self._timer.stop()
        QTimer.singleShot(0, self._schedule)

    def showEvent(self, event: Any) -> None:
        super().showEvent(event)
        self._schedule()
