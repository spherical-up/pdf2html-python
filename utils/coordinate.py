"""
坐标变换工具
"""
from typing import Tuple, List


def pdf_to_html_y(pdf_y: float, page_height: float) -> float:
    """
    PDF Y 坐标转 HTML Y 坐标
    
    PDF 坐标系：原点在左下角，Y 向上
    HTML 坐标系：原点在左上角，Y 向下
    
    Args:
        pdf_y: PDF Y 坐标
        page_height: 页面高度
    
    Returns:
        HTML Y 坐标
    """
    return page_height - pdf_y


def apply_transform(bbox: Tuple[float, float, float, float], 
                   matrix: List[float]) -> Tuple[float, float, float, float]:
    """
    应用变换矩阵到边界框
    
    Args:
        bbox: 边界框 (x0, y0, x1, y1)
        matrix: 变换矩阵 [a, b, c, d, e, f]
    
    Returns:
        变换后的边界框
    """
    x0, y0, x1, y1 = bbox
    a, b, c, d, e, f = matrix
    
    # 应用变换到四个角点
    def transform_point(x, y):
        new_x = a * x + c * y + e
        new_y = b * x + d * y + f
        return new_x, new_y
    
    # 变换四个角点
    corners = [
        transform_point(x0, y0),  # 左下
        transform_point(x1, y0),  # 右下
        transform_point(x0, y1),  # 左上
        transform_point(x1, y1),  # 右上
    ]
    
    # 计算新的边界框
    xs = [p[0] for p in corners]
    ys = [p[1] for p in corners]
    
    return (min(xs), min(ys), max(xs), max(ys))


def matrix_to_css_transform(matrix: List[float]) -> str:
    """
    将 PDF 变换矩阵转换为 CSS transform matrix
    
    Args:
        matrix: PDF 变换矩阵 [a, b, c, d, e, f]
    
    Returns:
        CSS transform matrix 字符串
    """
    a, b, c, d, e, f = matrix
    return f"matrix({a:.6f}, {b:.6f}, {c:.6f}, {d:.6f}, {e:.6f}, {f:.6f})"

