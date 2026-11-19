export type GroupableChange = {
  id: string; title: string; detail: string; before: string; after: string
  kind: 'format' | 'structure' | 'reference' | 'ai'; status: 'pending' | 'accepted' | 'rejected'
  anchor: string; rule: string; start?: number; end?: number; replacement?: string | null; line?: number; occurrence?: number
}
export const LOW_RISK_RULES = ['paragraph.spacing', 'heading.spacing', 'heading.numbering']
function rangeKey(change: GroupableChange) {
  return Number.isInteger(change.start) && Number.isInteger(change.end) ? change.start + ':' + change.end : 'id:' + change.id
}
function replacement(change: GroupableChange) { return change.replacement ?? change.after }
export function conflictingChangeIds(changes: GroupableChange[]) {
  const conflicts = new Set<string>()
  for (let i = 0; i < changes.length; i++) for (let j = i + 1; j < changes.length; j++) {
    const a = changes[i], b = changes[j]
    if (a.start === undefined || a.end === undefined || b.start === undefined || b.end === undefined) continue
    const exact = a.start === b.start && a.end === b.end
    const overlap = exact || (a.start === a.end ? a.start > b.start && a.start < b.end : b.start === b.end ? b.start > a.start && b.start < a.end : a.start < b.end && b.start < a.end)
    if (overlap && !(exact && replacement(a) === replacement(b))) { conflicts.add(a.id); conflicts.add(b.id) }
  }
  return conflicts
}
export function groupChanges<T extends GroupableChange>(changes: T[]) {
  const groups = new Map<string, { key: string; title: string; items: T[]; locations: { key: string; items: T[] }[] }>()
  const identities = new Map<string, { group: { items: T[] }; location: { items: T[] } }>()
  for (const change of changes) {
    const identity = JSON.stringify([rangeKey(change), replacement(change)])
    const duplicate = identities.get(identity)
    if (duplicate) { duplicate.group.items.push(change); duplicate.location.items.push(change); continue }
    const key = change.rule === 'heading.spacing' ? 'heading.spacing' : change.rule + ':' + change.title
    const title = change.rule === 'heading.spacing' ? '标题前后空行' : change.rule === 'heading.numbering' ? '标题编号' : change.title
    if (!groups.has(key)) groups.set(key, { key, title, items: [], locations: [] })
    const group = groups.get(key)!
    group.items.push(change)
    const location = { key: identity, items: [change] }
    group.locations.push(location)
    identities.set(identity, { group, location })
  }
  return [...groups.values()]
}
// Offsets from the API are UTF-16, matching JavaScript string positions.
export function changeLocation(source: string, start: number | undefined, quote: string, title: string) {
  if (start === undefined || start < 0 || start > source.length) return { anchor: quote }
  let offset = start
  if (!quote && title.includes('后') && offset > 0 && source[offset - 1] === '\n') offset--
  const lineStart = source.lastIndexOf('\n', Math.max(0, offset - 1)) + 1
  const nextBreak = source.indexOf('\n', offset)
  const anchor = quote || source.slice(lineStart, nextBreak < 0 ? source.length : nextBreak).trim()
  const anchorStart = quote ? start : source.indexOf(anchor, lineStart)
  const occurrence = anchor ? source.slice(0, Math.max(0, anchorStart)).split(anchor).length - 1 : undefined
  return { anchor, line: source.slice(0, offset).split('\n').length, occurrence }
}
export function styleChangeGroup(field: string) {
  return field.startsWith('headings.') ? '标题版式' : field.startsWith('margin_') ? '页面边距' : '正文版式'
}
