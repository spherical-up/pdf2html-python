"""
使用FontForge转换CID字体的工具
需要安装FontForge: brew install fontforge (macOS) 或 apt-get install fontforge (Ubuntu)
"""
import subprocess
import os
import tempfile
from pathlib import Path
from typing import Optional


class FontForgeConverter:
    """使用FontForge转换CID字体"""
    
    def __init__(self):
        self.fontforge_available = self._check_fontforge()
    
    def _check_fontforge(self) -> bool:
        """检查FontForge是否可用"""
        try:
            result = subprocess.run(['fontforge', '-version'], 
                                  capture_output=True, 
                                  timeout=5)
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False
    
    def convert_cid_to_ttf(self, cid_font_data: bytes, output_path: Optional[str] = None) -> Optional[bytes]:
        """
        将CID字体转换为TTF格式
        
        Args:
            cid_font_data: CID字体数据
            output_path: 输出路径（可选）
            
        Returns:
            转换后的TTF字体数据，如果失败则返回None
        """
        if not self.fontforge_available:
            print("FontForge未安装，无法转换CID字体")
            print("安装方法:")
            print("  macOS: brew install fontforge")
            print("  Ubuntu: sudo apt-get install fontforge")
            return None
        
        # 创建临时文件
        with tempfile.NamedTemporaryFile(suffix='.cid', delete=False) as tmp_input:
            tmp_input.write(cid_font_data)
            tmp_input_path = tmp_input.name
        
        try:
            if output_path is None:
                tmp_output = tempfile.NamedTemporaryFile(suffix='.ttf', delete=False)
                tmp_output_path = tmp_output.name
                tmp_output.close()
            else:
                tmp_output_path = output_path
            
            # 创建FontForge脚本
            script = f"""
import fontforge
font = fontforge.open("{tmp_input_path}")
font.generate("{tmp_output_path}")
font.close()
"""
            
            # 执行FontForge脚本
            result = subprocess.run(
                ['fontforge', '-script', '-'],
                input=script.encode('utf-8'),
                capture_output=True,
                timeout=30
            )
            
            if result.returncode == 0 and os.path.exists(tmp_output_path):
                # 读取转换后的字体
                with open(tmp_output_path, 'rb') as f:
                    ttf_data = f.read()
                
                # 清理临时文件
                if output_path is None:
                    os.unlink(tmp_output_path)
                
                return ttf_data
            else:
                print(f"FontForge转换失败: {result.stderr.decode('utf-8', errors='ignore')}")
                return None
                
        except Exception as e:
            print(f"转换过程中出错: {e}")
            return None
        finally:
            # 清理输入临时文件
            if os.path.exists(tmp_input_path):
                os.unlink(tmp_input_path)
    
    def convert_cid_to_woff(self, cid_font_data: bytes) -> Optional[bytes]:
        """
        将CID字体转换为WOFF格式
        
        Args:
            cid_font_data: CID字体数据
            
        Returns:
            转换后的WOFF字体数据，如果失败则返回None
        """
        # 先转换为TTF
        ttf_data = self.convert_cid_to_ttf(cid_font_data)
        if not ttf_data:
            return None
        
        # 然后使用fontTools转换为WOFF
        try:
            from fontTools.ttLib import TTFont
            import io
            
            font = TTFont(io.BytesIO(ttf_data))
            output = io.BytesIO()
            font.flavor = 'woff'
            font.save(output)
            return output.getvalue()
        except Exception as e:
            print(f"WOFF转换失败: {e}")
            return None


if __name__ == '__main__':
    import sys
    if len(sys.argv) < 3:
        print("用法: python fontforge_converter.py <input.cid> <output.ttf>")
        sys.exit(1)
    
    input_path = sys.argv[1]
    output_path = sys.argv[2]
    
    with open(input_path, 'rb') as f:
        cid_data = f.read()
    
    converter = FontForgeConverter()
    ttf_data = converter.convert_cid_to_ttf(cid_data, output_path)
    
    if ttf_data:
        print(f"转换成功: {output_path}")
    else:
        print("转换失败")

