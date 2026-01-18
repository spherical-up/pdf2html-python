"""
PDF to HTML 转换器
基于 pdf2htmlEX 的设计思路：glyph 分流 + 双层渲染模型

核心设计理念（参考 pdf2htmlEX）：
- 不是"截图+贴文字"，而是"glyph级别的拆分、分层与重组"
- 可提取的glyph → HTML文本层（可复制、可搜索）
- 不可提取的glyph → 背景层（图形、图片、复杂绘制）
- 背景层"看起来像去掉了文字"，实际上是那些glyph从未被画进去

注意：由于PyMuPDF的限制，我们采用"渲染后擦除"的方式来实现glyph分流
（理想情况下应该在渲染阶段就分流，但PyMuPDF的get_pixmap()会渲染整个页面）
"""
import fitz  # PyMuPDF
from PIL import Image, ImageDraw
import base64
import io
import re
from fontTools.ttLib import TTFont
from typing import Optional
from .font_handler import FontHandler
from .font_unicode_fixer import FontUnicodeFixer

NBSP_TOKEN = "__NBSP__"


class SimplePDFConverter:
    """
    PDF to HTML 转换器
    
    设计思路：
    1. Glyph提取阶段：识别所有可提取为HTML文本的glyph
    2. Glyph分流阶段：决定每个glyph的去向（文本层 or 背景层）
    3. 背景渲染阶段：渲染非文本内容，排除已提取的glyph
    4. 文本层生成阶段：生成HTML文本元素
    5. 双层叠加阶段：将背景层和文本层精确叠加
    """
    
    def __init__(self, dpi=150):
        """
        初始化转换器
        
        Args:
            dpi: 背景图像渲染DPI（默认150，可提高到300以获得更清晰的图像）
        """
        self.dpi = dpi
        self.font_handler = FontHandler()
        self.font_fixer = FontUnicodeFixer()
        self.pdf_path = None  # 保存PDF路径，用于字体修复
        self.font_name_mapping = {}  # {规范化名称: 原始名称} 的映射
    
    def extract_extractable_glyphs(self, page):
        """
        阶段1：提取可提取的glyph
        
        根据pdf2htmlEX的设计，可提取的glyph需要满足：
        - 可以映射到Unicode字符
        - 正常可见（未被完全遮挡）
        - 字体信息完整
        
        Args:
            page: PyMuPDF页面对象
            
        Returns:
            list: 可提取的glyph列表，每个glyph包含：
                - text: 文本内容
                - bbox: 边界框 (x0, y0, x1, y1)
                - font_size: 字体大小
                - font_name: 字体名称
                - color: 颜色 (r, g, b)
        """
        extractable_glyphs = []
        text_dict = page.get_text("rawdict")
        page_width = page.rect.width
        font_sizes = []
        for block in text_dict.get("blocks", []):
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    size = span.get("size")
                    if size:
                        font_sizes.append(size)
        if font_sizes:
            font_sizes.sort()
            median_font_size = font_sizes[len(font_sizes) // 2]
        else:
            median_font_size = 12
        
        for block in text_dict.get("blocks", []):
            if "lines" in block:  # 文本块
                block_lines = block["lines"]
                block_line_count = len(block_lines)
                block_bbox = block.get("bbox")
                lines_to_process = block_lines
                if block_line_count and block_line_count <= 3:
                    grouped = {}
                    for line in block_lines:
                        bbox = line.get("bbox")
                        if not bbox:
                            continue
                        key = round(bbox[1] / 0.5) * 0.5
                        grouped.setdefault(key, []).append(line)
                    merged_lines = []
                    for _, group in grouped.items():
                        if len(group) == 1:
                            merged_lines.append(group[0])
                            continue
                        merged_lines.append(self._merge_line_group(group))
                    lines_to_process = merged_lines
                else:
                    grouped = {}
                    for line in block_lines:
                        if not self._is_title_line(line):
                            continue
                        bbox = line.get("bbox")
                        if not bbox:
                            continue
                        key = round(bbox[1] / 0.5) * 0.5
                        grouped.setdefault(key, []).append(line)
                    if grouped:
                        merged_by_id = {}
                        for group in grouped.values():
                            if len(group) > 1:
                                merged_line = self._merge_line_group(group)
                                for line in group:
                                    merged_by_id[id(line)] = merged_line
                        if merged_by_id:
                            merged_seen = set()
                            ordered_lines = []
                            for line in block_lines:
                                merged_line = merged_by_id.get(id(line))
                                if merged_line:
                                    if id(merged_line) in merged_seen:
                                        continue
                                    merged_seen.add(id(merged_line))
                                    ordered_lines.append(merged_line)
                                else:
                                    ordered_lines.append(line)
                            lines_to_process = ordered_lines

                for line in lines_to_process:
                    line_bbox = line.get("bbox")
                    spans = line.get("spans", [])

                    # If a line is visually continuous and uses the same style,
                    # merge spans so centering applies to the full line.
                    can_merge = False
                    can_merge_from_text = False
                    if spans:
                        font_names = []
                        font_sizes = []
                        colors = []
                        for span in spans:
                            font_names.append(self._normalize_font_name(span.get("font", "Arial")))
                            font_sizes.append(span.get("size", 12))
                            colors.append(span.get("color", 0))
                        if len(set(font_names)) == 1 and len(set(colors)) == 1:
                            if max(font_sizes) - min(font_sizes) <= 0.2:
                                if all(span.get("chars") for span in spans):
                                    can_merge = True
                                else:
                                    span_texts = []
                                    for span in spans:
                                        span_texts.append(span.get("text", ""))
                                    if all(t.strip() for t in span_texts):
                                        can_merge_from_text = True

                    if can_merge:
                        merged_chars = []
                        for span in spans:
                            merged_chars.extend(span.get("chars", []))
                        merged_chars = [c for c in merged_chars if c.get("c") and c.get("bbox")]
                        merged_chars.sort(key=lambda c: (c["bbox"][0], c["bbox"][1]))
                        if not merged_chars:
                            continue
                        font_name = self._normalize_font_name(spans[0].get("font", "Arial"))
                        font_size = spans[0].get("size", 12)
                        color = spans[0].get("color", 0)
                        r = (color >> 16) & 0xFF
                        g = (color >> 8) & 0xFF
                        b = color & 0xFF

                        span_text = ""
                        line_groups = line.get("_line_groups")
                        if line_groups:
                            space_widths = []
                            for lg in line_groups:
                                for lg_span in lg.get("spans", []):
                                    for ch in lg_span.get("chars", []):
                                        if ch.get("c") == " " and ch.get("bbox"):
                                            space_widths.append(ch["bbox"][2] - ch["bbox"][0])
                            space_unit = None
                            if space_widths:
                                space_widths.sort()
                                space_unit = space_widths[len(space_widths) // 2]
                            parts = []
                            prev_bbox = None
                            for lg in line_groups:
                                lg_chars = []
                                for lg_span in lg.get("spans", []):
                                    lg_chars.extend(lg_span.get("chars", []))
                                lg_chars = [c for c in lg_chars if c.get("c") and c.get("bbox")]
                                if not lg_chars:
                                    continue
                                if prev_bbox and lg.get("bbox"):
                                    gap = lg["bbox"][0] - prev_bbox[2]
                                    if gap > max(font_size * 0.6, 3.0):
                                        if space_unit and space_unit > 0:
                                            count = int(round((gap / space_unit) * 2.2))
                                            count = max(6, min(20, count))
                                        else:
                                            unit = max(font_size * 0.3, 1.0)
                                            count = int(round(gap / unit))
                                            count = max(2, min(6, count))
                                        if gap > font_size * 2.0:
                                            count = min(count, 3)
                                        parts.append(NBSP_TOKEN * count)
                                parts.append("".join(ch.get("c", "") for ch in lg_chars))
                                if lg.get("bbox"):
                                    prev_bbox = lg["bbox"]
                            span_text = "".join(parts)
                        if not span_text:
                            span_text = self._rebuild_text_with_spacing(
                                merged_chars,
                                font_size,
                                False
                            )
                        if not span_text:
                            span_text = "".join(ch.get("c", "") for ch in merged_chars)
                        if not span_text.strip():
                            continue
                        force_uppercase, apply_small_caps, is_letter_spaced = self._span_caps_flags(span_text)

                        is_title = self._should_center_span(
                            span_text,
                            line_bbox,
                            page_width,
                            block_line_count,
                            font_size,
                            median_font_size
                        )
                        letter_spacing, word_spacing = self._compute_span_spacing(
                            merged_chars,
                            font_size,
                            is_letter_spaced
                        )
                        is_dropcap = self._is_dropcap_candidate(
                            span_text,
                            font_size,
                            line_bbox or spans[0].get("bbox"),
                            median_font_size,
                            font_name,
                            block_bbox
                        )
                        extractable_glyphs.append({
                            'text': span_text,
                            'bbox': line_bbox or spans[0].get("bbox"),
                            'font_size': font_size,
                            'font_name': font_name,
                            'color': (r, g, b),
                            'chars': merged_chars,
                            'force_uppercase': force_uppercase,
                            'apply_small_caps': apply_small_caps,
                            'letter_spacing': letter_spacing,
                            'word_spacing': word_spacing,
                            'align_center': is_title,
                            'line_bbox': line_bbox,
                            'block_bbox': block_bbox,
                            'page_width': page_width,
                            'dropcap_candidate': is_dropcap
                        })
                        continue
                    if can_merge_from_text:
                        font_name = self._normalize_font_name(spans[0].get("font", "Arial"))
                        font_size = spans[0].get("size", 12)
                        color = spans[0].get("color", 0)
                        r = (color >> 16) & 0xFF
                        g = (color >> 8) & 0xFF
                        b = color & 0xFF
                        ordered = sorted(
                            spans,
                            key=lambda s: (s.get("bbox", [0, 0, 0, 0])[0], s.get("bbox", [0, 0, 0, 0])[1])
                        )
                        merged_text = []
                        for i, span in enumerate(ordered):
                            text_part = span.get("text", "")
                            if i > 0:
                                prev_bbox = ordered[i - 1].get("bbox")
                                bbox = span.get("bbox")
                                if prev_bbox and bbox:
                                    gap = bbox[0] - prev_bbox[2]
                                    if gap > max(font_size * 0.4, 2.0):
                                        merged_text.append(" ")
                            merged_text.append(text_part)
                        span_text = "".join(merged_text)
                        force_uppercase, apply_small_caps, is_letter_spaced = self._span_caps_flags(span_text)
                        is_title = self._should_center_span(
                            span_text,
                            line_bbox,
                            page_width,
                            block_line_count,
                            font_size,
                            median_font_size
                        )
                        is_dropcap = self._is_dropcap_candidate(
                            span_text,
                            font_size,
                            line_bbox or ordered[0].get("bbox"),
                            median_font_size,
                            font_name,
                            block_bbox
                        )
                        extractable_glyphs.append({
                            'text': span_text,
                            'bbox': line_bbox or ordered[0].get("bbox"),
                            'font_size': font_size,
                            'font_name': font_name,
                            'color': (r, g, b),
                            'chars': None,
                            'force_uppercase': force_uppercase,
                            'apply_small_caps': apply_small_caps,
                            'letter_spacing': None,
                            'word_spacing': None,
                            'align_center': is_title,
                            'line_bbox': line_bbox,
                            'block_bbox': block_bbox,
                            'page_width': page_width,
                            'dropcap_candidate': is_dropcap
                        })
                        continue

                    for span in spans:
                        span_text = span.get("text", "")
                        if not span_text and span.get("chars"):
                            span_text = "".join(ch.get("c", "") for ch in span["chars"])
                        if not span_text.strip():
                            continue

                        # 提取glyph信息
                        font_size = span.get("size", 12)
                        font_name = span.get("font", "Arial")
                        # 规范化字体名称，确保与@font-face匹配
                        font_name = self._normalize_font_name(font_name)

                        # 提取颜色
                        color = span.get("color", 0)
                        r = (color >> 16) & 0xFF
                        g = (color >> 8) & 0xFF
                        b = color & 0xFF

                        chars = span.get("chars", [])
                        rebuilt_text = self._rebuild_text_with_spacing(
                            chars,
                            font_size,
                            False
                        )
                        if rebuilt_text:
                            span_text = rebuilt_text
                        force_uppercase, apply_small_caps, is_letter_spaced = self._span_caps_flags(span_text)
                        is_title = self._should_center_span(
                            span_text,
                            line_bbox,
                            page_width,
                            block_line_count,
                            font_size,
                            median_font_size
                        )
                        letter_spacing, word_spacing = self._compute_span_spacing(
                            chars,
                            font_size,
                            is_letter_spaced
                        )

                        bbox = span["bbox"]
                        is_dropcap = self._is_dropcap_candidate(
                            span_text,
                            font_size,
                            bbox,
                            median_font_size,
                            font_name,
                            block_bbox
                        )
                        extractable_glyphs.append({
                            'text': span_text,
                            'bbox': bbox,
                            'font_size': font_size,
                            'font_name': font_name,
                            'color': (r, g, b),
                            'chars': chars,
                            'force_uppercase': force_uppercase,
                            'apply_small_caps': apply_small_caps,
                            'letter_spacing': letter_spacing,
                            'word_spacing': word_spacing,
                            'align_center': is_title,
                            'line_bbox': line_bbox,
                            'block_bbox': block_bbox,
                            'page_width': page_width,
                            'dropcap_candidate': is_dropcap
                        })
        
        return extractable_glyphs

    def _span_caps_flags(self, text: str) -> tuple:
        normalized = text.replace(NBSP_TOKEN, " ")
        stripped_text = normalized.strip()
        if not stripped_text:
            return False, False, False
        tokens = stripped_text.split()
        letter_tokens = [t for t in tokens if re.fullmatch(r"[A-Za-z]", t)]
        force_uppercase = False
        apply_small_caps = False
        is_letter_spaced = False
        if len(tokens) >= 4 and len(letter_tokens) / len(tokens) >= 0.7:
            if any(t.islower() for t in letter_tokens):
                force_uppercase = True
                apply_small_caps = True
                is_letter_spaced = True
        roman_candidate = re.sub(r"\s+", "", stripped_text)
        if re.fullmatch(r"[ivxlcdm]+\.", roman_candidate):
            force_uppercase = True
        return force_uppercase, apply_small_caps, is_letter_spaced

    def _contains_cjk(self, text: str) -> bool:
        return bool(re.search(r"[\u4e00-\u9fff]", text))

    def _segment_bbox_from_chars(self, chars: list, default_bbox: list) -> list:
        if not chars:
            return default_bbox
        x0s = []
        y0s = []
        x1s = []
        y1s = []
        for ch in chars:
            bbox = ch.get("bbox")
            if not bbox:
                continue
            x0s.append(bbox[0])
            y0s.append(bbox[1])
            x1s.append(bbox[2])
            y1s.append(bbox[3])
        if not x0s:
            return default_bbox
        return [min(x0s), min(y0s), max(x1s), max(y1s)]

    def _get_span_container_width(self, page, align_center, block_bbox, page_width):
        if align_center and block_bbox and page_width:
            block_width = block_bbox[2] - block_bbox[0]
            if block_width > 0:
                if block_width < page_width * 0.7:
                    if block_bbox[0] > page_width * 0.05 and block_bbox[2] < page_width * 0.95:
                        return block_width
        return page.rect.width

    def _split_span_to_char_segments(self, chars: list) -> list:
        if not chars:
            return []
        segments = []
        for ch in chars:
            c = ch.get("c", "")
            bbox = ch.get("bbox")
            if c == "" or not bbox:
                continue
            segments.append({
                "text": c,
                "bbox": bbox
            })
        return segments

    def _split_span_by_double_spaces(self, text: str, chars: list) -> list:
        normalized = text.replace(NBSP_TOKEN, " ")
        if "  " not in normalized:
            return []
        if not self._contains_cjk(normalized):
            return []
        if not chars:
            return []
        segments = []
        current_chars = []
        current_text = []
        char_index = 0
        i = 0
        while i < len(normalized):
            ch = normalized[i]
            if ch == " ":
                j = i
                while j < len(normalized) and normalized[j] == " ":
                    j += 1
                run_len = j - i
                if run_len >= 2:
                    if current_chars:
                        segments.append({
                            "text": "".join(current_text),
                            "chars": current_chars
                        })
                        current_chars = []
                        current_text = []
                    while char_index < len(chars) and chars[char_index].get("c", "") == " ":
                        char_index += 1
                else:
                    current_text.append(" ")
                    if char_index < len(chars) and chars[char_index].get("c", "") == " ":
                        current_chars.append(chars[char_index])
                        char_index += 1
                i = j
                continue
            current_text.append(ch)
            if char_index < len(chars):
                current_chars.append(chars[char_index])
                char_index += 1
            i += 1
        if current_chars:
            segments.append({
                "text": "".join(current_text),
                "chars": current_chars
            })
        segments = [s for s in segments if s["text"].strip()]
        return segments

    def _build_text_span_html(
        self,
        page,
        text: str,
        bbox: list,
        font_size: float,
        font_name: str,
        color: tuple,
        apply_uppercase: bool,
        apply_small_caps: bool,
        letter_spacing: float,
        word_spacing: float,
        align_center: bool,
        line_bbox: list,
        block_bbox: list,
        page_width: float
    ) -> str:
        x0, y0, x1, y1 = bbox
        r, g, b = color

        html_y = y0
        is_serif = 'Garamond' in font_name or 'serif' in font_name.lower()
        fallback = 'serif' if is_serif else 'sans-serif'

        normalized = text.replace(NBSP_TOKEN, " ").strip()
        tokens = normalized.split()
        is_spaced_letters = (
            len(tokens) >= 4
            and all(len(t) == 1 for t in tokens)
            and all(re.fullmatch(r"[A-Za-z]", t) for t in tokens)
        )
        data_attrs = ""
        if is_spaced_letters and not align_center:
            bbox_width = x1 - x0
            if bbox_width > 0:
                data_attrs = (
                    f' data-letter-spaced="1"'
                    f' data-bbox-x1="{x1}"'
                    f' data-bbox-width="{bbox_width}"'
                )

        if align_center:
            use_block_width = False
            if block_bbox and page_width:
                block_width = block_bbox[2] - block_bbox[0]
                if block_width > 0:
                    if block_width < page_width * 0.7:
                        if block_bbox[0] > page_width * 0.05 and block_bbox[2] < page_width * 0.95:
                            use_block_width = True
            style = (
                f"left: {block_bbox[0] if use_block_width else 0}px; "
                f"top: {html_y}px; "
                f"width: {(block_bbox[2] - block_bbox[0]) if use_block_width else page.rect.width}px; "
                f"display: block; "
                f"text-align: center; "
                f"font-size: {font_size}px; "
                f"font-family: '{font_name}', {fallback}; "
                f"color: rgb({r}, {g}, {b});"
            )
        else:
            style = (
                f"left: {x0}px; "
                f"top: {html_y}px; "
                f"font-size: {font_size}px; "
                f"font-family: '{font_name}', {fallback}; "
                f"color: rgb({r}, {g}, {b});"
            )
        if apply_uppercase:
            style += " text-transform: uppercase;"
        if apply_small_caps:
            style += " font-variant-caps: all-small-caps;"
        if letter_spacing is not None:
            style += f" letter-spacing: {letter_spacing}px;"
        if word_spacing is not None:
            style += f" word-spacing: {word_spacing}px;"

        text_escaped = (
            text.replace("&", "&amp;")
               .replace("<", "&lt;")
               .replace(">", "&gt;")
        )
        if NBSP_TOKEN in text_escaped:
            text_escaped = text_escaped.replace(NBSP_TOKEN, "&nbsp;")

        return f'<span class="text-block" style="{style}"{data_attrs}>{text_escaped}</span>'

    def _is_dropcap_candidate(self, text, font_size, bbox, median_font_size, font_name, block_bbox):
        if not text or not bbox or not median_font_size:
            return False
        normalized = text.replace(NBSP_TOKEN, " ").strip()
        if len(normalized) != 1:
            return False
        if font_size < median_font_size * 2.4:
            return False
        x0, y0, x1, y1 = bbox
        width = max(0.0, x1 - x0)
        height = max(0.0, y1 - y0)
        if width == 0 or height == 0:
            return False
        aspect = width / height
        font_hint = font_name.lower() if font_name else ""
        font_flag = any(k in font_hint for k in ("drop", "decor", "orn", "init", "swash"))
        if block_bbox:
            block_x0 = block_bbox[0]
            if x0 > block_x0 + median_font_size * 0.5:
                return False
        if not font_flag and not (0.6 <= aspect <= 1.4):
            return False
        if height < median_font_size * 2.0:
            return False
        return True

    def _merge_line_group(self, group: list) -> dict:
        xs0 = []
        ys0 = []
        xs1 = []
        ys1 = []
        spans = []
        for line in group:
            lb = line.get("bbox")
            if lb:
                xs0.append(lb[0])
                ys0.append(lb[1])
                xs1.append(lb[2])
                ys1.append(lb[3])
            spans.extend(line.get("spans", []))
        return {
            "bbox": (min(xs0), min(ys0), max(xs1), max(ys1)),
            "spans": spans,
            "_line_groups": group
        }

    def _is_title_line(self, line: dict) -> bool:
        spans = line.get("spans", [])
        if not spans:
            return False
        parts = []
        for span in spans:
            chars = span.get("chars", [])
            if chars:
                parts.append("".join(ch.get("c", "") for ch in chars))
            else:
                text = span.get("text", "")
                if text:
                    parts.append(text)
        text = " ".join(parts)
        normalized = text.replace(NBSP_TOKEN, " ")
        stripped_text = normalized.strip()
        if not stripped_text:
            return False
        tokens = stripped_text.split()
        letter_tokens = [t for t in tokens if re.fullmatch(r"[A-Za-z]", t)]
        if len(tokens) >= 2 and letter_tokens and len(letter_tokens) / len(tokens) >= 0.7:
            return True
        roman_candidate = re.sub(r"\s+", "", stripped_text)
        if re.fullmatch(r"[ivxlcdmIVXLCDM]+\.", roman_candidate):
            return True
        return False

    def _should_center_span(
        self,
        text: str,
        line_bbox,
        page_width: float,
        block_line_count: int,
        font_size: float,
        median_font_size: float
    ) -> bool:
        normalized = text.replace(NBSP_TOKEN, " ")
        stripped_text = normalized.strip()
        if not stripped_text or not line_bbox or not page_width:
            return False
        if block_line_count and block_line_count > 3:
            return False
        line_width = line_bbox[2] - line_bbox[0]
        if line_width <= 0:
            return False
        if line_width > page_width * 0.85:
            return False
        line_center = (line_bbox[0] + line_bbox[2]) / 2
        center_offset = abs(line_center - page_width / 2)
        if center_offset > page_width * 0.08:
            return False
        # only short lines or letter-spaced titles should be centered
        tokens = stripped_text.split()
        letter_tokens = [t for t in tokens if re.fullmatch(r"[A-Za-z]", t)]
        is_letter_spaced = len(tokens) >= 4 and len(letter_tokens) / len(tokens) >= 0.7
        is_short_title = len(tokens) <= 6 and len(stripped_text) <= 40
        is_narrow = line_width <= page_width * 0.55
        is_large = bool(median_font_size) and font_size >= median_font_size * 1.12
        return is_letter_spaced or (is_short_title and (is_large or is_narrow))
    
    def _rebuild_text_with_spacing(
        self,
        chars: list,
        font_size: float,
        is_letter_spaced: bool
    ) -> str:
        if not chars or len(chars) < 2:
            return ""
        if is_letter_spaced:
            gaps = []
            for i in range(len(chars) - 1):
                bbox = chars[i].get("bbox")
                nbbox = chars[i + 1].get("bbox")
                if not bbox or not nbbox:
                    continue
                gap = nbbox[0] - bbox[2]
                if gap > 0:
                    gaps.append(gap)
            if not gaps:
                return ""
            gaps_sorted = sorted(gaps)
            median_gap = gaps_sorted[len(gaps_sorted) // 2]
            threshold = max(font_size * 0.6, median_gap * 4.0)
            rebuilt = []
            for i in range(len(chars) - 1):
                c = chars[i].get("c", "")
                rebuilt.append(c)
                bbox = chars[i].get("bbox")
                nbbox = chars[i + 1].get("bbox")
                if not bbox or not nbbox:
                    continue
                gap = nbbox[0] - bbox[2]
                if gap > threshold and chars[i + 1].get("c", "") != " ":
                    unit = max(font_size * 0.3, 1.0)
                    count = int(round(gap / unit))
                    count = max(2, min(6, count))
                    rebuilt.append(NBSP_TOKEN * count)
            rebuilt.append(chars[-1].get("c", ""))
            return "".join(rebuilt)
        gaps = []
        for i in range(len(chars) - 1):
            bbox = chars[i].get("bbox")
            nbbox = chars[i + 1].get("bbox")
            if not bbox or not nbbox:
                continue
            gap = nbbox[0] - bbox[2]
            if gap > 0:
                gaps.append(gap)
        if not gaps:
            return ""
        gaps_sorted = sorted(gaps)
        median_gap = gaps_sorted[len(gaps_sorted) // 2]
        if median_gap <= 0.5:
            return ""
        threshold = max(median_gap * 3.0, font_size * 0.4)
        rebuilt = []
        for i in range(len(chars) - 1):
            c = chars[i].get("c", "")
            rebuilt.append(c)
            bbox = chars[i].get("bbox")
            nbbox = chars[i + 1].get("bbox")
            if not bbox or not nbbox:
                continue
            gap = nbbox[0] - bbox[2]
            if gap > threshold and chars[i + 1].get("c", "") != " ":
                rebuilt.append(" ")
        rebuilt.append(chars[-1].get("c", ""))
        return "".join(rebuilt)

    def _compute_span_spacing(
        self,
        chars: list,
        font_size: float,
        is_letter_spaced: bool
    ) -> tuple:
        if not chars or len(chars) < 2:
            return None, None
        if is_letter_spaced:
            return None, None
        letter_gaps = []
        space_gaps = []
        for i in range(len(chars) - 1):
            current = chars[i]
            next_char = chars[i + 1]
            bbox = current.get("bbox")
            nbbox = next_char.get("bbox")
            if not bbox or not nbbox:
                continue
            gap = nbbox[0] - bbox[2]
            if gap < 0:
                continue
            c = current.get("c", "")
            n = next_char.get("c", "")
            if c == " " or n == " ":
                space_gaps.append(gap)
            else:
                letter_gaps.append(gap)
        if not letter_gaps and not space_gaps:
            return None, None
        if letter_gaps:
            letter_gaps_sorted = sorted(letter_gaps)
            letter_gap = letter_gaps_sorted[len(letter_gaps_sorted) // 2]
        else:
            letter_gap = 0.0
        space_gap = None
        if space_gaps:
            space_gaps_sorted = sorted(space_gaps)
            space_gap = space_gaps_sorted[len(space_gaps_sorted) // 2]
        else:
            # 大间隙视为词间距
            large_gaps = [g for g in letter_gaps if g > max(letter_gap * 3.0, font_size * 0.4)]
            if large_gaps:
                space_gap = sorted(large_gaps)[len(large_gaps) // 2]
        letter_spacing = letter_gap if letter_gap > 0.1 else None
        word_spacing = None
        if space_gap is not None and space_gap > 0.1:
            extra = space_gap - (letter_gap if letter_gap > 0 else 0.0)
            if extra > 0.1:
                word_spacing = extra
        return letter_spacing, word_spacing
    
    def _normalize_font_name(self, font_name: str) -> str:
        """
        规范化字体名称，提取基础名称
        
        PDF中的字体名称可能包含前缀（如 "OFSHEY+EBGaramond12-Italic"），
        需要提取基础名称（如 "EBGaramond12-Italic"）
        
        Args:
            font_name: 原始字体名称
            
        Returns:
            规范化后的字体名称
        """
        if not font_name:
            return font_name
        
        # 如果包含 "+"，提取 "+" 后面的部分
        if '+' in font_name:
            parts = font_name.split('+')
            if len(parts) > 1:
                return parts[-1]  # 返回最后一部分
        
        return font_name
    
    def _get_background_color(self, bg_image, bbox, zoom, text_color):
        """
        检测文字周围的背景颜色
        
        Args:
            bg_image: PIL Image对象
            bbox: 文字边界框 (x0, y0, x1, y1)
            zoom: DPI缩放比例
            text_color: 文字颜色 (r, g, b)
            
        Returns:
            tuple: 背景颜色 (r, g, b) 或 None（使用默认白色）
        """
        x0, y0, x1, y1 = bbox
        img_x0 = int(x0 * zoom)
        img_y0 = int(y0 * zoom)
        img_x1 = int(x1 * zoom)
        img_y1 = int(y1 * zoom)
        
        # 在文字区域周围采样，检测背景颜色
        # 采样区域：文字区域向外扩展一定距离
        expand = max(5, int(5 * zoom / 72.0))
        
        # 采样点：文字区域的上、下、左、右四个方向
        sample_regions = [
            # 上方
            (max(0, img_x0 - expand), max(0, img_y0 - expand * 2), 
             min(bg_image.width, img_x1 + expand), max(0, img_y0 - expand)),
            # 下方
            (max(0, img_x0 - expand), min(bg_image.height, img_y1 + expand),
             min(bg_image.width, img_x1 + expand), min(bg_image.height, img_y1 + expand * 2)),
            # 左侧
            (max(0, img_x0 - expand * 2), max(0, img_y0 - expand),
             max(0, img_x0 - expand), min(bg_image.height, img_y1 + expand)),
            # 右侧
            (min(bg_image.width, img_x1 + expand), max(0, img_y0 - expand),
             min(bg_image.width, img_x1 + expand * 2), min(bg_image.height, img_y1 + expand)),
        ]
        
        # 收集所有采样点的颜色
        colors = []
        for region_x0, region_y0, region_x1, region_y1 in sample_regions:
            if region_x0 < region_x1 and region_y0 < region_y1:
                # 在区域内采样多个点
                for y in range(region_y0, region_y1, max(1, (region_y1 - region_y0) // 3)):
                    for x in range(region_x0, region_x1, max(1, (region_x1 - region_x0) // 3)):
                        if 0 <= x < bg_image.width and 0 <= y < bg_image.height:
                            pixel = bg_image.getpixel((x, y))
                            if isinstance(pixel, tuple) and len(pixel) >= 3:
                                r, g, b = pixel[:3]
                                # 排除与文字颜色相同的像素
                                if text_color:
                                    color_diff = abs(r - text_color[0]) + abs(g - text_color[1]) + abs(b - text_color[2])
                                    if color_diff > 30:  # 颜色差异足够大，认为是背景
                                        colors.append((r, g, b))
                                else:
                                    colors.append((r, g, b))
        
        if not colors:
            return None  # 无法检测，使用默认白色
        
        # 计算平均颜色
        avg_r = sum(c[0] for c in colors) // len(colors)
        avg_g = sum(c[1] for c in colors) // len(colors)
        avg_b = sum(c[2] for c in colors) // len(colors)
        
        return (avg_r, avg_g, avg_b)
    
    def render_background_without_glyphs(self, page, extractable_glyphs):
        """
        阶段2：渲染背景层（排除已提取的glyph）
        
        这是pdf2htmlEX的核心思想：背景层不包含可提取的glyph
        
        注意：由于PyMuPDF的限制，我们采用"渲染后擦除"的方式：
        1. 先渲染完整页面（包括所有glyph）
        2. 然后擦除可提取glyph的区域
        
        理想情况下（如pdf2htmlEX使用Poppler），应该在渲染阶段就分流：
        - 可提取的glyph → 跳过，不绘制到背景
        - 不可提取的glyph → 绘制到背景
        
        Args:
            page: PyMuPDF页面对象
            extractable_glyphs: 可提取的glyph列表
            
        Returns:
            tuple: (base64编码的背景图像, 实际用于文本层的glyph列表)
        """
        # 渲染完整页面（包括所有glyph）
        # 使用DPI缩放以获得清晰图像
        zoom = self.dpi / 72.0
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        
        # 转换为PIL Image
        img_data = pix.tobytes("png")
        bg_image = Image.open(io.BytesIO(img_data))
        
        # 擦除可提取glyph的区域（实现"glyph分流"）
        # 这是"后期擦除"，但逻辑上符合pdf2htmlEX的"glyph分流"思想
        filtered_glyphs = []
        if extractable_glyphs:
            draw = ImageDraw.Draw(bg_image)
            page_height = page.rect.height
            
            for glyph in extractable_glyphs:
                bbox = glyph['bbox']
                x0, y0, x1, y1 = bbox
                text_color = glyph['color']
                r, g, b = text_color
                
                # 转换为图像坐标（考虑DPI缩放）
                # PyMuPDF的get_pixmap()生成的图像坐标系统与PDF bbox一致
                img_x0 = int(x0 * zoom)
                img_y0 = int(y0 * zoom)
                img_x1 = int(x1 * zoom)
                img_y1 = int(y1 * zoom)

                if glyph.get('dropcap_candidate'):
                    if self._is_complex_glyph_region(bg_image, (img_x0, img_y0, img_x1, img_y1)):
                        continue

                # 添加padding确保完全覆盖（包括抗锯齿边缘）
                padding = max(2, int(2 * zoom / 72.0))
                
                # 判断是否为白色或接近白色的文字
                # 如果文字是白色（或接近白色），需要检测背景颜色
                is_white_text = (r > 240 and g > 240 and b > 240)
                
                if is_white_text:
                    # 对于白色文字，检测背景颜色并用背景颜色填充
                    bg_color = self._get_background_color(bg_image, bbox, zoom, text_color)
                    if bg_color:
                        fill_color = bg_color
                    else:
                        # 如果无法检测背景颜色，使用白色（保持原逻辑）
                        fill_color = 'white'
                else:
                    # 对于非白色文字，使用白色填充（原逻辑）
                    fill_color = 'white'
                
                # 擦除glyph区域
                if isinstance(fill_color, tuple):
                    # 如果是RGB元组，转换为PIL格式
                    draw.rectangle(
                        (
                            max(0, img_x0 - padding),
                            max(0, img_y0 - padding),
                            min(bg_image.width, img_x1 + padding),
                            min(bg_image.height, img_y1 + padding)
                        ),
                        fill=fill_color
                    )
                    filtered_glyphs.append(glyph)
                else:
                    # 如果是字符串（如'white'）
                    draw.rectangle(
                        (
                            max(0, img_x0 - padding),
                            max(0, img_y0 - padding),
                            min(bg_image.width, img_x1 + padding),
                            min(bg_image.height, img_y1 + padding)
                        ),
                        fill=fill_color
                    )
                    filtered_glyphs.append(glyph)
        
        # 转换为base64
        img_buffer = io.BytesIO()
        bg_image.save(img_buffer, format='PNG')
        img_base64 = base64.b64encode(img_buffer.getvalue()).decode()
        
        return img_base64, filtered_glyphs

    def _is_complex_glyph_region(self, bg_image, img_bbox):
        x0, y0, x1, y1 = img_bbox
        if x1 <= x0 or y1 <= y0:
            return False
        x0 = max(0, x0)
        y0 = max(0, y0)
        x1 = min(bg_image.width, x1)
        y1 = min(bg_image.height, y1)
        width = x1 - x0
        height = y1 - y0
        if width * height < 64:
            return False
        step = max(1, min(width, height) // 6)
        samples = 0
        non_white = 0
        sum_r = sum_g = sum_b = 0.0
        sum_r2 = sum_g2 = sum_b2 = 0.0
        for y in range(y0, y1, step):
            for x in range(x0, x1, step):
                pixel = bg_image.getpixel((x, y))
                if not isinstance(pixel, tuple) or len(pixel) < 3:
                    continue
                r, g, b = pixel[:3]
                samples += 1
                sum_r += r
                sum_g += g
                sum_b += b
                sum_r2 += r * r
                sum_g2 += g * g
                sum_b2 += b * b
                if (r + g + b) / 3.0 < 245:
                    non_white += 1
        if samples < 12:
            return False
        non_white_ratio = non_white / samples
        mean_r = sum_r / samples
        mean_g = sum_g / samples
        mean_b = sum_b / samples
        var = (
            (sum_r2 / samples - mean_r * mean_r) +
            (sum_g2 / samples - mean_g * mean_g) +
            (sum_b2 / samples - mean_b * mean_b)
        )
        return non_white_ratio > 0.2 and var > 300

    def generate_text_layer(self, page, extractable_glyphs):
        """
        阶段3：生成文本层（HTML文本元素）
        
        将可提取的glyph转换为HTML文本元素，支持：
        - 文本选择
        - 文本复制
        - 文本搜索
        
        Args:
            page: PyMuPDF页面对象
            extractable_glyphs: 可提取的glyph列表
            
        Returns:
            list: HTML文本元素列表
        """
        text_html = []
        page_height = page.rect.height
        line_trackers = []

        def _get_line_tracker(line_bbox, fallback_bbox, font_size):
            use = line_bbox or fallback_bbox
            if not use:
                return None
            center_y = (use[1] + use[3]) / 2
            tolerance = max(1, (font_size or 0) * 0.6)
            for line in line_trackers:
                if abs(center_y - line["center_y"]) <= max(tolerance, line["tolerance"]):
                    return line
            line = {
                "center_y": center_y,
                "tolerance": tolerance,
                "right_edge": None,
                "last_type": None
            }
            line_trackers.append(line)
            return line

        def _text_script_type(text):
            if not text:
                return None
            has_ascii = False
            has_cjk = False
            for ch in text:
                code = ord(ch)
                if code <= 0x007F and not ch.isspace():
                    has_ascii = True
                elif code >= 0x2E80:
                    has_cjk = True
                if has_ascii and has_cjk:
                    return "mixed"
            if has_ascii:
                return "ascii"
            if has_cjk:
                return "cjk"
            return None

        def _estimate_text_width(text, font_size):
            if not text or not font_size:
                return 0.0
            total = 0.0
            for ch in text:
                code = ord(ch)
                if ch == " ":
                    total += font_size * 0.33
                elif code < 128:
                    total += font_size * 0.56
                else:
                    total += font_size * 1.0
            return total

        def _estimate_render_width(text, font_size, letter_spacing, word_spacing):
            base = _estimate_text_width(text, font_size)
            if not text:
                return base
            extra = 0.0
            if letter_spacing:
                extra += max(0, len(text) - 1) * letter_spacing
            if word_spacing:
                space_count = text.count(" ")
                if space_count:
                    extra += space_count * word_spacing
            return base + extra

        def _update_line_right_edge(line, run_bbox, text, font_size):
            if not line or not run_bbox:
                return
            run_width = run_bbox[2] - run_bbox[0]
            estimated = _estimate_text_width(text, font_size)
            right_by_estimate = run_bbox[0] + max(run_width, estimated)
            right_edge = line["right_edge"]
            line["right_edge"] = max(right_edge or right_by_estimate, right_by_estimate)
            current_type = _text_script_type(text)
            line["last_type"] = current_type or line["last_type"]

        def adjust_bbox_for_overlap(run_bbox, line_bbox, align_center, font_size, text):
            if not run_bbox or align_center:
                return run_bbox
            line = _get_line_tracker(line_bbox, run_bbox, font_size)
            if not line:
                return run_bbox
            right_edge = line["right_edge"]
            current_type = _text_script_type(text)
            last_type = line.get("last_type")
            allow_shift = (
                last_type in ("ascii", "cjk")
                and current_type in ("ascii", "cjk")
                and last_type != current_type
            )
            min_gap = max(0.5, (font_size or 0) * 0.08)
            if allow_shift and right_edge is not None and run_bbox[0] <= right_edge + 0.1:
                shift = right_edge - run_bbox[0] + min_gap
                run_bbox = [run_bbox[0] + shift, run_bbox[1], run_bbox[2] + shift, run_bbox[3]]
            _update_line_right_edge(line, run_bbox, text, font_size)
            return run_bbox
        
        for glyph in extractable_glyphs:
            text = glyph['text']
            bbox = glyph['bbox']
            font_size = glyph['font_size']
            font_name = glyph['font_name']
            r, g, b = glyph['color']
            
            apply_uppercase = glyph.get('force_uppercase', False)
            apply_small_caps = glyph.get('apply_small_caps', False)
            letter_spacing = glyph.get('letter_spacing')
            word_spacing = glyph.get('word_spacing')
            align_center = glyph.get('align_center', False)
            line_bbox = glyph.get('line_bbox')
            block_bbox = glyph.get('block_bbox')
            page_width = glyph.get('page_width')

            char_segments = self._split_span_to_char_segments(
                glyph.get('chars', [])
            )
            if char_segments:
                for segment in char_segments:
                    seg_text = segment["text"]
                    if seg_text == " ":
                        seg_text = NBSP_TOKEN
                    text_html.append(
                        self._build_text_span_html(
                            page,
                            seg_text,
                            segment["bbox"],
                            font_size,
                            font_name,
                            (r, g, b),
                            apply_uppercase,
                            apply_small_caps,
                            None,
                            None,
                            False,
                            line_bbox,
                            block_bbox,
                            page_width
                        )
                    )
                continue
            container_width = self._get_span_container_width(
                page,
                align_center,
                block_bbox,
                page_width
            )
            split_segments = []
            if not align_center:
                split_segments = self._split_span_by_double_spaces(
                    text,
                    glyph.get('chars', [])
                )
            if split_segments:
                for segment in split_segments:
                    segment_bbox = self._segment_bbox_from_chars(
                        segment["chars"],
                        bbox
                    )
                    segment_bbox = adjust_bbox_for_overlap(
                        segment_bbox,
                        line_bbox,
                        False,
                        font_size,
                        segment["text"]
                    )
                    text_html.append(
                        self._build_text_span_html(
                            page,
                            segment["text"],
                            segment_bbox,
                            font_size,
                            font_name,
                            (r, g, b),
                            apply_uppercase,
                            apply_small_caps,
                            letter_spacing,
                            word_spacing,
                            False,
                            line_bbox,
                            block_bbox,
                            page_width
                        )
                    )
                continue
            bbox = adjust_bbox_for_overlap(
                bbox,
                line_bbox,
                align_center,
                font_size,
                text
            )
            text_html.append(
                self._build_text_span_html(
                    page,
                    text,
                    bbox,
                    font_size,
                    font_name,
                    (r, g, b),
                    apply_uppercase,
                    apply_small_caps,
                    letter_spacing,
                    word_spacing,
                    align_center,
                    line_bbox,
                    block_bbox,
                    page_width
                )
            )
        
        return text_html
    
    def _generate_font_face_css(self, font_name: str, font_data: bytes, doc=None) -> str:
        """
        生成@font-face CSS声明
        
        Args:
            font_name: 字体名称（原始名称，可能包含前缀）
            font_data: 字体文件数据（原始格式）
            
        Returns:
            @font-face CSS字符串（包含原始名称和规范化名称的声明）
        """
        try:
            def _attempt_unicode_fix(raw_font: bytes) -> Optional[bytes]:
                if not self.pdf_path:
                    return None
                return self.font_fixer.fix_font_automatically(
                    raw_font,
                    self.pdf_path,
                    font_name,
                    doc
                )

            # 规范化字体名称
            normalized_name = self._normalize_font_name(font_name)
            font_data_to_use = font_data
            unicode_fix_failed = False
            
            # 首先尝试转换为WOFF格式（Web字体标准格式）
            woff_data = None
            font_format = None
            mime_type = None
            
            try:
                woff_data = self.font_handler.convert_to_woff(font_data_to_use)
                if woff_data and len(woff_data) > 0:
                    font_format = 'woff'
                    mime_type = 'font/woff'
            except Exception as e:
                # WOFF转换失败，这通常是因为字体是CID格式（Type0）
                # CID字体是PDF特有的格式，不是标准TrueType/OpenType
                # 这是正常的，我们会尝试其他方法
                error_msg = str(e)
                if "Unicode" in error_msg and ("缺少" in error_msg or "映射" in error_msg):
                    fixed_font = _attempt_unicode_fix(font_data_to_use)
                    if fixed_font:
                        font_data_to_use = fixed_font
                        try:
                            woff_data = self.font_handler.convert_to_woff(font_data_to_use)
                            if woff_data and len(woff_data) > 0:
                                font_format = 'woff'
                                mime_type = 'font/woff'
                                print(f"      ✓ 字体 {font_name} 已补充Unicode映射并转换为WOFF")
                        except Exception as e2:
                            woff_data = font_data_to_use
                            font_format = 'truetype'
                            mime_type = 'font/truetype'
                            print(f"      ✓ 字体 {font_name} 已补充Unicode映射，使用TrueType: {str(e2)[:50]}")
                    else:
                        unicode_fix_failed = True
                        print(f"      ✗ 字体 {font_name} 缺少Unicode映射且无法自动修复")
                elif "Not a TrueType" in error_msg or "bad sfntVersion" in error_msg:
                    # 这是CID字体，静默处理（不打印警告，因为这是预期的）
                    pass
                else:
                    print(f"提示: 字体 {font_name} WOFF转换失败: {e}")
            
            # 如果WOFF转换失败，检查是否是标准TrueType/OpenType格式
            if not woff_data:
                # 检查字体数据的前几个字节，判断格式
                if len(font_data_to_use) >= 4:
                    header = font_data_to_use[:4]
                    # TrueType: 0x00 01 00 00 或 'OTTO' (OpenType with CFF)
                    # OpenType: 'OTTO' 或 'ttcf' (TrueType Collection)
                    if header == b'\x00\x01\x00\x00' or font_data_to_use[:4] == b'OTTO' or font_data_to_use[:4] == b'ttcf':
                        if unicode_fix_failed:
                            print(f"      字体 {font_name} 缺少Unicode映射，将使用系统字体作为fallback")
                            return ""
                        # 看起来是TrueType/OpenType，直接使用
                        woff_data = font_data_to_use
                        font_format = 'truetype'
                        mime_type = 'font/truetype'
                    else:
                        # CID字体（Type0）浏览器无法直接使用
                        # 尝试从CID字体中提取嵌入的子字体
                        normalized_name = self._normalize_font_name(font_name)
                        print(f"提示: 字体 {font_name} ({normalized_name}) 是CID格式，尝试提取子字体...")
                        
                        try:
                            # 首先尝试使用FontForge转换（如果可用）
                            subfont_data = self.font_handler.try_extract_usable_font_from_cid_with_fontforge(font_data)
                            
                            # 如果FontForge不可用或失败，尝试其他方法
                            if not subfont_data:
                                subfont_data = self.font_handler.try_extract_usable_font_from_cid(font_data)
                            
                            if subfont_data and len(subfont_data) > 100:  # 确保子字体有合理的大小
                                print(f"      成功提取到子字体 ({len(subfont_data)} 字节)，尝试转换为WOFF...")
                                # 尝试转换子字体为WOFF
                                try:
                                    woff_data = self.font_handler.convert_to_woff(subfont_data)
                                    if woff_data and len(woff_data) > 0:
                                        font_format = 'woff'
                                        mime_type = 'font/woff'
                                        print(f"      成功！子字体已转换为WOFF格式 ({len(woff_data)} 字节)")
                                    else:
                                        raise ValueError("WOFF转换返回空数据")
                                except (ValueError, Exception) as e:
                                    # Unicode映射缺失，尝试从PDF提取ToUnicode映射并修复
                                    if "Unicode" in str(e) and ("缺少" in str(e) or "映射" in str(e)):
                                        print(f"      子字体缺少Unicode映射，尝试从PDF提取ToUnicode映射并修复...")
                                        # 尝试自动修复字体
                                        if self.pdf_path:
                                            fixed_font = self.font_fixer.fix_font_automatically(
                                                subfont_data,
                                                self.pdf_path,
                                                font_name,
                                                doc
                                            )
                                            if fixed_font:
                                                print(f"      ✓ 自动修复成功！已添加Unicode映射")
                                                # 使用修复后的字体
                                                try:
                                                    woff_data = self.font_handler.convert_to_woff(fixed_font)
                                                    if woff_data and len(woff_data) > 0:
                                                        font_format = 'woff'
                                                        mime_type = 'font/woff'
                                                        print(f"      ✓ 修复后的字体已转换为WOFF格式")
                                                    else:
                                                        woff_data = fixed_font
                                                        font_format = 'truetype'
                                                        mime_type = 'font/truetype'
                                                except Exception as e2:
                                                    woff_data = fixed_font
                                                    font_format = 'truetype'
                                                    mime_type = 'font/truetype'
                                            else:
                                                print(f"      ✗ 自动修复失败，将使用系统字体作为fallback")
                                                return ""
                                        else:
                                            print(f"      无法自动修复（缺少PDF路径），将使用系统字体作为fallback")
                                            return ""
                                    else:
                                        raise
                                except Exception as e:
                                    # WOFF转换失败，尝试直接使用子字体
                                    # 先验证子字体是否有效且有Unicode映射
                                    try:
                                        test_font = TTFont(io.BytesIO(subfont_data))
                                        # 检查是否有Unicode映射
                                        has_unicode = False
                                        if 'cmap' in test_font:
                                            cmap = test_font['cmap']
                                            for table in cmap.tables:
                                                if hasattr(table, 'cmap') and len(table.cmap) > 0:
                                                    has_unicode = True
                                                    break
                                        
                                        if not has_unicode:
                                            print(f"      子字体缺少Unicode映射，尝试从PDF提取ToUnicode映射并修复...")
                                            # 尝试自动修复字体
                                            if self.pdf_path:
                                                fixed_font = self.font_fixer.fix_font_automatically(
                                                    subfont_data,
                                                    self.pdf_path,
                                                    font_name,
                                                    doc
                                                )
                                                if fixed_font:
                                                    print(f"      ✓ 自动修复成功！已添加Unicode映射")
                                                    # 使用修复后的字体
                                                    try:
                                                        woff_data = self.font_handler.convert_to_woff(fixed_font)
                                                        if woff_data and len(woff_data) > 0:
                                                            font_format = 'woff'
                                                            mime_type = 'font/woff'
                                                            print(f"      ✓ 修复后的字体已转换为WOFF格式")
                                                        else:
                                                            woff_data = fixed_font
                                                            font_format = 'truetype'
                                                            mime_type = 'font/truetype'
                                                    except Exception as e2:
                                                        woff_data = fixed_font
                                                        font_format = 'truetype'
                                                        mime_type = 'font/truetype'
                                                else:
                                                    print(f"      ✗ 自动修复失败，将使用系统字体作为fallback")
                                                    return ""
                                            else:
                                                print(f"      无法自动修复（缺少PDF路径），将使用系统字体作为fallback")
                                                return ""
                                        
                                        woff_data = subfont_data
                                        font_format = 'truetype'
                                        mime_type = 'font/truetype'
                                        print(f"      子字体WOFF转换失败，使用TrueType格式: {str(e)[:50]}")
                                    except Exception as e2:
                                        print(f"      子字体无效，无法使用: {str(e2)[:50]}")
                                        return ""
                            else:
                                # 无法提取子字体
                                print(f"      无法提取子字体，将使用系统字体作为fallback")
                                return ""
                        except Exception as e:
                            print(f"      提取子字体时出错: {e}")
                            print(f"      将使用系统字体作为fallback")
                            return ""
            
            if not woff_data or len(woff_data) == 0:
                print(f"警告: 字体 {font_name} 数据无效，跳过")
                return ""
            
            # 转换为base64
            font_base64 = base64.b64encode(woff_data).decode('utf-8')
            
            # 生成@font-face声明
            # 如果规范化名称与原始名称不同，生成两个声明
            css = ""
            
            # 原始名称的声明
            css += f"""
@font-face {{
    font-family: '{font_name}';
    src: url('data:{mime_type};charset=utf-8;base64,{font_base64}') format('{font_format}');
    font-weight: normal;
    font-style: normal;
    font-display: swap;
}}"""
            
            # 如果规范化名称不同，添加别名声明
            if normalized_name != font_name:
                css += f"""
@font-face {{
    font-family: '{normalized_name}';
    src: url('data:{mime_type};charset=utf-8;base64,{font_base64}') format('{font_format}');
    font-weight: normal;
    font-style: normal;
    font-display: swap;
}}"""
            
            return css
        except Exception as e:
            print(f"警告: 处理字体 {font_name} 时出错: {e}")
            return ""
    
    def _extract_all_fonts(self, doc) -> dict:
        """
        从PDF文档中提取所有字体
        
        Args:
            doc: PyMuPDF文档对象
            
        Returns:
            dict: {字体名称: 字体数据} 的字典
        """
        fonts = {}
        
        # 收集所有页面的字体信息
        font_xrefs = {}  # {font_name: xref}
        
        for page_num in range(len(doc)):
            page = doc[page_num]
            font_list = page.get_fonts()
            
            for font_item in font_list:
                # font_item格式: (xref, ext, serif, basefont, name, type, encoding)
                font_name = font_item[3]  # basefont
                if not font_name:
                    font_name = font_item[4]  # name (备用)
                
                if font_name and font_name not in font_xrefs:
                    font_xrefs[font_name] = font_item[0]  # 保存xref
        
        # 从文档级别提取字体数据
        for font_name, xref in font_xrefs.items():
            try:
                # 使用doc.extract_font方法提取字体
                # extract_font返回tuple: (font_name, ext, type, font_data)
                font_result = doc.extract_font(xref)
                if font_result and isinstance(font_result, tuple) and len(font_result) >= 4:
                    font_data = font_result[3]  # 字体数据在最后一个位置
                    if font_data and isinstance(font_data, bytes) and len(font_data) > 0:
                        fonts[font_name] = font_data
                        print(f"提取字体: {font_name} (大小: {len(font_data)} 字节)")
                    else:
                        print(f"警告: 字体 {font_name} 数据为空或格式不正确")
                else:
                    print(f"警告: 字体 {font_name} 提取结果格式不正确: {type(font_result)}")
            except Exception as e:
                print(f"警告: 无法提取字体 {font_name}: {e}")
        
        return fonts
    
    def convert(self, pdf_path: str, output_path: str):
        """
        主转换流程：实现pdf2htmlEX的"glyph分流 + 双层渲染"模型
        
        流程：
        1. 打开PDF文档
        2. 提取所有字体并生成@font-face声明
        3. 对每一页：
           a. 提取可提取的glyph（阶段1）
           b. 渲染背景层（排除已提取的glyph）（阶段2）
           c. 生成文本层（阶段3）
           d. 双层叠加（阶段4）
        4. 输出HTML
        
        Args:
            pdf_path: PDF文件路径
            output_path: 输出HTML文件路径
        """
        # 打开PDF
        doc = fitz.open(pdf_path)
        # 保存PDF路径，用于字体修复
        self.pdf_path = pdf_path
        
        # 提取所有字体
        print("正在提取字体...")
        fonts_dict = self._extract_all_fonts(doc)
        
        # 生成字体CSS
        fonts_css = ""
        embedded_font_count = 0
        for font_name, font_data in fonts_dict.items():
            font_css = self._generate_font_face_css(font_name, font_data, doc)
            if font_css:  # 只统计实际生成的字体
                fonts_css += font_css
                embedded_font_count += 1
        
        # 生成HTML头部和样式
        html_parts = []
        html_parts.append("""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>PDF to HTML</title>
    <style>""")
        
        # 添加字体声明
        if fonts_css:
            html_parts.append(fonts_css)
        
        # 添加基础样式
        html_parts.append("""
        body { margin: 0; padding: 20px; background: #f0f0f0; }
        .page { 
            position: relative; 
            margin: 20px auto; 
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
            background: white;
        }
        /* 背景层：承载非文本内容（图形、图片、不可提取的glyph） */
        .bg-layer { 
            position: absolute; 
            top: 0; 
            left: 0; 
            z-index: 0; 
            pointer-events: none;
        }
        .bg-layer img { 
            display: block; 
            pointer-events: none;
        }
        /* 文本层：承载可提取的glyph（可复制、可搜索） */
        .text-layer { 
            position: absolute; 
            top: 0; 
            left: 0; 
            z-index: 1; 
            pointer-events: auto;
        }
        .text-block { 
            position: absolute; 
            white-space: pre;
            pointer-events: auto;
            user-select: text;
            -webkit-user-select: text;
            -moz-user-select: text;
            -ms-user-select: text;
            background: transparent;
            line-height: 1;
            margin: 0;
            padding: 0;
        }
        .text-block::selection {
            background: rgb(0, 123, 255) !important;
            color: #fff !important;
            -webkit-text-fill-color: #fff !important;
        }
        .text-block::-moz-selection {
            background: rgb(0, 123, 255) !important;
            color: #fff !important;
            -webkit-text-fill-color: #fff !important;
        }
    </style>
</head>
<body>
""")
        
        # 处理每一页
        for page_num in range(len(doc)):
            page = doc[page_num]
            
            # 获取页面尺寸
            page_rect = page.rect
            page_width = page_rect.width
            page_height = page_rect.height
            
            # 阶段1：提取可提取的glyph
            extractable_glyphs = self.extract_extractable_glyphs(page)
            
            # 阶段2：渲染背景层（排除已提取的glyph）
            bg_image_base64, filtered_glyphs = self.render_background_without_glyphs(
                page,
                extractable_glyphs
            )
            
            # 阶段3：生成文本层
            text_html = self.generate_text_layer(page, filtered_glyphs)
            
            # 阶段4：双层叠加
            # 计算背景图像显示尺寸（考虑DPI缩放）
            zoom = self.dpi / 72.0
            bg_display_width = page_width
            bg_display_height = page_height
            
            page_html = f"""
    <div class="page" style="width: {page_width}px; height: {page_height}px;">
        <!-- 背景层：非文本内容 -->
        <div class="bg-layer">
            <img src="data:image/png;base64,{bg_image_base64}" 
                 style="width: {bg_display_width}px; height: {bg_display_height}px;" />
        </div>
        <!-- 文本层：可提取的glyph -->
        <div class="text-layer">
            {''.join(text_html)}
        </div>
    </div>
"""
            html_parts.append(page_html)
        
        html_parts.append("""
<script>
(() => {
  const getLineOverlapShift = (el, all) => {
    const rect = el.getBoundingClientRect();
    const fontSize = parseFloat(el.style.fontSize || "0") || 0;
    let maxRight = -Infinity;
    for (const other of all) {
      if (other === el) {
        continue;
      }
      const orect = other.getBoundingClientRect();
      const sameLine = Math.abs((rect.top + rect.bottom) / 2 - (orect.top + orect.bottom) / 2) <= Math.max(1, fontSize * 0.6);
      if (!sameLine) {
        continue;
      }
      if (orect.left < rect.left && orect.right > maxRight) {
        maxRight = orect.right;
      }
    }
    if (maxRight !== -Infinity && maxRight > rect.left) {
      return maxRight - rect.left + 0.5;
    }
    return 0;
  };
  const adjust = () => {
    const allBlocks = Array.from(document.querySelectorAll('.text-block'));
    document.querySelectorAll('.text-block[data-letter-spaced="1"]').forEach((el) => {
      const currentLeft = parseFloat(el.style.left || "0");
      const shift = getLineOverlapShift(el, allBlocks);
      if (!Number.isNaN(currentLeft) && shift > 0) {
        el.style.left = `${currentLeft + shift}px`;
      }
    });
  };
  if (document.fonts && document.fonts.ready) {
    document.fonts.ready.then(() => requestAnimationFrame(adjust));
  } else {
    window.addEventListener('load', () => requestAnimationFrame(adjust));
  }
})();
</script>
</body></html>""")
        
        # 保存HTML
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(html_parts))
        
        doc.close()
        print(f"转换完成: {output_path}")
        if embedded_font_count > 0:
            print(f"已嵌入 {embedded_font_count} 个字体文件到HTML")
            if embedded_font_count < len(fonts_dict):
                failed_count = len(fonts_dict) - embedded_font_count
                print(f"注: 部分字体（{failed_count}个）由于ToUnicode映射不足，无法自动修复。")
                print(f"   这些字体将使用系统字体作为fallback显示。")
        elif fonts_dict:
            print(f"\n⚠️  字体显示说明:")
            print(f"   检测到 {len(fonts_dict)} 个CID格式字体，已尝试从PDF提取ToUnicode映射并自动修复。")
            print(f"   如果自动修复失败，HTML中已使用规范化字体名称和serif fallback，")
            print(f"   浏览器会使用系统字体作为fallback显示，确保文本可读。")


# 使用示例
if __name__ == '__main__':
    converter = SimplePDFConverter(dpi=150)
    converter.convert('input.pdf', 'output.html')
