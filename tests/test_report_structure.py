"""京都视频标注报告结构断言（直接读磁盘报告，无需后端）。

这是「导演级标注」产出的契约测试：镜头数、导演级字段、时间轴、合规/评分。
"""
import json
import os

REPORT = os.path.join(
    os.path.dirname(__file__), "..", "output", "reports", "videos_京都的夏日记忆.json"
)


def _load():
    assert os.path.exists(REPORT), "京都报告不存在，请先标注该视频"
    return json.load(open(REPORT, encoding="utf-8"))


def test_report_has_26_shots():
    d = _load()
    assert len(d["shots"]) == 26


def test_director_level_fields_filled():
    d = _load()
    for s in d["shots"]:
        assert s.get("shot_scale"), f"shot {s.get('shot_id')} 缺 shot_scale"
        assert s.get("composition"), f"shot {s.get('shot_id')} 缺 composition"
        assert s.get("mood"), f"shot {s.get('shot_id')} 缺 mood"
        assert s.get("aesthetic_score") is not None, f"shot {s.get('shot_id')} 缺 aesthetic_score"


def test_timeline_present():
    d = _load()
    for s in d["shots"]:
        assert s.get("start_sec") is not None, f"shot {s.get('shot_id')} 缺 start_sec"
        assert s.get("end_sec") is not None, f"shot {s.get('shot_id')} 缺 end_sec"


def test_compliance_and_scores_present():
    d = _load()
    for s in d["shots"]:
        comp = s.get("compliance")
        scores = s.get("scores")
        assert isinstance(comp, dict) and comp, f"shot {s.get('shot_id')} 缺 compliance"
        assert isinstance(scores, dict) and scores, f"shot {s.get('shot_id')} 缺 scores"
