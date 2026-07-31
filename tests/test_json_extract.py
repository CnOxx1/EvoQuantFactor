from __future__ import annotations

from factor_backend.llm.json_extract import extract_json, repair_common_json_issues


def test_extract_trailing_comma():
    raw = '''
    {
      "role_code": "R1",
      "reviews": [
        {"factor_id": "F1", "comment": "ok",}
      ],
    }
    '''
    data = extract_json(raw)
    assert data["role_code"] == "R1"
    assert data["reviews"][0]["factor_id"] == "F1"


def test_extract_markdown_fence_and_newlines():
    raw = '''```json
{"a": "line1
line2", "b": 1}
```'''
    data = extract_json(raw)
    assert "line1" in data["a"]
    assert data["b"] == 1


def test_extract_smart_quotes():
    raw = '{“ping”: true, “msg”: “你好”}'
    fixed = repair_common_json_issues(raw)
    data = extract_json(fixed)
    assert data["ping"] is True


def test_extract_prefix_suffix_noise():
    raw = '以下是结果：\n{"ok": true, "n": 2}\n完'
    data = extract_json(raw)
    assert data == {"ok": True, "n": 2}
