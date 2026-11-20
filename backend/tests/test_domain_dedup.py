"""指标域去重与聚合来源追溯测试。

覆盖需求：

1. 登记入口拒绝重复域编号；
2. 批量检查存量重复域，并裁决出唯一有效映射；
3. 新聚合计划从去重清单取有效映射，输出来源必须与计划一致；
4. 无法确认有效连接时拒绝执行计划；
5. 重复提交幂等；
6. 既有聚合结果仍能按生成时的来源查回；
7. 用两张各有一笔不同交易的表复核新计划取的是哪张表。
"""
from __future__ import annotations

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.engine.chain_orchestrator import ChainOrchestrator
from app.engine.metric_calculator import MetricCalculator
from app.engine.source_catalog import SOURCE_TABLE_ROWS
from app.services.aggregate_result import get_result, list_results
from app.services.data_aggregate import DataAggregateService
from app.services.domain_mapping import domain_mapping_service
from app.services.metric_domain import MetricDomainService
from app.store import store


def reset_store():
    store._tables = {}
    from app.seed import SEED_ROWS
    store._tables = {name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()}


def _codes(dedup: dict) -> dict[str, dict]:
    return {item["域编号"]: item for item in dedup["items"]}


# ───────────────────────────────────────────────
# 1. 登记入口
# ───────────────────────────────────────────────
def test_register_rejects_duplicate_domain_code():
    reset_store()
    service = MetricDomainService()
    before = len(store.rows("metric_domain"))

    entry, errors = service.create_entry({
        "域编号": "METR-TRADE", "域名称": "交易域（再次登记）", "业务域描述": "重复登记",
        "数据源表": "dwd_trade_order_di",
    })
    assert entry is None
    assert any("METR-TRADE" in message for message in errors)
    assert len(store.rows("metric_domain")) == before


def test_register_duplicate_ignores_whitespace_and_case():
    reset_store()
    service = MetricDomainService()

    entry, errors = service.create_entry({
        "域编号": "  metr-trade  ", "域名称": "交易域（大小写）", "业务域描述": "重复登记",
    })
    assert entry is None, errors

    entry, errors = service.create_entry({
        "域编号": "METR-NEW", "域名称": "新域", "业务域描述": "合法登记",
        "数据源表": "dwd_user_active_di",
    })
    assert entry is not None, errors
    assert entry["status"] == "草稿"
    # 新登记的域立即可在去重清单里看到，且暂时不能用于新计算。
    dedup = domain_mapping_service.build_dedup_list()
    item = _codes(dedup)["METR-NEW"]
    assert item["域记录数"] == 1
    assert item["新计划可用"] is False


# ───────────────────────────────────────────────
# 2. 存量重复域裁决
# ───────────────────────────────────────────────
def test_dedup_list_reports_duplicate_and_conflict():
    reset_store()
    dedup = domain_mapping_service.build_dedup_list()
    items = _codes(dedup)

    assert dedup["summary"]["指标域记录数"] == 6
    assert dedup["summary"]["去重后域编号数"] == 5
    assert dedup["summary"]["重复域编号数"] == 1
    assert dedup["summary"]["冗余记录数"] == 1
    assert dedup["summary"]["冲突域编号数"] == 1

    trade = items["METR-TRADE"]
    assert trade["域记录数"] == 2
    assert trade["重复"] and trade["冲突"]
    assert trade["有效映射编号"] == "MAP-METR-TRADE-006"
    assert trade["有效数据源表"] == "dwd_trade_order_v2_di"
    assert trade["新计划可用"] is True
    # 旧映射仍在清单里，供追溯。
    statuses = {m["映射编号"]: m["映射状态"] for m in trade["映射"]}
    assert statuses["MAP-METR-TRADE-001"] == "历史"
    assert statuses["MAP-METR-TRADE-006"] == "有效"


def test_dedup_is_idempotent():
    reset_store()
    first = domain_mapping_service.build_dedup_list()
    second = domain_mapping_service.build_dedup_list()
    assert first["summary"] == second["summary"]
    assert [m["映射编号"] for m in _codes(first)["METR-TRADE"]["映射"]] == [
        m["映射编号"] for m in _codes(second)["METR-TRADE"]["映射"]
    ]
    assert len(store.rows("metric_domain_mapping")) == len(store.rows("metric_domain"))


def test_apply_adjudication_keeps_history_and_is_repeatable():
    reset_store()
    first = domain_mapping_service.apply_adjudication()
    second = domain_mapping_service.apply_adjudication()

    assert first["applied_at"]
    assert second["adjudicated_codes"] == ["METR-TRADE"]
    assert first["summary"] == second["summary"]
    assert _codes(first)["METR-TRADE"]["有效映射编号"] == "MAP-METR-TRADE-006"
    # 历史映射没有被删除。
    old = domain_mapping_service.get_mapping("MAP-METR-TRADE-001")
    assert old["映射状态"] == "历史"
    assert old["数据源表"] == "dwd_trade_order_di"


def test_only_one_mapping_usable_per_code():
    reset_store()
    dedup = domain_mapping_service.build_dedup_list()
    for item in dedup["items"]:
        usable = [m for m in item["映射"] if m["可供新计算"]]
        assert len(usable) <= 1
        if item["新计划可用"]:
            assert len(usable) == 1
            assert usable[0]["映射编号"] == item["有效映射编号"]


# ───────────────────────────────────────────────
# 3. 新计划取有效映射，来源与输出一致
# ───────────────────────────────────────────────
def test_compute_plan_uses_effective_mapping():
    reset_store()
    plan = MetricCalculator().build_compute_plan(1)

    assert plan["ok"]
    assert plan["source_tables"] == ["dwd_trade_order_v2_di"]
    binding = plan["domain_mappings"][0]
    assert binding["mapping_code"] == "MAP-METR-TRADE-006"
    assert binding["source_table"] == "dwd_trade_order_v2_di"
    assert binding["connection_code"] == "DS-TRADE-V2"
    assert plan["source_fingerprint"] == "METR-TRADE=MAP-METR-TRADE-006"
    load_step = next(step for step in plan["steps"] if step["type"] == "load_domain")
    assert load_step["mapping"]["mapping_code"] == binding["mapping_code"]


def test_execute_output_matches_plan_sources():
    reset_store()
    executed = MetricCalculator().execute_compute_plan(1, executed_at="2026-11-20T10:00:00")
    assert executed["ok"], executed

    plan = executed["plan"]
    output = executed["output"]
    assert output, "执行应产出数据"
    # 两张表各一笔不同交易，新计划只能读到有效映射对应的那张表。
    assert {row["交易单号"] for row in output} == {"TRADE-B"}
    for row in output:
        assert row["__source_table"] == "dwd_trade_order_v2_di"
        assert row["__mapping"] == "MAP-METR-TRADE-006"
        assert row["__connection"] == "DS-TRADE-V2"

    record = get_result(executed["result_code"])
    assert record["计划来源指纹"] == plan["source_fingerprint"]
    assert record["来源表"] == plan["source_tables"]
    for row in record["输出数据"]:
        assert row["__source_table"] in record["来源表"]
    assert record["来源"][0]["mapping_code"] == "MAP-METR-TRADE-006"


def test_execute_uses_only_effective_table_for_both_stock_tables():
    """两张交易表各有一笔不同交易，执行结果只能来自计划选中的那一张。"""
    reset_store()
    service = DataAggregateService()
    result, message = service.execute(1)
    assert result is not None, message
    seen_tables = {row["__source_table"] for row in result["output"]}
    assert seen_tables == {"dwd_trade_order_v2_di"}
    assert len(SOURCE_TABLE_ROWS["dwd_trade_order_di"]) == 1
    assert len(SOURCE_TABLE_ROWS["dwd_trade_order_v2_di"]) == 1


# ───────────────────────────────────────────────
# 4. 无法确认连接时拒绝执行
# ───────────────────────────────────────────────
def test_execute_refused_when_connection_unconfirmed():
    reset_store()
    # 让补录映射指向的数据源断开：有效映射的连接不再可确认。
    ds = next(r for r in store.rows("datasource_connect") if r.get("连接编号") == "DS-TRADE-V2")
    ds["status"] = "已断开"

    plan = MetricCalculator().build_compute_plan(1)
    assert plan["ok"] is False
    assert plan["blocking_issues"]
    assert any("连接无法确认" in issue for issue in plan["blocking_issues"])

    executed = MetricCalculator().execute_compute_plan(1)
    assert executed["ok"] is False
    assert "拒绝执行" in executed["error"]
    assert list_results(1) == [] or all(
        r["结果编号"] != "AGG-TRADE-001-R002" for r in list_results(1)
    )

    # 落选的原登记映射虽然连接正常，也不能被用来「将就」执行。
    dedup = domain_mapping_service.build_dedup_list()
    trade = _codes(dedup)["METR-TRADE"]
    assert trade["新计划可用"] is False
    assert trade["有效映射编号"] == "MAP-METR-TRADE-006"


def test_aggregate_create_rejected_without_confirmed_connection():
    reset_store()
    ds = next(r for r in store.rows("datasource_connect") if r.get("连接编号") == "DS-TRADE-V2")
    ds["status"] = "待校验"

    service = DataAggregateService()
    entry, errors = service.create_entry({
        "配置编号": "AGG-TRADE-NEW", "数据来源": "METR-TRADE,DERI-GMV",
        "聚合维度": "dt", "聚合函数": "SUM(gmv)", "输出表名": "ads_trade_new",
    })
    assert entry is None
    assert any("连接无法确认" in message for message in errors)


# ───────────────────────────────────────────────
# 5. 重复提交
# ───────────────────────────────────────────────
def test_repeated_execution_records_distinct_results():
    reset_store()
    first = MetricCalculator().execute_compute_plan(1, executed_at="2026-11-20T10:00:00")
    second = MetricCalculator().execute_compute_plan(1, executed_at="2026-11-20T10:05:00")
    assert first["ok"] and second["ok"]
    assert first["result_code"] != second["result_code"]
    assert first["result_code"] == "AGG-TRADE-001-R002"  # R001 是存量结果
    assert second["result_code"] == "AGG-TRADE-001-R003"
    assert first["source_fingerprint"] == second["source_fingerprint"]

    rows = list_results(1)
    assert len({row["结果编号"] for row in rows}) == len(rows)


def test_repeated_adjudication_does_not_create_mappings():
    reset_store()
    domain_mapping_service.ensure_mappings()
    before = len(store.rows("metric_domain_mapping"))
    for _ in range(3):
        domain_mapping_service.apply_adjudication()
    assert len(store.rows("metric_domain_mapping")) == before == len(store.rows("metric_domain"))


# ───────────────────────────────────────────────
# 6. 既有结果可追溯
# ───────────────────────────────────────────────
def test_existing_result_traceable_to_original_mapping():
    reset_store()
    # 先裁决，再把有效映射切换到另一张表，都不应改写既有结果。
    domain_mapping_service.apply_adjudication()
    record = get_result("AGG-TRADE-001-R001")
    assert record is not None
    assert record["来源"][0]["mapping_code"] == "MAP-METR-TRADE-001"
    assert record["来源表"] == ["dwd_trade_order_di"]
    assert record["输出数据"][0]["交易单号"] == "TRADE-A"
    assert record["输出数据"][0]["__source_table"] == "dwd_trade_order_di"

    # 追溯入口能直接查到旧映射本身。
    mapping = domain_mapping_service.get_mapping(record["来源"][0]["mapping_code"])
    assert mapping["数据源表"] == "dwd_trade_order_di"
    assert mapping["映射状态"] == "历史"


def test_new_result_and_old_result_have_different_sources():
    reset_store()
    executed = MetricCalculator().execute_compute_plan(1, executed_at="2026-11-20T10:00:00")
    old = get_result("AGG-TRADE-001-R001")
    new = executed["result"]

    assert old["计划来源指纹"] != new["计划来源指纹"]
    assert old["来源表"] == ["dwd_trade_order_di"]
    assert new["来源表"] == ["dwd_trade_order_v2_di"]
    assert old["输出数据"][0]["交易单号"] == "TRADE-A"
    assert new["输出数据"][0]["交易单号"] == "TRADE-B"


# ───────────────────────────────────────────────
# 7. 链路级
# ───────────────────────────────────────────────
def test_metric_chain_uses_effective_mapping():
    reset_store()
    result = ChainOrchestrator().run_metric_compute_chain("METR-TRADE", "DERI-GMV", 1)
    assert result["ok"], result
    assert result["domain_mapping"]["映射编号"] == "MAP-METR-TRADE-006"
    assert result["compute_plan"]["source_tables"] == ["dwd_trade_order_v2_di"]


def test_metric_chain_fails_when_connection_unconfirmed():
    reset_store()
    ds = next(r for r in store.rows("datasource_connect") if r.get("连接编号") == "DS-TRADE-V2")
    ds["status"] = "已断开"

    result = ChainOrchestrator().run_metric_compute_chain("METR-TRADE", "DERI-GMV", 1)
    assert result["ok"] is False
    assert result["steps"][0]["status"] == "error"
    assert "compute_plan" not in result


def test_validate_metric_chain_reports_mapping():
    reset_store()
    calc = MetricCalculator()
    ok = calc.validate_metric_chain("METR-TRADE", "DERI-GMV")
    assert ok["ok"]
    assert ok["mapping_code"] == "MAP-METR-TRADE-006"
    assert ok["source_table"] == "dwd_trade_order_v2_di"

    # 待校验的重复域历史记录不会被误用。
    bad = calc.validate_metric_chain("METR-ITEM", "DERI-CTR")
    assert bad["ok"] is False
    assert any("有效映射" in error or "非已启用" in error for error in bad["errors"])
