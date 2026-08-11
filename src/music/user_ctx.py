"""多用户数据隔离。

每个用户拥有独立的 data/music/users/<user_id>/ 目录，存放：
  listening_history.json / enriched_songs.json / taste_profile.json /
  taste_profile_v3.json / sheet_profiles.json / user_melody_profile_v4.json

user_id=None 或 "default" 时回退到 data/music 根目录，保证现有数据兼容。
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
MUSIC_DIR = os.path.join(ROOT, "data", "music")
USERS_DIR = os.path.join(MUSIC_DIR, "users")

DEFAULT_FILES = (
    "listening_history.json",
    "enriched_songs.json",
    "taste_profile.json",
    "taste_profile_v3.json",
    "sheet_profiles.json",
    "user_melody_profile_v4.json",
)


def _clean_id(user_id: str | None) -> str:
    if not user_id:
        return "default"
    user_id = str(user_id).strip()
    return "default" if user_id.lower() in ("default", "") else user_id


def user_dir(user_id: str | None) -> str:
    uid = _clean_id(user_id)
    if uid == "default":
        return MUSIC_DIR
    return os.path.join(USERS_DIR, uid)


def user_path(user_id: str | None, filename: str) -> str:
    return os.path.join(user_dir(user_id), filename)


def ensure_user(user_id: str | None) -> str:
    """返回用户目录路径，不存在则创建。"""
    d = user_dir(user_id)
    os.makedirs(d, exist_ok=True)
    return d


def list_users() -> list[dict]:
    """列出所有用户目录。default 始终存在且优先。"""
    users = [{"id": "default", "name": "默认用户", "dir": MUSIC_DIR}]
    if not os.path.isdir(USERS_DIR):
        return users
    for name in sorted(os.listdir(USERS_DIR)):
        p = os.path.join(USERS_DIR, name)
        if os.path.isdir(p):
            users.append({"id": name, "name": name, "dir": p})
    return users


def user_exists(user_id: str | None) -> bool:
    return os.path.isdir(user_dir(user_id))


def guess_default_user_id() -> str:
    """若存在非 default 用户但没有默认数据，返回第一个用户；否则 default。"""
    users = list_users()
    if len(users) == 1:
        return "default"
    # 如果 default 目录没有听歌历史，而某个用户有，优先使用该用户
    for u in users:
        if u["id"] == "default":
            continue
        if os.path.exists(os.path.join(u["dir"], "listening_history.json")):
            return u["id"]
    return "default"
