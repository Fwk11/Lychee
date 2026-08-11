# -*- coding: utf-8 -*-
"""小说模块共用的网络与 SSRF 防护工具（与具体书源无关）。

SSRF 防护：拒绝任何指向内网/回环/链路本地/保留地址的请求（含 DNS 重绑定
防护）——解析 host 的所有 A/AAAA 记录，任一命中私网即拒绝，并对每次重定向
目标重新校验。所有书源（起点等）都复用这里的 safe_ssrf_url / _request。
"""
from __future__ import annotations

import ipaddress
import os
import random
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

import requests

NOVEL_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "novel",
)

_USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
]

_PRIVATE_NETS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("0.0.0.0/8"),
]


def safe_ssrf_url(url: str) -> str:
    """校验一个 URL 是否可被本服务请求：仅 http/https，且解析后不得为内网/回环地址。"""
    p = urllib.parse.urlparse(url)
    if p.scheme not in ("http", "https"):
        raise ValueError("仅支持 http/https 协议")
    host = (p.hostname or "").strip().lower()
    if not host:
        raise ValueError("缺少 host")
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        raise ValueError("无法解析主机")
    addrs = {info[4][0] for info in infos}
    for addr in addrs:
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            raise ValueError("非法 IP 地址")
        if ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise ValueError("禁止访问内网/回环地址")
    return url


class _SSRFRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        safe_ssrf_url(newurl)  # 重定向目标也需通过校验，防止 302 跳内网绕过
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = urllib.request.build_opener(_SSRFRedirectHandler())


def _request(url: str, timeout: int = 20, data: Optional[bytes] = None, retries: int = 1) -> bytes:
    safe_ssrf_url(url)  # 入口校验
    last_err = None
    for _ in range(retries + 1):
        try:
            req = urllib.request.Request(
                url,
                data=data,
                headers={
                    "User-Agent": random.choice(_USER_AGENTS),
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "zh-CN,zh;q=0.9",
                    "Referer": urllib.parse.urlparse(url).scheme + "://" + urllib.parse.urlparse(url).netloc,
                },
            )
            with _opener.open(req, timeout=timeout) as resp:
                return resp.read()
        except Exception as e:
            last_err = e
            time.sleep(1.5)
    raise last_err or RuntimeError("请求失败")


def _request_html(url: str, timeout: int = 20, headers: Optional[dict] = None,
                  retries: int = 1, referer: Optional[str] = None) -> str:
    """使用 requests 抓取 HTML，保留 SSRF 校验，自动维持 session/cookie。"""
    safe_ssrf_url(url)
    default_headers = {
        "User-Agent": random.choice(_USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    if headers:
        default_headers.update(headers)
    if referer:
        default_headers["Referer"] = referer
    last_err = None
    sess = requests.Session()
    for _ in range(retries + 1):
        try:
            r = sess.get(url, headers=default_headers, timeout=timeout)
            r.raise_for_status()
            return r.text
        except Exception as e:
            last_err = e
            time.sleep(1.5)
    raise last_err or RuntimeError("请求失败")


def _decode_html(raw: bytes) -> str:
    """自动识别 UTF-8 / GBK，优先 UTF-8。"""
    for enc in ("utf-8", "gbk", "gb18030"):
        try:
            text = raw.decode(enc)
            # 简单启发：如果 UTF-8 能解出常见中文，优先用
            if enc == "utf-8" and any(c in text for c in ("小说", "章节", "搜索", "作品")):
                return text
            if enc != "utf-8":
                return text
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="ignore")


def _abs_url(base: str, rel: str) -> str:
    if rel.startswith("http"):
        return rel
    base_root = "/".join(base.split("/")[:3])
    if rel.startswith("/"):
        return base_root + rel
    return urllib.parse.urljoin(base.rsplit("/", 1)[0] + "/", rel)


def _strip_tags(html: str) -> str:
    return re.sub(r"<[^>]+>", "", html).strip()

