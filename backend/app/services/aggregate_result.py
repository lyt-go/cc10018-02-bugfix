"""聚合结果与来源记录。

每次执行聚合计划都会落一条结果记录，记录里固化生成时使用的映射、连接与源表快照。
这样在重复域裁决之后：

* 新计划只能使用去重清单里的有效映射；
* 既有结果仍按自己生成时的映射快照可查回原始来源，不会被新裁决改写。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.store import store

MODULE = "data_aggregate_result"


def _result_code(aggregate_code: str, rows: list[dict[str, Any]]) -> str:
    seq = len(rows) + 1
    return f"{aggregate_code}-R{seq:03d}"


def list_results(aggregate_config_id: int | None = None) -> list[dict[str, Any]]:
    rows = store.rows(MODULE)
    if aggregate_config_id is not None:
        rows = [row for row in rows if int(row.get("aggregate_id", 0)) == int(aggregate_config_id)]
    return [dict(row) for row in rows]


def get_result(result_code: str) -> dict[str, Any] | None:
    for row in store.rows(MODULE):
        if row.get("结果编号") == result_code:
            return dict(row)
    return None


def append_result(
    *,
    agg: dict[str, Any],
    plan: dict[str, Any],
    output_rows: list[dict[str, Any]],
    executed_at: str | None = None,
) -> dict[str, Any]:
    rows = store.rows(MODULE)
    record = {
        "id": max((int(row.get("id", 0)) for row in rows), default=0) + 1,
        "结果编号": _result_code(str(agg.get("配置编号", "AGG")), rows),
        "aggregate_id": int(agg.get("id", 0)),
        "配置编号": agg.get("配置编号", ""),
        "输出表名": agg.get("输出表名", ""),
        "生成时间": executed_at or datetime.now().isoformat(timespec="seconds"),
        "计划来源指纹": plan.get("source_fingerprint", ""),
        "来源": [dict(item) for item in plan.get("domain_mappings", [])],
        "来源表": list(plan.get("source_tables", [])),
        "输出行数": len(output_rows),
        "输出数据": [dict(row) for row in output_rows],
        "status": "已生成",
        "pending": False,
        "abnormal": False,
    }
    rows.append(record)
    return dict(record)
