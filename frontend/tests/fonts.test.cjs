const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const React = require('react')
const { transformSync } = require('next/dist/build/swc')

const requests = []
let response
function compile(filename, dependencies = {}, extra = {}) {
  const source = fs.readFileSync(path.join(__dirname, '..', filename), 'utf8')
  const code = transformSync(source, { filename, jsc: { parser: { syntax: 'typescript', tsx: filename.endsWith('.tsx') }, target: 'es2020', transform: { react: { runtime: 'automatic' } } }, module: { type: 'commonjs' } }).code
  const module = { exports: {} }
  vm.runInNewContext(code, {
    module, exports: module.exports, Blob, AbortSignal,
    fetch: async (url, init) => {
      requests.push({ url, ...init })
      const value = response
      if (value.pending) await value.pending
      if (value.error) throw value.error
      return { ok: value.ok, status: value.status ?? (value.ok ? 200 : 409), json: async () => value.body, blob: async () => value.blob ?? new Blob(['%PDF-fixture']), headers: { get: name => value.headers?.[name] ?? null } }
    },
    require: name => name.endsWith('.css') ? {} : dependencies[name] ?? require(name),
    ...extra,
  })
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
function find(tree, predicate) { const value = all(tree, predicate)[0]; assert.ok(value, 'Expected FontPicker element'); return value }
const flush = () => new Promise(resolve => setImmediate(resolve))

async function main() {
  const fonts = compile('lib/fonts.ts')
  const entries = Array.from(fonts.COMMON_DOCUMENT_FONTS, (name, index) => ({ ...fonts.unknownFont(name), id: 'font-' + index, installed: index < 7, status: index < 7 ? 'installed' : 'substituted', substitute: index === 7 ? '仿宋' : index === 8 ? '楷体' : null, pdf_available: index < 7, pdf_reason: index < 7 ? null : '缺少精确字体文件', aliases: index === 7 ? ['FangSong_GB2312'] : index === 8 ? ['KaiTi_GB2312'] : [], preview_available: index < 7, has_cjk: index < 5, styles: ['Regular'] }))
  entries.push({ ...fonts.unknownFont('华文仿宋'), id: 'installed-stfangsong', installed: true, status: 'installed', aliases: ['STFangsong'], pdf_available: true, pdf_reason: null, preview_available: true, has_cjk: true })
  entries.push({ ...fonts.unknownFont('Microsoft JhengHei UI Light'), id: 'installed-jhenghei-ui-light', installed: true, status: 'installed', aliases: ['MicrosoftJhengHeiUILight'], pdf_available: true, pdf_reason: null, preview_available: true, has_cjk: true })
  const catalogue = { fonts: entries, total: entries.length, warnings: [], common_names: Array.from(fonts.COMMON_DOCUMENT_FONTS) }
  response = { ok: true, body: catalogue }
  const base = 'http://127.0.0.1:font-test'
  await Promise.all([fonts.getFontCatalogue(base), fonts.getFontCatalogue(base)])
  assert.equal(requests.length, 1, 'All pickers must share a single font catalogue request.')
  assert.equal(requests[0].url, base + '/document-fonts?limit=1000')
  assert.equal(fonts.COMMON_DOCUMENT_FONTS.length, 9)
  for (const name of ['仿宋_GB2312', '楷体_GB2312', '华文仿宋', 'New School Font (Regular)']) assert.equal(fonts.isValidFontName(name), true)
  for (const name of ['', ' A', 'A ', '../font.ttf', 'A; color:red', 'A"', 'A\x00', 'A\x7f']) assert.equal(fonts.isValidFontName(name), false)
  assert.equal(fonts.fontCssFamily('仿宋_GB2312'), '"仿宋_GB2312", serif')
  assert.match(fonts.fontAvailability(entries[7]), /缺少精确字体/)
  assert.match(fonts.fontAvailability(fonts.unknownFont('学校字体')), /待检测/)

  // Execute the actual compiled component and handlers with deterministic hook state.
  let values, references, effects, stateCursor, refCursor, effectCursor, pending, currentValue, selected, disabled
  const scrolls = []
  const react = { ...React,
    useState(initial) { const index = stateCursor++; if (!(index in values)) values[index] = typeof initial === 'function' ? initial() : initial; return [values[index], update => { values[index] = typeof update === 'function' ? update(values[index]) : update }] },
    useRef(initial) { const index = refCursor++; return references[index] ??= { current: initial } },
    useId() { return 'font-picker-list' },
    useMemo(callback) { return callback() },
    useEffect(callback, dependencies) { const index = effectCursor++; if (!effects[index] || dependencies.some((value, at) => value !== effects[index][at])) { effects[index] = dependencies; pending.push(callback) } },
  }
  const doc = { addEventListener() {}, removeEventListener() {}, getElementById: id => ({ scrollIntoView: () => scrolls.push(id) }) }
  const Picker = compile('components/FontPicker.tsx', { react, '../lib/fonts': fonts }, { document: doc }).default
  let pickerBase = base
  function reset() { values = []; references = []; effects = []; currentValue = '宋体'; selected = []; disabled = false }
  function render() { stateCursor = refCursor = effectCursor = 0; pending = []; const tree = Picker({ apiBase: pickerBase, value: currentValue, onChange: value => { selected.push(value); currentValue = value }, disabled, ariaLabel: '测试正文字体' }); pending.forEach(callback => callback()); return tree }
  const input = tree => find(tree, node => node.props?.role === 'combobox')
  const options = tree => all(tree, node => node.props?.role === 'option')
  reset()
  let tree = render(); await flush(); tree = render()
  assert.equal(input(tree).props.value, '宋体')
  assert.equal(input(tree).props['aria-expanded'], false)
  input(tree).props.onFocus(); tree = render()
  assert.equal(options(tree).length, 9)
  assert.match(text(tree), /常用 9 种/)
  assert.match(text(tree), /论文格式 Aa 123/)
  assert.match(text(tree), /PDF可导出/)
  assert.match(text(tree), /缺少精确字体/)
  input(tree).props.onChange({ target: { value: 'GB2312' } }); tree = render()
  assert.equal(options(tree).length, 2)
  assert.match(text(options(tree)[0]), /仿宋_GB2312/)
  assert.match(text(options(tree)[1]), /楷体_GB2312/)
  const keyboard = { prevented: 0, stopped: 0, preventDefault() { this.prevented++ }, stopPropagation() { this.stopped++ } }
  input(tree).props.onKeyDown({ key: 'ArrowDown', preventDefault: () => keyboard.prevented++, stopPropagation: () => keyboard.stopped++ }); tree = render()
  input(tree).props.onKeyDown({ key: 'Enter', preventDefault: () => keyboard.prevented++, stopPropagation: () => keyboard.stopped++ }); tree = render()
  assert.equal(currentValue, '楷体_GB2312', 'Selecting a GB2312 requirement must preserve its complete font name.')
  assert.equal(input(tree).props['aria-expanded'], false)
  assert.ok(scrolls.length > 0, 'Keyboard navigation must scroll active results into view.')
  input(tree).props.onFocus(); tree = render()
  input(tree).props.onChange({ target: { value: 'fangsong_gb' } }); tree = render()
  assert.equal(options(tree).length, 1)
  options(tree)[0].props.onClick(); tree = render()
  assert.equal(currentValue, '仿宋_GB2312')
  assert.equal(selected.includes('仿宋'), false)
  input(tree).props.onFocus(); tree = render()
  input(tree).props.onChange({ target: { value: 'STFANGSONG' } }); tree = render()
  assert.equal(options(tree).length, 1)
  assert.match(text(options(tree)[0]), /华文仿宋/)
  const escape = { key: 'Escape', preventDefault: () => keyboard.prevented++, stopPropagation: () => keyboard.stopped++ }
  input(tree).props.onKeyDown(escape); tree = render()
  assert.equal(input(tree).props['aria-expanded'], false)
  assert.equal(keyboard.stopped, 1, 'First Escape closes the dropdown without bubbling to the enclosing dialog.')
  input(tree).props.onKeyDown(escape)
  assert.equal(keyboard.stopped, 1, 'Second Escape is allowed to reach the enclosing dialog.')
  disabled = true; tree = render()
  assert.equal(input(tree).props.disabled, true)

  pickerBase = base + '/slow'
  let finishCatalogue
  response = { ok: true, body: catalogue, pending: new Promise(resolve => { finishCatalogue = resolve }) }
  reset(); tree = render()
  input(tree).props.onFocus(); tree = render()
  assert.equal(options(tree).length, 9, 'Initial common font requirements must remain selectable while the directory is loading.')
  assert.match(text(tree), /正在读取本机字体…/)
  assert.doesNotMatch(text(tree), /0 个已安装字体族/)
  input(tree).props.onChange({ target: { value: 'Microsoft' } }); tree = render()
  assert.equal(options(tree).length, 0)
  assert.match(text(tree), /正在读取字体目录，请稍候…/)
  assert.doesNotMatch(text(tree), /未找到已知字体/)
  finishCatalogue(); await flush(); tree = render()
  assert.equal(options(tree).length, 1, 'An installed Microsoft family must appear when its delayed catalogue completes.')
  assert.match(text(options(tree)[0]), /Microsoft JhengHei UI Light/)
  assert.doesNotMatch(text(tree), /正在读取本机字体|正在读取字体目录/)

  pickerBase = base + '/unavailable'
  response = { ok: false, status: 503, body: { detail: 'offline' } }
  reset(); tree = render(); await flush(); tree = render()
  input(tree).props.onFocus(); tree = render()
  assert.equal(options(tree).length, 9, 'A failed catalogue load must retain the nine common requirements.')
  assert.match(text(tree), /字体目录暂不可用/)
  assert.match(text(tree), /待检测/)
  response = { ok: true, body: catalogue }
  await fonts.getFontCatalogue(pickerBase)
  assert.equal(requests.filter(request => request.url.startsWith(pickerBase)).length, 2, 'A failed catalogue must be retryable, rather than cached forever.')

  const studio = compile('lib/documentStudio.ts', { './fonts': fonts })
  const style = studio.cloneDocumentStyle(studio.DEFAULT_DOCUMENT_STYLE)
  style.font_family = '仿宋_GB2312'
  assert.equal(studio.validateDocumentStyle(style).font_family, '仿宋_GB2312')
  assert.throws(() => studio.validateDocumentStyle({ ...style, font_family: 'bad; font' }))
  assert.equal(studio.documentStyleCss(style).fontFamily, '"仿宋_GB2312", serif')
  const before = JSON.stringify(style)
  const issues = [{ requested_font: '仿宋_GB2312', reason: '本机未安装精确字体', suggested_font: '仿宋' }]
  response = { ok: false, status: 409, body: { detail: { code: 'font_confirmation_required', message: '请确认字体替代', issues } } }
  await assert.rejects(() => studio.exportDocument(base, '论文.md', '正文', style, 'pdf', undefined, 'doc-1', 'preserve'), error => {
    assert.ok(error instanceof fonts.FontConfirmationRequiredError)
    assert.equal(error.issues[0].requested_font, '仿宋_GB2312')
    assert.equal(error.issues[0].suggested_font, '仿宋')
    return true
  })
  let payload = JSON.parse(requests.at(-1).body)
  assert.deepEqual(payload.font_substitutions, {})
  assert.equal(payload.style.font_family, '仿宋_GB2312')
  response = { ok: true, blob: new Blob(['%PDF-fixture']), headers: { 'X-Export-Limitations': encodeURIComponent(JSON.stringify(['本次已明确确认替代'])) } }
  const result = await studio.exportDocument(base, '论文.md', '正文', style, 'pdf', undefined, 'doc-1', 'preserve', { '仿宋_GB2312': '仿宋' })
  payload = JSON.parse(requests.at(-1).body)
  assert.equal(requests.at(-1).method, 'POST')
  assert.equal(requests.at(-1).url, base + '/document-export')
  assert.deepEqual(payload.font_substitutions, { '仿宋_GB2312': '仿宋' })
  assert.equal(payload.document_id, 'doc-1')
  assert.equal(payload.docx_mode, 'preserve')
  assert.equal(JSON.stringify(style), before, 'Confirming a single export must not rewrite the saved template/style requirement.')
  assert.equal(result.blob.size, 12)
  assert.equal(result.limitations[0], '本次已明确确认替代')
  console.log('PASS: shared font catalogue, nine defaults, multilingual alias search, exact GB2312 selection, keyboard scrolling/Escape propagation, explicit slow-directory loading and deferred search, failed-directory fallback/retry, safe font names, typed 409 confirmation and explicit export-only substitution payload')
}

main().catch(error => { console.error(error); process.exitCode = 1 })
