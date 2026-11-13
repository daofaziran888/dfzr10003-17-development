"""元数据目录接口：维护元数据记录，覆盖提交审批、发布元数据、废弃元数据等动作。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.metadata import MetadataService

router = APIRouter(prefix="/api/metadata", tags=["元数据目录"])

service = MetadataService()

LIST_FIELDS = ["记录编号", "表名称", "字段名称", "字段类型", "业务含义", "统计信息", "更新频率", "负责人"]
STATUSES = ["草稿", "待审批", "已生效", "已废弃"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按记录编号检索"),
    status: str | None = Query(default=None, description="草稿、待审批、已生效、已废弃"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按记录编号与状态过滤元数据目录列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/todos", response_model=PageResult[dict])
def list_todos(
    keyword: str | None = Query(default=None, description="按字段名称或记录编号检索"),
    status: str | None = Query(default=None, description="待确认、已确认、已忽略"),
    ticket_no: str | None = Query(default=None, description="按来源对照票过滤"),
    node_no: str | None = Query(default=None, description="按引用血缘节点过滤"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """元数据待办：列出对照票确认后生成的待确认事项，每条都引用血缘节点版本。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_todos(
        keyword=keyword, status=status, ticket_no=ticket_no, node_no=node_no, page=page, size=size,
    )
    return PageResult(items=items, total=total, page=page, size=size)


@router.post("/todos/{todo_id}/actions", response_model=ActionResult)
def run_todo_action(todo_id: int, payload: EntryPayload) -> ActionResult:
    """确认或忽略一条元数据待办事项。"""
    action = str(payload.values.get("action") or "").strip()
    todo, message = service.run_todo_action(todo_id, action)
    if todo is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=todo)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出元数据目录清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "metadata", "total": total, "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条元数据记录明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"元数据记录 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条元数据记录，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="元数据记录已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条元数据记录执行提交审批、发布元数据、废弃元数据；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
