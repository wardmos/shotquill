# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Minimal in-process i18n: an English/Chinese string table and ``t(key)``.

The current language is module-global state set from user config at startup and
whenever the user switches in Settings. Default is English. Strings with
placeholders are returned as templates for the caller to ``.format()``.
"""

from __future__ import annotations

import sys

DEFAULT_LANGUAGE = "en"
LANGUAGES = ("en", "zh")

# Display names for the language picker (shown in their own script).
LANGUAGE_NAMES = {"en": "English", "zh": "中文"}

_STRINGS: dict[str, dict[str, str]] = {
    # Tray menu
    "menu.smart": {"en": "Capture", "zh": "截图"},
    "menu.fullscreen": {"en": "Full Screen", "zh": "全屏截图"},
    "menu.open_folder": {"en": "Open Save Folder", "zh": "打开保存文件夹"},
    "menu.settings": {"en": "Settings…", "zh": "设置…"},
    "menu.about": {"en": "About ShotQuill", "zh": "关于 ShotQuill"},
    "menu.quit": {"en": "Quit ShotQuill", "zh": "退出 ShotQuill"},
    # Notifications / about
    "notify.capture_failed": {"en": "Capture failed: {error}", "zh": "截图失败：{error}"},
    "notify.capture_blocked": {
        "en": "{app} is on your blocklist — ShotQuill won't capture it.",
        "zh": "{app} 在你的排除名单里——ShotQuill 不会截取它。",
    },
    "notify.blocklist_unreadable": {
        "en": "Your blocklist couldn't be read, so ShotQuill won't capture "
        "(it can't tell what to protect). Fix it in Settings. ({error})",
        "zh": "无法读取你的排除名单，ShotQuill 不会截图"
        "（它无法判断该保护什么）。请在设置里修复。（{error}）",
    },
    "notify.capture_not_allowed": {
        "en": "{app} isn't on your allowlist — ShotQuill only captures allowlisted apps.",
        "zh": "{app} 不在你的白名单里——ShotQuill 只截白名单内的应用。",
    },
    "notify.allowlist_whole_screen": {
        "en": "Your allowlist is on, so only specific apps can be captured — "
        "full-screen and region capture are off. Pick an allowlisted window.",
        "zh": "你的白名单已开启，只能截取指定应用——全屏和区域截图已停用。请选择白名单内的窗口。",
    },
    "notify.allowlist_unreadable": {
        "en": "Your allowlist couldn't be read, so ShotQuill won't capture "
        "(it can't tell what's permitted). Fix it in Settings. ({error})",
        "zh": "无法读取你的白名单，ShotQuill 不会截图"
        "（它无法判断哪些被允许）。请在设置里修复。（{error}）",
    },
    "notify.window_policy_unavailable": {
        "en": "ShotQuill can't inspect windows on this desktop, so it won't capture "
        "while a blocklist or allowlist is active. ({error})",
        "zh": "ShotQuill 无法检查此桌面上的窗口；当排除名单或白名单启用时不会截图。（{error}）",
    },
    "notify.save_failed": {"en": "Save failed: {error}", "zh": "保存失败：{error}"},
    "notify.open_folder_failed": {
        "en": "Couldn't open the save folder: {error}",
        "zh": "无法打开保存文件夹：{error}",
    },
    "notify.capture_need_screen_recording": {
        "en": "Screenshots need Screen Recording permission. "
        "Enable it in System Settings, then restart ShotQuill.",
        "zh": "截图需要屏幕录制权限。请在系统设置中启用后重启 ShotQuill。",
    },
    "notify.hotkeys_need_input_monitoring": {
        "en": "Global hotkeys need Input Monitoring permission. "
        "Enable it in System Settings, then restart ShotQuill.",
        "zh": "全局快捷键需要输入监控权限。请在系统设置中启用后重启 ShotQuill。",
    },
    "notify.hotkeys_unavailable": {
        "en": "Global hotkeys are unavailable: {reason} The tray menu still works.",
        "zh": "全局快捷键不可用：{reason} 你仍然可以使用托盘菜单。",
    },
    "smart.hint": {
        "en": "Click a window · drag for a region · click here for full screen"
        " · arrows/WASD nudge the cursor · Esc cancels",
        "zh": "点击窗口截图 · 拖动框选区域 · 点此截全屏 · 方向键/WASD 微移光标 · Esc 取消",
    },
    # Shown on the full-screen crop-adjust surface (CropAdjustOverlay): how to
    # resize/move the selection, apply it, or back out.
    "smart.adjust_hint": {
        "en": "Drag the edges to adjust · drag inside to move"
        " · click inside or ↵ to apply · Esc cancels",
        "zh": "拖动边缘调整 · 框内拖动移动 · 框内单击或 ↵ 确认 · Esc 取消",
    },
    "editor.adjust_hint": {
        "en": "Drag edges or arrows to adjust crop · ⌥ resizes · ⇧ ×10 · until first annotation",
        "zh": "拖动边缘或方向键微调选区 · ⌥ 调大小 · ⇧ ×10 · 开始标注后固定",
    },
    # Linux/X11 variant: ⌥/⇧ are macOS keycap glyphs that speak Mac convention.
    # The editor picks this variant off-darwin so the hint reads natively.
    "editor.adjust_hint_text": {
        "en": "Drag edges or arrows to adjust crop · Alt resizes · Shift ×10"
        " · until first annotation",
        "zh": "拖动边缘或方向键微调选区 · Alt 调大小 · Shift ×10 · 开始标注后固定",
    },
    # System-tray missing dialog (shown when QSystemTrayIcon.isSystemTrayAvailable
    # is false — GNOME 42+ shipped without legacy tray support is the common case).
    "tray.unavailable_title": {
        "en": "ShotQuill needs a system tray",
        "zh": "ShotQuill 需要系统托盘",
    },
    "tray.unavailable_body_linux": {
        "en": "No system tray was detected. ShotQuill lives in the tray, so it "
        "can't start without one. On GNOME 42+ install the AppIndicator "
        "extension; KDE, XFCE, MATE and Cinnamon ship one by default. "
        "The `squill` CLI and MCP server still work without a tray.",
        "zh": "没有检测到系统托盘。ShotQuill 常驻托盘，没有托盘就无法启动。"
        "GNOME 42+ 请安装 AppIndicator 扩展；KDE / XFCE / MATE / Cinnamon "
        "默认就带托盘。`squill` 命令行和 MCP 服务在没有托盘的情况下仍可使用。",
    },
    "tray.unavailable_body_generic": {
        "en": "No system tray / menu bar is available on this system, so "
        "ShotQuill can't start. The `squill` CLI and MCP server still work.",
        "zh": "本系统没有可用的托盘或菜单栏，ShotQuill 无法启动。"
        "`squill` 命令行和 MCP 服务仍可使用。",
    },
    "about.body": {"en": "Screenshot & annotation tool.", "zh": "截图与标注工具。"},
    # Editor window titles
    "title.annotate": {"en": "ShotQuill — Annotate", "zh": "ShotQuill — 标注"},
    "title.ocr_running": {
        "en": "ShotQuill — Recognizing text…",
        "zh": "ShotQuill — 正在识别文字…",
    },
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
    "tool.rounded_rect": {"en": "Rounded rectangle", "zh": "圆角矩形"},
    "tool.ellipse": {"en": "Ellipse", "zh": "圆"},
    "tool.arrow": {"en": "Arrow", "zh": "箭头"},
    "tool.line": {"en": "Line", "zh": "直线"},
    "tool.pen": {"en": "Pen", "zh": "画笔"},
    "tool.highlighter": {"en": "Highlighter", "zh": "荧光笔"},
    "tool.mosaic": {"en": "Mosaic", "zh": "马赛克"},
    "tool.text": {"en": "Text", "zh": "文字"},
    # Toolbar controls
    "toolbar.spotlight": {"en": "Spotlight", "zh": "聚光"},
    "toolbar.color": {"en": "Color", "zh": "颜色"},
    "toolbar.width": {"en": "Width ", "zh": "粗细 "},
    "toolbar.font_size": {"en": "Font size ", "zh": "字号 "},
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
        "en": "Drag to move · right-click for actions · double-click or Esc to close",
        "zh": "拖动移动 · 右键菜单 · 双击或 Esc 关闭",
    },
    "pin.close": {"en": "Close", "zh": "关闭"},
    "toolbar.copy": {"en": "Copy", "zh": "复制"},
    "toolbar.copy_tip": {"en": "Copy to clipboard", "zh": "复制到剪贴板"},
    "toolbar.save": {"en": "Save", "zh": "保存"},
    "toolbar.save_tip": {"en": "Save to file", "zh": "保存到文件"},
    "dialog.pick_color": {"en": "Select Color", "zh": "选择颜色"},
    # Settings
    "settings.title": {"en": "ShotQuill Settings", "zh": "ShotQuill 设置"},
    "settings.save_dir": {"en": "Save Folder", "zh": "保存目录"},
    "settings.format": {"en": "Image Format", "zh": "图片格式"},
    "settings.hotkey_enabled": {"en": "Enable", "zh": "启用"},
    "settings.smart": {"en": "Capture", "zh": "截图"},
    "settings.fullscreen": {"en": "Full Screen", "zh": "全屏截图"},
    "settings.editor_copy": {"en": "Copy in Editor", "zh": "编辑器内复制"},
    "settings.editor_save": {"en": "Save in Editor", "zh": "编辑器内保存"},
    "settings.editor_key_conflict": {
        "en": "This key is already used by the editor (copy, save, undo, redo, or Esc). "
        "Choose another key.",
        "zh": "该按键已被编辑器占用（复制/保存/撤销/重做或 Esc），请换一个按键。",
    },
    "settings.capture_key_duplicate": {
        "en": "Capture and Full Screen can't share the same hotkey — "
        "one of them would silently stop working.",
        "zh": "截图和全屏截图不能使用同一个快捷键，否则其中一个会静默失效。",
    },
    "settings.editor_key_duplicate": {
        "en": "Copy and save can't share the same key.",
        "zh": "复制和保存不能使用同一个按键。",
    },
    "settings.editor_key_empty": {
        "en": "Record a key for the enabled editor action, or disable it.",
        "zh": "请为已启用的编辑器动作录制一个按键，或将其停用。",
    },
    "settings.editor_key_capture_conflict": {
        "en": "This key matches a global capture hotkey and would trigger both. "
        "Choose another key.",
        "zh": "该按键与全局截图热键相同，会同时触发截图和编辑器动作，请换一个按键。",
    },
    "settings.save_dir_invalid": {
        "en": "This save folder can't be used — it must be a writable folder "
        "(or one that can be created). Choose another folder.",
        "zh": "无法使用该保存目录：必须是可写目录（或可以创建的目录），请换一个。",
    },
    "settings.language": {"en": "Language", "zh": "界面语言"},
    "settings.browse": {"en": "Browse…", "zh": "浏览…"},
    "settings.choose_dir": {"en": "Choose Save Folder", "zh": "选择保存目录"},
    "settings.auto_output": {"en": "After capture", "zh": "截图后"},
    "settings.auto_save": {"en": "Auto-save to folder", "zh": "自动保存到目录"},
    "settings.auto_copy": {"en": "Auto-copy to clipboard", "zh": "自动复制到剪贴板"},
    "settings.autostart": {"en": "Launch at login", "zh": "开机自启"},
    "settings.include_cursor": {
        "en": "Include mouse pointer in screenshots",
        "zh": "截图包含鼠标指针",
    },
    "settings.debug_mode": {
        "en": "Debug mode (write detailed logs)",
        "zh": "Debug 模式（写入详细日志）",
    },
    "settings.flash": {"en": "Flash on capture", "zh": "截图时闪光"},
    "settings.sound": {"en": "Sound on capture", "zh": "截图时播放声音"},
    "settings.hover_switch": {"en": "Highlight window after", "zh": "悬停高亮窗口"},
    "settings.region_adjust": {
        "en": "Adjust region with arrow keys in the editor",
        "zh": "区域截图后可在编辑器中用方向键微调",
    },
    "settings.editor_backdrop": {
        "en": "Edit in place over the dimmed screen (frameless editor)",
        "zh": "原位编辑：编辑窗外保持变暗（无标题栏）",
    },
    "settings.toolbar_style": {"en": "Toolbar buttons", "zh": "工具栏按钮"},
    "settings.toolbar_style_both": {"en": "Icon and text", "zh": "图标 + 文字"},
    "settings.toolbar_style_icon": {"en": "Icon only", "zh": "仅图标"},
    "settings.toolbar_style_text": {"en": "Text only", "zh": "仅文字"},
    "settings.hover_switch_instant": {"en": "Immediately", "zh": "立即"},
    "settings.hover_switch_seconds": {"en": "{seconds} s", "zh": "{seconds} 秒"},
    "settings.hover_switch_never": {"en": "Only when clicked", "zh": "仅点击时选中"},
    "settings.permission_screen": {"en": "Screen Recording", "zh": "屏幕录制权限"},
    "settings.permission_input": {"en": "Input Monitoring", "zh": "输入监控权限"},
    "settings.permission_granted": {"en": "✓ Granted", "zh": "✓ 已授权"},
    "settings.permission_denied": {"en": "✕ Not granted", "zh": "✕ 未授权"},
    "settings.permission_unknown": {"en": "Status unknown", "zh": "状态未知"},
    "settings.open_system_settings": {
        "en": "Open System Settings…",
        "zh": "打开系统设置…",
    },
    "settings.uninstall": {"en": "Uninstall ShotQuill…", "zh": "卸载 ShotQuill…"},
    "uninstall.title": {"en": "Uninstall ShotQuill", "zh": "卸载 ShotQuill"},
    "uninstall.action": {"en": "Uninstall", "zh": "卸载"},
    "uninstall.cancel": {"en": "Cancel", "zh": "取消"},
    "uninstall.inspecting": {
        "en": "Checking the installation channel…",
        "zh": "正在检查安装来源…",
    },
    "uninstall.preparing": {
        "en": "Preparing the protected uninstaller…",
        "zh": "正在准备受保护的卸载程序…",
    },
    "uninstall.confirm": {
        "en": "Remove the application, ShotQuill-owned command links, and package receipts? "
        "Settings, recordings, logs, screenshots, and custom save folders are kept. "
        "Save and close any open editors first; unsaved annotations will be lost. "
        "ShotQuill closes before macOS requests administrator authorization. If authorization "
        "is cancelled, or removal fails before the app is removed, ShotQuill opens again. "
        "After approval, verified cleanup may take about 30 seconds; a completion notice follows. "
        "If cleanup only partially completes, follow-up steps are shown.\n\n{plan}",
        "zh": "是否移除应用、ShotQuill 自有命令链接及软件包收据？"
        "设置、录制、日志、截图和自定义保存目录都会保留。"
        "请先保存并关闭已打开的编辑器，未保存的标注将会丢失。"
        "ShotQuill 会先退出，然后 macOS 可能请求管理员授权；"
        "若取消授权，或在应用移除前失败，ShotQuill 会重新打开；"
        "授权后，校验和清理可能需要约 30 秒，完成后会显示通知；"
        "若只完成部分清理，将显示后续处理步骤。\n\n{plan}",
    },
    "uninstall.brew": {
        "en": "Homebrew manages this installation. Run this command in Terminal:\n\n"
        "{command}\n\n{plan}",
        "zh": "此安装由 Homebrew 管理。请在终端运行：\n\n{command}\n\n{plan}",
    },
    "uninstall.unavailable": {
        "en": "This installation cannot be removed automatically. No files were changed.\n\n{plan}",
        "zh": "无法自动移除此安装，未更改任何文件。\n\n{plan}",
    },
    "uninstall.start_failed": {
        "en": "The uninstaller could not be started: {error}",
        "zh": "无法启动卸载器：{error}",
    },
    "uninstall.failed": {
        "en": "The uninstaller did not complete (exit code {code}). ShotQuill will stay open "
        "when possible; no user data was targeted.",
        "zh": "卸载未完成（退出码 {code}）。如果条件允许，ShotQuill 会保持运行；"
        "卸载过程未将用户数据列为目标。",
    },
    "settings.blocklist": {"en": "Blocked apps", "zh": "排除名单"},
    "settings.blocklist_button": {"en": "Blocked apps…", "zh": "排除名单…"},
    # Blocklist editor
    "blocklist.title": {"en": "Blocked apps", "zh": "排除应用"},
    "blocklist.hint": {
        "en": "These apps are never captured — refused outright, and painted out of "
        "full-screen and region shots.",
        "zh": "这些应用永不被截图——直接拒绝，并在全屏和区域截图中打码。",
    },
    "blocklist.type_bundle": {"en": "Bundle ID", "zh": "Bundle ID"},
    "blocklist.type_name": {"en": "App name", "zh": "应用名"},
    "blocklist.add": {"en": "Add", "zh": "添加"},
    "blocklist.remove": {"en": "Remove", "zh": "移除"},
    "blocklist.add_running": {"en": "Add running app…", "zh": "添加运行中的应用…"},
    "blocklist.pick_running": {"en": "Pick a running app", "zh": "选择一个运行中的应用"},
    "blocklist.corrupt": {
        "en": "The blocklist file couldn't be read; starting from an empty list.",
        "zh": "排除名单文件无法读取，已从空列表开始。",
    },
    "settings.allowlist": {"en": "Allowed apps", "zh": "白名单"},
    "settings.allowlist_button": {"en": "Allowed apps…", "zh": "白名单…"},
    # Allowlist editor
    "allowlist.title": {"en": "Allowed apps", "zh": "白名单应用"},
    "allowlist.enabled": {
        "en": "Restrict capture to the allowlist (only these apps can be captured)",
        "zh": "限制为只截白名单内的应用(仅这些应用可被截图)",
    },
    "allowlist.hint": {
        "en": "When enabled, ONLY these apps can be captured — every other window, and "
        "any full-screen or region shot, is refused. A tight leash for agents using "
        "the CLI or MCP. Disabled by default.",
        "zh": "开启后只有这些应用能被截图——其它窗口、以及任何全屏或区域截图都会被拒绝。"
        "适合给通过 CLI 或 MCP 工作的 agent 套上紧约束。默认关闭。",
    },
    "allowlist.type_bundle": {"en": "Bundle ID", "zh": "Bundle ID"},
    "allowlist.type_name": {"en": "App name", "zh": "应用名"},
    "allowlist.add": {"en": "Add", "zh": "添加"},
    "allowlist.remove": {"en": "Remove", "zh": "移除"},
    "allowlist.add_running": {"en": "Add running app…", "zh": "添加运行中的应用…"},
    "allowlist.pick_running": {"en": "Pick a running app", "zh": "选择一个运行中的应用"},
    "allowlist.corrupt": {
        "en": "The allowlist file couldn't be read; starting from an empty list.",
        "zh": "白名单文件无法读取，已从空列表开始。",
    },
    "allowlist.empty_enabled": {
        "en": "The allowlist is on but has no rules, so nothing can be captured. Add an "
        "app, or turn the allowlist off.",
        "zh": "白名单已开启但没有任何规则,因此什么都截不了。请添加一个应用,或关闭白名单。",
    },
}

# Localized display names for keys shown in tooltips, keyed by the Qt
# QKeySequence portable name. Applied to the final (non-modifier) segment so
# combos like "Ctrl+Return" keep their modifier prefix.
_KEY_NAMES: dict[str, dict[str, str]] = {
    "Space": {"en": "Space", "zh": "空格"},
    "Return": {"en": "Enter", "zh": "回车"},
}

_current = DEFAULT_LANGUAGE


def set_language(language: str) -> None:
    global _current
    _current = language if language in LANGUAGES else DEFAULT_LANGUAGE


def current_language() -> str:
    return _current


def key_display_name(portable: str) -> str:
    """Localize a key-sequence display string (e.g. ``Return`` → 回车).

    Only the final segment is looked up, so combos keep their modifier prefix;
    unknown names (including platform-native symbols like ⌘D) pass through.
    """
    parts = portable.split("+")
    entry = _KEY_NAMES.get(parts[-1])
    if entry:
        parts[-1] = entry.get(_current) or entry.get(DEFAULT_LANGUAGE) or parts[-1]
    return "+".join(parts)


def t(key: str) -> str:
    """Translate ``key`` into the current language (falls back to English, then key)."""
    entry = _STRINGS.get(key)
    if entry is None:
        return key
    return entry.get(_current) or entry.get(DEFAULT_LANGUAGE) or key


def adjust_hint_key() -> str:
    """The right ``editor.adjust_hint*`` key for this platform.

    macOS uses ``⌥`` / ``⇧`` keycap glyphs throughout its UI, so the original
    hint reads natively there. Linux/X11 (and other) users expect ``Alt`` /
    ``Shift`` text, so they get a sibling string. Centralised here so the
    editor doesn't repeat the ``sys.platform`` check inline.
    """
    return "editor.adjust_hint" if sys.platform == "darwin" else "editor.adjust_hint_text"


def tray_unavailable_body_key() -> str:
    """The right ``tray.unavailable_body_*`` key for this platform.

    Linux gets a body that points at the AppIndicator extension (the common
    GNOME 42+ stumble); other platforms get a shorter generic body.
    """
    if sys.platform.startswith("linux"):
        return "tray.unavailable_body_linux"
    return "tray.unavailable_body_generic"
