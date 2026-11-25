const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const React = require('react')
const { renderToStaticMarkup } = require('react-dom/server')
const { transformSync } = require('next/dist/build/swc')
const root = path.join(__dirname, '..')
function compile(filename, requireModule) {
  const source = fs.readFileSync(path.join(root, filename), 'utf8')
  const code = transformSync(source, { filename, jsc: { parser: { syntax: 'typescript', tsx: filename.endsWith('.tsx') }, target: 'es2020', transform: { react: { runtime: 'automatic' } } }, module: { type: 'commonjs' } }).code
  const module = { exports: {} }
  vm.runInNewContext(code, { module, exports: module.exports, require: requireModule, process, window: { matchMedia: () => ({ matches: narrow }), setTimeout: () => 0 } })
  return module.exports
}
let narrow = false
const layout = compile('lib/workspaceLayout.ts', require)
const storage = { value: null, getItem() { return this.value }, setItem(key, value) { this.value = value } }
assert.equal(layout.readWorkspaceLayout(storage, false).leftRailOpen, true)
assert.equal(layout.readWorkspaceLayout(storage, false).rightPanelOpen, false)
assert.equal(layout.readWorkspaceLayout(storage, true).leftRailOpen, false)
assert.equal(layout.readWorkspaceLayout(storage, true).showDiff, false)
const remembered = { leftRailOpen: false, rightPanelOpen: true, showDiff: true, reviewTab: 'assistance' }
layout.saveWorkspaceLayout(storage, remembered)
assert.equal(JSON.stringify(layout.readWorkspaceLayout(storage, false)), JSON.stringify(remembered))
storage.value = '{invalid'
assert.equal(layout.readWorkspaceLayout(storage, false).rightPanelOpen, false)
assert.doesNotThrow(() => layout.saveWorkspaceLayout({ setItem() { throw Error('blocked') } }, remembered))

// Render the actual page and exercise its layout handlers without network or editor dependencies.
const source = fs.readFileSync(path.join(root, 'app/page.tsx'), 'utf8')
const names = source.split(String.fromCharCode(10)).filter(line => line.includes('const [') && line.includes('] = useState')).map(line => line.split('const [')[1].split(',')[0])
const values = new Map([['workspaceLoading', false], ['activeDocumentId', 'doc-layout-test'], ['documentName', 'layout.md']])
let cursor = 0
const react = { ...React, useEffect() {}, useRef: value => ({ current: value }), useState(initial) {
  const name = names[cursor++]
  if (!values.has(name)) values.set(name, typeof initial === 'function' ? initial() : initial)
  return [values.get(name), update => values.set(name, typeof update === 'function' ? update(values.get(name)) : update)]
} }
function component(name) { return { __esModule: true, default: props => React.createElement('section', { 'data-component': name, hidden: props.hidden }) } }
const Home = compile('app/page.tsx', name => {
  if (name === 'react') return react
  if (name === '@tiptap/react') return { useEditor: () => null, EditorContent: () => React.createElement('div', { 'data-testid': 'document-editor' }) }
  if (name === 'lucide-react') return new Proxy({}, { get: (_, icon) => () => React.createElement('svg', { 'data-icon': icon }) })
  if (name === '../lib/workspaceLayout') return layout
  if (name === '../lib/changeGrouping') return compile('lib/changeGrouping.ts', require)
  if (name === '../lib/exampleDocument') return compile('lib/exampleDocument.ts', require)
  if (name === '../lib/documentStudio') return { DEFAULT_DOCUMENT_STYLE: {}, documentStyleCss: () => ({}) }
  if (name === '../lib/markdown') return { editorExtensions: [], serializeMarkdown: () => '' }
  if (name.startsWith('../components/')) return component(name.split('/').at(-1))
  if (name.endsWith('.css') || name === '../lib/schoolTemplates') return {}
  return require(name)
}).default
function render() { cursor = 0; return Home() }
function all(node, predicate) {
  if (!node || typeof node !== 'object') return []
  const children = React.Children.toArray(node.props?.children)
  return [...(predicate(node) ? [node] : []), ...children.flatMap(child => all(child, predicate))]
}
function text(node) { return typeof node === 'string' || typeof node === 'number' ? String(node) : node && typeof node === 'object' ? React.Children.toArray(node.props?.children).map(text).join('') : '' }
function find(tree, predicate) { const item = all(tree, predicate)[0]; assert.ok(item, 'Expected page element'); return item }
function button(tree, label) { return find(tree, node => node.type === 'button' && text(node) === label) }
function grid(tree) { return find(tree, node => String(node.props?.className).startsWith('workspace-grid')).props.className }
let tree = render()
assert.match(grid(tree), /review-collapsed/)
assert.doesNotMatch(grid(tree), /nav-collapsed/)
assert.equal(find(tree, node => node.props?.className === 'format-diff-container').props.hidden, true)
assert.equal(find(tree, node => node.props?.className === 'formula-bar').props.hidden, true)
assert.doesNotMatch(renderToStaticMarkup(tree), /<details[^>]+open/)
find(tree, node => node.props?.['aria-label'] === '收起文档侧栏').props.onClick()
tree = render(); assert.match(grid(tree), /nav-collapsed/)
button(tree, '文档助手').props.onClick()
tree = render(); assert.doesNotMatch(grid(tree), /review-collapsed/); assert.match(grid(tree), /nav-collapsed/)
assert.equal(values.get('tab'), 'assistant')
find(tree, node => node.props?.['aria-label'] === '收起审查面板').props.onClick()
tree = render(); assert.match(grid(tree), /review-collapsed/); assert.match(grid(tree), /nav-collapsed/)
narrow = true
find(tree, node => node.props?.['aria-label'] === '展开文档侧栏').props.onClick()
tree = render(); assert.doesNotMatch(grid(tree), /nav-collapsed/)
button(tree, '查找文献').props.onClick()
tree = render(); assert.match(grid(tree), /nav-collapsed/); assert.doesNotMatch(grid(tree), /review-collapsed/)
assert.equal(values.get('tab'), 'literature')
assert.ok(all(tree, node => typeof node.type === 'function' && node.props?.documentId === 'doc-layout-test').length)
find(tree, node => node.props?.['aria-label'] === '关闭侧栏，返回正文').props.onClick()
tree = render(); assert.match(grid(tree), /nav-collapsed/); assert.match(grid(tree), /review-collapsed/)
for (const action of ['新建文档', '导入文档', '文档模板', '调整排版与导出', '显示正文对比', '插入公式', '恢复本次原稿', '导出修订报告', '模型设置']) button(tree, action)
button(tree, '显示正文对比').props.onClick(); button(tree, '插入公式').props.onClick()
tree = render()
assert.equal(find(tree, node => node.props?.className === 'format-diff-container').props.hidden, false)
assert.equal(find(tree, node => node.props?.className === 'formula-bar').props.hidden, false)
assert.ok(source.includes('/batches'))
assert.ok(source.includes('setLastBatch(null)'))
console.log('PASS: layout persistence, safe defaults, actual page DOM, independent collapse, narrow drawer switching, tools, comparison, formula and assistant entry')
