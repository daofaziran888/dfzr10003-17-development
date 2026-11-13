"""历史重跑业务规则：状态流转、字段校验与筛选口径都收在这里。

补数与血缘版本绑定：
- 已存在批次保留它生成时的绑定版本与旧票，历史补数继续绑定旧票；
- 新批次只能选择评审确认后（已生效）的血缘版本，待评审的候选版本不可选。
"""
from __future__ import annotations

from typing import Any

from app.store import store
from app.services.lineage_ticket import LineageTicketService

MODULE = "backfill"
REQUIRED_FIELDS = ["批次编号", "目标表名", "开始日期", "绑定版本号"]
STATUS_ORDER = ["草稿", "运行中", "已暂停", "已完成"]
ACTION_RULES = {"启动重跑": "草稿", "暂停重跑": "运行中", "归档批次": "已暂停"}
NEGATIVE_ACTIONS = []

_tickets = LineageTicketService()


class BackfillService:
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
            rows = [row for row in rows if keyword in str(row.get("批次编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def available_versions(self, node_no: str | None = None) -> list[dict[str, Any]]:
        """新补数可选版本：只有已确认（已生效）版本。"""
        return _tickets.versions_for_backfill(node_no)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        binding = str(values.get("绑定版本号") or "").strip()
        confirmed = _tickets.confirmed_binding(binding)
        if confirmed is None:
            return None, []
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        entry["绑定版本号"] = binding
        entry["绑定票号"] = confirmed.get("票号")
        entry["重跑口径"] = "已确认版本"
        entry["重跑状态"] = STATUS_ORDER[0]
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"重跑批次 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于历史重跑可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        if action == "启动重跑":
            binding = str(entry.get("绑定版本号") or "")
            # 历史批次可能绑在已被取代的旧版本上，只要当年评审确认过就能继续跑。
            if _tickets.confirmed_binding(binding, include_archived=True) is None:
                return None, f"批次绑定的血缘版本 {binding or '未绑定'} 尚未评审确认，不能启动重跑"
        entry["status"] = target
        entry["重跑状态"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"重跑批次已{action}"
