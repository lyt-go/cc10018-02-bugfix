"""数据聚合业务规则：状态流转、字段校验、跨模块依赖校验与计算计划生成。"""
from __future__ import annotations

from typing import Any

from app.store import store
from app.engine.metric_calculator import MetricCalculator
from app.services.domain_mapping import domain_mapping_service

MODULE = "data_aggregate"
LIST_FIELDS = ["配置编号", "数据来源", "聚合维度", "聚合函数", "过滤条件", "输出表名", "增量键", "更新策略"]
REQUIRED_FIELDS = ["配置编号", "数据来源", "聚合维度"]
STATUS_ORDER = ["草稿", "待校验", "已生效", "已停用"]
ACTION_RULES = {"提交校验": "待校验", "标记生效": "已生效", "停用配置": "已停用"}
NEGATIVE_ACTIONS = ["停用配置"]


class DataAggregateService:
    def list_entries(
        self,
        *,
        filters: dict[str, str] | None = None,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        for field, expected in (filters or {}).items():
            rows = [row for row in rows if expected.casefold() in str(row.get(field, "")).casefold()]
        if keyword:
            rows = [row for row in rows if keyword.casefold() in str(row.get("配置编号", "")).casefold()]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = (page - 1) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        if entry:
            # 附加计算计划预览
            calc = MetricCalculator()
            entry["_compute_plan"] = calc.build_compute_plan(entry_id)
            entry["_preview_sql"] = calc.preview_sql(entry_id)
        return entry

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing

        # 校验数据来源中的指标域和派生指标是否存在
        source = values.get("数据来源", "")
        errors = self._validate_source(source)
        if errors:
            return None, errors

        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in LIST_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"聚合配置 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于数据聚合可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"聚合配置已{action}"

    def _validate_source(self, source: str) -> list[str]:
        errors = []
        calc = MetricCalculator()
        for part in str(source).split(","):
            part = part.strip()
            if part.startswith("METR-"):
                mapping = domain_mapping_service.resolve_effective(part)
                if mapping is None:
                    errors.append(f"指标域 {part} 没有可供新计算使用的有效映射")
                elif not mapping.get("可供新计算"):
                    errors.append(
                        f"指标域 {part} 的有效映射 {mapping.get('映射编号')} 连接无法确认"
                    )
            elif part.startswith("DERI-"):
                metric = calc.get_metric_by_code(part)
                if not metric:
                    errors.append(f"派生指标 {part} 不存在")
                elif metric.get("status") != "已生效":
                    errors.append(f"派生指标 {part} 未生效")
        return errors

    def get_compute_plan(self, entry_id: int) -> dict[str, Any]:
        calc = MetricCalculator()
        return calc.build_compute_plan(entry_id)

    def execute(self, entry_id: int) -> tuple[dict[str, Any] | None, str]:
        """执行聚合计划；无法确认有效连接时拒绝执行。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"聚合配置 {entry_id} 不存在或已归档"
        if entry.get("status") != "已生效":
            return None, f"聚合配置状态为 {entry.get('status')}，仅已生效配置可执行"
        result = MetricCalculator().execute_compute_plan(entry_id)
        if not result.get("ok"):
            reasons = "；".join(result.get("blocking_issues") or [result.get("error", "计算计划不可执行")])
            return None, f"无法确认有效连接，已拒绝执行：{reasons}"
        return result, f"聚合计划已执行，结果 {result.get('result_code')}"
