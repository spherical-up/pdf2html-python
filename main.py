#!/usr/bin/env python3
"""
PDF to HTML Converter - 命令行工具
"""
import argparse
import sys
from pathlib import Path
from core.simple_converter import SimplePDFConverter


def main():
    parser = argparse.ArgumentParser(
        description='PDF to HTML Converter (Python Implementation)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python main.py input.pdf
  python main.py input.pdf -o output.html
  python main.py input.pdf -o output.html --dpi 200
        """
    )
    parser.add_argument('input', help='输入 PDF 文件路径')
    parser.add_argument('-o', '--output', help='输出 HTML 文件路径（默认：输入文件名.html）')
    parser.add_argument('--dpi', type=int, default=150, 
                       help='渲染 DPI（默认：150）')
    
    args = parser.parse_args()
    
    # 检查输入文件
    input_path = Path(args.input)
    if not input_path.exists():
        print(f"错误: 文件不存在: {args.input}", file=sys.stderr)
        sys.exit(1)
    
    if not input_path.suffix.lower() == '.pdf':
        print(f"警告: 输入文件不是 PDF 格式: {args.input}", file=sys.stderr)
    
    # 确定输出路径
    if args.output:
        output_path = Path(args.output)
    else:
        output_path = input_path.with_suffix('.html')
    
    # 执行转换
    try:
        print(f"开始转换: {args.input}")
        print(f"输出文件: {output_path}")
        print(f"DPI: {args.dpi}")
        
        converter = SimplePDFConverter(dpi=args.dpi)
        converter.convert(str(input_path), str(output_path))
        
        print(f"\n✓ 转换成功!")
        print(f"输出文件: {output_path.absolute()}")
        
    except FileNotFoundError as e:
        print(f"错误: 找不到文件或依赖: {e}", file=sys.stderr)
        print("\n提示: 请确保已安装 poppler:")
        print("  macOS: brew install poppler")
        print("  Ubuntu: sudo apt-get install poppler-utils")
        sys.exit(1)
    except Exception as e:
        print(f"错误: 转换失败: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()

