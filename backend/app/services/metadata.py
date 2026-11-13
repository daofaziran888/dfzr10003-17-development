"""元数据目录业务规则：状态流转、字段校验与筛选口径都收在这里。

除了正式的元数据记录，本模块还要承接血缘对照票确认后生成的「待确认事项」。
待确认事项必须引用产生它的血缘节点版本与对照票，方便评审人回溯。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.store import store

MODULE = "metadata"
TODO_MODULE = "_metadata_todos"
REQUIRED_FIELDS = ["记录编号", "表名称", "字段名称"]
STATUS_ORDER = ["草稿", "待审批", "已生效", "已废弃"]
ACTION_RULES = {"提交审批": "待审批", "发布元数据": "已生效", "废弃元数据": "已废弃"}
NEGATIVE_ACTIONS = []
TODO_STATUS_ORDER = ["待确认", "已确认", "已忽略"]
TODO_ACTION_RULES = {"确认事项": "已确认", "忽略事项": "已忽略"}


class MetadataService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("记录编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"元数据记录 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于元数据目录可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"元数据记录已{action}"

    # ---------------------------------------------------------------- 待确认事项
    def list_todos(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        ticket_no: str | None = None,
        node_no: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        """按字段名、状态、对照票或血缘节点过滤待确认事项。"""
        rows = list(store.rows(TODO_MODULE))
        if keyword:
            rows = [
                row for row in rows
                if keyword in str(row.get("字段名称", "")) or keyword in str(row.get("记录编号", ""))
            ]
        if status:
            rows = [row for row in rows if row.get("状态") == status]
        if ticket_no:
            rows = [row for row in rows if row.get("引用对照票") == ticket_no]
        if node_no:
            rows = [row for row in rows if row.get("引用血缘节点") == node_no]
        rows.sort(key=lambda row: int(row.get("id", 0)), reverse=True)
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_todo(self, todo_id: int) -> dict[str, Any] | None:
        return store.find(TODO_MODULE, todo_id)

    def run_todo_action(self, todo_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        """确认或忽略一条待确认事项；正式元数据记录仍走原有审批动作。"""
        todo = store.find(TODO_MODULE, todo_id)
        if todo is None:
            return None, f"待确认事项 {todo_id} 不存在"
        if action not in TODO_ACTION_RULES:
            return None, f"动作「{action}」不属于元数据待办可执行范围"
        target = TODO_ACTION_RULES[action]
        if todo.get("状态") != TODO_STATUS_ORDER[0]:
            return None, f"待确认事项当前状态为「{todo.get('状态')}」，不能重复处理"
        todo["状态"] = target
        todo["确认人"] = "值班管理员"
        todo["确认时间"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        return todo, f"待确认事项已{action}"
