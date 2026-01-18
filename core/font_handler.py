"""
字体处理模块
"""
from fontTools.ttLib import TTFont
from fontTools.subset import Subsetter
try:
    from fontTools.cffLib import TopDict, FontDict
    HAS_CFF = True
except ImportError:
    HAS_CFF = False
import io
import struct
import re
from pathlib import Path
from typing import Dict, Set, Optional, List, Tuple


class FontHandler:
    """字体处理器"""
    
    def extract_fonts_from_pdf(self, doc) -> Dict[str, bytes]:
        """从 PDF 提取字体"""
        fonts = {}
        
        for page_num in range(len(doc)):
            page = doc[page_num]
            font_list = page.get_fonts()
            
            for font_item in font_list:
                font_name = font_item[3]
                if font_name not in fonts:
                    try:
                        font_data = page.get_font_data(font_item[0])
                        if font_data:
                            fonts[font_name] = font_data
                    except Exception as e:
                        print(f"警告: 无法提取字体 {font_name}: {e}")
        
        return fonts
    
    def subset_font(self, font_data: bytes, used_chars: Optional[Set[str]] = None) -> bytes:
        """
        字体子集化
        
        Args:
            font_data: 字体文件数据
            used_chars: 使用的字符集合（可选）
        
        Returns:
            子集化后的字体数据
        """
        if not font_data:
            return None
        
        try:
            font = TTFont(io.BytesIO(font_data))
            subsetter = Subsetter()
            
            # TODO: 根据 used_chars 设置要保留的字形
            # 目前简化处理，保留所有字形
            # subsetter.populate(glyphs=glyph_names)
            
            subsetter.subset(font)
            
            output = io.BytesIO()
            font.save(output)
            return output.getvalue()
            
        except Exception as e:
            print(f"警告: 字体子集化失败: {e}")
            return font_data  # 失败时返回原字体
    
    def convert_to_woff(self, font_data: bytes) -> bytes:
        """
        转换为 WOFF 格式
        
        Args:
            font_data: 字体文件数据
        
        Returns:
            WOFF 格式的字体数据，如果转换失败则抛出异常（不返回原格式）
        """
        if not font_data:
            raise ValueError("字体数据为空")
        
        # 尝试加载字体
        font = TTFont(io.BytesIO(font_data))
        
        # 验证字体是否有Unicode映射（cmap表）
        # 浏览器需要Unicode格式的cmap（平台ID 0或3）
        has_unicode_mapping = False
        if 'cmap' in font:
            cmap = font['cmap']
            for table in cmap.tables:
                # 检查Unicode格式的cmap（平台ID 0 = Unicode, 平台ID 3 = Windows Unicode）
                if table.platformID in (0, 3) and hasattr(table, 'cmap'):
                    # 检查是否有实际的字符映射（排除只有控制字符的情况）
                    if len(table.cmap) > 0:
                        # 检查是否有非控制字符的映射
                        for unicode_val, glyph_name in table.cmap.items():
                            # 排除控制字符（0x00-0x1F, 0x7F-0x9F）和null字符
                            if unicode_val > 0x1F and (unicode_val < 0x7F or unicode_val > 0x9F):
                                has_unicode_mapping = True
                                break
                        if has_unicode_mapping:
                            break
        
        if not has_unicode_mapping:
            raise ValueError("字体缺少有效的Unicode字符映射，浏览器无法正确显示文本")
        
        output = io.BytesIO()
        font.flavor = 'woff'
        font.save(output)
        return output.getvalue()
    
    def convert_to_woff2(self, font_data: bytes) -> bytes:
        """
        转换为 WOFF2 格式（体积更小）
        
        需要安装 brotli: pip install brotli
        """
        try:
            from fontTools.woff2 import compress
            return compress(font_data)
        except ImportError:
            print("警告: 未安装 brotli，降级到 WOFF")
            return self.convert_to_woff(font_data)
        except Exception as e:
            print(f"警告: WOFF2 转换失败: {e}")
            return self.convert_to_woff(font_data)
    
    def extract_subfonts_from_cid(self, cid_font_data: bytes) -> List[bytes]:
        """
        从CID字体中提取嵌入的子字体（TrueType/OpenType）
        
        CID字体（Type0）可能包含：
        1. CFF格式的字体数据（需要转换为TrueType）
        2. 嵌入的TrueType/OpenType子字体
        3. 混合格式
        
        Args:
            cid_font_data: CID字体数据
            
        Returns:
            提取到的子字体列表（bytes格式）
        """
        subfonts = []
        
        if not cid_font_data or len(cid_font_data) < 4:
            return subfonts
        
        # 方法1: 检查数据开头是否已经是TrueType/OpenType格式
        header = cid_font_data[:4]
        if header == b'\x00\x01\x00\x00' or header == b'OTTO' or header == b'ttcf':
            try:
                TTFont(io.BytesIO(cid_font_data))
                subfonts.append(cid_font_data)
                return subfonts
            except:
                pass
        
        # 方法2: 尝试解析CID字体结构，查找嵌入的字体数据
        # CID字体通常包含CFF数据，但也可能包含嵌入的TrueType字体
        
        # 2.1: 在整个数据中搜索TrueType/OpenType签名（更精确的方法）
        data = cid_font_data
        signatures = [
            (b'\x00\x01\x00\x00', 'TrueType'),
            (b'OTTO', 'OpenType-CFF'),
            (b'ttcf', 'TrueType-Collection'),
        ]
        
        for sig_bytes, sig_name in signatures:
            pos = 0
            while True:
                pos = data.find(sig_bytes, pos)
                if pos == -1:
                    break
                
                try:
                    if sig_bytes == b'\x00\x01\x00\x00':
                        # TrueType字体，需要读取完整的sfnt表结构
                        if pos + 12 <= len(data):
                            num_tables = struct.unpack('>H', data[pos+4:pos+6])[0]
                            if 0 < num_tables < 100:
                                # 计算字体大小：读取所有表的offset和length
                                max_offset = 0
                                for i in range(num_tables):
                                    table_offset = pos + 12 + i * 16
                                    if table_offset + 12 <= len(data):
                                        offset = struct.unpack('>I', data[table_offset+8:table_offset+12])[0]
                                        length = struct.unpack('>I', data[table_offset+12:table_offset+16])[0]
                                        max_offset = max(max_offset, offset + length)
                                
                                if max_offset > 0 and pos + max_offset <= len(data):
                                    subfont = data[pos:pos+max_offset]
                                    try:
                                        font = TTFont(io.BytesIO(subfont))
                                        subfonts.append(subfont)
                                        pos = pos + max_offset
                                        continue
                                    except:
                                        pass
                    elif sig_bytes == b'OTTO':
                        # OpenType with CFF，尝试提取完整字体
                        # 需要解析sfnt表来确定大小
                        if pos + 12 <= len(data):
                            num_tables = struct.unpack('>H', data[pos+4:pos+6])[0]
                            if 0 < num_tables < 100:
                                max_offset = 0
                                for i in range(num_tables):
                                    table_offset = pos + 12 + i * 16
                                    if table_offset + 12 <= len(data):
                                        offset = struct.unpack('>I', data[table_offset+8:table_offset+12])[0]
                                        length = struct.unpack('>I', data[table_offset+12:table_offset+16])[0]
                                        max_offset = max(max_offset, offset + length)
                                
                                if max_offset > 0 and pos + max_offset <= len(data):
                                    subfont = data[pos:pos+max_offset]
                                    try:
                                        font = TTFont(io.BytesIO(subfont))
                                        subfonts.append(subfont)
                                        pos = pos + max_offset
                                        continue
                                    except:
                                        pass
                    elif sig_bytes == b'ttcf':
                        # TrueType Collection，包含多个字体
                        # 简化处理：尝试提取整个TTC
                        if pos + 12 <= len(data):
                            version = struct.unpack('>I', data[pos+4:pos+8])[0]
                            num_fonts = struct.unpack('>I', data[pos+8:pos+12])[0]
                            if 0 < num_fonts < 100:
                                # 读取最后一个字体的offset来确定大小
                                last_offset_pos = pos + 12 + (num_fonts - 1) * 4
                                if last_offset_pos + 4 <= len(data):
                                    last_offset = struct.unpack('>I', data[last_offset_pos:last_offset_pos+4])[0]
                                    # 估算大小（简化处理）
                                    estimated_size = min(len(data) - pos, last_offset + 100000)
                                    subfont = data[pos:pos+estimated_size]
                                    try:
                                        font = TTFont(io.BytesIO(subfont))
                                        subfonts.append(subfont)
                                        break
                                    except:
                                        pass
                except Exception as e:
                    pass
                
                pos += 1
        
        # 方法3: 如果CID字体是CFF格式，尝试直接使用（虽然浏览器可能不支持）
        # 但至少尝试一下
        if not subfonts:
            # 检查是否是CFF格式（CID字体常见格式）
            if data[:4] == b'\x01\x00\x04\x04' or b'%!PS' in data[:100]:
                # 这是CFF或PostScript格式，浏览器无法直接使用
                # 但我们可以尝试提取其中的二进制数据
                pass
        
        return subfonts
    
    def try_extract_usable_font_from_cid_with_fontforge(self, cid_font_data: bytes) -> Optional[bytes]:
        """
        使用FontForge尝试转换CID字体（如果可用）
        
        Args:
            cid_font_data: CID字体数据
            
        Returns:
            转换后的字体数据，如果失败则返回None
        """
        try:
            import sys
            sys.path.insert(0, str(Path(__file__).parent.parent / 'utils'))
            from fontforge_converter import FontForgeConverter
            
            converter = FontForgeConverter()
            if converter.fontforge_available:
                woff_data = converter.convert_cid_to_woff(cid_font_data)
                return woff_data
        except ImportError:
            pass
        except Exception as e:
            print(f"      FontForge转换失败: {e}")
        
        return None
    
    def try_extract_usable_font_from_cid(self, cid_font_data: bytes) -> Optional[bytes]:
        """
        尝试从CID字体中提取可用的子字体
        
        使用多种方法：
        1. 搜索嵌入的TrueType/OpenType字体
        2. 尝试解析CFF格式
        3. 使用启发式方法查找字体数据
        
        Args:
            cid_font_data: CID字体数据
            
        Returns:
            提取到的可用字体数据，如果失败则返回None
        """
        # 方法1: 尝试提取子字体
        subfonts = self.extract_subfonts_from_cid(cid_font_data)
        
        if subfonts:
            # 返回第一个有效的子字体
            for subfont in subfonts:
                try:
                    # 验证字体是否有效
                    font = TTFont(io.BytesIO(subfont))
                    if len(subfont) > 1000:  # 确保字体有合理的大小
                        return subfont
                except:
                    continue
        
        # 方法2: 尝试使用更智能的搜索
        # 查找可能包含字体数据的区域
        data = cid_font_data
        
        # 查找常见的字体表签名
        font_table_signatures = [
            b'cmap', b'head', b'hhea', b'hmtx', b'maxp',
            b'name', b'OS/2', b'post', b'glyf', b'loca',
            b'CFF ', b'CFF2', b'fpgm', b'prep', b'cvt '
        ]
        
        # 查找这些签名出现的位置
        found_positions = []
        for sig in font_table_signatures:
            pos = data.find(sig)
            if pos != -1:
                found_positions.append((pos, sig))
        
        if found_positions:
            # 找到了一些字体表，尝试从最早的位置开始提取
            found_positions.sort()
            start_pos = found_positions[0][0]
            
            # 尝试向前查找TrueType头部
            for check_pos in range(max(0, start_pos - 100), start_pos):
                if check_pos + 4 <= len(data):
                    sig = data[check_pos:check_pos+4]
                    if struct.unpack('>I', sig)[0] == 0x00010000:
                        # 找到TrueType头部，尝试提取
                        try:
                            if check_pos + 12 <= len(data):
                                num_tables = struct.unpack('>H', data[check_pos+4:check_pos+6])[0]
                                if 0 < num_tables < 100:
                                    # 计算完整字体大小
                                    max_offset = 0
                                    for i in range(num_tables):
                                        table_pos = check_pos + 12 + i * 16
                                        if table_pos + 16 <= len(data):
                                            offset = struct.unpack('>I', data[table_pos+8:table_pos+12])[0]
                                            length = struct.unpack('>I', data[table_pos+12:table_pos+16])[0]
                                            max_offset = max(max_offset, offset + length)
                                    
                                    if max_offset > 0 and check_pos + max_offset <= len(data):
                                        subfont = data[check_pos:check_pos+max_offset]
                                        try:
                                            font = TTFont(io.BytesIO(subfont))
                                            if len(subfont) > 1000:
                                                return subfont
                                        except:
                                            pass
                        except:
                            pass
        
        # 方法3: 如果数据很小，可能是提取不完整，尝试使用完整数据
        # （某些情况下，CID字体数据本身就是可用的，只是格式特殊）
        if len(cid_font_data) > 1000:
            # 尝试直接使用，看看是否是某种可解析的格式
            try:
                # 检查是否包含可识别的字体结构
                # 这里简化处理，如果数据足够大，尝试解析
                pass
            except:
                pass
        
        # 如果所有方法都失败，返回None
        return None

