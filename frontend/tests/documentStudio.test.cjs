const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const React = require('react')
const { transformSync } = require('next/dist/build/swc')

function compile(filename, dependencies = {}, globals = {}) {
  const source = fs.readFileSync(path.join(__dirname, '..', filename), 'utf8')
  const code = transformSync(source, { filename, jsc: { parser: { syntax: 'typescript', tsx: filename.endsWith('.tsx') }, target: 'es2020', transform: { react: { runtime: 'automatic' } } }, module: { type: 'commonjs' } }).code
  const module = { exports: {} }
  vm.runInNewContext(code, { module, exports: module.exports, Blob, AbortController, AbortSignal, Error, Map, Set, ...globals, require: name => name.endsWith('.css') ? {} : dependencies[name] ?? require(name) })
  return module.exports
}
function all(node, predicate) {
  if (!node || typeof node !== 'object') return []
  return [...(predicate(node) ? [node] : []), ...React.Children.toArray(node.props?.children).flatMap(child => all(child, predicate))]
}
function text(node) {
  if (typeof node === 'string' || typeof node === 'number') return String(node)
  return node && typeof node === 'object' ? React.Children.toArray(node.props?.children).map(text).join('') : ''
}
function find(tree, predicate, label = 'expected element') { const node = all(tree, predicate)[0]; assert.ok(node, label); return node }
const button = (tree, name) => find(tree, node => node.type === 'button' && text(node) === name, name)
const labelled = (tree, label) => find(tree, node => node.props?.['aria-label'] === label, label)
const confirmation = tree => all(tree, node => node.props?.['aria-label'] === '确认PDF字体替代')[0]
const plain = value => JSON.parse(JSON.stringify(value))
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no }); return { promise, resolve, reject } }

// Execute the real compiled component and its event handlers. Effects and their
// cleanups run after each render, with async completion flushed between renders.
function hooks() {
  const states = [], refs = [], effects = []
  let stateIndex = 0, refIndex = 0, effectIndex = 0, pending = [], dirty = false, component, props, tree
  const react = { ...React,
    useState(initial) { const at = stateIndex++; if (!(at in states)) states[at] = typeof initial === 'function' ? initial() : initial; return [states[at], next => { const value = typeof next === 'function' ? next(states[at]) : next; if (!Object.is(value, states[at])) { states[at] = value; dirty = true } }] },
    useRef(initial) { const at = refIndex++; return refs[at] ??= { current: initial } },
    useId() { return 'test-picker-list' },
    useMemo(callback) { return callback() },
    useEffect(callback, dependencies) { const at = effectIndex++; const old = effects[at]; if (!old || !dependencies || dependencies.some((value, index) => !Object.is(value, old.dependencies?.[index]))) pending.push(() => { old?.cleanup?.(); effects[at] = { dependencies, cleanup: callback() } }) },
  }
  function render() {
    let turns = 0
    do {
      assert.ok(++turns < 30, 'Component effects must settle.')
      dirty = false; stateIndex = refIndex = effectIndex = 0; pending = []
      tree = component(props); pending.forEach(run => run())
    } while (dirty)
    return tree
  }
  return { react, mount(value, input) { component = value; props = input; return render() }, render, get tree() { return tree }, update(patch) { props = { ...props, ...patch }; return render() }, async settle() { for (let i = 0; i < 6; i++) { await new Promise(resolve => setImmediate(resolve)); if (dirty) render() } return tree }, dispose() { effects.forEach(effect => effect.cleanup?.()) } }
}

const fontHelpers = compile('lib/fonts.ts')
const styleHelpers = compile('lib/documentStudio.ts', { './fonts': fontHelpers })
const grouping = compile('lib/changeGrouping.ts')
const result = (label = 'PDF') => ({ blob: new Blob(['%PDF-' + label]), limitations: [label + '说明'] })
const issue = (name, replacement = '宋体') => ({ requested_font: name, reason: name + '缺少精确字体', suggested_font: replacement })
const missing = (...issues) => new fontHelpers.FontConfirmationRequiredError('字体需要确认', issues)

function studioFixture(overrides = {}) {
  const h = hooks(), pickerHooks = hooks()
  const calls = [], saves = [], callbacks = [], anchors = [], created = [], revoked = [], timers = []
  let closed = 0, exporter = async () => result(), revision = 4
  const document = { activeElement: null, body: { style: { overflow: '' }, appendChild() {} }, addEventListener() {}, removeEventListener() {}, getElementById: () => ({ scrollIntoView() {} }), createElement() { const anchor = { href: '', download: '', click() { anchors.push({ href: this.href, download: this.download }) }, remove() {} }; return anchor } }
  const fonts = { ...fontHelpers, getFontCatalogue: async () => ({ fonts: Array.from(fontHelpers.COMMON_DOCUMENT_FONTS, name => ({ ...fontHelpers.unknownFont(name), common: true })), warnings: [] }) }
  const Picker = compile('components/FontPicker.tsx', { react: pickerHooks.react, '../lib/fonts': fonts }, { document }).default
  const helpers = { ...styleHelpers,
    getDocumentLayout: async () => ({ revision, style: styleHelpers.cloneDocumentStyle(styleHelpers.DEFAULT_DOCUMENT_STYLE) }),
    saveDocumentLayout: async (...args) => { saves.push(args); return { revision: ++revision, style: styleHelpers.cloneDocumentStyle(args[3]) } },
    exportDocument: (...args) => { calls.push(args); return exporter(...args) },
  }
  const Studio = compile('components/DocumentStudio.tsx', { react: h.react, '../lib/documentStudio': helpers, '../lib/changeGrouping': grouping, '../lib/markdown': { parseMarkdown: text => ({ text }), editorExtensions: [] }, '@tiptap/react': { EditorContent() {}, useEditor() { return null } }, './FontPicker': { __esModule: true, default: Picker } }, {
    document, HTMLElement: class HTMLElement {}, fetch: async () => ({ ok: true, json: async () => ({ retained: true, warnings: [] }) }),
    URL: { createObjectURL(blob) { const url = 'blob:test-' + created.length; created.push({ url, blob }); return url }, revokeObjectURL: url => revoked.push(url) },
    setTimeout: (callback, delay) => { timers.push({ callback, delay }); return timers.length },
  }).default
  const initial = { apiBase: 'http://127.0.0.1:studio-test', open: true, onClose() { closed++ }, documentId: 'document-a', documentEpoch: 1, documentName: '论文.docx', content: '# 当前正文', originalContent: '# 原稿正文', suggestedContent: '# 预期正文', profileStyle: null, disabled: false, onStyleChange: value => callbacks.push(plain(value)), ...overrides }
  h.mount(Studio, initial)
  return { h, pickerHooks, Picker, calls, saves, callbacks, anchors, created, revoked, timers, document, initial, get closed() { return closed }, setExporter(callback) { exporter = callback }, body() { return find(h.tree, node => node.type === Picker && node.props.ariaLabel === '正文字体') }, select(label, value) { labelled(h.tree, label).props.onChange({ target: { value } }); return h.render() }, confirm() { button(h.tree, '确认替代并继续').props.onClick(); return h.render() }, export() { button(h.tree, '导出所选稿件').props.onClick(); return h.render() }, preview() { button(h.tree, '生成 PDF 对照').props.onClick(); return h.render() } }
}

async function pendingExport(fixture) {
  fixture.setExporter(async () => { throw missing(issue('仿宋_GB2312')) })
  fixture.export(); await fixture.h.settle()
  assert.ok(confirmation(fixture.h.tree), 'A missing PDF font must request confirmation.')
  return button(fixture.h.tree, '确认替代并继续')
}

async function main() {
  {
    const f = studioFixture(); await f.h.settle()
    const initialCallbacks = f.callbacks.length
    const pickers = all(f.h.tree, node => node.type === f.Picker)
    assert.equal(pickers.length, 7, 'Body and six heading controls must share the actual FontPicker component.')
    assert.equal(f.body().props.value, '宋体')
    f.body().props.onChange('仿宋_GB2312'); f.h.render()
    assert.equal(f.body().props.value, '仿宋_GB2312')
    find(f.h.tree, node => node.type === f.Picker && node.props.ariaLabel === '1级标题字体').props.onChange('楷体_GB2312'); f.h.render()
    assert.equal(find(f.h.tree, node => node.type === f.Picker && node.props.ariaLabel === '1级标题字体').props.value, '楷体_GB2312')
    assert.equal(f.saves.length, 0, 'Changing draft fonts must not save the document automatically.')
    assert.equal(f.callbacks.length, initialCallbacks, 'Changing draft fonts must not announce a saved style.')
    f.h.dispose()
  }

  {
    const f = studioFixture(); await f.h.settle()
    f.body().props.onChange('楷体_GB2312'); f.h.render()
    const first = deferred(), second = deferred()
    f.setExporter((...args) => args[1].endsWith('-原稿') ? first.promise : second.promise)
    f.preview()
    assert.equal(f.calls.length, 2, 'Both PDF previews must start before either completes.')
    first.reject(missing(issue('仿宋_GB2312'), issue('重复字体')))
    await f.h.settle()
    assert.equal(confirmation(f.h.tree), undefined, 'Promise.allSettled must wait for the second font result.')
    second.reject(missing(issue('楷体_GB2312', '楷体'), issue('重复字体')))
    await f.h.settle()
    const panel = confirmation(f.h.tree)
    assert.ok(panel)
    assert.equal(all(panel, node => node.type === f.Picker).length, 3, 'Merge both preview font issues and deduplicate the requested font.')
    assert.match(text(panel), /仿宋_GB2312/)
    assert.match(text(panel), /楷体_GB2312/)
    assert.equal(f.created.length, 0)
    const beforeStyles = f.calls.map(call => plain(call[3]))
    const callbacks = f.callbacks.length
    f.setExporter(async (...args) => result(args[1].endsWith('-原稿') ? '原稿' : '预期稿'))
    f.confirm(); await f.h.settle()
    assert.equal(f.calls.length, 4)
    const expected = { '仿宋_GB2312': '宋体', '楷体_GB2312': '楷体', '重复字体': '宋体' }
    for (const call of f.calls.slice(2)) assert.deepEqual(plain(call[8]), expected)
    assert.deepEqual(f.calls.slice(2).map(call => plain(call[3])), beforeStyles, 'PDF replacement must leave original and draft styles unchanged.')
    assert.equal(f.body().props.value, '楷体_GB2312')
    assert.equal(f.saves.length, 0)
    assert.equal(f.callbacks.length, callbacks)
    assert.equal(confirmation(f.h.tree), undefined)
    assert.equal(all(f.h.tree, node => node.type === 'iframe').length, 2)
    assert.equal(f.created.length, 2)
    assert.match(text(f.h.tree), /原稿：原稿说明/)
    assert.match(text(f.h.tree), /预期稿：预期稿说明/)
    f.export(); await f.h.settle()
    assert.deepEqual(plain(f.calls.at(-1)[8]), {}, 'A previous PDF confirmation must never become consent for another export.')
    f.h.dispose()
  }

  {
    const f = studioFixture(); await f.h.settle(); await pendingExport(f)
    const count = f.calls.length
    button(f.h.tree, '取消本次操作').props.onClick(); f.h.render()
    assert.equal(confirmation(f.h.tree), undefined)
    assert.match(text(f.h.tree), /已取消本次字体替代/)
    assert.equal(f.calls.length, count)
    assert.equal(f.saves.length, 0)
    f.h.dispose()
  }

  {
    const f = studioFixture(); await f.h.settle()
    f.setExporter(async () => { throw missing(issue('未安装字体', '')) })
    f.export(); await f.h.settle()
    assert.equal(button(f.h.tree, '确认替代并继续').props.disabled, true, 'An empty replacement must not be confirmable.')
    find(confirmation(f.h.tree), node => node.type === f.Picker).props.onChange('未安装字体'); f.h.render()
    assert.equal(button(f.h.tree, '确认替代并继续').props.disabled, true, 'Selecting the same unavailable font must not be confirmable.')
    find(confirmation(f.h.tree), node => node.type === f.Picker).props.onChange('宋体'); f.h.render()
    assert.equal(button(f.h.tree, '确认替代并继续').props.disabled, false)
    f.h.dispose()
  }

  {
    const f = studioFixture(); await f.h.settle(); await pendingExport(f)
    const panel = confirmation(f.h.tree), replacement = find(panel, node => node.type === f.Picker)
    replacement.props.onChange('华文仿宋'); f.h.render()
    const callbacks = f.callbacks.length
    f.setExporter(async () => result('确认导出'))
    f.confirm(); await f.h.settle()
    assert.deepEqual(plain(f.calls.at(-1)[8]), { '仿宋_GB2312': '华文仿宋' })
    assert.equal(f.calls.at(-1)[3].font_family, '宋体')
    assert.equal(f.body().props.value, '宋体')
    assert.equal(f.saves.length, 0)
    assert.equal(f.callbacks.length, callbacks)
    assert.equal(f.anchors.length, 1)
    assert.equal(f.anchors[0].download, '论文-预期稿.pdf')
    assert.equal(f.timers[0].delay, 60000)
    assert.equal(confirmation(f.h.tree), undefined)
    f.export(); await f.h.settle()
    assert.deepEqual(plain(f.calls.at(-1)[8]), {})
    f.h.dispose()
  }

  // Old confirmation callbacks are deliberately retained to verify the ref guard
  // as well as removal of the visible confirmation after any snapshot change.
  const invalidators = [
    ['draft font', f => { f.body().props.onChange('仿宋_GB2312'); f.h.render() }],
    ['draft heading', f => { find(f.h.tree, node => node.type === f.Picker && node.props.ariaLabel === '2级标题字体').props.onChange('楷体_GB2312'); f.h.render() }],
    ['content', f => f.h.update({ content: '# 新当前正文' })],
    ['expected content', f => f.h.update({ suggestedContent: '# 新预期正文' })],
    ['original content', f => f.h.update({ originalContent: '# 新原稿正文' })],
    ['document identity', f => f.h.update({ documentId: 'document-b' })],
    ['document epoch', f => f.h.update({ documentEpoch: 2 })],
    ['export scope', f => f.select('导出范围', 'current')],
    ['export format', f => f.select('导出格式', 'md')],
    ['closed modal', f => f.h.update({ open: false })],
  ]
  for (const [name, invalidate] of invalidators) {
    const f = studioFixture(); await f.h.settle(); const oldConfirm = await pendingExport(f), count = f.calls.length
    invalidate(f); await f.h.settle()
    assert.equal(confirmation(f.h.tree), undefined, name + ' must invalidate visible font consent.')
    oldConfirm.props.onClick(); f.h.render(); await f.h.settle()
    assert.equal(f.calls.length, count, name + ' must reject a stale confirmation event.')
    f.h.dispose()
  }

  {
    const f = studioFixture(); await f.h.settle()
    f.body().props.onChange('楷体_GB2312'); f.h.render()
    const oldConfirm = await pendingExport(f), count = f.calls.length
    button(f.h.tree, '应用全部排版').props.onClick(); f.h.render(); await f.h.settle()
    assert.equal(f.saves.length, 1)
    assert.equal(confirmation(f.h.tree), undefined, 'A saved current-style change must invalidate prior PDF consent.')
    oldConfirm.props.onClick(); f.h.render(); await f.h.settle()
    assert.equal(f.calls.length, count)
    f.h.dispose()
  }

  {
    const f = studioFixture(); await f.h.settle(); f.select('导出格式', 'docx')
    assert.match(text(f.h.tree), /结构保护导出保留原 DOCX 字体和版式/)
    f.setExporter(async () => { throw missing(issue('仿宋_GB2312')) })
    f.preview(); await f.h.settle()
    const oldConfirm = button(f.h.tree, '确认替代并继续'), count = f.calls.length
    f.select('DOCX 导出方式', 'editable_copy'); await f.h.settle()
    assert.equal(confirmation(f.h.tree), undefined, 'A DOCX-mode change must invalidate pending PDF-preview consent.')
    oldConfirm.props.onClick(); f.h.render(); await f.h.settle()
    assert.equal(f.calls.length, count)
    f.h.dispose()
  }

  {
    const f = studioFixture(); await f.h.settle()
    f.setExporter(async (...args) => { if (args[1].endsWith('-原稿')) throw missing(issue('仿宋_GB2312')); throw new Error('预期稿服务异常') })
    f.preview(); await f.h.settle()
    assert.equal(confirmation(f.h.tree), undefined, 'A non-font preview failure must not be presented as consent-solvable.')
    assert.match(text(find(f.h.tree, node => node.props?.role === 'alert')), /预期稿服务异常/)
    assert.equal(f.created.length, 0)
    f.h.dispose()
  }

  {
    const f = studioFixture(); await f.h.settle()
    const pending = deferred(); f.setExporter(() => pending.promise)
    f.export(); f.h.update({ content: '# 导出期间正文更新' })
    pending.reject(missing(issue('仿宋_GB2312'))); await f.h.settle()
    assert.equal(confirmation(f.h.tree), undefined, 'A stale in-flight font error must not open a confirmation for newer content.')
    assert.equal(f.anchors.length, 0)
    f.h.dispose()
  }

  {
    const f = studioFixture(); await f.h.settle()
    const pending = deferred(); f.setExporter(() => pending.promise)
    f.export(); f.h.update({ content: '# 成功响应前正文更新' })
    pending.resolve(result('旧版')); await f.h.settle()
    assert.equal(f.created.length, 0, 'A stale successful export must not create a download URL.')
    assert.equal(f.anchors.length, 0)
    assert.match(text(f.h.tree), /文档已变化，请重新导出当前版本/)
    f.h.dispose()
  }

  {
    const f = studioFixture(); await f.h.settle()
    const picker = f.body()
    f.pickerHooks.mount(f.Picker, picker.props); await f.pickerHooks.settle()
    labelled(f.pickerHooks.tree, '正文字体').props.onFocus(); f.pickerHooks.render()
    const modal = find(f.h.tree, node => node.props?.role === 'dialog')
    function escape() {
      const event = { key: 'Escape', stopped: false, prevented: false, preventDefault() { this.prevented = true }, stopPropagation() { this.stopped = true } }
      labelled(f.pickerHooks.tree, '正文字体').props.onKeyDown(event)
      f.pickerHooks.render()
      if (!event.stopped) modal.props.onKeyDown(event)
      return event
    }
    assert.equal(escape().stopped, true)
    assert.equal(f.closed, 0, 'First Escape must close the actual FontPicker dropdown without closing DocumentStudio.')
    assert.equal(labelled(f.pickerHooks.tree, '正文字体').props['aria-expanded'], false)
    assert.equal(escape().stopped, false)
    assert.equal(f.closed, 1, 'A second Escape may close the DocumentStudio modal.')
    f.pickerHooks.dispose(); f.h.dispose()
  }

  console.log('PASS: actual DocumentStudio FontPicker integration; allSettled dual-preview issue merge/deduplication and mixed errors; preview/download confirmation; export-only substitutions; cancel; invalid replacement blocked; twelve context/style/content invalidations; stale in-flight success/error; first Escape dropdown/second Escape modal')
}

main().catch(error => { console.error(error); process.exitCode = 1 })
