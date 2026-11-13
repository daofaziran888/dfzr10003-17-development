"""血缘图谱接口：维护血缘节点，并提供字段影响对照票的生成、检索与评审确认。

对照票相关接口一律只读：生成和确认都不会提前把候选字段映射写进正式图谱，
只有评审确认才会更新血缘版本并生成元数据待确认事项。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.lineage import LineageService
from app.services.lineage_ticket import LineageTicketService

router = APIRouter(prefix="/api/lineage", tags=["血缘图谱"])

service = LineageService()
ticket_service = LineageTicketService()

LIST_FIELDS = ["节点编号", "节点名称", "节点类型", "上游节点", "下游节点", "字段映射", "转换逻辑", "图谱版本"]
STATUSES = ["草稿", "待审批", "已生效", "已废弃"]
TICKET_STATUSES = ["待评审", "已确认"]
SORT_FIELDS = ["字段名", "变化类型", "确认状态", "影响范围"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按节点编号检索"),
    status: str | None = Query(default=None, description="草稿、待审批、已生效、已废弃"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按节点编号与状态过滤血缘图谱列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出血缘图谱清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "lineage", "total": total, "items": items}


# ---------------------------------------------------------------- 对照票
# 静态路径必须排在 /{entry_id} 之前，否则 "tickets" 会被当成 entry_id 解析失败。
@router.get("/tickets", response_model=PageResult[dict])
def list_tickets(
    node_no: str | None = Query(default=None, description="按节点编号过滤"),
    status: str | None = Query(default=None, description="待评审、已确认"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """字段影响对照票列表；默认按生成顺序倒序返回。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = ticket_service.list_tickets(node_no=node_no, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/tickets/{ticket_no}", response_model=dict)
def get_ticket(ticket_no: str) -> dict:
    """读取一张对照票的完整内容（含每个字段的影响范围）。"""
    ticket = ticket_service.get_ticket(ticket_no)
    if ticket is None:
        raise HTTPException(status_code=404, detail=f"对照票 {ticket_no} 不存在")
    return ticket


@router.get("/tickets/{ticket_no}/items", response_model=PageResult[dict])
def list_ticket_items(
    ticket_no: str,
    keyword: str | None = Query(default=None, description="按字段名检索"),
    change_type: str | None = Query(default=None, description="新增、移除、语义变化"),
    sort_by: str = Query(default="字段名", description="字段名、变化类型、确认状态、影响范围"),
    order: str = Query(default="asc", description="asc、desc"),
    page: int = 1,
    size: int = 50,
) -> PageResult[dict]:
    """按字段名检索、按变化类型筛选并排序对照票差异项。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    if sort_by not in SORT_FIELDS:
        raise HTTPException(status_code=400, detail=f"排序字段只支持：{'、'.join(SORT_FIELDS)}")
    if order not in ("asc", "desc"):
        raise HTTPException(status_code=400, detail="排序方向只支持 asc 或 desc")
    result = ticket_service.ticket_items(
        ticket_no, keyword=keyword, change_type=change_type, sort_by=sort_by, order=order, page=page, size=size,
    )
    if result is None:
        raise HTTPException(status_code=404, detail=f"对照票 {ticket_no} 不存在")
    items, total = result
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/tickets/{ticket_no}/summary", response_model=dict)
def ticket_summary(ticket_no: str) -> dict:
    """三个入口共用的摘要：字段名、旧值、新值、确认状态。"""
    summary = ticket_service.ticket_summary(ticket_no)
    if summary is None:
        raise HTTPException(status_code=404, detail=f"对照票 {ticket_no} 不存在")
    return summary


@router.post("/tickets/{ticket_no}/confirm", response_model=ActionResult)
def confirm_ticket(ticket_no: str, payload: EntryPayload) -> ActionResult:
    """评审确认：唯一会更新血缘版本、归档旧版本并生成元数据待确认事项的入口。"""
    operator = str(payload.values.get("operator") or payload.remark or "").strip()
    ticket, message, todos = ticket_service.confirm(ticket_no, operator=operator)
    if ticket is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(
        ok=True,
        message=message,
        entry={"ticket": ticket, "todos": todos},
    )


@router.post("/{entry_id}/tickets", response_model=ActionResult)
def generate_ticket(entry_id: int, payload: EntryPayload) -> ActionResult:
    """按候选字段映射生成对照票；同一候选版本重复提交返回同一张票。"""
    ticket, message = ticket_service.generate(entry_id, payload.values)
    if ticket is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=ticket)


# ---------------------------------------------------------------- 节点
@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条血缘节点明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"血缘节点 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条血缘节点，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="血缘节点已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条血缘节点执行提交审批、发布节点、废弃节点；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
