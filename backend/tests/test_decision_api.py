"""M12 的 API：Offer 矩阵与翻转、风险与合同、薪资谈判。

三个验收点都落在这里：
1. 矩阵能随权重变化翻转排名；
2. 避坑条目全部可点开原始来源；
3. 谈判给出底线守住率。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

BIG = {
    "company": "大厂",
    "title": "后端工程师",
    "monthly_salary": 30_000,
    "months_per_year": 16,
    "annual_bonus": 60_000,
    "factors": {"growth": 3, "stability": 6, "commute": 4},
}
STARTUP = {
    "company": "创业公司",
    "title": "后端工程师",
    "monthly_salary": 25_000,
    "months_per_year": 14,
    "factors": {"growth": 9, "stability": 5, "commute": 8},
}


def _make_offers(client: TestClient) -> None:
    for payload in (BIG, STARTUP):
        response = client.post("/api/offers", json=payload)
        assert response.status_code == 201, response.text


# --------------------------------------------------------------------------- #
# 选项
# --------------------------------------------------------------------------- #
def test_options_endpoint(client: TestClient) -> None:
    body = client.get("/api/decision/options").json()

    assert {"compensation", "growth"} <= {item["key"] for item in body["dimensions"]}
    assert body["tactics"]
    assert body["risk_signals"]
    assert body["contract_clauses"]
    # 每个风险信号都要能点开来源（验收要求）
    for signal in body["risk_signals"]:
        assert signal["sources"], signal["label"]
    # 来源都有真实 URL
    assert all(item["url"].startswith("http") for item in body["sources"])
    assert all(item["verified"] is False for item in body["sources"])


# --------------------------------------------------------------------------- #
# Offer
# --------------------------------------------------------------------------- #
def test_create_offer_computes_total_comp(client: TestClient) -> None:
    body = client.post("/api/offers", json=BIG).json()
    # 30000 × 16 + 60000
    assert body["total_comp"] == 540_000
    assert body["id"]


def test_offer_crud(client: TestClient) -> None:
    created = client.post("/api/offers", json=BIG).json()
    offer_id = created["id"]

    updated = client.put(f"/api/offers/{offer_id}", json={**BIG, "status": "accepted"}).json()
    assert updated["status"] == "accepted"

    assert len(client.get("/api/offers").json()) == 1
    assert client.delete(f"/api/offers/{offer_id}").status_code == 204
    assert client.get("/api/offers").json() == []
    assert client.put(f"/api/offers/{offer_id}", json=BIG).status_code == 404


def test_signing_bonus_counts_once(client: TestClient) -> None:
    body = client.post("/api/offers", json={**BIG, "signing_bonus": 50_000}).json()
    assert body["total_comp"] == 590_000


# --------------------------------------------------------------------------- #
# 矩阵与翻转（验收 1）
# --------------------------------------------------------------------------- #
def test_ranking_flips_with_weights_over_api(client: TestClient) -> None:
    _make_offers(client)

    pay_heavy = client.post(
        "/api/offers/matrix", json={"weights": {"compensation": 0.9, "growth": 0.05, "stability": 0.05}}
    ).json()
    growth_heavy = client.post(
        "/api/offers/matrix", json={"weights": {"compensation": 0.05, "growth": 0.9, "stability": 0.05}}
    ).json()

    assert pay_heavy["rows"][0]["company"] == "大厂"
    assert growth_heavy["rows"][0]["company"] == "创业公司"
    # 翻转点必须被报出来，而且带可读的话
    assert growth_heavy["flips"] or pay_heavy["flips"]


def test_flip_message_is_readable(client: TestClient) -> None:
    _make_offers(client)
    body = client.post("/api/offers/matrix", json={"weights": {"compensation": 0.5, "growth": 0.5}}).json()

    flips = [item for item in body["flips"] if item["factor"] == "compensation"]
    assert flips
    flip = flips[0]
    assert flip["label"] == "薪酬"
    assert "权重" in flip["message"]
    assert 0 < flip["weight"] < 1


def test_matrix_without_offers(client: TestClient) -> None:
    body = client.post("/api/offers/matrix", json={}).json()
    assert body["rows"] == []
    assert body["notes"]


def test_matrix_notes_warn_about_missing_pay(client: TestClient) -> None:
    client.post("/api/offers", json={"company": "没填薪资", "factors": {"growth": 5}})
    body = client.post("/api/offers/matrix", json={}).json()
    assert any("没填薪资结构" in note for note in body["notes"])


# --------------------------------------------------------------------------- #
# 风险与合同（验收 2）
# --------------------------------------------------------------------------- #
def test_risk_scan_carries_sources_and_never_concludes(client: TestClient) -> None:
    body = client.post(
        "/api/decision/risk", json={"text": "急招后端，入职需交培训费 2000 元，试用期不上社保，大小周"}
    ).json()

    assert body["signals"]
    for signal in body["signals"]:
        assert signal["sources"], signal["label"]
        assert signal["matched"]
        assert signal["action"]
        assert "不代表事实" in signal["disclaimer"]
    assert "不代表任何结论" in body["disclaimer"]
    assert all(item["url"].startswith("http") for item in body["sources"])


def test_risk_scan_clean_text(client: TestClient) -> None:
    body = client.post("/api/decision/risk", json={"text": "负责后端开发，要求三年经验"}).json()
    assert body["signals"] == []
    assert body["sources"]


def test_contract_report_without_text(client: TestClient) -> None:
    body = client.post("/api/decision/contract", json={"text": ""}).json()
    assert body["checklist"]
    assert body["mentioned"] == []
    assert "不是法律意见" in body["disclaimer"]
    # 每条都必须是能直接拿去问 HR 的问句
    assert all(item["clause"]["question"].endswith("？") for item in body["checklist"])


def test_contract_report_detects_mentions(client: TestClient) -> None:
    body = client.post(
        "/api/decision/contract", json={"text": "试用期三个月，年终奖发放时需在职，有竞业限制"}
    ).json()
    labels = [item["clause"]["label"] for item in body["mentioned"]]
    assert "试用期时长与待遇" in labels
    assert "竞业限制" in labels


# --------------------------------------------------------------------------- #
# 谈判（验收 3）
# --------------------------------------------------------------------------- #
def test_negotiation_flow_and_brief(client: TestClient) -> None:
    started = client.post(
        "/api/negotiations",
        json={"company": "大厂", "title": "后端工程师", "bottom_line": 450_000, "target": 550_000},
    )
    assert started.status_code == 201, started.text
    session = started.json()
    session_id = session["id"]

    assert session["turns"][0]["speaker"] == "interviewer"
    assert session["plan"]["bottom_line"] == 450_000
    assert session["plan"]["ceiling"] > 550_000

    for text in ["想先了解一下这个岗位的薪资区间是多少", "我期望 55 万", "低于 48 万我无法接受"]:
        response = client.post(f"/api/negotiations/{session_id}/reply", json={"text": text})
        assert response.status_code == 200, response.text

    detail = client.get(f"/api/negotiations/{session_id}").json()
    assert len([turn for turn in detail["turns"] if turn["speaker"] == "candidate"]) == 3
    assert len([turn for turn in detail["turns"] if turn["speaker"] == "interviewer"]) == 4

    brief = detail["brief"]
    assert brief["bottom_line"] == 450_000
    assert brief["first_ask"] == 550_000
    assert brief["last_ask"] == 480_000
    # 底线守住率：48 万 > 45 万 → 守住了
    assert brief["bottom_line_held"] is True
    assert brief["tactics_used"]
    assert 0 <= brief["score"] <= 100
    assert brief["summary"]
    # 不能对"对方报价是否合理"下结论
    assert "不评价对方" in brief["caveat"]


def test_negotiation_broken_bottom_line(client: TestClient) -> None:
    session = client.post(
        "/api/negotiations",
        json={"company": "小厂", "bottom_line": 500_000, "target": 600_000, "ceiling": 520_000},
    ).json()
    session_id = session["id"]

    client.post(f"/api/negotiations/{session_id}/reply", json={"text": "我期望 60 万"})
    client.post(f"/api/negotiations/{session_id}/reply", json={"text": "那 45 万我也接受吧"})

    brief = client.get(f"/api/negotiations/{session_id}").json()["brief"]
    assert brief["bottom_line_held"] is False
    assert any("底线" in issue for issue in brief["issues"])


def test_negotiation_404(client: TestClient) -> None:
    assert client.get("/api/negotiations/99999").status_code == 404
    assert client.post("/api/negotiations/99999/reply", json={"text": "在吗"}).status_code == 404


def test_negotiation_history(client: TestClient) -> None:
    client.post("/api/negotiations", json={"company": "A", "target": 400_000})
    client.post("/api/negotiations", json={"company": "B", "target": 500_000})

    items = client.get("/api/negotiations").json()
    assert len(items) == 2
    assert items[0]["id"] > items[1]["id"]  # 最近的在前


def test_negotiation_does_not_pollute_interview_list(client: TestClient) -> None:
    client.post("/api/negotiations", json={"company": "A", "target": 400_000})
    sessions = client.get("/api/sessions").json()
    # 谈判复用 interview_session，但不该出现在面试列表里
    assert all(item["mode"] != "negotiation" for item in sessions)
