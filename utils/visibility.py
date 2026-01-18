"""
文本可见性检测
"""
from PIL import Image
import numpy as np
from typing import Tuple


def check_text_visibility(text_bbox: Tuple[float, float, float, float], 
                          bg_image: Image.Image) -> bool:
    """
    检查文本是否可见（简化版）
    
    Args:
        text_bbox: 文本边界框 (x0, y0, x1, y1)
        bg_image: 背景图像
    
    Returns:
        是否可见
    """
    x0, y0, x1, y1 = text_bbox
    img_array = np.array(bg_image)
    
    # 采样点：四个角（略微内缩）
    inset = min((x1 - x0) * 0.1, (y1 - y0) * 0.1, 2.0)
    
    sample_points = [
        (int(x0 + inset), int(y0 + inset)),      # 左上
        (int(x1 - inset), int(y0 + inset)),      # 右上
        (int(x0 + inset), int(y1 - inset)),      # 左下
        (int(x1 - inset), int(y1 - inset)),      # 右下
    ]
    
    # 检查采样点
    visible_count = 0
    for x, y in sample_points:
        if 0 <= x < bg_image.width and 0 <= y < bg_image.height:
            pixel = img_array[y, x]
            # 简单判断：如果不是接近白色，认为可见
            if len(pixel) >= 3:
                r, g, b = pixel[:3]
                # 如果像素值不是接近白色（255, 255, 255），认为可见
                if not (r > 250 and g > 250 and b > 250):
                    visible_count += 1
    
    return visible_count > 0


def check_text_visibility_detailed(text_bbox: Tuple[float, float, float, float],
                                   bg_image: Image.Image,
                                   text_color: Tuple[int, int, int] = None) -> dict:
    """
    详细的文本可见性检测
    
    Args:
        text_bbox: 文本边界框
        bg_image: 背景图像
        text_color: 文本颜色 (r, g, b)
    
    Returns:
        可见性信息字典
    """
    x0, y0, x1, y1 = text_bbox
    img_array = np.array(bg_image)
    
    # 采样点
    inset = min((x1 - x0) * 0.1, (y1 - y0) * 0.1, 2.0)
    sample_points = [
        (int(x0 + inset), int(y0 + inset)),
        (int(x1 - inset), int(y0 + inset)),
        (int(x0 + inset), int(y1 - inset)),
        (int(x1 - inset), int(y1 - inset)),
    ]
    
    visible_count = 0
    for x, y in sample_points:
        if 0 <= x < bg_image.width and 0 <= y < bg_image.height:
            pixel = img_array[y, x]
            if len(pixel) >= 3:
                r, g, b = pixel[:3]
                # 如果文本颜色已知，可以更精确判断
                if text_color:
                    # 计算颜色差异
                    color_diff = abs(r - text_color[0]) + abs(g - text_color[1]) + abs(b - text_color[2])
                    if color_diff > 50:  # 颜色差异足够大，认为可见
                        visible_count += 1
                else:
                    # 简单判断
                    if not (r > 250 and g > 250 and b > 250):
                        visible_count += 1
    
    is_fully_occluded = visible_count == 0
    is_partially_occluded = 0 < visible_count < 4
    
    return {
        'visible': visible_count > 0,
        'fully_occluded': is_fully_occluded,
        'partially_occluded': is_partially_occluded,
        'visible_points': visible_count
    }

