const assert = require('node:assert/strict');
const { test } = require('node:test');
const { parseMarkdown, serializeMarkdown, sanitizeEditorDocument, migrateLegacyDocument, MARKDOWN_SCHEMA_VERSION } = require('../lib/markdown.ts');
// parseMarkdown calls Node.check(), so a successful parse validates the schema.
function items(node, result = []) {
  if (node.type === 'listItem' || node.type === 'taskItem') result.push(node);
  for (const child of node.content || []) items(child, result);
  return result;
}
for (const [name, input, count] of [
  ['empty ordered item', '1. ', 1],
  ['empty final ordered item', '1. 第一项\n2. ', 2],
  ['empty middle ordered item', '1. 第一项\n2. \n3. 第三项', 3],
  ['empty unordered item', '- 第一项\n- \n- 第三项', 3],
  ['empty task item', '- [ ] \n- [x] 已完成', 2],
  ['nested ordered item', '1. 父项\n   1. ', 2],
]) {
  test(name, () => {
    const parsed = parseMarkdown(input);
    const list = items(parsed);
    assert.equal(list.length, count);
    assert.ok(list.every(item => item.content[0].type === 'paragraph'));
    const again = parseMarkdown(serializeMarkdown(parsed));
    assert.equal(items(again).length, count);
  });
}
for (const type of ['listItem', 'taskItem']) {
  for (const content of [undefined, [], [{ type: 'bulletList', content: [{ type: 'listItem', content: [{ type: 'paragraph', content: [{ type: 'text', text: '子项' }] }] }] }]]) {
    test('repair persisted ' + type + ' ' + JSON.stringify(content), () => {
      const item = { type, ...(content === undefined ? {} : { content }), ...(type === 'taskItem' ? { attrs: { checked: true } } : {}) };
      const original = { type: 'doc', markdownSchema: MARKDOWN_SCHEMA_VERSION, content: [{ type: type === 'listItem' ? 'orderedList' : 'taskList', content: [item] }] };
      const before = JSON.stringify(original);
      const repaired = migrateLegacyDocument('', original);
      assert.equal(repaired.content[0].content[0].content[0].type, 'paragraph');
      assert.equal(JSON.stringify(original), before);
      assert.deepEqual(sanitizeEditorDocument(repaired), repaired);
      assert.doesNotThrow(() => parseMarkdown(serializeMarkdown(repaired)));
      if (content?.length) assert.equal(repaired.content[0].content[0].content[1].content[0].content[0].content[0].text, '子项');
      if (type === 'taskItem') assert.equal(repaired.content[0].content[0].attrs.checked, true);
    });
  }
}
test('preserve ordinary text, formatting, tables, and formulas', () => {
  const source = '# 导入验收\n\n1. **第一项**\n2. 第二项\n\n38 份有效样本。$x^2$\n\n| 指标 | 数值 |\n| --- | --- |\n| 样本 | 38 |';
  const first = parseMarkdown(source);
  const second = parseMarkdown(serializeMarkdown(first));
  assert.deepEqual(second, first);
});
