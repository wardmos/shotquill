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
    "menu.window": {"en": "Capture Window", "zh": "窗口截图"},
    "menu.settings": {"en": "Settings…", "zh": "设置…"},
    "menu.about": {"en": "About ShotQuill", "zh": "关于 ShotQuill"},
    "menu.permissions": {
        "en": "Open Screen Recording Settings…",
        "zh": "打开屏幕录制权限设置…",
    },
    "menu.input_monitoring": {
        "en": "Open Input Monitoring Settings…",
        "zh": "打开输入监控权限设置…",
    },
    "menu.quit": {"en": "Quit ShotQuill", "zh": "退出 ShotQuill"},
    # Notifications / about
    "notify.capture_failed": {"en": "Capture failed: {error}", "zh": "截图失败：{error}"},
    "notify.no_windows": {
        "en": "No capturable windows found.",
        "zh": "没有找到可截图的窗口。",
    },
    "picker.hint": {
        "en": "Click a window to capture · Esc to cancel",
        "zh": "点击要截图的窗口 · Esc 取消",
    },
    "about.body": {"en": "Screenshot & annotation tool.", "zh": "截图与标注工具。"},
    # Editor window titles
    "title.annotate": {"en": "ShotQuill — Annotate", "zh": "ShotQuill — 标注"},
    "title.copied": {"en": "ShotQuill — Copied to clipboard", "zh": "ShotQuill — 已复制到剪贴板"},
    "title.saved": {"en": "ShotQuill — Saved {name}", "zh": "ShotQuill — 已保存 {name}"},
    "title.ocr_failed": {
        "en": "ShotQuill — OCR failed: {error}",
        "zh": "ShotQuill — OCR 失败：{error}",
    },
    "title.ocr_copied": {
        "en": "ShotQuill — Copied {count} line(s)",
        "zh": "ShotQuill — 已复制 {count} 行文字",
    },
    "title.ocr_empty": {"en": "ShotQuill — No text found", "zh": "ShotQuill — 未识别到文字"},
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
    "toolbar.pin": {"en": "Pin", "zh": "钉屏"},
    "toolbar.pin_tip": {
        "en": "Pin this shot on top of the screen",
        "zh": "把截图钉在屏幕最前",
    },
    "pin.tip": {
        "en": "Drag to move · double-click or Esc to close",
        "zh": "拖动移动 · 双击或 Esc 关闭",
    },
    "toolbar.copy": {"en": "Copy", "zh": "复制"},
    "toolbar.copy_tip": {"en": "Copy to clipboard (Enter)", "zh": "复制到剪贴板（回车）"},
    "toolbar.save": {"en": "Save", "zh": "保存"},
    "toolbar.save_tip": {"en": "Save to file (Space)", "zh": "保存到文件（空格）"},
    "dialog.pick_color": {"en": "Select Color", "zh": "选择颜色"},
    # Settings
    "settings.title": {"en": "ShotQuill Settings", "zh": "ShotQuill 设置"},
    "settings.save_dir": {"en": "Save Folder", "zh": "保存目录"},
    "settings.format": {"en": "Image Format", "zh": "图片格式"},
    "settings.region": {"en": "Region Capture", "zh": "区域截图"},
    "settings.fullscreen": {"en": "Full Screen", "zh": "全屏截图"},
    "settings.window": {"en": "Capture Window", "zh": "窗口截图"},
    "settings.language": {"en": "Language", "zh": "界面语言"},
    "settings.browse": {"en": "Browse…", "zh": "浏览…"},
    "settings.choose_dir": {"en": "Choose Save Folder", "zh": "选择保存目录"},
    "settings.auto_output": {"en": "After capture", "zh": "截图后"},
    "settings.auto_save": {"en": "Auto-save to folder", "zh": "自动保存到目录"},
    "settings.auto_copy": {"en": "Auto-copy to clipboard", "zh": "自动复制到剪贴板"},
    "settings.autostart": {"en": "Launch at login", "zh": "开机自启"},
    "settings.flash": {"en": "Flash on capture", "zh": "截图时闪光"},
    "settings.sound": {"en": "Sound on capture", "zh": "截图时播放声音"},
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
