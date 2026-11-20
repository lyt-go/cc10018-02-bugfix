"""指标域管理接口：维护指标域，覆盖提交校验、开启域、停用域等动作。"""
from __future__ import annotations

import csv
import io

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.schemas import ActionPayload, ActionResult, EntryPayload, PageResult
from app.services.metric_domain import MetricDomainService
from app.services.domain_mapping import domain_mapping_service

router = APIRouter(prefix="/api/metric_domain", tags=["指标域管理"])

service = MetricDomainService()

LIST_FIELDS = ["域编号", "域名称", "业务域描述", "数据源表", "更新频率", "负责人", "域状态", "创建时间"]
STATUSES = ["草稿", "待校验", "已启用", "已停用"]


def query_filters(request: Request) -> dict[str, str]:
    """只接受当前模块声明过的字段，忽略分页等保留参数。"""
    return {
        key: value.strip()
        for key, value in request.query_params.items()
        if key in LIST_FIELDS and value.strip()
    }


@router.get("", response_model=PageResult[dict])
def list_entries(
    request: Request,
    keyword: str | None = Query(default=None, description="按域编号检索"),
    status: str | None = Query(default=None, description="草稿、待校验、已启用、已停用"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=200),
) -> PageResult[dict]:
    """按页面字段、域编号关键字与状态过滤指标域管理列表。"""
    items, total = service.list_entries(
        filters=query_filters(request), keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/export")
def export_entries(
    request: Request,
    keyword: str | None = Query(default=None),
    status: str | None = Query(default=None),
) -> StreamingResponse:
    """下载当前筛选条件下的 UTF-8 CSV；必须声明在 /{entry_id} 之前。"""
    items, _ = service.list_entries(
        filters=query_filters(request), keyword=keyword, status=status, page=1, size=1000000)
    output = io.StringIO()
    output.write("\ufeff")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["id", *LIST_FIELDS, "status"])
    for item in items:
        row = []
        for value in [item.get("id"), *(item.get(field, "") for field in LIST_FIELDS), item.get("status", "")]:
            text = "" if value is None else str(value)
            row.append("'" + text if text.startswith(("=", "+", "-", "@")) else text)
        writer.writerow(row)
    return StreamingResponse(
        iter([output.getvalue()]), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="metric_domain.csv"'})


@router.get("/mapping/dedup-list", response_model=dict)
def get_dedup_list() -> dict:
    """指标域去重清单：每个域编号只给出一个有效映射，重复项与冲突全部可见。"""
    return domain_mapping_service.build_dedup_list()


@router.post("/mapping/adjudicate", response_model=dict)
def adjudicate_mappings() -> dict:
    """批量检查存量重复域并落定裁决，重复调用不会改变结果。"""
    result = domain_mapping_service.apply_adjudication()
    return {"ok": True, "message": "存量重复域已完成裁决", **result}


@router.get("/mapping/{mapping_code}", response_model=dict)
def get_mapping(mapping_code: str) -> dict:
    """读取单条映射，用于追溯既有聚合结果生成时使用的来源。"""
    mapping = domain_mapping_service.get_mapping(mapping_code)
    if mapping is None:
        raise HTTPException(status_code=404, detail=f"指标域映射 {mapping_code} 不存在")
    return mapping


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条指标域明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"指标域 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: dict[str, object]) -> ActionResult:
    """登记一条指标域；同一域编号只允许登记一次，重复时说明原因。"""
    entry, errors = service.create_entry(payload)
    if entry is None:
        return ActionResult(ok=False, message="；".join(errors))
    return ActionResult(ok=True, message="指标域已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: ActionPayload) -> ActionResult:
    """对单条指标域执行提交校验、开启域、停用域；不允许的动作会被拦下并说明原因。"""
    entry, message = service.run_action(entry_id, payload.action.strip())
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
