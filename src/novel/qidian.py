# -*- coding: utf-8 -*-
"""起点中文网（qidian.com / qdmm.com）书籍抓取。

诚实说明：
  - 起点对正文有强反爬 + 登录墙，免费章节的部分正文可能可抓、付费/登录章节会被拦截。
  - 本模块“尽力而为”：能抓到书名/作者/简介/目录就先落盘，正文若被拦截则
    content_available=False，并明确提示用户改用「粘贴正文」兜底路径，绝不伪造内容。
  - 所有请求复用 _net 的 SSRF 防护（拒绝内网/回环）。

提供三种入口：
  1. fetch_qidian_book(url)        —— 粘贴起点书籍页 URL，尽力抓取
  2. search_qidian(keyword)        —— 按书名搜起点，返回候选
  3. fetch_qidian_by_paste(...)    —— 用户从起点复制正文后粘贴导入（最稳妥路径）
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import List, Optional

from ._net import (safe_ssrf_url, _request, _request_html, _decode_html,
                    _abs_url, _strip_tags, NOVEL_DIR)

# 起点合法域名（白名单，防止 SSRF 把任意站当起点解析）
_QIDIAN_NETLOCS = (
    "book.qidian.com", "www.qidian.com", "m.qidian.com", "read.qidian.com",
    "qidian.com", "qdmm.com", "www.qdmm.com", "m.qdmm.com",
)

_USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1",
]


def _is_qidian_url(url: str) -> bool:
    from urllib.parse import urlparse
    host = (urlparse(url).hostname or "").lower()
    return any(host == n or host.endswith("." + n) for n in _QIDIAN_NETLOCS)


def _extract_book_id(url: str) -> Optional[str]:
    m = re.search(r"(?:info/|book/|chapter/)?(\d{4,12})", url)
    return m.group(1) if m else None


def _meta_from_info_html(html: str) -> dict:
    """从书籍详情页 HTML 解析元信息（兼容 PC 详情页与 m.qidian.com 目录页）。"""
    meta = {"title": "", "author": "", "intro": "", "category": "", "status": "", "cover": ""}

    # 移动端 meta / microdata 优先
    m = re.search(r'<meta[^>]*itemprop=["\']name["\'][^>]*content=["\']([^"\']+)["\']', html, re.I)
    if m:
        meta["title"] = m.group(1).strip()
    m = re.search(r'<meta[^>]*property=["\']og:novel:author["\'][^>]*content=["\']([^"\']+)["\']', html, re.I)
    if m:
        meta["author"] = m.group(1).strip()

    # 书名：优先 <h1 class="book-info-title"> 或 <em class="book_info_title">
    if not meta["title"]:
        for pat in (r"<h1[^>]*class=\"[^\"]*book-info-title[^\"]*\"[^>]*>(.*?)</h1>",
                    r"<em[^>]*class=\"[^\"]*book_info_title[^\"]*\"[^>]*>(.*?)</em>",
                    r"<h1[^>]*>(.*?)</h1>"):
            m = re.search(pat, html, re.S | re.I)
            if m:
                meta["title"] = _strip_tags(m.group(1)).strip()
                if meta["title"]:
                    break

    # 作者：<a class="writer"> 或 <span ...>作者：<a>xxx</a>
    if not meta["author"]:
        for pat in (r"<a[^>]*class=\"[^\"]*writer[^\"]*\"[^>]*>(.*?)</a>",
                    r"作者[：:]\s*<a[^>]*>(.*?)</a>",
                    r"作者[：:]\s*([^\s<]{1,20})"):
            m = re.search(pat, html, re.S | re.I)
            if m:
                meta["author"] = _strip_tags(m.group(1)).strip()
                if meta["author"]:
                    break

    # 简介：<div class="book-intro"> ... <p>
    intro_m = re.search(r"<div[^>]*class=\"[^\"]*book-intro[^\"]*\"[^>]*>(.*?)</div>",
                        html, re.S | re.I)
    if intro_m:
        paras = re.findall(r"<p[^>]*>(.*?)</p>", intro_m.group(1), re.S | re.I)
        meta["intro"] = "\n".join(_strip_tags(p).strip() for p in paras if p.strip()).strip()
        if not meta["intro"]:
            meta["intro"] = _strip_tags(intro_m.group(1)).strip()

    # 分类 / 状态 等
    cat_m = re.search(r"<a[^>]*href=\"[^\"]*fenlei[^\"]*\"[^>]*>(.*?)</a>", html, re.S | re.I)
    if cat_m:
        meta["category"] = _strip_tags(cat_m.group(1)).strip()
    st_m = re.search(r"(连载中|已完结|完本|已经完本)", html)
    if st_m:
        meta["status"] = st_m.group(1)

    # 封面
    cov_m = re.search(r"<img[^>]*class=\"[^\"]*book-cover[^\"]*\"[^>]*src=\"([^\"]+)\"",
                     html, re.S | re.I)
    if cov_m:
        meta["cover"] = cov_m.group(1)
    return meta


def _catalog_from_ajax(book_id: str) -> List[dict]:
    """通过起点公开 ajax 接口拿章节目录（cId + cN）。"""
    url = f"https://www.qidian.com/ajax/book/category?bookId={book_id}"
    try:
        raw = _request(url, timeout=15, retries=1)
        data = json.loads(_decode_html(raw))
        vols = data.get("data", {}).get("vs", []) or []
        out = []
        for v in vols:
            for c in v.get("cs", []) or []:
                cid = str(c.get("cId", "")).strip()
                cname = _strip_tags(str(c.get("cN", ""))).strip()
                if cid and cname:
                    out.append({"cid": cid, "title": cname,
                                "vip": bool(c.get("cVip", 0)) or bool(c.get("vip", 0))})
        return out
    except Exception:
        return []


def _chapter_text_from_read(book_id: str, cid: str) -> str:
    """尝试从 read.qidian.com 抓单章正文（免费章可能可抓，付费/登录章会失败）。"""
    url = f"https://read.qidian.com/chapter/{book_id}/{cid}"
    try:
        html = _decode_html(_request(url, timeout=15, retries=0))
        # 正文容器
        m = re.search(r"<div[^>]*class=\"[^\"]*read-content[^\"]*\"[^>]*>(.*?)</div>",
                      html, re.S | re.I)
        if not m:
            m = re.search(r"<div[^>]*id=\"chapter-[^\"]*\"[^>]*>(.*?)</div>",
                          html, re.S | re.I)
        if not m:
            return ""
        txt = m.group(1)
        txt = re.sub(r"<br\s*/?>\s*<br\s*/?>", "\n", txt, flags=re.I)
        txt = re.sub(r"<[^>]+>", "", txt)
        txt = txt.replace("&nbsp;", " ").replace("&amp;", "&")
        lines = [l.strip() for l in txt.splitlines() if l.strip()]
        lines = [l for l in lines if not any(ad in l for ad in
                  ("起点", "qidian", "请登录", "下载起点", "手机阅读", "本章未完", "作家的话"))]
        body = "\n".join(lines)
        # 反爬/登录墙特征：正文过短或含提示语
        if len(body) < 30 or any(k in body for k in ("请使用起点", "登录后阅读", "本章为VIP")):
            return ""
        return body
    except Exception:
        return ""


def _catalog_from_mobile(book_id: str) -> List[dict]:
    """通过 m.qidian.com 目录页抓取章节目录（PC ajax 被 WAF 拦截时的主路径）。"""
    url = f"https://m.qidian.com/book/{book_id}/catalog"
    try:
        html = _request_html(
            url, timeout=20, retries=1,
            headers={
                "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
            referer="https://m.qidian.com/",
        )
        out = []
        # 目录页章节以 data-cid + h2 呈现
        for m in re.finditer(
            r'data-cid=["\'](\d+)["\'][^>]*>.*?<h2>([^<]+)</h2>', html, re.S | re.I
        ):
            cid, cname = m.group(1).strip(), m.group(2).strip()
            if cid and cname:
                out.append({"cid": cid, "title": cname, "vip": False})
        return out
    except Exception:
        return []


def _chapter_text_from_mobile(book_id: str, cid: str) -> str:
    """尝试从 m.qidian.com 单章页抓正文。"""
    url = f"https://m.qidian.com/book/{book_id}/{cid}.html"
    try:
        html = _request_html(
            url, timeout=15, retries=0,
            headers={
                "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
            referer=f"https://m.qidian.com/book/{book_id}/catalog",
        )
        lines = []
        for p in re.findall(r"<p[^>]*>(.*?)</p>", html, re.S | re.I):
            txt = _strip_tags(p).replace("&nbsp;", " ").strip()
            if len(txt) < 20:
                continue
            if any(ad in txt for ad in (
                "起点", "qidian", "请登录", "下载起点", "手机阅读",
                "本章未完", "作家的话", "返回书架", "下一章", "上一章",
                "加入书架", "立即阅读"
            )):
                continue
            lines.append(txt)
        body = "\n".join(lines)
        if len(body) < 30 or any(k in body for k in ("请使用起点", "登录后阅读", "本章为VIP")):
            return ""
        return body
    except Exception:
        return ""


def fetch_qidian_book(url: str, title: Optional[str] = None, max_chapters: int = 200) -> dict:
    """粘贴起点书籍页 URL，尽力抓取。返回落盘结果。

    抓不到正文时 content_available=False，并把已抓到的元信息/目录写入占位 txt，
    同时在返回里给出明确提示，引导用户用 fetch_qidian_by_paste 兜底。
    """
    if not _is_qidian_url(url):
        raise ValueError("仅支持起点中文网（qidian.com / qdmm.com）链接")
    safe_ssrf_url(url)

    # 优先走 m.qidian.com 目录页：PC 详情页被 WAF probe.js 拦截，移动端仍可获得元信息/目录
    book_id = _extract_book_id(url)
    if book_id:
        try:
            mobile_html = _request_html(
                f"https://m.qidian.com/book/{book_id}/catalog",
                timeout=20, retries=1,
                headers={
                    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                },
                referer="https://m.qidian.com/",
            )
            meta = _meta_from_info_html(mobile_html)
        except Exception:
            mobile_html = ""
            meta = {"title": "", "author": "", "intro": "", "category": "", "status": "", "cover": ""}
    else:
        mobile_html = ""
        meta = {"title": "", "author": "", "intro": "", "category": "", "status": "", "cover": ""}

    # 若移动端也没解析到标题，再回退到原 URL（PC 页可能被反爬但偶尔能拿到）
    if not meta["title"]:
        html = _decode_html(_request(url, timeout=20, retries=1))
        meta = _meta_from_info_html(html)

    title = (title or meta["title"] or "unknown").strip()
    safe_title = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", title).strip("_") or "qidian_novel"
    out_path = os.path.join(NOVEL_DIR, f"{safe_title}.txt")
    os.makedirs(NOVEL_DIR, exist_ok=True)

    # 目录：优先移动端目录页，回退 ajax
    catalog = _catalog_from_mobile(book_id) if book_id else []
    if not catalog and book_id:
        catalog = _catalog_from_ajax(book_id)
    catalog = catalog[:max_chapters]

    parts = [f"《{title}》", ""]
    if meta["author"]:
        parts.append(f"作者：{meta['author']}")
    if meta["category"]:
        parts.append(f"分类：{meta['category']}   状态：{meta['status'] or '未知'}")
    if meta["intro"]:
        parts.append("")
        parts.append("【作品简介】")
        parts.append(meta["intro"])
    if catalog:
        parts.append("")
        parts.append("【目录】")
        for i, c in enumerate(catalog, 1):
            tag = "【VIP】" if c.get("vip") else ""
            parts.append(f"{i}. {c['title']}{tag}")
    parts.append("")

    content_available = False
    ok = 0
    if catalog and book_id:
        body_parts = list(parts)
        for i, c in enumerate(catalog, 1):
            # 优先移动端章节页，回退 PC read.qidian.com
            txt = _chapter_text_from_mobile(book_id, c["cid"])
            if not txt:
                txt = _chapter_text_from_read(book_id, c["cid"])
            if txt:
                content_available = True
                ok += 1
                body_parts.append(f"\n第{i}章 {c['title']}\n")
                body_parts.append(txt)
                body_parts.append("\n")
            if i % 15 == 0:
                time.sleep(0.3)
        if content_available:
            parts = body_parts

    # 无论是否抓到正文，元信息/目录都落盘（便于后续分析 / 粘贴补全）
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(parts))

    return {
        "path": out_path,
        "title": title,
        "author": meta["author"],
        "category": meta["category"],
        "status": meta["status"],
        "intro": meta["intro"][:500],
        "n_chapters_catalog": len(catalog),
        "n_chapters_downloaded": ok,
        "content_available": content_available,
        "source": "qidian",
        "message": (
            "已抓到书名/作者/简介与目录。" if content_available
            else "已抓到书名/作者/简介与目录，但起点对正文有登录墙，正文未能自动抓取。"
                 "请在「起点·粘贴正文」处把章节文字粘贴进来补全，或用上传 txt。绝不会伪造正文。"
        ),
    }


def search_qidian(keyword: str, max_results: int = 5) -> List[dict]:
    """按书名搜起点，返回候选书籍（含书籍页 URL）。"""
    import urllib.parse
    encoded = urllib.parse.quote(keyword)
    url = f"https://www.qidian.com/search/books?kw={encoded}"
    try:
        html = _decode_html(_request(url, timeout=15, retries=1))
    except Exception:
        return []
    results = []
    for m in re.finditer(
        r"<li[^>]*class=\"[^\"]*res-book-item[^\"]*\"[^>]*>.*?"
        r"<h3[^>]*class=\"[^\"]*res-book-name[^\"]*\"[^>]*>.*?"
        r"<a[^>]+href=\"([^\"]+)\"[^>]*>(.*?)</a>.*?"
        r"<span[^>]*class=\"[^\"]*res-author[^\"]*\"[^>]*>(.*?)</span>",
        html, re.S | re.I,
    ):
        book_url = _abs_url("https://www.qidian.com", _strip_tags(m.group(1)))
        results.append({
            "title": _strip_tags(m.group(2)).strip(),
            "author": _strip_tags(m.group(3)).strip(),
            "url": book_url,
            "source": "qidian.com",
        })
        if len(results) >= max_results:
            break
    return results


def fetch_qidian_by_paste(title: str, author: str, text: str) -> dict:
    """用户从起点复制正文后粘贴导入（最稳路径，绕过反爬）。"""
    if not text.strip():
        raise ValueError("正文不能为空")
    # 标题为空时自动从正文提取（如《书名》或首行），让「智能导入」更稳健
    if not title:
        m = re.search(r"《([^》]+)》", text)
        if m:
            title = m.group(1).strip()
        else:
            first_line = next((s.strip() for s in text.splitlines() if s.strip()), "")
            title = first_line[:30].strip() or "粘贴小说"
    safe_title = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", title).strip("_") or "qidian_paste"
    out_path = os.path.join(NOVEL_DIR, f"{safe_title}.txt")
    os.makedirs(NOVEL_DIR, exist_ok=True)
    # 尝试按「第X章」切分，否则整段
    body = f"《{title}》\n作者：{author}\n\n{text.strip()}\n"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(body)
    n = len(re.findall(r"第[一二三四五六七八九十百千零〇0-9]+[回章节]", text))
    return {
        "path": out_path,
        "title": title,
        "author": author,
        "n_chapters_detected": n,
        "content_available": True,
        "source": "qidian_paste",
        "message": "已从粘贴正文导入（绕过反爬），可直接分析/分镜。",
    }


if __name__ == "__main__":
    import sys
    q = sys.argv[1] if len(sys.argv) > 1 else "诡秘之主"
    for r in search_qidian(q, max_results=3):
        print(r)

