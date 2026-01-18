"""
CID字体分析工具
用于分析和提取CID字体中的可用数据
"""
import fitz
import struct
import io
from typing import Dict, List, Optional, Tuple
from fontTools.ttLib import TTFont


class CIDFontAnalyzer:
    """CID字体分析器"""
    
    def __init__(self, pdf_path: str):
        self.doc = fitz.open(pdf_path)
        self.fonts_info = []
    
    def analyze_all_fonts(self) -> List[Dict]:
        """
        分析PDF中的所有字体
        
        Returns:
            字体信息列表
        """
        fonts_info = []
        
        for page_num in range(len(self.doc)):
            page = self.doc[page_num]
            font_list = page.get_fonts()
            
            for font_item in font_list:
                font_name = font_item[3]
                font_type = font_item[2]  # Type0, TrueType等
                font_ext = font_item[1]  # cid, ttf等
                
                # 检查是否已分析过
                if any(f['name'] == font_name for f in fonts_info):
                    continue
                
                try:
                    result = self.doc.extract_font(font_item[0])
                    if result and isinstance(result, tuple) and len(result) >= 4:
                        font_data = result[3]
                        
                        info = {
                            'name': font_name,
                            'type': font_type,
                            'ext': font_ext,
                            'size': len(font_data),
                            'data': font_data,
                            'analysis': self._analyze_font_data(font_data)
                        }
                        fonts_info.append(info)
                except Exception as e:
                    print(f"分析字体 {font_name} 时出错: {e}")
        
        self.fonts_info = fonts_info
        return fonts_info
    
    def _analyze_font_data(self, font_data: bytes) -> Dict:
        """
        分析字体数据的结构
        
        Returns:
            分析结果字典
        """
        analysis = {
            'format': 'unknown',
            'has_ttf_signature': False,
            'has_otf_signature': False,
            'has_cff_signature': False,
            'potential_fonts': [],
            'recommendations': []
        }
        
        if not font_data or len(font_data) < 4:
            return analysis
        
        header = font_data[:4]
        
        # 检查TrueType签名
        if struct.unpack('>I', header)[0] == 0x00010000:
            analysis['format'] = 'TrueType'
            analysis['has_ttf_signature'] = True
            analysis['recommendations'].append('可以直接使用，转换为WOFF格式')
            return analysis
        
        # 检查OpenType签名
        if header == b'OTTO':
            analysis['format'] = 'OpenType-CFF'
            analysis['has_otf_signature'] = True
            analysis['recommendations'].append('可以直接使用，转换为WOFF格式')
            return analysis
        
        # 检查CFF签名（CID字体常见）
        if header == b'\x01\x00\x04\x04' or b'%!PS' in font_data[:100]:
            analysis['format'] = 'CFF/CID'
            analysis['has_cff_signature'] = True
            analysis['recommendations'].append('需要使用FontForge转换为TrueType/OpenType')
            analysis['recommendations'].append('或使用mutool extract提取后手动转换')
        
        # 搜索嵌入的字体
        potential_fonts = self._search_embedded_fonts(font_data)
        analysis['potential_fonts'] = potential_fonts
        
        if potential_fonts:
            analysis['recommendations'].append(f'找到 {len(potential_fonts)} 个潜在的嵌入字体')
        
        return analysis
    
    def _search_embedded_fonts(self, data: bytes) -> List[Dict]:
        """
        搜索数据中嵌入的字体
        
        Returns:
            找到的潜在字体列表
        """
        found_fonts = []
        
        # 搜索TrueType签名
        pos = 0
        while True:
            pos = data.find(b'\x00\x01\x00\x00', pos)
            if pos == -1:
                break
            
            try:
                if pos + 12 <= len(data):
                    num_tables = struct.unpack('>H', data[pos+4:pos+6])[0]
                    if 0 < num_tables < 100:
                        # 计算字体大小
                        max_offset = 0
                        for i in range(num_tables):
                            table_pos = pos + 12 + i * 16
                            if table_pos + 16 <= len(data):
                                offset = struct.unpack('>I', data[table_pos+8:table_pos+12])[0]
                                length = struct.unpack('>I', data[table_pos+12:table_pos+16])[0]
                                max_offset = max(max_offset, offset + length)
                        
                        if max_offset > 0 and max_offset < len(data):
                            font_size = max_offset
                            # 验证大小是否合理
                            if font_size > 100 and pos + font_size <= len(data):
                                font_data = data[pos:pos+font_size]
                                # 验证是否是有效的TrueType字体
                                try:
                                    TTFont(io.BytesIO(font_data))
                                    found_fonts.append({
                                        'position': pos,
                                        'size': font_size,
                                        'type': 'TrueType',
                                        'data': font_data
                                    })
                                    pos += font_size
                                    continue
                                except:
                                    pass
            except:
                pass
            
            pos += 1
        
        # 搜索OpenType签名
        pos = 0
        while True:
            pos = data.find(b'OTTO', pos)
            if pos == -1:
                break
            
            try:
                if pos + 12 <= len(data):
                    num_tables = struct.unpack('>H', data[pos+4:pos+6])[0]
                    if 0 < num_tables < 100:
                        max_offset = 0
                        for i in range(num_tables):
                            table_pos = pos + 12 + i * 16
                            if table_pos + 16 <= len(data):
                                offset = struct.unpack('>I', data[table_pos+8:table_pos+12])[0]
                                length = struct.unpack('>I', data[table_pos+12:table_pos+16])[0]
                                max_offset = max(max_offset, offset + length)
                        
                        if max_offset > 0 and max_offset < len(data):
                            font_size = max_offset
                            # 验证大小是否合理
                            if font_size > 100 and pos + font_size <= len(data):
                                font_data = data[pos:pos+font_size]
                                # 验证是否是有效的OpenType字体
                                try:
                                    TTFont(io.BytesIO(font_data))
                                    found_fonts.append({
                                        'position': pos,
                                        'size': font_size,
                                        'type': 'OpenType',
                                        'data': font_data
                                    })
                                    pos += font_size
                                    continue
                                except:
                                    pass
            except:
                pass
            
            pos += 1
        
        return found_fonts
    
    def generate_report(self) -> str:
        """
        生成分析报告
        
        Returns:
            报告文本
        """
        report = ["=" * 60]
        report.append("CID字体分析报告")
        report.append("=" * 60)
        report.append("")
        
        for i, font_info in enumerate(self.fonts_info, 1):
            report.append(f"字体 {i}: {font_info['name']}")
            report.append(f"  类型: {font_info['type']} ({font_info['ext']})")
            report.append(f"  大小: {font_info['size']} 字节")
            
            analysis = font_info['analysis']
            report.append(f"  格式: {analysis['format']}")
            
            if analysis['potential_fonts']:
                report.append(f"  找到 {len(analysis['potential_fonts'])} 个嵌入字体:")
                for j, pf in enumerate(analysis['potential_fonts'], 1):
                    report.append(f"    {j}. {pf['type']} 字体，位置: {pf['position']}, 大小: {pf['size']} 字节")
            
            if analysis['recommendations']:
                report.append("  建议:")
                for rec in analysis['recommendations']:
                    report.append(f"    - {rec}")
            
            report.append("")
        
        report.append("=" * 60)
        return "\n".join(report)
    
    def extract_usable_fonts(self) -> Dict[str, bytes]:
        """
        提取所有可用的字体
        
        Returns:
            {字体名称: 字体数据} 字典
        """
        usable_fonts = {}
        
        for font_info in self.fonts_info:
            analysis = font_info['analysis']
            
            # 如果本身就是TrueType/OpenType
            if analysis['has_ttf_signature'] or analysis['has_otf_signature']:
                usable_fonts[font_info['name']] = font_info['data']
                continue
            
            # 如果有嵌入的字体，使用第一个
            if analysis['potential_fonts']:
                for pf in analysis['potential_fonts']:
                    if pf['data']:
                        try:
                            # 验证字体是否有效
                            TTFont(io.BytesIO(pf['data']))
                            usable_fonts[font_info['name']] = pf['data']
                            break
                        except:
                            continue
        
        return usable_fonts
    
    def close(self):
        """关闭文档"""
        if self.doc:
            self.doc.close()


if __name__ == '__main__':
    import sys
    if len(sys.argv) < 2:
        print("用法: python cid_font_analyzer.py <pdf_file>")
        sys.exit(1)
    
    pdf_path = sys.argv[1]
    analyzer = CIDFontAnalyzer(pdf_path)
    
    print("正在分析字体...")
    fonts_info = analyzer.analyze_all_fonts()
    
    print("\n" + analyzer.generate_report())
    
    print("\n正在提取可用字体...")
    usable_fonts = analyzer.extract_usable_fonts()
    print(f"找到 {len(usable_fonts)} 个可用字体")
    
    analyzer.close()

