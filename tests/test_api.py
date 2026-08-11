"""API 层冒烟测试（FastAPI TestClient，不依赖运行中服务）。

覆盖：
  * /api/health                               健康检查
  * /api/videos                               视频列表（含已上传的京都视频）
  * /api/reports/{id}/annotations?format=json       报告结构
  * /api/reports/{id}/annotations?format=label_studio  LS 导出结构
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi.testclient import TestClient

from src.api.config import settings
from src.api.server import app

client = TestClient(app)
KEY = settings.api_keys[0]
H = {"X-API-Key": KEY}

KYOTO_REPORT = "videos_京都的夏日记忆"


def test_health_ok():
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json().get("status") == "ok"


def test_videos_list_includes_kyoto():
    r = client.get("/api/videos", headers=H)
    assert r.status_code == 200
    names = [v.get("name") for v in r.json().get("videos", [])]
    assert "京都的夏日记忆.mp4" in names


def test_report_json_structure():
    r = client.get(f"/api/reports/{KYOTO_REPORT}/annotations?format=json", headers=H)
    assert r.status_code == 200
    d = r.json()
    # 标准标注 JSON：镜头在 annotations 列表下
    assert "annotations" in d
    assert len(d["annotations"]) == 26


def test_report_label_studio_export():
    r = client.get(f"/api/reports/{KYOTO_REPORT}/annotations?format=label_studio", headers=H)
    assert r.status_code == 200
    d = r.json()
    assert "data" in d and "video_url" in d["data"]
    # 视频 URL 必须是绝对地址，否则 Label Studio 跨域播放失败
    assert d["data"]["video_url"].startswith("http")
    # 时间轴标签必须用 timelinelabels 键，否则 LS 渲染为 Empty
    res = d["annotations"][0]["result"][0]
    assert "timelinelabels" in res["value"]
