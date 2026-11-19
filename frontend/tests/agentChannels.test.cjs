const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const { transformSync } = require('next/dist/build/swc')
const React = require('react')
const { renderToStaticMarkup } = require('react-dom/server')

const requests = []
const events = []
const configured = { id: 'channel/a', name: '日常润色', base_url: 'https://alpha.example/v1', model: 'MODEL-A', configured: true, has_api_key: true, source: 'local', reason: '已配置', active: true, config_revision: 2, read_only: false }
const incomplete = { ...configured, id: 'channel-b', name: '课程报告', base_url: 'https://beta.example/v1', model: 'model-b', configured: false, has_api_key: false, active: false }
const environment = { ...configured, id: 'environment', name: '环境配置', source: 'environment', active: false, read_only: true }
const library = { revision: 7, active_channel_id: configured.id, channels: [configured, incomplete, environment], active: configured }
let response = { ok: true, body: library }
class UIEvent { constructor(type, init) { this.type = type; this.detail = init?.detail } }

function compile(relative, dependencies = {}) {
  const source = fs.readFileSync(path.join(__dirname, '..', relative), 'utf8')
  const code = transformSync(source, { filename: relative, jsc: { parser: { syntax: 'typescript', tsx: relative.endsWith('.tsx') }, target: 'es2020', transform: { react: { runtime: 'automatic' } } }, module: { type: 'commonjs' } }).code
  const module = { exports: {} }
  vm.runInNewContext(code, {
    module, exports: module.exports, AbortSignal,
    Event: UIEvent, CustomEvent: UIEvent, window: { dispatchEvent: event => events.push(event) },
    fetch: async (url, init) => { requests.push({ url, ...init }); return { ok: response.ok, json: async () => response.body } },
    require: name => name.endsWith('.css') ? {} : dependencies[name] || require(name),
  })
  return module.exports
}

async function main() {
  const helpers = compile('lib/agentSettings.ts')
  assert.deepEqual(Array.from(helpers.filterAgentChannels(library.channels, 'MODEL-a   alpha', 'all'), item => item.id), [configured.id, environment.id])
  assert.deepEqual(Array.from(helpers.filterAgentChannels(library.channels, '课程', 'all'), item => item.id), [incomplete.id])
  assert.deepEqual(Array.from(helpers.filterAgentChannels(library.channels, 'BETA.EXAMPLE', 'incomplete'), item => item.id), [incomplete.id])
  assert.equal(helpers.filterAgentChannels(library.channels, '', 'configured').length, 2)
  assert.equal(helpers.filterAgentChannels(library.channels, '', 'incomplete').length, 1)
  assert.equal(helpers.filterAgentChannels(library.channels, 'missing', 'all').length, 0)
  assert.equal(library.channels[0].id, configured.id, 'Filtering must not reorder/mutate the saved library.')
  const empty = helpers.channelDraft()
  assert.equal(empty.api_key, '')
  assert.equal(empty.name, '')
  const draft = helpers.channelDraft({ ...configured, api_key: 'must-never-prefill', credential: 'must-never-prefill' })
  assert.equal(draft.api_key, '', 'Public row data must never populate a password draft.')
  assert.equal(draft.clear_api_key, false)
  assert.equal(helpers.channelDraftChanged(draft, configured), false)
  assert.equal(helpers.channelDraftChanged({ ...draft, name: '重命名' }, configured), true)
  assert.equal(helpers.channelDraftChanged({ ...draft, api_key: 'offline-draft-value' }, configured), true)
  assert.equal(helpers.channelDraftChanged({ ...draft, clear_api_key: true }, configured), true)

  const base = 'http://127.0.0.1:8000'
  await helpers.getAgentChannels(base)
  assert.equal(requests.at(-1).url, base + '/agent-settings/channels')
  assert.equal(requests.at(-1).cache, 'no-store')
  assert.equal(events.length, 0)
  const input = { name: '独立渠道', base_url: 'https://new.example/v1', model: 'model-new', api_key: '', clear_api_key: false, expected_revision: 7 }
  await helpers.createAgentChannel(base, input)
  assert.equal(requests.at(-1).method, 'POST')
  assert.equal(requests.at(-1).url, base + '/agent-settings/channels')
  assert.deepEqual(JSON.parse(requests.at(-1).body), input)
  assert.equal(requests.length, 2, 'Creating a saved channel must not implicitly issue activate.')
  assert.deepEqual(events.map(event => event.type), ['agent-settings-changed', 'agent-channel-changed'])
  assert.equal(events.at(-1).detail.channelId, configured.id)
  assert.equal(events.at(-1).detail.revision, 7)
  assert.equal(JSON.stringify(events).includes('api_key'), false)
  await helpers.updateAgentChannel(base, configured.id, { ...input, name: '新名称' })
  assert.equal(requests.at(-1).url, base + '/agent-settings/channels/channel%2Fa')
  assert.equal(requests.at(-1).method, 'PUT')
  await helpers.activateAgentChannel(base, incomplete.id, 8)
  assert.equal(requests.at(-1).url, base + '/agent-settings/channels/channel-b/activate')
  assert.deepEqual(JSON.parse(requests.at(-1).body), { expected_revision: 8 })
  await helpers.deleteAgentChannel(base, configured.id, 9)
  assert.equal(requests.at(-1).method, 'DELETE')
  assert.equal(requests.at(-1).url, base + '/agent-settings/channels/channel%2Fa?expected_revision=9')
  const mutationEvents = events.length
  response = { ok: true, body: { ok: true, message: '连接成功' } }
  await helpers.testAgentChannel(base, configured.id, input)
  assert.equal(requests.at(-1).url, base + '/agent-settings/channels/channel%2Fa/test')
  await helpers.testAgentChannel(base, null, { ...input, api_key: '' })
  assert.equal(requests.at(-1).url, base + '/agent-settings/channels/test')
  assert.equal(events.length, mutationEvents, 'Connection testing must not publish a save/switch event.')
  response = { ok: false, body: { detail: '渠道库已变化，请刷新后重试。' } }
  await assert.rejects(() => helpers.activateAgentChannel(base, incomplete.id, 1), /渠道库已变化/)
  assert.equal(events.length, mutationEvents, 'Failed writes must not publish a successful change event.')

  const { AgentChannelList, default: Manager } = compile('components/AgentSettings.tsx', { '../lib/agentSettings': helpers })
  const markup = renderToStaticMarkup(React.createElement(AgentChannelList, { channels: library.channels, selectedId: configured.id, disabled: false, draftIds: [incomplete.id], onSelect() {} }))
  assert.ok(markup.includes('当前启用'))
  assert.ok(markup.includes('环境来源 · 只读'))
  assert.ok(markup.includes('有未保存修改'))
  assert.match(markup, /aria-pressed="true"/)
  assert.doesNotMatch(markup, /must-never-prefill|offline-draft-value|api_key/)
  assert.equal(renderToStaticMarkup(React.createElement(Manager, { apiBase: base, open: false, onClose() {} })), '')
  const { default: Switcher } = compile('components/AgentChannelSwitcher.tsx', { '../lib/agentSettings': helpers })
  const switcher = renderToStaticMarkup(React.createElement(Switcher, { apiBase: base, onManage() {} }))
  assert.ok(switcher.includes('当前 Agent 渠道'))
  assert.ok(switcher.includes('管理 Agent 渠道'))
  assert.ok(switcher.includes('正在读取'))
  console.log('PASS: channel search/filter, empty independent password drafts, rename detection, encoded CRUD/test/activate contracts, revision guards, separate save/enable events, safe channel list, closed manager and accessible switcher')
}

main().catch(error => { console.error(error); process.exitCode = 1 })
