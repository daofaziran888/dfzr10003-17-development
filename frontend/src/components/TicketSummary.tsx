import { useEffect, useState } from 'react'

import { fetchJson } from '@/api/client'

type SummaryItem = {
  字段名: string
  旧值: string
  新值: string
  变化类型: string
  确认状态: string
  裁决版本: string
  是否冲突: boolean
}

type Summary = {
  票号: string
  节点编号: string
  节点名称: string
  基线版本: string
  候选版本: string
  状态: string
  只读: boolean
  items: SummaryItem[]
}

/** 三个入口（血缘节点列表、元数据待办、历史补数）共用同一份对照票摘要。 */
export default function TicketSummary({ ticketNo, emptyText }: { ticketNo: string; emptyText?: string }) {
  const [summary, setSummary] = useState<Summary | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    if (!ticketNo) {
      setSummary(null)
      return
    }
    setError('')
    fetchJson<Summary>(`/api/lineage/tickets/${encodeURIComponent(ticketNo)}/summary`)
      .then(setSummary)
      .catch((reason: unknown) => {
        setSummary(null)
        setError(reason instanceof Error ? reason.message : '对照票摘要读取失败')
      })
  }, [ticketNo])

  if (!ticketNo) {
    return <p className="hint-text">{emptyText ?? '选择一张对照票后可查看字段名、旧值、新值与确认状态'}</p>
  }
  if (error) {
    return <p className="error-text">{error}</p>
  }
  if (!summary) {
    return <p className="hint-text">对照票摘要加载中…</p>
  }

  return (
    <article className="section-card">
      <header className="section-head">
        <div>
          <strong>对照票 {summary.票号}</strong>
          <span className="hint-text">
            {' '}节点 {summary.节点编号} · 基线 {summary.基线版本 || '—'} → 候选 {summary.候选版本}
          </span>
        </div>
        <span className={`tag ${summary.状态 === '已确认' ? 'ok' : 'warn'}`}>
          {summary.状态}{summary.只读 ? ' · 只读' : ''}
        </span>
      </header>
      <table className="data-table compact">
        <thead>
          <tr>
            <th>字段名</th>
            <th>旧值</th>
            <th>新值</th>
            <th>变化类型</th>
            <th>确认状态</th>
            <th>裁决版本</th>
          </tr>
        </thead>
        <tbody>
          {summary.items.map((item) => (
            <tr key={item.字段名}>
              <td>{item.字段名}</td>
              <td className="mono">{item.旧值}</td>
              <td className="mono">{item.新值}</td>
              <td>
                <span className={`tag ${item.变化类型 === '移除' ? 'danger' : item.变化类型 === '新增' ? 'ok' : 'warn'}`}>
                  {item.变化类型}
                </span>
                {item.是否冲突 && <span className="tag danger conflict">冲突</span>}
              </td>
              <td>{item.确认状态}</td>
              <td className="mono">{item.裁决版本}</td>
            </tr>
          ))}
          {summary.items.length === 0 && (
            <tr>
              <td className="empty-state" colSpan={6}>候选版本与稳定版本的字段映射一致，无差异项</td>
            </tr>
          )}
        </tbody>
      </table>
    </article>
  )
}
