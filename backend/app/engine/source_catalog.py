"""源表样例数据：用于验证聚合计划到底读取了哪一张表。

同一个指标域可能同时存在两条校验通过的映射（原登记 + 补录），它们指向不同的数据表。
为了让「新计划读的是哪张表」可以被直接观察，这里给两张交易表各放一笔不同的交易：

* ``dwd_trade_order_di``   —— 原登记映射对应的表，交易单号 ``TRADE-A``
* ``dwd_trade_order_v2_di`` —— 补录映射对应的表，交易单号 ``TRADE-B``

真实项目里这里会换成查询引擎；当前实现只保留足够验证来源的可读数据。
"""
from __future__ import annotations

from typing import Any

SOURCE_TABLE_ROWS: dict[str, list[dict[str, Any]]] = {
    "dwd_trade_order_di": [
        {"交易单号": "TRADE-A", "店铺": "SHOP-101", "订单金额": 100.0, "dt": "2026-11-10"},
    ],
    "dwd_trade_order_v2_di": [
        {"交易单号": "TRADE-B", "店铺": "SHOP-202", "订单金额": 250.0, "dt": "2026-11-15"},
    ],
    "dwd_user_active_di": [
        {"用户ID": "USER-1", "访问时长秒": 45, "dt": "2026-11-10"},
    ],
    "dwd_item_view_di": [
        {"类目": "CAT-1", "点击数": 10, "曝光数": 200, "dt": "2026-11-10"},
    ],
    "dwd_logis_delivery_di": [
        {"仓库": "WH-1", "配送小时": 24.0, "dt": "2026-11-10"},
    ],
}


def source_rows(table: str) -> list[dict[str, Any]]:
    """返回源表数据副本，避免调用方改到样例数据。"""
    return [dict(row) for row in SOURCE_TABLE_ROWS.get(str(table or "").strip(), [])]


def source_tables() -> list[str]:
    return sorted(SOURCE_TABLE_ROWS)
