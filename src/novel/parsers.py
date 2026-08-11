# -*- coding: utf-8 -*-
"""电子书多格式解析：统一输出纯文本。

支持 .txt / .docx / .epub / .pdf；对 .mobi / .azw3 建议先转换为 epub/txt。
解析后的文本会保存为 UTF-8 txt，再由 loader.load_novel 做章节切分。
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from .loader import _read_text

# 支持的扩展名（小写，包含点）
SUPPORTED_EXTS = {".txt", ".docx", ".epub", ".pdf", ".mobi", ".azw3"}


def _plain_name(raw: str) -> str:
    stem = Path(raw).stem
    safe = re.sub(r"[^\w\u4e00-\u9fff-]", "_", stem).strip("_") or "upload"
    return safe


def parse_txt(path: str) -> str:
    return _read_text(path)


def parse_docx(path: str) -> str:
    from docx import Document

    doc = Document(path)
    paragraphs = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
    return "\n\n".join(paragraphs)


def parse_epub(path: str) -> str:
    import ebooklib
    from ebooklib import epub
    from bs4 import BeautifulSoup

    book = epub.read_epub(path)
    parts = []
    # 按文档项顺序读取正文
    for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
        body = item.get_content()
        soup = BeautifulSoup(body, "html.parser")
        # 去掉 script/style
        for tag in soup(["script", "style"]):
            tag.decompose()
        text = soup.get_text(separator="\n", strip=True)
        if text:
            parts.append(text)
    return "\n\n".join(parts)


def parse_pdf(path: str) -> str:
    from PyPDF2 import PdfReader

    reader = PdfReader(path)
    parts = []
    for page in reader.pages:
        try:
            text = page.extract_text()
        except Exception:
            text = ""
        if text:
            parts.append(text)
    return "\n\n".join(parts)


def _try_binary_text_fallback(path: str) -> str:
    """对未知/损坏格式做最后兜底：读二进制，按 UTF-8/GBK 尝试提取可打印文本。"""
    raw = open(path, "rb").read()
    for enc in ("utf-8", "gb18030", "latin-1"):
        try:
            text = raw.decode(enc, errors="strict")
            break
        except UnicodeDecodeError:
            text = raw.decode("utf-8", errors="ignore")
    # 只保留看起来像文本的行
    lines = [ln.strip() for ln in text.splitlines() if re.search(r"[\u4e00-\u9fff]", ln)]
    return "\n".join(lines[:5000])  # 兜底限制行数


def parse_book_file(path: str) -> tuple[str, str, str]:
    """解析书籍文件，返回 (plain_text, safe_name, ext)。

    - plain_text: 提取出的正文
    - safe_name: 可用于保存为 .txt 的文件名（不含扩展名）
    - ext: 原始扩展名（小写）

    不支持的格式会抛出 ValueError（带明确提示）。
    """
    path = str(path)
    ext = Path(path).suffix.lower()
    name = _plain_name(path)

    if ext not in SUPPORTED_EXTS:
        raise ValueError(f"不支持的格式 {ext}，请上传 txt / docx / epub / pdf / mobi / azw3")

    if ext == ".txt":
        text = parse_txt(path)
    elif ext == ".docx":
        text = parse_docx(path)
    elif ext == ".epub":
        text = parse_epub(path)
    elif ext == ".pdf":
        text = parse_pdf(path)
    elif ext in (".mobi", ".azw3"):
        # 优先尝试 calibre 命令行转换
        import shutil
        if shutil.which("ebook-convert"):
            import tempfile
            with tempfile.TemporaryDirectory() as td:
                mid = os.path.join(td, f"{name}.epub")
                os.system(f'ebook-convert "{path}" "{mid}" >/dev/null 2>&1')
                if os.path.exists(mid):
                    text = parse_epub(mid)
                else:
                    raise ValueError(f"{ext} 转换失败，请先用 Calibre 转换为 txt/epub/pdf 再上传")
        else:
            # 没有 Calibre：尝试直接二进制兜底（mobi 内部通常混有可读文本，但质量差）
            text = _try_binary_text_fallback(path)
            if not text or len(text) < 200:
                raise ValueError(
                    f"暂不支持 {ext} 直接解析（本机未安装 Calibre）。"
                    f"请先用 Calibre/Kindle 预览等工具转换为 txt/epub/pdf 后上传。"
                )
    else:
        raise ValueError(f"不支持的格式 {ext}")

    if not text or not text.strip():
        raise ValueError("未能从文件中提取到文本内容，请检查文件是否加密或为空")

    return text, name, ext

