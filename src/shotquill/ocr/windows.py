# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Windows OCR via the WinRT ``Windows.Media.Ocr`` engine (on-device, offline).

The WinRT projection (``winrt`` / ``winsdk``) is an optional, Windows-only
dependency, so — like the macOS Vision backend — it is imported lazily and this
module still imports cleanly on every platform (the CI smoke test and the GUI
factory's availability probe both rely on that).

The split mirrors the capture backends: the part that calls into WinRT (image
conversion + the async OCR call) is a thin shim marked ``# pragma: no cover`` —
it needs a real Windows session with the OCR language packs installed, which the
test platform doesn't have — while turning a recognised ``OcrResult`` into the
ordered list of lines the editor/CLI expect lives in the pure
:func:`lines_from_result`, unit-tested with a plain fake.

The WinRT shim here is written to the documented ``Windows.Media.Ocr`` API but
has **not** been validated against a live engine; it needs a smoke run on a real
Windows host (see the CI Windows job) before being relied on.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from shotquill.ocr.base import TextBox, TextRecognizer

if TYPE_CHECKING:
    from PySide6.QtGui import QImage


def _load_ocr_namespace():
    """Import the WinRT OCR projection, trying both distribution names.

    Microsoft ships the projection as ``winrt-*`` packages (modern) that import
    under ``winrt.windows.*``; the older ``winsdk`` package imports under
    ``winsdk.windows.*``. Returns the package root module (``winrt`` or
    ``winsdk``) so callers reach submodules off it, or raises ImportError when
    neither is installed."""
    try:
        import winrt as root
        import winrt.windows.media.ocr  # noqa: F401

        return root
    except ImportError as winrt_err:
        # Fall back to the older distribution, but chain the original failure so
        # a partial winrt install (root imports, OCR submodule doesn't) isn't
        # masked behind a misleading "winsdk not found".
        try:
            import winsdk as root
            import winsdk.windows.media.ocr  # noqa: F401
        except ImportError as winsdk_err:
            raise ImportError(
                f"no WinRT OCR projection: winrt ({winrt_err}); winsdk ({winsdk_err})"
            ) from winsdk_err
        return root


def is_available() -> bool:
    """Whether the WinRT OCR projection can be imported (cheap-ish, one-time).

    The GUI factory uses this to decide whether to offer the OCR action at all,
    rather than showing a button that can only fail when the optional dependency
    isn't installed."""
    try:
        _load_ocr_namespace()
    except ImportError:
        return False
    return True


def boxes_from_result(result) -> list[TextBox]:
    """Extract ordered text spans with pixel boxes from a WinRT ``OcrResult``.

    ``OcrResult.lines`` already comes back in reading order (top-to-bottom,
    left-to-right). WinRT puts the bounding rectangle on each ``OcrWord``, not the
    line, so a line's box is the union of its words' ``BoundingRect`` (pixels,
    top-left origin — already the space we mask in). A line with no usable word
    rects still contributes its text with a zero box rather than vanishing.
    Defensive against a ``None`` line list (an image with no text). This is the one
    decision worth testing without a live engine."""
    out: list[TextBox] = []
    for line in getattr(result, "lines", None) or []:
        text = (getattr(line, "text", "") or "").strip()
        if not text:
            continue
        rects = [
            rect
            for word in getattr(line, "words", None) or []
            if (rect := getattr(word, "bounding_rect", None)) is not None
        ]
        if rects:
            x0 = min(r.x for r in rects)
            y0 = min(r.y for r in rects)
            x1 = max(r.x + r.width for r in rects)
            y1 = max(r.y + r.height for r in rects)
            out.append(
                TextBox(text=text, x=int(x0), y=int(y0), width=int(x1 - x0), height=int(y1 - y0))
            )
        else:
            out.append(TextBox(text=text, x=0, y=0, width=0, height=0))
    return out


def lines_from_result(result) -> list[str]:
    """Ordered, non-empty text lines from a WinRT ``OcrResult`` (text-only view)."""
    return [box.text for box in boxes_from_result(result)]


class WindowsOcrRecognizer(TextRecognizer):
    backend_name = "Windows OCR"

    def recognize_boxes(
        self, image: QImage
    ) -> list[TextBox]:  # pragma: no cover - needs Windows + WinRT
        import asyncio

        # OCR runs on a worker thread (the editor offloads it), so there is no
        # live event loop here; asyncio.run drives the WinRT async calls to
        # completion. Any conversion/engine failure degrades to "no text" rather
        # than crashing the editor — the same contract the Vision backend keeps.
        try:
            result = asyncio.run(self._recognize_async(image))
        except Exception:
            return []
        return boxes_from_result(result)

    async def _recognize_async(self, image: QImage):  # pragma: no cover - needs Windows + WinRT
        root = _load_ocr_namespace()
        ocr = root.windows.media.ocr
        imaging = root.windows.graphics.imaging
        streams = root.windows.storage.streams

        from PySide6.QtCore import QBuffer, QByteArray, QIODevice

        # QImage -> PNG bytes -> in-memory WinRT stream -> SoftwareBitmap.
        buffer_bytes = QByteArray()
        buffer = QBuffer(buffer_bytes)
        buffer.open(QIODevice.WriteOnly)
        image.save(buffer, "PNG")
        buffer.close()

        stream = streams.InMemoryRandomAccessStream()
        writer = streams.DataWriter(stream)
        writer.write_bytes(bytes(buffer_bytes))
        await writer.store_async()
        await writer.flush_async()
        stream.seek(0)

        decoder = await imaging.BitmapDecoder.create_async(stream)
        software_bitmap = await decoder.get_software_bitmap_async()

        # Prefer the user's configured languages; fall back to any single
        # language the engine supports if the profile yields no OCR engine.
        engine = ocr.OcrEngine.try_create_from_user_profile_languages()
        if engine is None:
            languages = ocr.OcrEngine.available_recognizer_languages
            if not languages:
                return None
            engine = ocr.OcrEngine.try_create_from_language(languages[0])
        if engine is None:
            return None
        return await engine.recognize_async(software_bitmap)
