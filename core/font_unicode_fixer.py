"""
字体Unicode映射修复工具
从PDF中提取ToUnicode映射并自动修复字体
"""
from fontTools.ttLib import TTFont, newTable
from fontTools.ttLib.tables._c_m_a_p import CmapSubtable
import io
import re
from typing import Dict, Optional, Set, Tuple
try:
    import pypdf
    HAS_PYPDF = True
except ImportError:
    HAS_PYPDF = False


class FontUnicodeFixer:
    """字体Unicode映射修复器"""
    
    def __init__(self):
        self.text_samples = {}  # {font_name: set of characters}
    
    def extract_tounicode_from_pypdf(self, pdf_path: str, font_name: str) -> Optional[Dict[int, int]]:
        """
        使用pypdf从PDF中提取ToUnicode映射
        
        Args:
            pdf_path: PDF文件路径
            font_name: 字体名称（可能包含前缀）
            
        Returns:
            {cid: unicode} 映射字典，其中cid是CID值（通常是glyph索引）
        """
        if not HAS_PYPDF:
            return None
        
        tounicode = {}
        
        try:
            with open(pdf_path, 'rb') as f:
                pdf_reader = pypdf.PdfReader(f)
                
                # 规范化字体名称用于匹配
                normalized_name = font_name
                if '+' in font_name:
                    normalized_name = font_name.split('+', 1)[1]
                
                # 遍历所有页面
                for page_num, page in enumerate(pdf_reader.pages):
                    # 获取页面资源
                    if '/Resources' not in page:
                        continue
                    
                    resources = page['/Resources']
                    if '/Font' not in resources:
                        continue
                    
                    fonts = resources['/Font']
                    
                    # 查找匹配的字体
                    for font_ref, font_obj in fonts.items():
                        if isinstance(font_obj, pypdf.generic.IndirectObject):
                            font_obj = font_obj.get_object()
                        
                        # 检查字体名称（支持前缀匹配）
                        base_font = font_obj.get('/BaseFont', '')
                        base_font_clean = base_font.lstrip('/')
                        
                        # 匹配字体名称
                        if (font_name in base_font_clean or 
                            base_font_clean in font_name or
                            normalized_name in base_font_clean or
                            base_font_clean in normalized_name):
                            
                            # 查找ToUnicode
                            if '/ToUnicode' in font_obj:
                                to_unicode_stream = font_obj['/ToUnicode']
                                if isinstance(to_unicode_stream, pypdf.generic.IndirectObject):
                                    to_unicode_stream = to_unicode_stream.get_object()
                                
                                # 解析ToUnicode流
                                if hasattr(to_unicode_stream, 'get_data'):
                                    data = to_unicode_stream.get_data()
                                    mapping = self._parse_tounicode_cmap(data)
                                    if mapping:
                                        tounicode.update(mapping)
                                        print(f"        从PDF提取到 {len(mapping)} 个ToUnicode映射")
                                        
        except Exception as e:
            print(f"        从PDF提取ToUnicode时出错: {e}")
            import traceback
            traceback.print_exc()
        
        return tounicode if tounicode else None
    
    def _parse_tounicode_cmap(self, data: bytes) -> Dict[int, int]:
        """
        解析ToUnicode CMap数据（PostScript格式）
        
        Args:
            data: CMap流数据
            
        Returns:
            {cid: unicode} 映射，其中cid是CID值
        """
        mapping = {}
        
        try:
            # 将字节数据转换为字符串
            text = data.decode('latin-1', errors='ignore')
            # 去掉注释，避免干扰正则匹配
            text = re.sub(r'%.*', '', text)

            def _hex_to_int(hex_str: str) -> Optional[int]:
                cleaned = re.sub(r'\s+', '', hex_str)
                if not cleaned:
                    return None
                try:
                    return int(cleaned, 16)
                except ValueError:
                    return None

            def _decode_unicode_hex(hex_str: str) -> Optional[int]:
                cleaned = re.sub(r'\s+', '', hex_str)
                if not cleaned:
                    return None
                try:
                    data_bytes = bytes.fromhex(cleaned)
                except ValueError:
                    return None
                if not data_bytes:
                    return None
                # ToUnicode通常是UTF-16BE编码
                try:
                    decoded = data_bytes.decode('utf-16-be')
                except UnicodeDecodeError:
                    decoded = data_bytes.decode('utf-8', errors='ignore')
                if not decoded:
                    return None
                # 只能映射单字符；多字符用首字符降级处理
                return ord(decoded[0])
            
            # 解析bfchar块
            # 格式: N beginbfchar
            #       <src1> <dst1>
            #       <src2> <dst2>
            #       ...
            #       endbfchar
            bfchar_pattern = r'(\d+)\s+beginbfchar\s+(.*?)endbfchar'
            for match in re.finditer(bfchar_pattern, text, re.DOTALL):
                entries_text = match.group(2)
                # 匹配每个条目: <src> <dst>
                entries = re.findall(r'<([0-9A-Fa-f\s]+)>\s+<([0-9A-Fa-f\s]+)>', entries_text)
                for src_hex, dst_hex in entries:
                    cid = _hex_to_int(src_hex)
                    unicode_val = _decode_unicode_hex(dst_hex)
                    if cid is None or unicode_val is None:
                        continue
                    mapping[cid] = unicode_val
            
            # 解析bfrange块
            # 格式1: N beginbfrange
            #        <src1> <src2> <dst>
            #        ...
            #        endbfrange
            bfrange_pattern = r'(\d+)\s+beginbfrange\s+(.*?)endbfrange'
            for match in re.finditer(bfrange_pattern, text, re.DOTALL):
                entries_text = match.group(2)
                # 先处理数组格式: <src1> <src2> [<dst1> <dst2> ...]
                bfrange_array_pattern = r'<([0-9A-Fa-f\s]+)>\s+<([0-9A-Fa-f\s]+)>\s+\[(.*?)\]'
                for arr_match in re.finditer(bfrange_array_pattern, entries_text, re.DOTALL):
                    start_hex, end_hex, dst_array = arr_match.groups()
                    start_cid = _hex_to_int(start_hex)
                    end_cid = _hex_to_int(end_hex)
                    if start_cid is None or end_cid is None:
                        continue
                    dst_values = re.findall(r'<([0-9A-Fa-f\s]+)>', dst_array)
                    for i, cid in enumerate(range(start_cid, end_cid + 1)):
                        if i >= len(dst_values):
                            break
                        unicode_val = _decode_unicode_hex(dst_values[i])
                        if unicode_val is None:
                            continue
                        mapping[cid] = unicode_val

                # 再处理连续区间格式: <src1> <src2> <dst_start>
                entries_text = re.sub(bfrange_array_pattern, '', entries_text, flags=re.DOTALL)
                entries = re.findall(
                    r'<([0-9A-Fa-f\s]+)>\s+<([0-9A-Fa-f\s]+)>\s+<([0-9A-Fa-f\s]+)>',
                    entries_text
                )
                for start_hex, end_hex, dst_start_hex in entries:
                    start_cid = _hex_to_int(start_hex)
                    end_cid = _hex_to_int(end_hex)
                    dst_start = _decode_unicode_hex(dst_start_hex)
                    if start_cid is None or end_cid is None or dst_start is None:
                        continue
                    for i, cid in enumerate(range(start_cid, end_cid + 1)):
                        mapping[cid] = dst_start + i
                    
        except Exception as e:
            print(f"        解析ToUnicode CMap时出错: {e}")
            import traceback
            traceback.print_exc()
        
        return mapping
    
    def fix_font_with_tounicode(
        self, 
        font_data: bytes, 
        cid_to_unicode: Dict[int, int]
    ) -> Optional[bytes]:
        """
        使用ToUnicode映射修复字体
        
        Args:
            font_data: 字体数据
            cid_to_unicode: {cid: unicode} 映射
            
        Returns:
            修复后的字体数据，如果失败则返回None
        """
        if not cid_to_unicode:
            return None
        
        try:
            font = TTFont(io.BytesIO(font_data))
            
            # 获取glyph顺序
            glyph_order = font.getGlyphOrder()
            
            # 获取或创建cmap表
            if 'cmap' not in font:
                font['cmap'] = newTable('cmap')
                font['cmap'].tableVersion = 0
                font['cmap'].tables = []
            
            cmap = font['cmap']
            
            # 查找或创建Windows Unicode表（平台ID 3, 编码ID 1）
            unicode_table = None
            for table in cmap.tables:
                if table.platformID == 3 and table.platEncID == 1:
                    unicode_table = table
                    break
            
            if not unicode_table:
                # 创建新的Unicode cmap表（格式4）
                unicode_table = CmapSubtable.newSubtable(4)
                unicode_table.platformID = 3
                unicode_table.platEncID = 1
                unicode_table.language = 0
                unicode_table.cmap = {}
                cmap.tables.append(unicode_table)
            
            # 将CID映射转换为Unicode到glyph名称的映射
            # CID字体的glyph名称通常是 "Identity.XX" 格式，其中XX是CID值
            # 注意：CID值可能很大（如2042），但glyph_order中可能没有对应的glyph
            # 这种情况下，我们需要创建或使用最接近的glyph
            
            mapping_count = 0
            max_unicode = 0xFFFF  # cmap格式4支持的最大值
            
            # 统计glyph_order中的Identity格式glyph
            identity_glyphs = {g: g for g in glyph_order if g.startswith('Identity.')}
            
            for cid, unicode_val in cid_to_unicode.items():
                # 跳过超出范围的Unicode值（cmap格式4只支持0-65535）
                if unicode_val > max_unicode:
                    continue
                
                # 跳过控制字符和无效字符
                if unicode_val < 0x20 or (0x7F <= unicode_val <= 0x9F):
                    continue
                
                # 尝试找到对应的glyph
                glyph_name = None
                
                # 方法1: 使用Identity.XX格式（CID字体标准格式）
                # CID字体的glyph名称格式是 Identity.XX，其中XX是CID值
                identity_name = f'Identity.{cid}'
                if identity_name in glyph_order:
                    glyph_name = identity_name
                
                # 方法2: 如果Identity.XX不存在，尝试查找最接近的Identity glyph
                # 某些CID字体可能只包含部分glyph，我们需要使用最接近的
                if not glyph_name:
                    # 尝试查找包含相同CID值的其他格式
                    for gname in glyph_order:
                        # 检查glyph名称是否包含CID值
                        if f'.{cid}' in gname or gname.endswith(f'.{cid}'):
                            if 'Identity' in gname:
                                glyph_name = gname
                                break
                
                # 方法3: 如果仍然找不到，且CID值在glyph_order范围内
                # 尝试使用索引（虽然不推荐，但作为最后手段）
                if not glyph_name and cid < len(glyph_order):
                    candidate = glyph_order[cid]
                    # 只使用Identity格式的glyph
                    if candidate and candidate.startswith('Identity.'):
                        glyph_name = candidate
                
                # 如果找到了glyph，添加映射
                if glyph_name:
                    unicode_table.cmap[unicode_val] = glyph_name
                    mapping_count += 1
                else:
                    # 调试信息：记录未找到glyph的CID
                    if 0x41 <= unicode_val <= 0x5A:  # 大写字母
                        char = chr(unicode_val)
                        print(f"        警告: 未找到CID {cid} (字符 '{char}') 对应的glyph")

            # 启发式检测：大量小写映射到与大写相同的glyph时，说明映射可能错误
            lower_upper_pairs = 0
            lower_upper_same_glyph = 0
            for code in range(ord('a'), ord('z') + 1):
                lower_glyph = unicode_table.cmap.get(code)
                upper_glyph = unicode_table.cmap.get(code - 32)
                if lower_glyph and upper_glyph:
                    lower_upper_pairs += 1
                    if lower_glyph == upper_glyph:
                        lower_upper_same_glyph += 1
            if lower_upper_pairs > 0:
                min_bad = max(3, lower_upper_pairs // 2)
                if lower_upper_same_glyph >= min_bad:
                    print(
                        "        警告: 小写/大写映射到相同glyph过多，判定映射异常，放弃修复字体"
                    )
                    return None
            
            # 如果创建了足够的映射，保存字体
            # 装饰性字体可能glyph数量很少，适当降低门槛
            min_required = max(1, min(10, len(glyph_order) // 4))
            if mapping_count >= min_required:
                print(f"        成功创建 {mapping_count} 个Unicode映射")
                try:
                    output = io.BytesIO()
                    font.save(output)
                    return output.getvalue()
                except Exception as e:
                    print(f"        保存字体时出错: {e}")
                    # 如果格式4失败，尝试使用格式12（支持32位Unicode）
                    try:
                        # 创建格式12的cmap表
                        unicode_table_12 = CmapSubtable.newSubtable(12)
                        unicode_table_12.platformID = 3
                        unicode_table_12.platEncID = 10  # 格式12使用编码ID 10
                        unicode_table_12.language = 0
                        unicode_table_12.cmap = {}
                        
                        # 重新添加所有映射（包括超出范围的）
                        for cid, unicode_val in cid_to_unicode.items():
                            if unicode_val < 0x20 or (0x7F <= unicode_val <= 0x9F):
                                continue
                            glyph_name = None
                            if cid < len(glyph_order):
                                glyph_name = glyph_order[cid]
                            if glyph_name and not glyph_name.startswith('.notdef'):
                                unicode_table_12.cmap[unicode_val] = glyph_name
                        
                        # 移除格式4的表，添加格式12的表
                        cmap.tables = [t for t in cmap.tables if not (t.platformID == 3 and t.platEncID == 1)]
                        cmap.tables.append(unicode_table_12)
                        
                        output = io.BytesIO()
                        font.save(output)
                        print(f"        使用cmap格式12保存字体（支持32位Unicode）")
                        return output.getvalue()
                    except Exception as e2:
                        print(f"        使用格式12也失败: {e2}")
                        return None
            else:
                print(f"        警告: 只创建了 {mapping_count} 个Unicode映射，需要至少{min_required}个")
                return None
            
        except Exception as e:
            print(f"        修复字体时出错: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def fix_font_automatically(
        self, 
        font_data: bytes, 
        pdf_path: str,
        font_name: str,
        doc
    ) -> Optional[bytes]:
        """
        自动修复字体：从PDF提取ToUnicode映射并修复
        
        Args:
            font_data: 字体数据
            pdf_path: PDF文件路径
            font_name: 字体名称
            doc: PyMuPDF文档对象（未使用，保留接口兼容）
            
        Returns:
            修复后的字体数据，如果失败则返回None
        """
        # 步骤1: 从PDF提取ToUnicode映射
        cid_to_unicode = self.extract_tounicode_from_pypdf(pdf_path, font_name)
        
        if not cid_to_unicode:
            print(f"        无法从PDF提取ToUnicode映射")
            return None
        
        # 步骤2: 使用ToUnicode映射修复字体
        fixed_font = self.fix_font_with_tounicode(font_data, cid_to_unicode)
        
        return fixed_font
