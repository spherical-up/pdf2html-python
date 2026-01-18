# pdf2html-python

一个基于 Python 的 PDF 转 HTML 工具，采用“glyph 分流 + 双层渲染”模型生成可复制、可搜索的文本层，并与背景层精确叠加。适合需要“视觉一致 + 文本可检索”的场景，比如文档预览、归档与检索系统。

## 特性

- 文本层可复制、可搜索，保留字体、字号、颜色与排版位置
- 背景层仅渲染不可提取的内容（图形/图片/复杂绘制）
- 字体处理：提取、子集化、WOFF/WOFF2 转换
- ToUnicode 映射修复，改善乱码与缺字场景
- 支持 DPI 控制渲染质量与性能

## 适用场景

- 需要浏览器端预览且可复制文本的 PDF
- 文档检索/归档系统，要求图文一致与可搜索并存
- 版式复杂、含嵌入字体或混合内容的文档

## 快速开始

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py input.pdf -o output.html
```

## 安装

目前建议从源码安装：

```bash
git clone https://github.com/yourusername/pdf2html-python.git
cd pdf2html-python
pip install -r requirements.txt
```

可选依赖：

- Poppler（`pdf2image` 渲染）
- FontForge（CID 字体转换）
- brotli（WOFF2 压缩）

```bash
# macOS
brew install poppler

# Ubuntu
sudo apt-get install poppler-utils
```

## 使用方法

### CLI

```bash
python main.py input.pdf
python main.py input.pdf -o output.html
python main.py input.pdf -o output.html --dpi 200
```

### 参数说明

| 参数 | 说明 | 默认值 |
| --- | --- | --- |
| `input` | 输入 PDF 路径 | 必填 |
| `-o / --output` | 输出 HTML 路径 | 与输入同名 |
| `--dpi` | 渲染 DPI | 150 |

## 工作原理（简述）

1. 从页面提取可映射到 Unicode 的 glyph（文本层候选）
2. 对 glyph 进行分流：可提取 → 文本层；不可提取 → 背景层
3. 渲染背景层时“擦除”已提取文字，以避免重复绘制
4. 生成 HTML 文本元素并与背景层精确对齐

> 受 PyMuPDF `get_pixmap()` 的限制，当前采用“渲染后擦除”的策略，而非渲染阶段分流。

## 目录结构

```
pdf2html_py/
├─ main.py                  # CLI 入口
├─ core/
│  ├─ simple_converter.py   # 转换主流程与布局逻辑
│  ├─ font_handler.py       # 字体提取、子集化与 WOFF/WOFF2
│  └─ font_unicode_fixer.py # ToUnicode 修复与映射解析
├─ utils/
│  ├─ coordinate.py         # 坐标与矩阵工具
│  ├─ visibility.py         # 文本可见性检测
│  ├─ cid_font_analyzer.py  # CID 字体分析工具
│  └─ fontforge_converter.py# FontForge 转换辅助
└─ requirements.txt
```

## 输出说明

- HTML 文件包含背景层（图片）与文本层（绝对定位文本）
- 字体会尽量提取并转换为浏览器友好的格式，必要时进行子集化
- 输出质量与渲染速度主要受 DPI 和页面复杂度影响
- 如果字体缺少有效的 Unicode 映射，会尝试通过 ToUnicode 修复

## 已知限制

- 依赖 PyMuPDF 的渲染行为，复杂透明叠加或矢量效果可能存在偏差
- 复杂字体（尤其是 CID 字体）在缺少 ToUnicode 映射时仍可能出现少量乱码
- 大型文档建议分批处理或降低 DPI 以减少内存占用

## 常见问题

**Q: 输出文字存在乱码或缺字怎么办？**  
A: 尝试提高 DPI 或确保 PDF 内嵌字体完整；CID 字体可结合 `utils/fontforge_converter.py` 进行转换辅助。

**Q: 为什么输出文件体积较大？**  
A: 背景层是图片渲染结果，DPI 越高体积越大。可降低 DPI 或分批处理页面。

**Q: 某些图形与文字有轻微偏移？**  
A: 这是渲染与布局近似带来的差异，可调整 DPI 或优化布局逻辑。

## 开发与扩展建议

- 在 `core/simple_converter.py` 中调整 glyph 分流与文本布局策略
- 在 `core/font_unicode_fixer.py` 中扩展 ToUnicode 解析与修复逻辑
- 在 `utils/fontforge_converter.py` 中接入更稳定的字体转换工具链

## Roadmap（建议）

- 更精细的文本合并与断行策略
- 更稳定的字体映射与子集化流程
- 页内多栏与复杂版式的对齐优化

## 贡献

欢迎 PR 与 Issue。建议在提交前说明：

- 使用的 PDF 样例特征（是否扫描件、是否含嵌入字体、语言与字号）
- 期望行为与实际输出差异
- 运行环境（Python 版本、操作系统）
