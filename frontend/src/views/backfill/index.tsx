import { useCallback, useEffect, useMemo, useState } from 'react'

import { request } from '@/api/client'
import TicketSummary from '@/components/TicketSummary'

const ENDPOINT = '/api/backfill'
const columns = [
  "批次编号",
  "目标表名",
  "开始日期",
  "结束日期",
  "并行数量",
  "覆盖模式",
  "重跑状态",
  "异常区间",
  "绑定版本号",
  "绑定票号"
]
const actions = [
  "启动重跑",
  "暂停重跑",
  "归档批次"
]
const statuses = [
  "草稿",
  "运行中",
  "已暂停",
  "已完成"
]
const stats = [
  {
    "label": "重跑批次总数",
    "value": 0
  },
  {
    "label": "运行中批次",
    "value": 0
  },
  {
    "label": "已暂停批次",
    "value": 0
  }
]

type Row = Record<string, string | number | null>

type Version = {
  节点编号: string
  节点名称: string
  版本号: string
  绑定版本号: string
  票号: string
  生效时间: string
  状态: string
}

export default function BackfillView() {
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [errorMessage, setErrorMessage] = useState('')
  const [filters, setFilters] = useState<Record<string, string>>({})
  const filterFields = useMemo(() => columns.slice(0, 3), [])

  const [versions, setVersions] = useState<Version[]>([])
  const [form, setForm] = useState({ 批次编号: '', 目标表名: '', 开始日期: '', 绑定版本号: '' })
  const [activeTicketNo, setActiveTicketNo] = useState('')
  const [notice, setNotice] = useState('')

  const reload = useCallback(async () => {
    setErrorMessage('')
    const query = new URLSearchParams(filters).toString()
    try {
      const response = await request(`${ENDPOINT}?${query}`)
      if (!response.ok) {
        throw new Error('重跑批次列表读取失败')
      }
      const payload = await response.json()
      setRows(payload.items ?? [])
      setTotal(payload.total ?? 0)
      setActiveTicketNo((current) => current || (payload.items?.[0]?.['绑定票号'] as string) || '')
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '历史重跑列表读取失败')
    }
  }, [filters])

  const reloadVersions = useCallback(async () => {
    try {
      const response = await request(`${ENDPOINT}/versions`)
      if (!response.ok) {
        throw new Error('已确认版本列表读取失败')
      }
      const payload = await response.json()
      const items: Version[] = payload.items ?? []
      setVersions(items)
      setForm((prev) => ({
        ...prev,
        绑定版本号: prev.绑定版本号 || (items[0]?.绑定版本号 ?? ''),
      }))
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '已确认版本列表读取失败')
    }
  }, [])

  useEffect(() => {
    void reload()
  }, [reload])

  useEffect(() => {
    void reloadVersions()
  }, [reloadVersions])

  async function runAction(action: string, row: Row) {
    setErrorMessage('')
    setNotice('')
    try {
      const response = await request(`${ENDPOINT}/${row.id}/actions`, {
        method: 'POST',
        body: JSON.stringify({ action }),
      })
      const payload = await response.json()
      if (!response.ok || !payload.ok) {
        throw new Error(payload.message ?? '历史重跑动作未生效，请稍后重试')
      }
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '历史重跑操作失败')
    }
  }

  async function createBatch() {
    setErrorMessage('')
    setNotice('')
    try {
      const response = await request(ENDPOINT, {
        method: 'POST',
        body: JSON.stringify({ values: form }),
      })
      const payload = await response.json()
      if (!response.ok || !payload.ok) {
        throw new Error(payload.message ?? '重跑批次登记失败')
      }
      setNotice(`${payload.message}：${form.批次编号} → ${form.绑定版本号}`)
      setForm((prev) => ({ ...prev, 批次编号: '', 开始日期: '' }))
      await reload()
      await reloadVersions()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '重跑批次登记失败')
    }
  }

  return (
    <section className="page" data-module="backfill">
      <header className="page-head">
        <div>
          <h2>历史重跑管理</h2>
          <p className="page-desc">
            维护历史重跑批次的日期范围、并行参数与状态追踪。历史补数继续绑定旧票；
            新补数只能选择评审确认后（已生效）的血缘版本。
          </p>
        </div>
        <div className="page-actions">
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出历史重跑清单
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
            <tr key={String(row.id)} className={activeTicketNo === row['绑定票号'] ? 'row-active' : ''}>
              {columns.map((column) => <td key={column}>{row[column] ?? '—'}</td>)}
              <td className="row-actions">
                {actions.map((action) => (
                  <button key={action} className="link" type="button" onClick={() => void runAction(action, row)}>
                    {action}
                  </button>
                ))}
              </td>
              <td>
                {row['绑定票号'] ? (
                  <button className="link" type="button" onClick={() => setActiveTicketNo(String(row['绑定票号']))}>
                    查看 {String(row['绑定票号'])}
                  </button>
                ) : '—'}
              </td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td className="empty-state" colSpan={columns.length + 2}>暂无历史重跑数据，可先登记重跑批次</td>
            </tr>
          )}
        </tbody>
      </table>

      <section className="ticket-panel" data-testid="backfill-ticket-panel">
        <header className="section-head">
          <div>
            <h3>新补数登记（只能选择已确认版本）</h3>
            <p className="page-desc">待评审的候选血缘版本不会出现在可选列表里；登记时后端会再次校验绑定版本是否已确认。</p>
          </div>
        </header>
        <form
          className="filter-bar"
          onSubmit={(event) => { event.preventDefault(); void createBatch() }}
        >
          <label className="filter-item">
            <span>批次编号</span>
            <input
              value={form.批次编号}
              placeholder="如 BACK-0005"
              onChange={(event) => setForm((prev) => ({ ...prev, 批次编号: event.target.value }))}
            />
          </label>
          <label className="filter-item">
            <span>目标表名</span>
            <input
              value={form.目标表名}
              placeholder="如 dws_order_summary"
              onChange={(event) => setForm((prev) => ({ ...prev, 目标表名: event.target.value }))}
            />
          </label>
          <label className="filter-item">
            <span>开始日期</span>
            <input
              value={form.开始日期}
              placeholder="如 2026-11-01"
              onChange={(event) => setForm((prev) => ({ ...prev, 开始日期: event.target.value }))}
            />
          </label>
          <label className="filter-item">
            <span>绑定血缘版本</span>
            <select
              value={form.绑定版本号}
              onChange={(event) => setForm((prev) => ({ ...prev, 绑定版本号: event.target.value }))}
            >
              {versions.map((version) => (
                <option key={version.绑定版本号} value={version.绑定版本号}>
                  {version.绑定版本号} · {version.状态} · {version.票号}
                </option>
              ))}
            </select>
          </label>
          <button className="btn primary" type="submit" disabled={versions.length === 0}>登记重跑批次</button>
        </form>

        <TicketSummary
          ticketNo={activeTicketNo}
          emptyText="补数批次绑定的是哪张对照票，就在同一张票里查看字段名、旧值、新值与确认状态"
        />
      </section>

      <footer className="page-foot">
        <span>共 {total} 条历史重跑记录</span>
        {notice && <span className="ok-text">{notice}</span>}
        {errorMessage && <span className="error-text">{errorMessage}</span>}
      </footer>
    </section>
  )
}
