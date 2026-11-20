"""指标域管理业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.services.domain_mapping import (
    SOURCE_ORIGINAL,
    domain_mapping_service,
    normalize_code,
)
from app.store import store

MODULE = "metric_domain"
LIST_FIELDS = ["域编号", "域名称", "业务域描述", "数据源表", "更新频率", "负责人", "域状态", "创建时间"]
REQUIRED_FIELDS = ["域编号", "域名称", "业务域描述"]
STATUS_ORDER = ["草稿", "待校验", "已启用", "已停用"]
ACTION_RULES = {"提交校验": "待校验", "开启域": "已启用", "停用域": "已停用"}
NEGATIVE_ACTIONS = ["停用域"]


class MetricDomainService:
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
            rows = [row for row in rows if keyword.casefold() in str(row.get("域编号", "")).casefold()]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = (page - 1) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        """登记指标域；域编号必须全平台唯一，重复登记直接拒绝。"""
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, [f"缺少必填字段：{'、'.join(missing)}"]

        domain_code = str(values.get("域编号") or "").strip()
        duplicate = self.find_by_code(domain_code)
        if duplicate is not None:
            return None, [
                f"域编号「{domain_code}」已由记录 id={duplicate.get('id')} 登记，不允许重复登记"
            ]

        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in LIST_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        entry["域状态"] = STATUS_ORDER[0]
        entry["创建时间"] = datetime.now().strftime("%Y-%m-%d")
        entry["登记来源"] = SOURCE_ORIGINAL
        entry["校验时间"] = ""
        rows.append(entry)
        domain_mapping_service.ensure_mappings()
        return entry, []

    def find_by_code(self, domain_code: str) -> dict[str, Any] | None:
        """按比较口径（去空白、忽略大小写）查找已登记的指标域。"""
        wanted = normalize_code(domain_code)
        if not wanted:
            return None
        return next(
            (row for row in store.rows(MODULE) if normalize_code(row.get("域编号")) == wanted),
            None,
        )

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"指标域 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于指标域管理可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        entry["域状态"] = target
        if action == "提交校验":
            entry["校验时间"] = datetime.now().isoformat(timespec="seconds")
        domain_mapping_service.ensure_mappings()
        domain_mapping_service.reconcile()
        return entry, f"指标域已{action}"
