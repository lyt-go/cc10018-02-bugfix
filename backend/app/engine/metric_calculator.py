"""指标计算引擎：支持原子指标→派生指标→聚合指标的链式计算。

核心能力：
1. 公式解析：将「SUM(订单金额)/COUNT(DISTINCT 用户ID)」解析为可执行结构
2. 口径版本控制：同一指标编号支持多版本，按生效时间自动选择
3. 依赖追踪：记录派生指标对原子指标、聚合配置对派生指标的依赖关系
4. 计算编排：按依赖拓扑排序执行，避免循环依赖
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from app.engine.source_catalog import source_rows
from app.services.domain_mapping import domain_mapping_service, normalize_code
from app.store import store


class MetricFormula:
    """单个公式的解析表示。"""

    def __init__(self, raw: str) -> None:
        self.raw = raw.strip()
        self.agg_func: str | None = None
        self.agg_field: str | None = None
        self.operators: list[str] = []
        self.operands: list[str] = []
        self._parse()

    def _parse(self) -> None:
        raw = self.raw
        # 提取聚合函数
        m = re.match(r"(\w+)\(([^)]+)\)", raw)
        if m:
            self.agg_func = m.group(1).upper()
            self.agg_field = m.group(2).strip()
        # 提取四则运算
        parts = re.split(r"([+\-*/])", raw)
        for p in parts:
            p = p.strip()
            if p in {"+", "-", "*", "/"}:
                self.operators.append(p)
            elif p:
                self.operands.append(p)

    def to_sql_expr(self, table_alias: str = "t") -> str:
        if self.agg_func and self.agg_field:
            if self.agg_func == "COUNT_DISTINCT":
                return f"COUNT(DISTINCT {table_alias}.{self.agg_field})"
            return f"{self.agg_func}({table_alias}.{self.agg_field})"
        return self.raw


class MetricCalculator:
    """指标计算引擎。"""

    def __init__(self) -> None:
        self._cache: dict[str, Any] = {}

    def list_active_domains(self) -> list[dict[str, Any]]:
        """获取所有已启用的指标域；重复域编号只保留有效映射对应的那条。"""
        domain_mapping_service.reconcile()
        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in store.rows("metric_domain"):
            if row.get("status") != "已启用":
                continue
            code = normalize_code(row.get("域编号"))
            if code in seen:
                continue
            mapping = domain_mapping_service.resolve_effective(row.get("域编号", ""))
            if not mapping or int(mapping.get("域记录id", 0)) != int(row.get("id", 0)):
                continue
            seen.add(code)
            result.append(row)
        return result

    def list_effective_metrics(self) -> list[dict[str, Any]]:
        """获取所有已生效的派生指标。"""
        return [r for r in store.rows("derived_metric") if r.get("status") == "已生效"]

    def get_metric_by_code(self, code: str) -> dict[str, Any] | None:
        for r in store.rows("derived_metric"):
            if r.get("指标编号") == code:
                return r
        return None

    def parse_formula(self, formula_raw: str) -> MetricFormula:
        return MetricFormula(formula_raw)

    def compute_dependencies(self, metric_code: str) -> list[str]:
        """获取指定指标的直接依赖（父指标编号列表）。"""
        metric = self.get_metric_by_code(metric_code)
        if not metric:
            return []
        parent = metric.get("父指标编号", "")
        if parent and parent != metric_code:
            return [parent]
        return []

    def topological_sort(self, metric_codes: list[str]) -> list[str]:
        """按依赖拓扑排序指标，保证先计算原子指标。"""
        visited: set[str] = set()
        result: list[str] = []

        def dfs(code: str) -> None:
            if code in visited:
                return
            visited.add(code)
            for dep in self.compute_dependencies(code):
                dfs(dep)
            result.append(code)

        for code in metric_codes:
            dfs(code)
        return result

    def build_compute_plan(self, aggregate_config_id: int) -> dict[str, Any]:
        """为一条聚合配置生成计算计划。

        指标域不再按「域表第一条」取，而是统一从指标域去重清单取得唯一有效映射。
        计划里固化本次使用的映射、连接与源表快照；只要有一项有效连接无法确认，
        计划就不可执行（``ok=False``），路由层据此拒绝执行。
        """
        agg = store.find("data_aggregate", aggregate_config_id)
        if not agg:
            return {"ok": False, "error": "聚合配置不存在"}

        source = agg.get("数据来源", "")
        dims = agg.get("聚合维度", "")
        funcs = agg.get("聚合函数", "")

        # 从数据来源解析依赖的指标域和派生指标
        domain_codes = []
        metric_codes = []
        for part in str(source).split(","):
            part = part.strip()
            if part.startswith("METR-"):
                domain_codes.append(part)
            elif part.startswith("DERI-"):
                metric_codes.append(part)

        plan: dict[str, Any] = {
            "ok": True,
            "aggregate_id": aggregate_config_id,
            "aggregate_code": agg.get("配置编号", ""),
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "steps": [],
            "domain_mappings": [],
            "sources": [],
            "source_tables": [],
            "blocking_issues": [],
        }

        # Step 1: 从去重清单加载指标域有效映射
        for dc in domain_codes:
            mapping = domain_mapping_service.resolve_effective(dc)
            if mapping is None:
                plan["ok"] = False
                plan["blocking_issues"].append(f"指标域 {dc} 没有可用于新计算的有效映射")
                plan["steps"].append({
                    "step": 1,
                    "type": "load_domain",
                    "domain_code": dc,
                    "mapping": None,
                    "status": "error",
                    "description": f"指标域 {dc} 没有有效映射，计划不可执行",
                })
                continue

            binding = {
                "domain_code": dc,
                "mapping_code": mapping.get("映射编号", ""),
                "domain_record_id": int(mapping.get("域记录id", 0)),
                "mapping_source": mapping.get("映射来源", ""),
                "source_table": mapping.get("数据源表", ""),
                "connection_code": mapping.get("连接编号", ""),
                "connection_status": mapping.get("连接状态", ""),
                "connection_address": mapping.get("连接地址", ""),
                "connection_confirmed": bool(mapping.get("连接可确认")),
                "usable": bool(mapping.get("可供新计算")),
            }
            plan["domain_mappings"].append(binding)
            plan["sources"].append({
                "domain_code": dc,
                "mapping_code": binding["mapping_code"],
                "source_table": binding["source_table"],
                "connection_code": binding["connection_code"],
                "connection_status": binding["connection_status"],
            })
            if binding["source_table"] not in plan["source_tables"]:
                plan["source_tables"].append(binding["source_table"])

            if not binding["usable"]:
                plan["ok"] = False
                plan["blocking_issues"].append(
                    f"指标域 {dc} 的有效映射 {binding['mapping_code']} 连接无法确认（连接 {binding['connection_code'] or '未登记'}，状态 {binding['connection_status'] or '未知'}）"
                )
                plan["steps"].append({
                    "step": 1,
                    "type": "load_domain",
                    "domain_code": dc,
                    "mapping": binding,
                    "status": "error",
                    "description": f"无法确认有效连接，不可执行",
                })
            else:
                plan["steps"].append({
                    "step": 1,
                    "type": "load_domain",
                    "domain_code": dc,
                    "mapping": binding,
                    "status": "ok",
                    "description": (
                        f"从指标域 {dc} 的有效映射 {binding['mapping_code']}"
                        f"（{binding['mapping_source']}）读取 {binding['source_table']}，"
                        f"连接 {binding['connection_code']}"
                    ),
                })

        # Step 2: 计算派生指标（拓扑排序后）
        sorted_metrics = self.topological_sort(metric_codes)
        for idx, mc in enumerate(sorted_metrics, start=2):
            metric = self.get_metric_by_code(mc)
            formula = self.parse_formula(metric.get("计算公式", "") if metric else "")
            plan["steps"].append({
                "step": idx,
                "type": "compute_derived",
                "metric_code": mc,
                "formula": formula.raw,
                "sql_expr": formula.to_sql_expr(),
                "description": f"计算派生指标 {mc}: {formula.raw}",
            })

        # Step 3: 聚合
        plan["steps"].append({
            "step": len(plan["steps"]) + 1,
            "type": "aggregate",
            "dimensions": dims,
            "functions": funcs,
            "output_table": agg.get("输出表名", ""),
            "description": f"按 {dims} 聚合，函数 {funcs}",
        })

        plan["source_fingerprint"] = self.source_fingerprint(plan)
        return plan

    def source_fingerprint(self, plan: dict[str, Any]) -> str:
        """计划来源指纹：由域编号→映射编号配对决定，用于核对输出与计划一致。"""
        pairs = [
            f"{item['domain_code']}={item['mapping_code']}"
            for item in plan.get("domain_mappings", [])
        ]
        return "|".join(sorted(pairs))

    def execute_compute_plan(self, aggregate_config_id: int, executed_at: str | None = None) -> dict[str, Any]:
        """执行聚合计划并落一条结果记录。

        执行前必须能从去重清单确认有效连接，否则直接拒绝。输出的每行都带上
        ``__source`` 来源快照，保证「输出数据的来源」与「计划使用的映射」可逐字段核对。
        """
        from app.services.aggregate_result import append_result

        agg = store.find("data_aggregate", aggregate_config_id)
        if not agg:
            return {"ok": False, "error": f"聚合配置 {aggregate_config_id} 不存在"}

        plan = self.build_compute_plan(aggregate_config_id)
        if not plan.get("ok"):
            return {
                "ok": False,
                "error": "无法确认有效连接，已拒绝执行聚合计划",
                "blocking_issues": plan.get("blocking_issues", []),
                "plan": plan,
            }

        output_rows: list[dict[str, Any]] = []
        for binding in plan["domain_mappings"]:
            for row in source_rows(binding["source_table"]):
                row["__domain"] = binding["domain_code"]
                row["__mapping"] = binding["mapping_code"]
                row["__source_table"] = binding["source_table"]
                row["__connection"] = binding["connection_code"]
                output_rows.append(row)

        record = append_result(agg=agg, plan=plan, output_rows=output_rows, executed_at=executed_at)
        return {
            "ok": True,
            "aggregate_id": aggregate_config_id,
            "result_code": record["结果编号"],
            "result": record,
            "plan": plan,
            "output": output_rows,
            "source_fingerprint": plan.get("source_fingerprint", ""),
        }

    def preview_sql(self, aggregate_config_id: int) -> str:
        """为聚合配置生成预览SQL；指标域一律使用有效映射的源表。"""
        plan = self.build_compute_plan(aggregate_config_id)
        if not plan.get("ok"):
            reasons = "；".join(plan.get("blocking_issues") or [plan.get("error", "计算计划生成失败")])
            return f"-- 计算计划不可执行：{reasons}"

        lines = ["-- 指标聚合计算SQL（由计算引擎自动生成）"]
        lines.append(f"-- 来源指纹: {plan.get('source_fingerprint', '')}")
        lines.append("WITH")

        ctes: list[str] = []
        for step in plan["steps"]:
            if step["type"] == "load_domain":
                mapping = step.get("mapping") or {}
                table = mapping.get("source_table", "")
                ctes.append(
                    f"  domain_{step['domain_code']} AS (\n"
                    f"    -- 映射 {mapping.get('mapping_code', '')}（{mapping.get('mapping_source', '')}）→ {table}\n"
                    f"    SELECT * FROM {table} WHERE domain_id = '{step['domain_code']}'\n"
                    f"  )"
                )
            elif step["type"] == "compute_derived":
                ctes.append(f"  metric_{step['metric_code']} AS (\n    SELECT {step['sql_expr']} AS val FROM domain_base\n  )")
            elif step["type"] == "aggregate":
                dims = step["dimensions"]
                funcs = step["functions"]
                output = step["output_table"]
                lines.append(",\n".join(ctes))
                lines.append(f"\nSELECT {dims}, {funcs}\nFROM aggregated\nGROUP BY {dims};")
                lines.append(f"\n-- 输出目标表: {output}")
                return "\n".join(lines)

        return "\n".join(lines)

    def validate_metric_chain(self, domain_code: str, metric_code: str) -> dict[str, Any]:
        """校验从指标域到派生指标的链路完整性；指标域以有效映射为准。"""
        mapping = domain_mapping_service.resolve_effective(domain_code)
        domain = self._domain_row_for(mapping)
        metric = self.get_metric_by_code(metric_code)

        errors = []
        if not mapping:
            errors.append(f"指标域 {domain_code} 没有可供新计算使用的有效映射")
        elif not mapping.get("可供新计算"):
            errors.append(
                f"指标域 {domain_code} 的有效映射 {mapping.get('映射编号')} 连接无法确认"
            )
        if domain is None:
            errors.append(f"指标域 {domain_code} 不存在")
        elif domain.get("status") != "已启用":
            errors.append(f"指标域 {domain_code} 状态为 {domain.get('status')}，非已启用")

        if not metric:
            errors.append(f"派生指标 {metric_code} 不存在")
        elif metric.get("status") != "已生效":
            errors.append(f"派生指标 {metric_code} 状态为 {metric.get('status')}，非已生效")

        if domain and metric:
            # 校验父指标是否属于该域
            parent = metric.get("父指标编号", "")
            if parent.startswith("METR-") and normalize_code(parent) != normalize_code(domain_code):
                errors.append(f"父指标 {parent} 不属于指标域 {domain_code}")

        return {
            "ok": len(errors) == 0,
            "domain_code": domain_code,
            "metric_code": metric_code,
            "mapping_code": mapping.get("映射编号", "") if mapping else "",
            "source_table": mapping.get("数据源表", "") if mapping else "",
            "errors": errors,
        }

    @staticmethod
    def _domain_row_for(mapping: dict[str, Any] | None) -> dict[str, Any] | None:
        if not mapping:
            return None
        return store.find("metric_domain", int(mapping.get("域记录id", 0)))
