"""血缘节点字段影响对照票：候选字段映射与上一个稳定版本的差异比对。

业务约束：
- 对照票生成只读，不改变正式图谱；候选版本在评审确认前一直保持「待评审」。
- 同一节点 + 同一候选版本重复生成返回同一张票（票号由节点编号与版本号决定）。
- 确认时按字段重新裁决冲突：版本生效时间优先、同生效时间按版本序号，只允许一个版本落库。
- 确认后更新血缘版本，并把受影响元数据记录写成「待确认事项」，引用该血缘节点版本。
"""
from __future__ import annotations

import copy
import re
import threading
from datetime import datetime
from typing import Any

from app.store import store

TICKET_MODULE = "_lineage_tickets"
VERSION_MODULE = "_lineage_versions"
CONSUMER_MODULE = "_lineage_consumers"
TODO_MODULE = "_metadata_todos"
NODE_MODULE = "lineage"
METADATA_MODULE = "metadata"
BACKFILL_MODULE = "backfill"

PENDING = "待评审"
CONFIRMED = "已确认"
EFFECTIVE = "已生效"
ARCHIVED = "已归档"
TODO_PENDING = "待确认"

CHANGE_TYPES = ["新增", "移除", "语义变化"]
ACTIVE_BACKFILL_STATUSES = ["草稿", "运行中", "已暂停"]
# 串行化「生成 + 确认」：并发确认同一张票时只有一个线程能落库。
_TICKET_LOCK = threading.Lock()


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _version_rank(version_no: str) -> tuple[int, str]:
    match = re.search(r"(\d+)", version_no or "")
    return (int(match.group(1)) if match else 0, version_no or "")


def _version_order(row: dict[str, Any]) -> tuple[str, tuple[int, str]]:
    """裁决口径：先比生效时间，再比版本序号，版本号本身只作兜底。"""
    return (str(row.get("生效时间") or ""), _version_rank(str(row.get("版本号") or "")))


def _binding(node_no: str, version_no: str) -> str:
    return f"{node_no}@{version_no}"


def _describe(mapping: dict[str, Any] | None) -> str:
    if not mapping:
        return "—"
    parts = [f"{mapping.get('来源字段') or '—'} → {mapping.get('目标字段') or '—'}"]
    parts.append(str(mapping.get("类型") or "—"))
    parts.append(str(mapping.get("语义") or "—"))
    transform = str(mapping.get("转换逻辑") or "").strip()
    if transform and transform != "直接映射":
        parts.append(f"转换：{transform}")
    return " · ".join(parts)


def _same_mapping(left: dict[str, Any], right: dict[str, Any]) -> bool:
    # 转换逻辑留空等同默认「直接映射」，否则同一映射会被误判成语义变化。
    keys = ("来源字段", "目标字段", "类型", "语义")

    def transform(mapping: dict[str, Any]) -> str:
        return str(mapping.get("转换逻辑") or "").strip() or "直接映射"

    return (
        all(str(left.get(key) or "") == str(right.get(key) or "") for key in keys)
        and transform(left) == transform(right)
    )


class LineageTicketService:
    # ---------------------------------------------------------------- 基础查询
    def node(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(NODE_MODULE, entry_id)

    def node_by_no(self, node_no: str) -> dict[str, Any] | None:
        return store.find_by(NODE_MODULE, "节点编号", node_no)

    def versions(self, node_no: str) -> list[dict[str, Any]]:
        rows = [row for row in store.rows(VERSION_MODULE) if row.get("节点编号") == node_no]
        return sorted(rows, key=_version_order)

    def effective_version(self, node_no: str) -> dict[str, Any] | None:
        rows = [row for row in self.versions(node_no) if row.get("状态") == EFFECTIVE]
        return max(rows, key=_version_order) if rows else None

    def pending_versions(self, node_no: str) -> list[dict[str, Any]]:
        rows = [row for row in self.versions(node_no) if row.get("状态") == PENDING]
        return sorted(rows, key=_version_order)

    def ticket_no(self, node_no: str, version_no: str) -> str:
        return f"TICK-{node_no}-{version_no}"

    def get_ticket(self, ticket_no: str) -> dict[str, Any] | None:
        row = store.find_by(TICKET_MODULE, "票号", ticket_no)
        return copy.deepcopy(row) if row else None

    def latest_ticket_for_node(self, node_no: str) -> dict[str, Any] | None:
        rows = [row for row in store.rows(TICKET_MODULE) if row.get("节点编号") == node_no]
        if not rows:
            return None
        return copy.deepcopy(max(rows, key=lambda row: int(row.get("id", 0))))

    def list_tickets(
        self,
        *,
        node_no: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = list(store.rows(TICKET_MODULE))
        if node_no:
            rows = [row for row in rows if row.get("节点编号") == node_no]
        if status:
            rows = [row for row in rows if row.get("状态") == status]
        rows.sort(key=lambda row: int(row.get("id", 0)), reverse=True)
        total = len(rows)
        start = max(page - 1, 0) * size
        return [copy.deepcopy(row) for row in rows[start:start + size]], total

    # ---------------------------------------------------------------- 生成对照票
    def generate(self, entry_id: int, values: dict[str, Any] | None = None) -> tuple[dict[str, Any] | None, str]:
        """生成（或返回已生成的）候选版本对照票；只读，不改动节点/版本状态。"""
        values = values or {}
        node = self.node(entry_id)
        if node is None:
            return None, f"血缘节点 {entry_id} 不存在或已归档"
        node_no = str(node.get("节点编号") or "")
        version_no = str(values.get("候选版本") or "").strip()
        mapping = values.get("字段映射")
        effective_time = str(values.get("生效时间") or "").strip()

        with _TICKET_LOCK:
            candidate: dict[str, Any] | None = None
            if version_no:
                candidate = store.find_by(VERSION_MODULE, "票号", self.ticket_no(node_no, version_no))
                if candidate is not None and candidate.get("节点编号") != node_no:
                    candidate = None
                if candidate is None:
                    candidate = next(
                        (row for row in self.versions(node_no) if row.get("版本号") == version_no),
                        None,
                    )
                if candidate is None:
                    if not isinstance(mapping, dict) or not mapping:
                        return None, f"节点 {node_no} 不存在候选版本 {version_no}，请同时提交候选字段映射"
                    if not effective_time:
                        return None, f"候选版本 {version_no} 缺少生效时间，无法生成对照票"
                    candidate = {
                        "id": store.next_id(VERSION_MODULE),
                        "节点编号": node_no,
                        "节点id": int(node.get("id", 0)),
                        "版本号": version_no,
                        "状态": PENDING,
                        "生效时间": effective_time,
                        "确认时间": "",
                        "票号": self.ticket_no(node_no, version_no),
                        "字段映射": copy.deepcopy(mapping),
                    }
                    store.rows(VERSION_MODULE).append(candidate)
            else:
                pending = self.pending_versions(node_no)
                if not pending:
                    return None, f"节点 {node_no} 没有待评审候选版本，请先提供候选版本与字段映射"
                candidate = pending[-1]

            ticket_no = self.ticket_no(node_no, str(candidate.get("版本号") or ""))
            existing = store.find_by(TICKET_MODULE, "票号", ticket_no)
            if existing is not None:
                return copy.deepcopy(existing), f"候选版本 {candidate.get('版本号')} 已生成对照票，返回同一张票"

            baseline = self.effective_version(node_no)
            ticket = self._build_ticket(node, candidate, baseline)
            store.rows(TICKET_MODULE).append(ticket)
            return copy.deepcopy(ticket), "候选版本字段影响对照票已生成"

    def _build_ticket(
        self,
        node: dict[str, Any],
        candidate: dict[str, Any],
        baseline: dict[str, Any] | None,
    ) -> dict[str, Any]:
        node_no = str(node.get("节点编号") or "")
        node_name = str(node.get("节点名称") or "")
        base_map: dict[str, Any] = dict(baseline.get("字段映射") or {}) if baseline else {}
        cand_map: dict[str, Any] = dict(candidate.get("字段映射") or {})
        items: list[dict[str, Any]] = []
        for field in sorted(set(base_map) | set(cand_map)):
            old = base_map.get(field)
            new = cand_map.get(field)
            if old is None and new is None:
                continue
            if new is None:
                change = "移除"
            elif old is None:
                change = "新增"
            elif _same_mapping(old, new):
                continue
            else:
                change = "语义变化"
            adjudicated, conflict, reason = self._adjudicate(node_no, str(field), candidate)
            items.append({
                "字段名": field,
                "变化类型": change,
                "旧值": _describe(old),
                "新值": _describe(new),
                "影响元数据记录": self._affected_metadata(node_name, str(field)),
                "下游节点": self._downstream_nodes(node_no, str(field)),
                "仍在使用旧映射的补数批次": self._active_backfills(
                    node_name, str(field), str(candidate.get("生效时间") or ""),
                ),
                "确认状态": TODO_PENDING,
                "裁决版本": adjudicated,
                "是否冲突": conflict,
                "冲突说明": reason,
            })
        return {
            "id": store.next_id(TICKET_MODULE),
            "票号": self.ticket_no(node_no, str(candidate.get("版本号") or "")),
            "节点编号": node_no,
            "节点id": int(node.get("id", 0)),
            "节点名称": node_name,
            "基线版本": str((baseline or {}).get("版本号") or ""),
            "基线票号": str((baseline or {}).get("票号") or ""),
            "候选版本": str(candidate.get("版本号") or ""),
            "候选生效时间": str(candidate.get("生效时间") or ""),
            "状态": PENDING,
            "生成时间": _now(),
            "确认时间": "",
            "确认人": "",
            "只读": True,
            "幂等键": _binding(node_no, str(candidate.get("版本号") or "")),
            "items": items,
        }

    def _affected_metadata(self, node_name: str, field: str) -> list[dict[str, Any]]:
        rows = [
            row for row in store.rows(METADATA_MODULE)
            if str(row.get("表名称") or "") == node_name and str(row.get("字段名称") or "") == field
        ]
        return [
            {"id": int(row.get("id", 0)), "记录编号": row.get("记录编号"), "表名称": row.get("表名称"),
             "字段名称": row.get("字段名称")}
            for row in rows
        ]

    def _downstream_nodes(self, node_no: str, field: str) -> list[dict[str, Any]]:
        codes = [
            str(row.get("下游节点") or "") for row in store.rows(CONSUMER_MODULE)
            if row.get("节点编号") == node_no and row.get("字段名称") == field
        ]
        nodes: list[dict[str, Any]] = []
        for code in sorted(set(codes)):
            target = self.node_by_no(code)
            nodes.append({"节点编号": code, "节点名称": (target or {}).get("节点名称") or "未登记节点"})
        return nodes

    def _active_backfills(self, node_name: str, field: str, candidate_time: str) -> list[dict[str, Any]]:
        """仍在使用旧映射的补数批次：未归档、绑定版本早于候选版本的补数批次。"""
        batches: list[dict[str, Any]] = []
        for row in store.rows(BACKFILL_MODULE):
            if str(row.get("目标表名") or "") != node_name:
                continue
            if str(row.get("重跑状态") or row.get("status") or "") not in ACTIVE_BACKFILL_STATUSES:
                continue
            binding = str(row.get("绑定版本号") or "")
            if not binding:
                continue
            version_no = binding.split("@", 1)[-1]
            bound = next((v for v in self.versions(binding.split("@", 1)[0]) if v.get("版本号") == version_no), None)
            if bound is None:
                continue
            if "字段名称" in row and str(row.get("字段名称") or "") != field:
                continue
            if str(bound.get("生效时间") or "") >= candidate_time:
                continue
            if str(bound.get("状态") or "") in (EFFECTIVE, ARCHIVED):
                batches.append({
                    "批次编号": row.get("批次编号"),
                    "目标表名": row.get("目标表名"),
                    "绑定版本号": binding,
                    "绑定票号": row.get("绑定票号"),
                    "状态": row.get("重跑状态") or row.get("status"),
                })
        return batches

    def _adjudicate(self, node_no: str, field: str, candidate: dict[str, Any]) -> tuple[str, bool, str]:
        """冲突字段按节点版本与生效时间裁决；返回（裁决版本, 是否冲突, 说明）。"""
        pool = [
            row for row in self.versions(node_no)
            if row.get("状态") in (PENDING, EFFECTIVE) and field in dict(row.get("字段映射") or {})
        ]
        if not pool:
            pool = [candidate]
        elif candidate.get("版本号") not in {row.get("版本号") for row in pool}:
            pool.append(candidate)
        winner = max(pool, key=_version_order)
        competing = [
            row for row in pool
            if row.get("版本号") != candidate.get("版本号") and _version_order(row) >= _version_order(candidate)
        ]
        if not competing:
            return _binding(node_no, str(candidate.get("版本号") or "")), False, ""
        winner_binding = _binding(node_no, str(winner.get("版本号") or ""))
        reason = (
            f"字段「{field}」被版本 {winner.get('版本号')} 与候选版本同时定义，"
            f"按生效时间 {winner.get('生效时间')} 与版本序号裁决为 {winner.get('版本号')}"
        )
        return winner_binding, True, reason

    # ---------------------------------------------------------------- 检索与摘要
    def ticket_items(
        self,
        ticket_no: str,
        *,
        keyword: str | None = None,
        change_type: str | None = None,
        sort_by: str = "字段名",
        order: str = "asc",
        page: int = 1,
        size: int = 50,
    ) -> tuple[list[dict[str, Any]], int] | None:
        ticket = store.find_by(TICKET_MODULE, "票号", ticket_no)
        if ticket is None:
            return None
        items = list(ticket.get("items") or [])
        if keyword:
            items = [item for item in items if keyword in str(item.get("字段名") or "")]
        if change_type:
            items = [item for item in items if item.get("变化类型") == change_type]
        sort_keys = {
            "字段名": lambda item: str(item.get("字段名") or ""),
            "变化类型": lambda item: CHANGE_TYPES.index(item.get("变化类型")) if item.get("变化类型") in CHANGE_TYPES else 99,
            "确认状态": lambda item: str(item.get("确认状态") or ""),
            "影响范围": lambda item: len(item.get("影响元数据记录") or []) + len(item.get("下游节点") or []),
        }
        key = sort_keys.get(sort_by, sort_keys["字段名"])
        items.sort(key=key, reverse=(order == "desc"))
        total = len(items)
        start = max(page - 1, 0) * size
        return copy.deepcopy(items[start:start + size]), total

    def ticket_summary(self, ticket_no: str) -> dict[str, Any] | None:
        """三个入口共用的摘要：字段名、旧值、新值、确认状态。"""
        ticket = store.find_by(TICKET_MODULE, "票号", ticket_no)
        if ticket is None:
            return None
        return {
            "票号": ticket.get("票号"),
            "节点编号": ticket.get("节点编号"),
            "节点名称": ticket.get("节点名称"),
            "基线版本": ticket.get("基线版本"),
            "候选版本": ticket.get("候选版本"),
            "状态": ticket.get("状态"),
            "只读": bool(ticket.get("只读", True)),
            "items": [
                {"字段名": item.get("字段名"), "旧值": item.get("旧值"), "新值": item.get("新值"),
                 "变化类型": item.get("变化类型"), "确认状态": item.get("确认状态"),
                 "裁决版本": item.get("裁决版本"), "是否冲突": bool(item.get("是否冲突"))}
                for item in (ticket.get("items") or [])
            ],
        }

    # ---------------------------------------------------------------- 评审确认
    def confirm(self, ticket_no: str, operator: str = "") -> tuple[dict[str, Any] | None, str, list[dict[str, Any]]]:
        """评审确认：只允许一个候选版本落库，并把受影响元数据写成待确认事项。"""
        operator = operator or "值班管理员"
        with _TICKET_LOCK:
            ticket = store.find_by(TICKET_MODULE, "票号", ticket_no)
            if ticket is None:
                return None, f"对照票 {ticket_no} 不存在", []
            if ticket.get("状态") == CONFIRMED:
                return copy.deepcopy(ticket), "对照票已确认，重复确认不会重复落库", []
            if ticket.get("状态") != PENDING:
                return None, f"对照票当前状态为「{ticket.get('状态')}」，不能执行确认", []

            node_no = str(ticket.get("节点编号") or "")
            version_no = str(ticket.get("候选版本") or "")
            candidate = next((v for v in self.versions(node_no) if v.get("版本号") == version_no), None)
            if candidate is None:
                return None, f"候选版本 {version_no} 不存在，无法确认", []
            if candidate.get("状态") == EFFECTIVE:
                ticket["状态"] = CONFIRMED
                return copy.deepcopy(ticket), "候选版本已生效，对照票已同步为已确认", []
            if candidate.get("状态") != PENDING:
                return None, f"候选版本 {version_no} 当前状态为「{candidate.get('状态')}」，不能确认", []

            node = self.node_by_no(node_no)
            if node is None:
                return None, f"血缘节点 {node_no} 不存在或已归档", []

            # 重新裁决：生成之后状态可能变化，确认时以最新版本/生效时间为准。
            blocked: list[str] = []
            for item in ticket.get("items") or []:
                adjudicated, _, _ = self._adjudicate(node_no, str(item.get("字段名") or ""), candidate)
                item["裁决版本"] = adjudicated
                if adjudicated != _binding(node_no, version_no):
                    blocked.append(f"{item.get('字段名')}（裁决版本 {adjudicated}）")
            if blocked:
                return None, f"字段 {'、'.join(blocked)} 已由更晚或同版本的候选裁决，当前版本不能落库", []

            baseline = self.effective_version(node_no)
            if baseline is not None and _version_order(candidate) <= _version_order(baseline):
                return None, (
                    f"候选版本 {version_no} 的生效时间 {candidate.get('生效时间')} "
                    f"不晚于当前稳定版本 {baseline.get('版本号')}，不能落库"
                ), []

            now = _now()
            # 1) 版本落库：候选版本生效，被取代的稳定版本归档，历史版本保留。
            candidate["状态"] = EFFECTIVE
            candidate["确认时间"] = now
            for row in self.versions(node_no):
                if row.get("版本号") == version_no:
                    continue
                if row.get("状态") == EFFECTIVE and _version_order(row) < _version_order(candidate):
                    row["状态"] = ARCHIVED
            # 2) 血缘节点列表立即反映新版本，候选版本不再提前出现为「图谱版本」。
            cand_map = dict(candidate.get("字段映射") or {})
            node["图谱版本"] = version_no
            node["字段映射"] = "、".join(sorted(cand_map))
            node["转换逻辑"] = "按候选版本字段映射汇总"
            node["status"] = EFFECTIVE
            node["pending"] = False
            node["abnormal"] = False
            # 3) 对照票与明细状态。
            ticket["状态"] = CONFIRMED
            ticket["确认时间"] = now
            ticket["确认人"] = operator
            for item in ticket.get("items") or []:
                item["确认状态"] = CONFIRMED
            # 4) 元数据待办：受影响记录写成待确认事项，引用节点版本与对照票。
            todos = self._create_metadata_todos(ticket, version_no, baseline, now)
            return copy.deepcopy(ticket), "血缘版本已更新，受影响元数据记录已记为待确认事项", todos

    def _create_metadata_todos(
        self,
        ticket: dict[str, Any],
        version_no: str,
        baseline: dict[str, Any] | None,
        now: str,
    ) -> list[dict[str, Any]]:
        node_no = str(ticket.get("节点编号") or "")
        binding = _binding(node_no, version_no)
        created: list[dict[str, Any]] = []
        for item in ticket.get("items") or []:
            for record in item.get("影响元数据记录") or []:
                record_id = int(record.get("id", 0))
                exists = any(
                    int(row.get("元数据记录id", 0)) == record_id
                    and row.get("引用对照票") == ticket.get("票号")
                    and row.get("字段名称") == item.get("字段名")
                    for row in store.rows(TODO_MODULE)
                )
                if exists:
                    continue
                todo = {
                    "id": store.next_id(TODO_MODULE),
                    "事项编号": "",
                    "元数据记录id": record_id,
                    "记录编号": record.get("记录编号"),
                    "表名称": record.get("表名称"),
                    "字段名称": record.get("字段名称") or item.get("字段名"),
                    "变化类型": item.get("变化类型"),
                    "旧值": item.get("旧值"),
                    "新值": item.get("新值"),
                    "引用血缘节点": node_no,
                    "引用血缘节点版本": binding,
                    "引用对照票": ticket.get("票号"),
                    "状态": TODO_PENDING,
                    "生成时间": now,
                    "确认时间": "",
                    "确认人": "",
                }
                todo["事项编号"] = f"TODO-{todo['id']:04d}"
                store.rows(TODO_MODULE).append(todo)
                created.append(copy.deepcopy(todo))
        return created

    # ---------------------------------------------------------------- 补数可用版本
    def versions_for_backfill(self, node_no: str | None = None) -> list[dict[str, Any]]:
        """新补数只能选择已确认版本：这里只返回已生效版本。"""
        rows = [row for row in store.rows(VERSION_MODULE) if row.get("状态") == EFFECTIVE]
        if node_no:
            rows = [row for row in rows if row.get("节点编号") == node_no]
        result: list[dict[str, Any]] = []
        for row in sorted(rows, key=_version_order):
            node = self.node_by_no(str(row.get("节点编号") or ""))
            result.append({
                "节点编号": row.get("节点编号"),
                "节点名称": (node or {}).get("节点名称") or "",
                "版本号": row.get("版本号"),
                "绑定版本号": _binding(str(row.get("节点编号") or ""), str(row.get("版本号") or "")),
                "票号": row.get("票号"),
                "生效时间": row.get("生效时间"),
                "状态": row.get("状态"),
            })
        return result

    def confirmed_binding(self, binding: str, *, include_archived: bool = False) -> dict[str, Any] | None:
        """校验补数批次绑定的版本是否已评审确认。

        include_archived 用于历史批次：曾经确认过、后来被新版本取代归档的版本仍算「旧票」。
        """
        if "@" not in binding:
            return None
        node_no, version_no = binding.split("@", 1)
        allowed = (EFFECTIVE, ARCHIVED) if include_archived else (EFFECTIVE,)
        row = next(
            (
                v for v in self.versions(node_no)
                if v.get("版本号") == version_no and v.get("状态") in allowed and str(v.get("确认时间") or "")
            ),
            None,
        )
        return copy.deepcopy(row) if row else None
