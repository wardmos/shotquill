# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Minimal in-process i18n: an English/Chinese string table and ``t(key)``.

The current language is module-global state set from user config at startup and
whenever the user switches in Settings. Default is English. Strings with
placeholders are returned as templates for the caller to ``.format()``.
"""

from __future__ import annotations

DEFAULT_LANGUAGE = "en"
LANGUAGES = ("en", "zh")

# Display names for the language picker (shown in their own script).
LANGUAGE_NAMES = {"en": "English", "zh": "中文"}

_STRINGS: dict[str, dict[str, str]] = {
    # Tray menu
    "menu.region": {"en": "Region Capture", "zh": "区域截图"},
    "menu.fullscreen": {"en": "Full Screen", "zh": "全屏截图"},
    "menu.settings": {"en": "Settings…", "zh": "设置…"},
    "menu.about": {"en": "About Shotquill", "zh": "关于 Shotquill"},
    "menu.permissions": {
        "en": "Open Screen Recording Settings…",
        "zh": "打开屏幕录制权限设置…",
    },
    "menu.quit": {"en": "Quit Shotquill", "zh": "退出 Shotquill"},
    # Notifications / about
    "notify.capture_failed": {"en": "Capture failed: {error}", "zh": "截图失败：{error}"},
    "about.body": {"en": "Screenshot & annotation tool.", "zh": "截图与标注工具。"},
    # Editor window titles
    "title.annotate": {"en": "Shotquill — Annotate", "zh": "Shotquill — 标注"},
    "title.copied": {"en": "Shotquill — Copied to clipboard", "zh": "Shotquill — 已复制到剪贴板"},
    "title.saved": {"en": "Shotquill — Saved {name}", "zh": "Shotquill — 已保存 {name}"},
    "title.ocr_failed": {
        "en": "Shotquill — OCR failed: {error}",
        "zh": "Shotquill — OCR 失败：{error}",
    },
    "title.ocr_copied": {
        "en": "Shotquill — Copied {count} line(s)",
        "zh": "Shotquill — 已复制 {count} 行文字",
    },
    "title.ocr_empty": {"en": "Shotquill — No text found", "zh": "Shotquill — 未识别到文字"},
    # Tools
    "tool.select": {"en": "Select", "zh": "选择"},
    "tool.rect": {"en": "Rectangle", "zh": "矩形"},
    "tool.ellipse": {"en": "Ellipse", "zh": "圆"},
    "tool.arrow": {"en": "Arrow", "zh": "箭头"},
    "tool.line": {"en": "Line", "zh": "直线"},
    "tool.pen": {"en": "Pen", "zh": "画笔"},
    "tool.highlighter": {"en": "Highlighter", "zh": "荧光笔"},
    "tool.mosaic": {"en": "Mosaic", "zh": "马赛克"},
    "tool.text": {"en": "Text", "zh": "文字"},
    # Toolbar controls
    "toolbar.color": {"en": "Color", "zh": "颜色"},
    "toolbar.width": {"en": "Width ", "zh": "粗细 "},
    "toolbar.undo": {"en": "Undo", "zh": "撤销"},
    "toolbar.redo": {"en": "Redo", "zh": "重做"},
    "toolbar.ocr": {"en": "Copy Text", "zh": "取字"},
    "toolbar.ocr_tip": {"en": "Recognize and copy text (OCR)", "zh": "识别图中文字并复制（OCR）"},
    "toolbar.copy": {"en": "Copy", "zh": "复制"},
    "toolbar.save": {"en": "Save", "zh": "保存"},
    "dialog.pick_color": {"en": "Select Color", "zh": "选择颜色"},
    # Settings
    "settings.title": {"en": "Shotquill Settings", "zh": "Shotquill 设置"},
    "settings.save_dir": {"en": "Save Folder", "zh": "保存目录"},
    "settings.format": {"en": "Image Format", "zh": "图片格式"},
    "settings.region": {"en": "Region Capture", "zh": "区域截图"},
    "settings.fullscreen": {"en": "Full Screen", "zh": "全屏截图"},
    "settings.language": {"en": "Language", "zh": "界面语言"},
    "settings.browse": {"en": "Browse…", "zh": "浏览…"},
    "settings.choose_dir": {"en": "Choose Save Folder", "zh": "选择保存目录"},
}

_current = DEFAULT_LANGUAGE


def set_language(language: str) -> None:
    global _current
    _current = language if language in LANGUAGES else DEFAULT_LANGUAGE


def current_language() -> str:
    return _current


def t(key: str) -> str:
    """Translate ``key`` into the current language (falls back to English, then key)."""
    entry = _STRINGS.get(key)
    if entry is None:
        return key
    return entry.get(_current) or entry.get(DEFAULT_LANGUAGE) or key
