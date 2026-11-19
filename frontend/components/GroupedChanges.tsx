'use client'
import { useEffect, useMemo, useState } from 'react'
import { TextDiff } from './RevisionDiff'
import { conflictingChangeIds, groupChanges, LOW_RISK_RULES, type GroupableChange } from '../lib/changeGrouping'
import './grouped-changes.css'

type Props<T extends GroupableChange> = {
  changes: T[]; activeId: string; contextKey: string; disabled: boolean; blockedIds: string[]
  onLocate: (change: T) => void; onDecide: (change: T, decision: 'accept' | 'reject') => void
  onBatch: (changes: T[], decision: 'accept' | 'reject') => void
}
export default function GroupedChanges<T extends GroupableChange>({changes, activeId, contextKey, disabled, blockedIds, onLocate, onDecide, onBatch}: Props<T>) {
  const groups = useMemo(() => groupChanges(changes), [changes])
  const conflicts = useMemo(() => conflictingChangeIds(changes), [changes])
  const [nextAt, setNextAt] = useState<Record<string, number>>({})
  useEffect(() => setNextAt({}), [contextKey])
  return <div className="grouped-changes" key={contextKey}>
    {!!groups.length && <p className="change-group-count" role="status">{groups.length} 组建议 · {changes.length} 项修改</p>}
    {groups.map(group => {
      const safe = group.items.every(item => LOW_RISK_RULES.includes(item.rule) && !blockedIds.includes(item.id) && !conflicts.has(item.id))
      const inLimit = group.items.length <= 500
      return <section className="change-group" key={group.key} data-change-group={group.key}>
        <details><summary><strong>{group.title}</strong><span>{group.locations.length} 处{group.items.length !== group.locations.length ? ' · ' + group.items.length + ' 条来源' : ''}</span></summary>
          <p className="change-group-reason">{group.items[0].detail}</p>
          <div className="change-group-locations">{group.locations.map((location, index) => {
            const item = location.items[0]
            return <article id={'change-' + item.id} className={'change-card grouped-location' + (location.items.some(value => value.id === activeId) ? ' selected' : '')} key={location.key} data-rule={item.rule}>
              <button type="button" className="change-card-content" onClick={() => onLocate(item)} aria-label={'定位修改：' + item.title + ' 第' + (index + 1) + '处'}>
                <small>{item.line ? '第 ' + item.line + ' 行 · ' : ''}{item.title}</small>
                {item.anchor && <p className="group-anchor" title={item.anchor}>{item.anchor}</p>}
                <div className="change-diff"><span className="before">− <TextDiff before={item.before} after={item.after} side="before"/></span><span className="after">＋ <TextDiff before={item.before} after={item.after} side="after"/></span></div>
              </button>
              {conflicts.has(item.id) && <p className="group-conflict" role="status">此位置存在不同建议，请选择一项。</p>}
              {location.items.length > 1 && <details className="group-sources"><summary>{location.items.length} 条相同建议的来源</summary><ul>{location.items.map(value => <li key={value.id}>{value.title} · {value.rule}{value.detail !== group.items[0].detail && <p>{value.detail}</p>}</li>)}</ul></details>}
              <div className="change-card-actions"><button type="button" className="accept-btn" disabled={disabled || blockedIds.includes(item.id)} onClick={() => location.items.length > 1 && safe ? onBatch(location.items, 'accept') : onDecide(item, 'accept')}>接受此项</button><button type="button" className="reject-btn" disabled={disabled} onClick={() => location.items.length > 1 ? onBatch(location.items, 'reject') : onDecide(item, 'reject')}>保留此项</button></div>
            </article>
          })}</div>
        </details>
        <div className="change-group-actions"><button type="button" className="ghost-button" disabled={disabled} onClick={() => { const index = (nextAt[group.key] ?? 0) % group.locations.length; onLocate(group.locations[index].items[0]); setNextAt(value => ({...value,[group.key]:index + 1})) }}>定位下一处</button><button type="button" className="accept-btn" disabled={disabled || !safe || !inLimit} title={safe ? '整组接受并复检，可撤销本批' : '内容、引用或冲突建议请展开后逐项审阅'} onClick={() => onBatch(group.items, 'accept')}>接受此组</button><button type="button" className="reject-btn" disabled={disabled || !inLimit} onClick={() => onBatch(group.items, 'reject')}>保留此组</button></div>
        {!inLimit && <small className="group-conflict">每批最多 500 项，请展开后逐项处理。</small>}
      </section>
    })}
  </div>
}
