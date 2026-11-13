import { useCallback, useEffect, useMemo, useState } from 'react'

import { request } from '@/api/client'
import TicketSummary from '@/components/TicketSummary'

const ENDPOINT = '/api/lineage'
const columns = [
  "节点编号",
  "节点名称",
  "节点类型",
  "上游节点",
  "下游节点",
  "字段映射",
  "转换逻辑",
  "图谱版本",
  "候选版本"
]
const actions = [
  "提交审批",
  "发布节点",
  "废弃节点"
]
const statuses = [
  "草稿",
  "待审批",
  "已生效",
  "已废弃"
]
const stats = [
  {
    "label": "血缘节点总数",
    "value": 0
  },
  {
    "label": "已生效节点",
    "value": 0
  },
  {
    "label": "待审批节点",
    "value": 0
  }
]
const changeTypes = ['新增', '移除', '语义变化']
const sortFields = [
  { key: '字段名', label: '字段名' },
  { key: '变化类型', label: '变化类型' },
  { key: '确认状态', label: '确认状态' },
  { key: '影响范围', label: '影响范围' },
]

type Row = Record<string, string | number | null>

type TicketItem = {
  id: number
  字段名: string
  变化类型: string
  旧值: string
  新值: string
  确认状态: string
  裁决版本: string
  是否冲突: boolean
  冲突说明: string
  影响元数据记录: { id: number; 记录编号: string; 表名称: string; 字段名称: string }[]
  下游节点: { 节点编号: string; 节点名称: string }[]
  仍在使用旧映射的补数批次: { 批次编号: string; 绑定版本号: string; 绑定票号: string; 状态: string }[]
}

type Ticket = {
  id: number
  票号: string
  节点编号: string
  节点id: number
  节点名称: string
  基线版本: string
  基线票号: string
  候选版本: string
  候选生效时间: string
  状态: string
  只读: boolean
  生成时间: string
  确认时间: string
  确认人: string
  items: TicketItem[]
}

type TicketListItem = Omit<Ticket, 'items'> & { items?: TicketItem[] }

export default function LineageView() {
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [errorMessage, setErrorMessage] = useState('')
  const [filters, setFilters] = useState<Record<string, string>>({})
  const filterFields = useMemo(() => columns.slice(0, 3), [])

  const [tickets, setTickets] = useState<TicketListItem[]>([])
  const [activeTicket, setActiveTicket] = useState<Ticket | null>(null)
  const [ticketItems, setTicketItems] = useState<TicketItem[]>([])
  const [itemTotal, setItemTotal] = useState(0)
  const [itemFilters, setItemFilters] = useState({ keyword: '', changeType: '', sortBy: '字段名', order: 'asc' })
  const [candidateVersion, setCandidateVersion] = useState('')
  const [candidateTime, setCandidateTime] = useState('')
  const [notice, setNotice] = useState('')

  const reload = useCallback(async () => {
    setErrorMessage('')
    const query = new URLSearchParams(filters).toString()
    try {
      const response = await request(`${ENDPOINT}?${query}`)
      if (!response.ok) {
        throw new Error('血缘节点列表读取失败')
      }
      const payload = await response.json()
      setRows(payload.items ?? [])
      setTotal(payload.total ?? 0)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '血缘图谱列表读取失败')
    }
  }, [filters])

  const reloadTickets = useCallback(async () => {
    try {
      const response = await request(`${ENDPOINT}/tickets?size=50`)
      if (!response.ok) {
        throw new Error('对照票列表读取失败')
      }
      const payload = await response.json()
      const items: TicketListItem[] = payload.items ?? []
      setTickets(items)
      return items
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '对照票列表读取失败')
      return []
    }
  }, [])

  const reloadItems = useCallback(async (ticketNo: string) => {
    const query = new URLSearchParams()
    if (itemFilters.keyword) query.set('keyword', itemFilters.keyword)
    if (itemFilters.changeType) query.set('change_type', itemFilters.changeType)
    query.set('sort_by', itemFilters.sortBy)
    query.set('order', itemFilters.order)
    query.set('size', '100')
    try {
      const response = await request(`${ENDPOINT}/tickets/${encodeURIComponent(ticketNo)}/items?${query.toString()}`)
      if (!response.ok) {
        throw new Error('对照票差异项读取失败')
      }
      const payload = await response.json()
      setTicketItems(payload.items ?? [])
      setItemTotal(payload.total ?? 0)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '对照票差异项读取失败')
    }
  }, [itemFilters])

  async function openTicket(ticketNo: string) {
    try {
      const response = await request(`${ENDPOINT}/tickets/${encodeURIComponent(ticketNo)}`)
      if (!response.ok) {
        throw new Error(`对照票 ${ticketNo} 读取失败`)
      }
      setActiveTicket((await response.json()) as Ticket)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '对照票读取失败')
    }
  }

  useEffect(() => {
    void reload()
  }, [reload])

  useEffect(() => {
    let cancelled = false
    void reloadTickets().then((items) => {
      if (cancelled) return
      const pending = items.find((item) => item.状态 === '待评审') ?? items[0]
      if (pending) {
        void openTicket(pending.票号)
      }
    })
    return () => { cancelled = true }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!activeTicket) return
    void reloadItems(activeTicket.票号)
  }, [activeTicket?.票号, reloadItems]) // eslint-disable-line react-hooks/exhaustive-deps

  async function runAction(action: string, row: Row) {
    setErrorMessage('')
    try {
      const response = await request(`${ENDPOINT}/${row.id}/actions`, {
        method: 'POST',
        body: JSON.stringify({ action }),
      })
      if (!response.ok) {
        throw new Error('血缘图谱动作未生效，请稍后重试')
      }
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '血缘图谱操作失败')
    }
  }

  async function generateTicket(row: Row) {
    setErrorMessage('')
    setNotice('')
    const values: Record<string, string> = {}
    if (candidateVersion.trim()) values['候选版本'] = candidateVersion.trim()
    if (candidateTime.trim()) values['生效时间'] = candidateTime.trim()
    try {
      const response = await request(`${ENDPOINT}/${row.id}/tickets`, {
        method: 'POST',
        body: JSON.stringify({ values }),
      })
      const payload = await response.json()
      if (!response.ok || !payload.ok) {
        throw new Error(payload.message ?? '对照票生成失败')
      }
      setNotice(payload.message)
      setActiveTicket(payload.entry as Ticket)
      await reloadTickets()
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '对照票生成失败')
    }
  }

  async function confirmTicket() {
    if (!activeTicket) return
    setErrorMessage('')
    setNotice('')
    try {
      const response = await request(`${ENDPOINT}/tickets/${encodeURIComponent(activeTicket.票号)}/confirm`, {
        method: 'POST',
        body: JSON.stringify({ values: { operator: '值班管理员' } }),
      })
      const payload = await response.json()
      if (!response.ok || !payload.ok) {
        throw new Error(payload.message ?? '对照票确认失败')
      }
      setNotice(`${payload.message}；待确认事项 ${payload.entry?.todos?.length ?? 0} 条`)
      await openTicket(activeTicket.票号)
      await reload()
      await reloadTickets()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '对照票确认失败')
    }
  }

  // 冲突判断以整张票为准，不能只看当前筛选出来的差异项。
  const canConfirm = activeTicket?.状态 === '待评审' && !activeTicket.items.some((item) => item.是否冲突)

  return (
    <section className="page" data-module="lineage">
      <header className="page-head">
        <div>
          <h2>血缘图谱管理</h2>
          <p className="page-desc">
            维护血缘图谱的节点依赖、字段映射与转换记录，为数据溯源提供可审计的数据工程流程。
            候选字段映射先出只读的字段影响对照票，评审确认后才更新血缘版本。
          </p>
        </div>
        <div className="page-actions">
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出血缘图谱清单
          </button>
        </div>
      </header>

      <div className="stat-row">
        {stats.map((item) => (
          <article key={item.label} className="stat-card">
            <span className="stat-label">{item.label}</span>
            <strong className="stat-value">{item.value}</strong>
          </article>
        ))}
      </div>

      <form className="filter-bar" onSubmit={(event) => { event.preventDefault(); void reload() }}>
        {filterFields.map((field) => (
          <label key={field} className="filter-item">
            <span>{field}</span>
            <input
              value={filters[field] ?? ''}
              placeholder={`按${field}检索`}
              onChange={(event) => setFilters((prev) => ({ ...prev, [field]: event.target.value }))}
            />
          </label>
        ))}
        <button className="btn" type="submit">查询</button>
        <button className="btn ghost" type="button" onClick={() => setFilters({})}>重置条件</button>
      </form>

      <table className="data-table">
        <thead>
          <tr>
            {columns.map((column) => <th key={column}>{column}</th>)}
            <th>可执行动作</th>
            <th>字段影响对照</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={String(row.id)}>
              {columns.map((column) => <td key={column}>{row[column] ?? '—'}</td>)}
              <td className="row-actions">
                {actions.map((action) => (
                  <button key={action} className="link" type="button" onClick={() => void runAction(action, row)}>
                    {action}
                  </button>
                ))}
              </td>
              <td className="row-actions">
                <button className="link" type="button" onClick={() => void generateTicket(row)}>
                  生成对照票
                </button>
                {tickets.filter((ticket) => ticket.节点id === row.id).slice(0, 1).map((ticket) => (
                  <button key={ticket.票号} className="link" type="button" onClick={() => void openTicket(ticket.票号)}>
                    查看 {ticket.票号}
                  </button>
                ))}
              </td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td className="empty-state" colSpan={columns.length + 2}>暂无血缘图谱数据，可先登记血缘节点</td>
            </tr>
          )}
        </tbody>
      </table>

      <section className="ticket-panel" data-testid="lineage-ticket-panel">
        <header className="section-head">
          <div>
            <h3>字段影响对照票</h3>
            <p className="page-desc">候选映射按行列出新增、移除和语义变化；每个变化项都带上受影响元数据、下游节点与仍在用旧映射的补数批次。</p>
          </div>
          <div className="ticket-generate">
            <label className="filter-item">
              <span>候选版本</span>
              <input
                value={candidateVersion}
                placeholder="如 v3；留空取待评审版本"
                onChange={(event) => setCandidateVersion(event.target.value)}
              />
            </label>
            <label className="filter-item">
              <span>生效时间</span>
              <input
                value={candidateTime}
                placeholder="如 2026-12-01 09:00"
                onChange={(event) => setCandidateTime(event.target.value)}
              />
            </label>
            <span className="hint-text">生成可留空，直接在节点行点「生成对照票」</span>
          </div>
        </header>

        <div className="ticket-switch">
          {tickets.map((ticket) => (
            <button
              key={ticket.票号}
              type="button"
              className={`btn ${activeTicket?.票号 === ticket.票号 ? 'primary' : ''}`}
              onClick={() => void openTicket(ticket.票号)}
            >
              {ticket.票号} · {ticket.状态}
            </button>
          ))}
          {tickets.length === 0 && <span className="hint-text">还没有对照票，先在节点行生成一张</span>}
        </div>

        {activeTicket && (
          <>
            <div className="ticket-meta">
              <span>基线版本：{activeTicket.基线版本 || '—'}（{activeTicket.基线票号 || '无'}）</span>
              <span>候选版本：{activeTicket.候选版本}</span>
              <span>候选生效时间：{activeTicket.候选生效时间}</span>
              <span className={`tag ${activeTicket.只读 ? 'warn' : 'ok'}`}>{activeTicket.只读 ? '只读' : '可编辑'}</span>
              <span className={`tag ${activeTicket.状态 === '已确认' ? 'ok' : 'warn'}`}>{activeTicket.状态}</span>
            </div>
            {activeTicket.状态 === '已确认' && (
              <p className="hint-text">确认时间 {activeTicket.确认时间} · 确认人 {activeTicket.确认人}；历史补数仍绑定旧票，新补数只能选择已确认版本。</p>
            )}

            <form className="filter-bar" onSubmit={(event) => { event.preventDefault(); void reloadItems(activeTicket.票号) }}>
              <label className="filter-item">
                <span>字段名</span>
                <input
                  value={itemFilters.keyword}
                  placeholder="按字段名检索"
                  onChange={(event) => setItemFilters((prev) => ({ ...prev, keyword: event.target.value }))}
                />
              </label>
              <label className="filter-item">
                <span>变化类型</span>
                <select
                  value={itemFilters.changeType}
                  onChange={(event) => setItemFilters((prev) => ({ ...prev, changeType: event.target.value }))}
                >
                  <option value="">全部</option>
                  {changeTypes.map((type) => <option key={type} value={type}>{type}</option>)}
                </select>
              </label>
              <label className="filter-item">
                <span>排序</span>
                <select
                  value={itemFilters.sortBy}
                  onChange={(event) => setItemFilters((prev) => ({ ...prev, sortBy: event.target.value }))}
                >
                  {sortFields.map((field) => <option key={field.key} value={field.key}>{field.label}</option>)}
                </select>
              </label>
              <button className="btn" type="button" onClick={() => setItemFilters((prev) => ({ ...prev, order: prev.order === 'asc' ? 'desc' : 'asc' }))}>
                {itemFilters.order === 'asc' ? '升序 ↑' : '降序 ↓'}
              </button>
              <button className="btn" type="submit">查询</button>
            </form>

            <table className="data-table">
              <thead>
                <tr>
                  <th>字段名</th>
                  <th>变化类型</th>
                  <th>旧值</th>
                  <th>新值</th>
                  <th>影响元数据记录</th>
                  <th>下游节点</th>
                  <th>仍在使用旧映射的补数批次</th>
                  <th>确认状态</th>
                </tr>
              </thead>
              <tbody>
                {ticketItems.map((item) => (
                  <tr key={item.字段名}>
                    <td>{item.字段名}</td>
                    <td>
                      <span className={`tag ${item.变化类型 === '移除' ? 'danger' : item.变化类型 === '新增' ? 'ok' : 'warn'}`}>
                        {item.变化类型}
                      </span>
                    </td>
                    <td className="mono">{item.旧值}</td>
                    <td className="mono">{item.新值}</td>
                    <td>
                      {item.影响元数据记录.length > 0
                        ? item.影响元数据记录.map((record) => (
                            <span key={record.记录编号} className="chip">{record.记录编号} · {record.字段名称}</span>
                          ))
                        : '—'}
                    </td>
                    <td>
                      {item.下游节点.length > 0
                        ? item.下游节点.map((node) => (
                            <span key={node.节点编号} className="chip">{node.节点编号} · {node.节点名称}</span>
                          ))
                        : '—'}
                    </td>
                    <td>
                      {item.仍在使用旧映射的补数批次.length > 0
                        ? item.仍在使用旧映射的补数批次.map((batch) => (
                            <span key={batch.批次编号} className="chip warn">{batch.批次编号} · {batch.绑定版本号}</span>
                          ))
                        : '—'}
                    </td>
                    <td>
                      {item.确认状态}
                      {item.是否冲突 && (
                        <span className="tag danger conflict" title={item.冲突说明}>
                          冲突 → {item.裁决版本}
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
                {ticketItems.length === 0 && (
                  <tr>
                    <td className="empty-state" colSpan={8}>没有符合检索条件的字段差异项</td>
                  </tr>
                )}
              </tbody>
            </table>
            <footer className="page-foot">
              <span>共 {itemTotal} 个字段差异项</span>
              <span className="row-actions">
                <button className="btn primary" type="button" disabled={!canConfirm} onClick={() => void confirmTicket()}>
                  {activeTicket.状态 === '已确认' ? '已确认，版本已落库' : canConfirm ? '评审确认并落库' : '存在冲突，暂不能确认'}
                </button>
              </span>
            </footer>
          </>
        )}

        {activeTicket && (
          <TicketSummary ticketNo={activeTicket.票号} emptyText="生成对照票后展示字段名、旧值、新值与确认状态" />
        )}
      </section>

      <footer className="page-foot">
        <span>共 {total} 条血缘图谱记录</span>
        {notice && <span className="ok-text">{notice}</span>}
        {errorMessage && <span className="error-text">{errorMessage}</span>}
      </footer>
    </section>
  )
}
