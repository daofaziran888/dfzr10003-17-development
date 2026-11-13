import { useCallback, useEffect, useMemo, useState } from 'react'

import { request } from '@/api/client'
import TicketSummary from '@/components/TicketSummary'

const ENDPOINT = '/api/metadata'
const columns = [
  "记录编号",
  "表名称",
  "字段名称",
  "字段类型",
  "业务含义",
  "统计信息",
  "更新频率",
  "负责人"
]
const actions = [
  "提交审批",
  "发布元数据",
  "废弃元数据"
]
const statuses = [
  "草稿",
  "待审批",
  "已生效",
  "已废弃"
]
const stats = [
  {
    "label": "元数据记录总数",
    "value": 0
  },
  {
    "label": "已生效记录",
    "value": 0
  },
  {
    "label": "待审批记录",
    "value": 0
  }
]
const todoStatuses = ['待确认', '已确认', '已忽略']

type Row = Record<string, string | number | null>

type Todo = {
  id: number
  事项编号: string
  记录编号: string
  表名称: string
  字段名称: string
  变化类型: string
  旧值: string
  新值: string
  引用血缘节点: string
  引用血缘节点版本: string
  引用对照票: string
  状态: string
  生成时间: string
  [key: string]: string | number | null
}

export default function MetadataView() {
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [errorMessage, setErrorMessage] = useState('')
  const [filters, setFilters] = useState<Record<string, string>>({})
  const filterFields = useMemo(() => columns.slice(0, 3), [])

  const [todos, setTodos] = useState<Todo[]>([])
  const [todoTotal, setTodoTotal] = useState(0)
  const [todoFilters, setTodoFilters] = useState({ keyword: '', status: '', ticketNo: '' })
  const [activeTicketNo, setActiveTicketNo] = useState('')
  const [notice, setNotice] = useState('')

  const reload = useCallback(async () => {
    setErrorMessage('')
    const query = new URLSearchParams(filters).toString()
    try {
      const response = await request(`${ENDPOINT}?${query}`)
      if (!response.ok) {
        throw new Error('元数据记录列表读取失败')
      }
      const payload = await response.json()
      setRows(payload.items ?? [])
      setTotal(payload.total ?? 0)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '元数据目录列表读取失败')
    }
  }, [filters])

  const reloadTodos = useCallback(async () => {
    const query = new URLSearchParams()
    if (todoFilters.keyword) query.set('keyword', todoFilters.keyword)
    if (todoFilters.status) query.set('status', todoFilters.status)
    if (todoFilters.ticketNo) query.set('ticket_no', todoFilters.ticketNo)
    query.set('size', '100')
    try {
      const response = await request(`${ENDPOINT}/todos?${query.toString()}`)
      if (!response.ok) {
        throw new Error('元数据待办读取失败')
      }
      const payload = await response.json()
      setTodos(payload.items ?? [])
      setTodoTotal(payload.total ?? 0)
      setActiveTicketNo((current) => current || (payload.items?.[0]?.引用对照票 ?? ''))
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '元数据待办读取失败')
    }
  }, [todoFilters])

  useEffect(() => {
    void reload()
  }, [reload])

  useEffect(() => {
    void reloadTodos()
  }, [reloadTodos])

  async function runAction(action: string, row: Row) {
    setErrorMessage('')
    setNotice('')
    try {
      const response = await request(`${ENDPOINT}/${row.id}/actions`, {
        method: 'POST',
        body: JSON.stringify({ action }),
      })
      if (!response.ok) {
        throw new Error('元数据目录动作未生效，请稍后重试')
      }
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '元数据目录操作失败')
    }
  }

  async function runTodoAction(action: string, todo: Todo) {
    setErrorMessage('')
    setNotice('')
    try {
      const response = await request(`${ENDPOINT}/todos/${todo.id}/actions`, {
        method: 'POST',
        body: JSON.stringify({ action }),
      })
      const payload = await response.json()
      if (!response.ok || !payload.ok) {
        throw new Error(payload.message ?? '待确认事项处理失败')
      }
      setNotice(`${payload.message}：${todo.事项编号}（引用 ${todo.引用血缘节点版本}）`)
      await reloadTodos()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '待确认事项处理失败')
    }
  }

  return (
    <section className="page" data-module="metadata">
      <header className="page-head">
        <div>
          <h2>元数据目录管理</h2>
          <p className="page-desc">维护元数据目录的字段定义、业务含义与统计信息，为数据资产盘点提供可追溯的数据工程流程。</p>
        </div>
        <div className="page-actions">
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出元数据目录清单
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
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td className="empty-state" colSpan={columns.length + 1}>暂无元数据目录数据，可先登记元数据记录</td>
            </tr>
          )}
        </tbody>
      </table>

      <section className="ticket-panel" data-testid="metadata-todo-panel">
        <header className="section-head">
          <div>
            <h3>元数据待办（血缘版本变更）</h3>
            <p className="page-desc">血缘对照票确认后，受影响的元数据记录会生成待确认事项，每条都引用产生它的血缘节点版本。</p>
          </div>
        </header>

        <form className="filter-bar" onSubmit={(event) => { event.preventDefault(); void reloadTodos() }}>
          <label className="filter-item">
            <span>字段名 / 记录编号</span>
            <input
              value={todoFilters.keyword}
              placeholder="按字段名检索"
              onChange={(event) => setTodoFilters((prev) => ({ ...prev, keyword: event.target.value }))}
            />
          </label>
          <label className="filter-item">
            <span>状态</span>
            <select
              value={todoFilters.status}
              onChange={(event) => setTodoFilters((prev) => ({ ...prev, status: event.target.value }))}
            >
              <option value="">全部</option>
              {todoStatuses.map((status) => <option key={status} value={status}>{status}</option>)}
            </select>
          </label>
          <label className="filter-item">
            <span>对照票</span>
            <input
              value={todoFilters.ticketNo}
              placeholder="按对照票号检索"
              onChange={(event) => setTodoFilters((prev) => ({ ...prev, ticketNo: event.target.value }))}
            />
          </label>
          <button className="btn" type="submit">查询</button>
        </form>

        <table className="data-table">
          <thead>
            <tr>
              <th>事项编号</th>
              <th>记录编号</th>
              <th>字段名称</th>
              <th>变化类型</th>
              <th>旧值</th>
              <th>新值</th>
              <th>引用血缘节点版本</th>
              <th>引用对照票</th>
              <th>状态</th>
              <th>可执行动作</th>
            </tr>
          </thead>
          <tbody>
            {todos.map((todo) => (
              <tr key={todo.id} className={activeTicketNo === todo.引用对照票 ? 'row-active' : ''}>
                <td>{todo.事项编号}</td>
                <td>{todo.记录编号}</td>
                <td>{todo.字段名称}</td>
                <td>
                  <span className={`tag ${todo.变化类型 === '移除' ? 'danger' : todo.变化类型 === '新增' ? 'ok' : 'warn'}`}>
                    {todo.变化类型}
                  </span>
                </td>
                <td className="mono">{todo.旧值}</td>
                <td className="mono">{todo.新值}</td>
                <td className="mono">{todo.引用血缘节点版本}</td>
                <td>
                  <button className="link" type="button" onClick={() => setActiveTicketNo(todo.引用对照票)}>
                    {todo.引用对照票}
                  </button>
                </td>
                <td>{todo.状态}</td>
                <td className="row-actions">
                  {todo.状态 === '待确认' && (
                    <>
                      <button className="link" type="button" onClick={() => void runTodoAction('确认事项', todo)}>
                        确认事项
                      </button>
                      <button className="link" type="button" onClick={() => void runTodoAction('忽略事项', todo)}>
                        忽略事项
                      </button>
                    </>
                  )}
                </td>
              </tr>
            ))}
            {todos.length === 0 && (
              <tr>
                <td className="empty-state" colSpan={10}>暂无血缘版本变更产生的待确认事项</td>
              </tr>
            )}
          </tbody>
        </table>
        <footer className="page-foot">
          <span>共 {todoTotal} 条待确认事项</span>
        </footer>

        <TicketSummary
          ticketNo={activeTicketNo}
          emptyText="点击待办行的对照票号，可查看同一张对照票的字段名、旧值、新值与确认状态"
        />
      </section>

      <footer className="page-foot">
        <span>共 {total} 条元数据目录记录</span>
        {notice && <span className="ok-text">{notice}</span>}
        {errorMessage && <span className="error-text">{errorMessage}</span>}
      </footer>
    </section>
  )
}
