"""HTTP 端点级复核：登记去重、去重清单、聚合计划/执行/结果追溯。"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient

from app.main import app
from app.store import store


def reset_store():
    store._tables = {}
    from app.seed import SEED_ROWS
    store._tables = {name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()}


def _client() -> TestClient:
    reset_store()
    return TestClient(app)


def test_api_register_duplicate_rejected():
    client = _client()
    response = client.post("/api/metric_domain", json={
        "域编号": "METR-TRADE", "域名称": "交易域副本", "业务域描述": "重复登记",
        "数据源表": "dwd_trade_order_di",
    })
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert "METR-TRADE" in body["message"]

    total = client.get("/api/metric_domain").json()["total"]
    assert total == 6


def test_api_dedup_list_and_repeat_adjudication():
    client = _client()
    response = client.get("/api/metric_domain/mapping/dedup-list")
    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["重复域编号数"] == 1

    first = client.post("/api/metric_domain/mapping/adjudicate").json()
    second = client.post("/api/metric_domain/mapping/adjudicate").json()
    assert first["ok"] and second["ok"]
    assert first["summary"] == second["summary"]
    assert first["applied_at"] == second["applied_at"] or first["summary"] == second["summary"]


def test_api_mapping_lookup_by_code():
    client = _client()
    response = client.get("/api/metric_domain/mapping/MAP-METR-TRADE-001")
    assert response.status_code == 200
    body = response.json()
    assert body["数据源表"] == "dwd_trade_order_di"
    assert body["映射状态"] == "历史"

    missing = client.get("/api/metric_domain/mapping/MAP-NOT-THERE")
    assert missing.status_code == 404


def test_api_compute_plan_and_execute_and_results():
    client = _client()

    plan = client.get("/api/data_aggregate/1/compute-plan")
    assert plan.status_code == 200
    plan_body = plan.json()
    assert plan_body["source_tables"] == ["dwd_trade_order_v2_di"]
    assert plan_body["domain_mappings"][0]["mapping_code"] == "MAP-METR-TRADE-006"

    first = client.post("/api/data_aggregate/1/execute")
    assert first.status_code == 200
    first_body = first.json()
    assert first_body["ok"] is True
    assert first_body["entry"]["结果编号"] == "AGG-TRADE-001-R002"

    second = client.post("/api/data_aggregate/1/execute")
    assert second.json()["entry"]["结果编号"] == "AGG-TRADE-001-R003"

    results = client.get("/api/data_aggregate/1/results").json()
    assert results["total"] == 3  # 存量 R001 + 两次新执行
    codes = [item["结果编号"] for item in results["items"]]
    assert codes[0] == "AGG-TRADE-001-R001"

    # 旧结果来源为原登记表，新结果为补录表，各自可查。
    old = results["items"][0]
    new = results["items"][-1]
    assert old["来源表"] == ["dwd_trade_order_di"]
    assert new["来源表"] == ["dwd_trade_order_v2_di"]
    assert old["来源"][0]["mapping_code"] != new["来源"][0]["mapping_code"]

    # 输出与计划来源一致。
    for row in new["输出数据"]:
        assert row["__mapping"] == new["来源"][0]["mapping_code"]
        assert row["__source_table"] in new["来源表"]


def test_api_execute_refused_when_connection_unconfirmed():
    client = _client()
    ds = next(r for r in store.rows("datasource_connect") if r.get("连接编号") == "DS-TRADE-V2")
    ds["status"] = "已断开"

    response = client.post("/api/data_aggregate/1/execute")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert "拒绝执行" in body["message"]

    results = client.get("/api/data_aggregate/1/results").json()
    assert results["total"] == 1  # 只有存量结果，没有新增

    chain = client.get("/api/chain/metric-compute?domain_code=METR-TRADE&metric_code=DERI-GMV&aggregate_id=1")
    assert chain.status_code == 400


def test_api_metric_chain_uses_effective_mapping():
    client = _client()
    response = client.get("/api/chain/metric-compute?domain_code=METR-TRADE&metric_code=DERI-GMV&aggregate_id=1")
    assert response.status_code == 200
    body = response.json()
    assert body["domain_mapping"]["映射编号"] == "MAP-METR-TRADE-006"
    assert body["compute_plan"]["source_tables"] == ["dwd_trade_order_v2_di"]

    validate = client.get("/api/chain/engine/validate-metric-chain?domain_code=METR-TRADE&metric_code=DERI-GMV")
    assert validate.status_code == 200
    assert validate.json()["mapping_code"] == "MAP-METR-TRADE-006"
