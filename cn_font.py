"""中文文字绘制辅助模块

cv2.putText 只支持 ASCII（Helvetica 字形），中文会显示为问号。
本模块用 Pillow 在 BGR 图像上绘制中文文字，字体查找顺序：
1. 项目内置 fonts/ 目录（随镜像打包，NAS/Linux 容器首选）
2. Linux 常见系统 CJK 字体路径
3. macOS 系统字体（本地开发）
"""
import os
from typing import Optional, Tuple

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

_FONT_CANDIDATES = [
    os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fonts', 'HiraginoSansGB.ttc'),
    '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
    '/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf',
    '/usr/share/fonts/truetype/wqy/wqy-microhei.ttc',
    '/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc',
    '/usr/share/fonts/truetype/arphic/uming.ttc',
    '/System/Library/Fonts/Hiragino Sans GB.ttc',
    '/System/Library/Fonts/STHeiti Light.ttc',
]

_font_cache = {}          # size -> ImageFont
_font_path = None         # 已选定的字体文件路径
_font_path_resolved = False


def _resolve_font_path() -> Optional[str]:
    global _font_path, _font_path_resolved
    if _font_path_resolved:
        return _font_path
    _font_path_resolved = True
    for p in _FONT_CANDIDATES:
        if os.path.isfile(p):
            _font_path = p
            break
    return _font_path


def get_font(size: int) -> Optional[ImageFont.ImageFont]:
    """按字号缓存加载 CJK 字体；找不到中文字体返回 None。"""
    if size in _font_cache:
        return _font_cache[size]
    path = _resolve_font_path()
    font = None
    if path:
        try:
            font = ImageFont.truetype(path, size)
        except Exception:
            font = None
    if font is None:
        font = ImageFont.load_default()
    _font_cache[size] = font
    return font


def has_cn_font() -> bool:
    """是否找到了可渲染中文的字体。"""
    return _resolve_font_path() is not None


def _text_size(draw: ImageDraw.ImageDraw, text: str, font) -> Tuple[int, int]:
    try:
        l, t, r, b = draw.textbbox((0, 0), text, font=font)
        return r - l, b - t
    except AttributeError:  # 老版本 Pillow
        return draw.textsize(text, font=font)


def put_text_cn(img: np.ndarray, text: str, org: Tuple[int, int],
                color: Tuple[int, int, int] = (255, 255, 255),
                size: int = 20) -> np.ndarray:
    """在 OpenCV BGR 图像上绘制中文文字。

    Args:
        img: BGR 图像（会被原地修改并返回）
        text: 文字内容（支持中文）
        org: 文字左上角坐标 (x, y)
        color: BGR 颜色
        size: 字号（像素）
    """
    font = get_font(size)
    rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    pil = Image.fromarray(rgb)
    draw = ImageDraw.Draw(pil)
    # 转换为 RGB 颜色
    r, g, b = color
    draw.text((int(org[0]), int(org[1])), text, font=font, color=(r, g, b))
    img[:] = cv2.cvtColor(np.asarray(pil), cv2.COLOR_RGB2BGR)
    return img


def text_size_cn(text: str, size: int) -> Tuple[int, int]:
    """估算文字渲染尺寸 (w, h)，用于面板布局。"""
    font = get_font(size)
    pil = Image.new('RGB', (8, 8))
    draw = ImageDraw.Draw(pil)
    return _text_size(draw, text, font)
