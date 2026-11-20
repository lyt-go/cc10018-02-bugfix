"""指标域映射登记与去重裁决。

背景
----
指标域登记入口过去只校验字段是否齐全，不校验「域编号」唯一性，导致同一个域编号
可能对应多条指标域记录（例如原登记之外又补录了一条），并且这些记录可能指向不同的
数据源表。指标计算链过去用 ``next(...)`` 取第一条域记录，聚合计划既不记录用了哪条
映射，也不记录连接与源表，既无法保证新计划读的是哪张表，也无法追溯旧结果的来源。

本模块提供三件事：

1. ``ensure_mappings``：为每条指标域记录建立一条稳定的映射（映射编号由域记录 id 决定，
   重复调用不会重复建）。
2. ``reconcile`` / ``build_dedup_list``：批量检查存量重复域，按固定规则为每个域编号
   裁决出**唯一**一条「可供新计算使用」的有效映射；其余映射标记为历史，保留下来供
   既有聚合结果追溯，不会被删除。
3. ``resolve_effective``：供聚合计划取有效映射，计划只认这个入口，不再自己翻域表。

裁决规则（固定、可复现）
------------------------
* 只有「校验状态=已校验」的映射参与裁决；
* 在其中取校验时间最新的一条（时间相同取域记录 id 较大者）作为唯一有效映射；
* 选择只看校验时间，不看连接的实时状态——连接断开时不会静默切回旧表，
  而是把有效映射标记为「连接未确认」，计划执行直接拒绝；
* 落选映射一律标记为「历史」，不删除，旧结果按生成时的映射快照仍可查回原来源；
* 当且仅当有效映射的连接存在且为「已连接」时，才允许新计算使用。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.store import store

MODULE = "metric_domain_mapping"
DOMAIN_MODULE = "metric_domain"

SOURCE_ORIGINAL = "原始登记"
SOURCE_SUPPLEMENT = "补录"

RULE_TEXT = "校验通过的映射中取校验时间最新的一条为唯一有效映射；历史映射保留供旧结果追溯"

# 域记录处于这些状态，说明其映射曾经通过校验。
VALIDATED_DOMAIN_STATUSES = {"已启用", "已停用"}


def normalize_code(code: Any) -> str:
    """域编号比较口径：去首尾空白、忽略大小写。"""
    return str(code or "").strip().casefold()


def _clean(value: Any) -> str:
    return str(value or "").strip()


class DomainMappingService:
    """指标域映射的唯一权威入口。"""

    # ───────────────────────────────────────────────
    # 同步与查询
    # ───────────────────────────────────────────────
    def ensure_mappings(self) -> list[dict[str, Any]]:
        """为每条指标域记录补齐映射，并同步域记录上的展示字段。

        映射编号由「域编号 + 域记录 id」决定，因此重复调用是幂等的；存量历史映射
        即使已被裁决为历史，也始终保留。
        """
        mappings = store.rows(MODULE)
        by_domain_id = {int(m.get("域记录id", 0)): m for m in mappings}
        for row in store.rows(DOMAIN_MODULE):
            domain_id = int(row.get("id", 0))
            mapping = by_domain_id.get(domain_id)
            if mapping is None:
                mapping = {
                    "id": max((int(m.get("id", 0)) for m in mappings), default=0) + 1,
                    "映射编号": self.mapping_code(row),
                    "域记录id": domain_id,
                }
                mappings.append(mapping)
                by_domain_id[domain_id] = mapping
            # 域记录可能被修正过，映射始终跟随当前值。
            mapping["域编号"] = _clean(row.get("域编号"))
            mapping["域名称"] = _clean(row.get("域名称"))
            mapping["数据源表"] = _clean(row.get("数据源表"))
            mapping["映射来源"] = _clean(row.get("登记来源")) or SOURCE_ORIGINAL
            mapping["校验状态"] = "已校验" if row.get("status") in VALIDATED_DOMAIN_STATUSES else "待校验"
            mapping["校验时间"] = _clean(row.get("校验时间")) or _clean(row.get("创建时间"))
        return mappings

    def mapping_code(self, domain_row: dict[str, Any]) -> str:
        domain_id = int(domain_row.get("id", 0))
        return f"MAP-{_clean(domain_row.get('域编号'))}-{domain_id:03d}"

    def list_mappings(self, domain_code: str | None = None) -> list[dict[str, Any]]:
        self.reconcile()
        rows = store.rows(MODULE)
        if domain_code is not None:
            wanted = normalize_code(domain_code)
            rows = [row for row in rows if normalize_code(row.get("域编号")) == wanted]
        return [self._public(row) for row in rows]

    def get_mapping(self, mapping_code: str) -> dict[str, Any] | None:
        self.reconcile()
        for row in store.rows(MODULE):
            if row.get("映射编号") == mapping_code:
                return self._public(row)
        return None

    def find_by_domain_record(self, domain_id: int) -> dict[str, Any] | None:
        self.reconcile()
        for row in store.rows(MODULE):
            if int(row.get("域记录id", 0)) == int(domain_id):
                return self._public(row)
        return None

    def resolve_effective(self, domain_code: str) -> dict[str, Any] | None:
        """返回该域编号下唯一可供新计算使用的映射；没有则返回 None。"""
        self.reconcile()
        group = self._groups().get(normalize_code(domain_code))
        if not group:
            return None
        winner = group.get("_winner")
        return self._public(winner) if winner else None

    def effective_domain_row(self, domain_code: str) -> dict[str, Any] | None:
        mapping = self.resolve_effective(domain_code)
        if not mapping:
            return None
        return store.find(DOMAIN_MODULE, int(mapping.get("域记录id", 0)))

    # ───────────────────────────────────────────────
    # 连接确认
    # ───────────────────────────────────────────────
    def _connection(self, table: str) -> dict[str, Any] | None:
        if not table:
            return None
        datasources = store.rows("datasource_connect")
        for ds in datasources:
            if _clean(ds.get("数据库名")) == table:
                return ds
        for ds in datasources:
            if _clean(ds.get("连接编号")) == table:
                return ds
        return None

    def _connection_fields(self, table: str) -> dict[str, Any]:
        ds = self._connection(table)
        status = _clean(ds.get("status")) if ds else ""
        confirmed = bool(ds) and status == "已连接"
        return {
            "连接编号": _clean(ds.get("连接编号")) if ds else "",
            "连接状态": status or "未登记",
            "连接地址": _clean(ds.get("连接地址")) if ds else "",
            "连接可确认": confirmed,
        }

    # ───────────────────────────────────────────────
    # 裁决
    # ───────────────────────────────────────────────
    def reconcile(self) -> dict[str, Any]:
        """批量检查并裁决存量重复域，返回分组结果（幂等）。"""
        self.ensure_mappings()
        return self._groups()

    def _groups(self) -> dict[str, dict[str, Any]]:
        self.ensure_mappings()
        mappings = store.rows(MODULE)
        domain_rows = store.rows(DOMAIN_MODULE)

        records_by_code: dict[str, list[dict[str, Any]]] = {}
        for row in domain_rows:
            records_by_code.setdefault(normalize_code(row.get("域编号")), []).append(row)

        mappings_by_code: dict[str, list[dict[str, Any]]] = {}
        for mapping in mappings:
            mappings_by_code.setdefault(normalize_code(mapping.get("域编号")), []).append(mapping)

        groups: dict[str, dict[str, Any]] = {}
        for code, rows in records_by_code.items():
            code_mappings = mappings_by_code.get(code, [])
            for mapping in code_mappings:
                table = _clean(mapping.get("数据源表"))
                mapping.update(self._connection_fields(table))

            validated = [m for m in code_mappings if m.get("校验状态") == "已校验"]
            winner = max(
                validated,
                key=lambda m: (str(m.get("校验时间") or ""), int(m.get("域记录id", 0))),
            ) if validated else None

            for mapping in code_mappings:
                if mapping is winner:
                    mapping["映射状态"] = "有效" if mapping.get("连接可确认") else "有效（连接未确认）"
                    mapping["可供新计算"] = bool(mapping.get("连接可确认"))
                    mapping["替代映射编号"] = ""
                elif mapping.get("校验状态") != "已校验":
                    mapping["映射状态"] = "待校验"
                    mapping["可供新计算"] = False
                    mapping["替代映射编号"] = _clean(winner.get("映射编号")) if winner else ""
                else:
                    mapping["映射状态"] = "历史"
                    mapping["可供新计算"] = False
                    mapping["替代映射编号"] = _clean(winner.get("映射编号")) if winner else ""

            source_tables = sorted({_clean(m.get("数据源表")) for m in code_mappings if _clean(m.get("数据源表"))})
            conflict = len(source_tables) > 1 and len(code_mappings) > 1
            groups[code] = {
                "域编号": _clean(rows[0].get("域编号")),
                "域记录数": len(rows),
                "重复": len(rows) > 1,
                "冲突": conflict,
                "记录": [self._record_summary(row) for row in rows],
                "映射": [self._public(m) for m in code_mappings],
                "有效映射": self._public(winner) if winner else None,
                "有效映射编号": _clean(winner.get("映射编号")) if winner else "",
                "有效数据源表": _clean(winner.get("数据源表")) if winner else "",
                "连接编号": _clean(winner.get("连接编号")) if winner else "",
                "连接状态": _clean(winner.get("连接状态")) if winner else "未登记",
                "新计划可用": bool(winner and winner.get("可供新计算")),
                "冲突说明": self._conflict_note(rows, code_mappings, winner, source_tables),
                "保留规则": RULE_TEXT,
                "_winner": winner,
            }
        return groups

    def _record_summary(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": int(row.get("id", 0)),
            "域名称": _clean(row.get("域名称")),
            "数据源表": _clean(row.get("数据源表")),
            "status": _clean(row.get("status")),
            "登记来源": _clean(row.get("登记来源")) or SOURCE_ORIGINAL,
            "创建时间": _clean(row.get("创建时间")),
            "校验时间": _clean(row.get("校验时间")) or _clean(row.get("创建时间")),
        }

    def _conflict_note(
        self,
        rows: list[dict[str, Any]],
        mappings: list[dict[str, Any]],
        winner: dict[str, Any] | None,
        source_tables: list[str],
    ) -> str:
        code = _clean(rows[0].get("域编号"))
        if len(mappings) <= 1:
            return ""
        if len(source_tables) > 1:
            parts = []
            for mapping in mappings:
                parts.append(f"{mapping.get('映射来源')}指向 {mapping.get('数据源表')}")
            note = f"指标域 {code} 存在 {len(mappings)} 条映射：" + "，".join(parts)
            if winner:
                note += (
                    f"；新计划采用 {winner.get('映射编号')}（{winner.get('数据源表')}），"
                    f"其余映射保留供既有聚合结果追溯"
                )
            else:
                note += "；当前没有校验通过的有效映射，新计划不可执行"
            return note
        return f"指标域 {code} 重复登记但数据源表一致（{source_tables[0]}），已归并为一条有效映射"

    @staticmethod
    def _public(mapping: dict[str, Any] | None) -> dict[str, Any] | None:
        if mapping is None:
            return None
        return {key: value for key, value in mapping.items() if not key.startswith("_")}

    # ───────────────────────────────────────────────
    # 去重清单
    # ───────────────────────────────────────────────
    def build_dedup_list(self) -> dict[str, Any]:
        """输出指标域去重清单：每个域编号一条有效映射，重复项全部可见。"""
        applied = any(_clean(m.get("裁决时间")) for m in store.rows(MODULE))
        groups = self._groups()
        items = [self._public_group(group) for _, group in sorted(groups.items())]

        duplicate_codes = [item for item in items if item["重复"]]
        usable = [item for item in items if item["新计划可用"]]
        summary = {
            "指标域记录数": len(store.rows(DOMAIN_MODULE)),
            "去重后域编号数": len(items),
            "重复域编号数": len(duplicate_codes),
            "冗余记录数": sum(int(item["域记录数"]) - 1 for item in duplicate_codes),
            "冲突域编号数": sum(1 for item in items if item["冲突"]),
            "有效映射数": sum(1 for item in items if item["有效映射编号"]),
            "新计划可用域数": len(usable),
            "不可用域数": sum(1 for item in items if not item["新计划可用"]),
        }
        return {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "applied": applied,
            "rule": RULE_TEXT,
            "summary": summary,
            "items": items,
        }

    def apply_adjudication(self) -> dict[str, Any]:
        """执行（并持久化）存量重复域裁决，重复调用结果一致。"""
        groups = self.reconcile()
        applied_at = datetime.now().isoformat(timespec="seconds")
        for mapping in store.rows(MODULE):
            mapping["裁决时间"] = applied_at
        result = self.build_dedup_list()
        result["applied_at"] = applied_at
        result["adjudicated_codes"] = sorted(group["域编号"] for group in groups.values() if group["重复"])
        return result

    def _public_group(self, group: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in group.items() if not key.startswith("_")}


# 计算引擎与业务服务共用一个无状态实例即可。
domain_mapping_service = DomainMappingService()
