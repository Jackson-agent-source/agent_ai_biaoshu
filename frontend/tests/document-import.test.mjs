import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { build } from 'esbuild'
import { JSDOM } from 'jsdom'

const { window } = new JSDOM()
Object.assign(globalThis, { window, document: window.document, DOMParser: window.DOMParser })
const bundle = await build({ stdin: {
  contents: "export * from './src/utils/documentStructure'; export * from './src/utils/layoutEditing'; export * from './src/utils/importWord'; export * from './src/utils/markdownLayout'; export * from './src/utils/autoLayout'",
  resolveDir: process.cwd(), loader: 'ts',
}, bundle: true, platform: 'browser', format: 'esm', write: false })
const { recognizePlainText, importWord, renderMarkdownLayout, arrangeQuotes, formatSelection, layoutDocument, prepareArticleLayout, arrangePointLists, layoutBlocks, applyLayoutPlan } = await import(`data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].text).toString('base64')}`)

test('plain prose keeps paragraphs, Chinese headings, lists and literal symbols', () => {
  const result = recognizePlainText('AI 企业研究\n\n一、落地成本\n正文包含 *字面符号*。\n\n• 成本\n• 收益\n\n1、验证\n2、复盘')
  const html = renderMarkdownLayout(result, 'blue', 16, 1.9)
  const doc = new DOMParser().parseFromString(html, 'text/html')
  assert.equal(doc.querySelector('h1')?.textContent, 'AI 企业研究')
  assert.equal(doc.querySelector('h2')?.textContent, '一、落地成本')
  assert.equal(doc.querySelectorAll('li').length, 4)
  assert.ok(doc.body.textContent.includes('*字面符号*'))
  assert.equal(doc.querySelector('em'), null)
})

test('unnumbered standalone section headings are recognized without promoting sentences', () => {
  const result = recognizePlainText('文章标题\n\n这是普通正文。\n\n让工具服务于真实任务\n\n继续研究。')
  assert.ok(result.includes('## 让工具服务于真实任务'))
  assert.ok(!result.includes('## 这是普通正文'))
})

test('DOCX becomes styled headings and a table; all themes render its content', async () => {
  const bytes = await readFile(new URL('./fixtures/structure.docx', import.meta.url))
  const result = await importWord({ arrayBuffer: async () => bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) })
  for (const theme of ['tech', 'blue', 'green', 'editorial', 'technical']) {
    const doc = new DOMParser().parseFromString(renderMarkdownLayout(result.markdown, theme, 16, 1.9), 'text/html')
    assert.equal(doc.querySelector('h1')?.textContent, 'AI 企业研究示例')
    assert.equal(doc.querySelector('h2')?.textContent, '研究维度')
    assert.equal(doc.querySelectorAll('table tbody tr').length, 1)
    assert.ok(doc.body.textContent.includes('实测数据'))
  }
})

test('invalid Word rejects and untrusted text cannot create executable elements', async () => {
  await assert.rejects(importWord({ arrayBuffer: async () => new ArrayBuffer(5) }))
  const html = renderMarkdownLayout(recognizePlainText('<script>alert(1)</script>\n<img src=x onerror=alert(1)>'), 'blue', 16, 1.9)
  const doc = new DOMParser().parseFromString(html, 'text/html')
  assert.equal(doc.querySelector('script,img'), null)
})


test('magazine export keeps title, lead, quotation and spacing without losing text', () => {
  const text = '开场段落。\n\n## 保持独立判断\n\n> “先核查证据。”\n\n正文与**重点**。'
  const html = renderMarkdownLayout(text, 'editorial', 16, 1.9, { lead: true, paragraphGap: 28, displayTitle: '研究与思考' })
  const doc = new DOMParser().parseFromString(layoutDocument('文档', html), 'text/html')
  assert.equal(doc.querySelector('h1')?.textContent, '研究与思考')
  assert.equal(doc.querySelector('h2')?.style.borderBottomWidth, '0px')
  assert.equal(doc.querySelector('p')?.style.paddingBottom, '26px')
  assert.equal(doc.querySelector('blockquote')?.style.fontSize, '1.18em')
  assert.ok(doc.body.textContent.includes('正文与重点。'))
  const existing = new DOMParser().parseFromString(renderMarkdownLayout('# 原标题', 'editorial', 16, 1.9, { displayTitle: '不应重复' }), 'text/html')
  assert.equal(existing.querySelectorAll('h1').length, 1)
  assert.equal(existing.querySelector('h1').textContent, '原标题')
})

test('quotation arrangement is idempotent and preserves code and selection boundaries', () => {
  const source = '“独立引文。”\n\n```text\n“代码里的文字”\n```'
  const result = arrangeQuotes(source)
  assert.ok(result.startsWith('> “独立引文。”'))
  assert.ok(result.includes('```text\n“代码里的文字”\n```'))
  assert.equal(arrangeQuotes(result), result)
  assert.equal(formatSelection('前文\n小标题\n后文', 3, 6, 'heading'), '前文\n## 小标题\n后文')
  assert.equal(formatSelection('前文\n## 小标题\n后文', 3, 9, 'paragraph'), '前文\n小标题\n后文')
  assert.equal(formatSelection('这是重点内容', 2, 4, 'bold'), '这是**重点**内容')
})


test('technology theme styles every block and preserves the complete article on export', () => {
  const source = '# 科技观察\n\n这是导语。\n\n## 工具与实践\n\n正文包含**重要判断**和[参考资料](https://example.com)。\n\n> 引文保留原意。\n\n- 第一项\n- 第二项\n\n## 研究数据\n\n| 指标 | 数值 |\n| --- | --- |\n| 成本 | 10 |\n\n```js\nconsole.log("最后的代码");\n```\n\n全文最后一段不能丢失。'
  const plain = new DOMParser().parseFromString(renderMarkdownLayout(source, 'blue', 16, 1.9), 'text/html')
  const html = renderMarkdownLayout(source, 'tech', 16, 1.9, { lead: true })
  const doc = new DOMParser().parseFromString(layoutDocument('科技观察', html), 'text/html')
  const paper = doc.body.firstElementChild
  assert.equal(paper.textContent.replace(/\s/g, ''), plain.body.textContent.replace(/\s/g, ''))
  assert.equal(paper.querySelector('h1').style.backgroundColor, '')
  assert.equal(paper.querySelector('h1').style.textAlign, 'center')
  assert.equal(paper.querySelector('h1').style.color, 'rgb(39, 44, 65)')
  assert.equal(paper.querySelector('h2').style.borderLeftWidth, '')
  assert.equal(paper.querySelector('blockquote').style.backgroundColor, '')
  assert.equal(paper.querySelector('pre code').style.color, 'inherit')
  assert.ok(paper.querySelector('th').style.backgroundColor)
  assert.ok(paper.querySelector('strong').style.backgroundColor)
  assert.ok(paper.textContent.endsWith('全文最后一段不能丢失。\n'))
  assert.ok([...paper.querySelectorAll('h1,h2,p,blockquote,ul,li,table,th,td,pre,code,a,strong')].every(node => node.hasAttribute('style')))
  const outlined = new DOMParser().parseFromString(renderMarkdownLayout(source, 'tech', 16, 1.9, { outline: true }), 'text/html')
  assert.ok(outlined.body.textContent.includes('本文脉络'))
  assert.equal(outlined.querySelectorAll('h2').length, 2)
})


test('full-article formatting preserves Markdown without headings and recognizes plain text', () => {
  const markdown = '- 工具 A\n- 工具 B\n\n这是**重点**和[链接](https://example.com)。'
  assert.equal(prepareArticleLayout(markdown), markdown)
  const text = '研究笔记\n\n一、实践方法\n\n保留全部正文。\n\n“保留引用。”'
  const result = prepareArticleLayout(text)
  assert.ok(result.includes('## 一、实践方法'))
  assert.ok(result.includes('> “保留引用。”'))
  assert.equal(prepareArticleLayout(result), result)
})


test('featured quotes have a different hierarchy without adding or losing words', () => {
  const source = '一段值得读者记住的文字。'
  const formatted = formatSelection(source, 0, source.length, 'pullquote')
  const doc = new DOMParser().parseFromString(renderMarkdownLayout(formatted, 'tech', 16, 1.9), 'text/html')
  assert.equal(doc.querySelector('blockquote').textContent.trim(), source)
  assert.equal(doc.querySelector('blockquote').style.textAlign, 'center')
  assert.equal(doc.querySelector('blockquote').style.borderLeftWidth, '')
})


test('clear question groups become lists without changing sentences or nested content', () => {
  const source = '# 文章\n\n这些是需要核查的问题：\n\n工具能做什么？\n\n成本是多少？\n\n继续叙述，不应成为列表。\n\n```text\n这些问题：\n\n要改变吗？\n\n不应该吧？\n```\n\n> 这些问题：\n> 能改变吗？\n> 不应该吧？'
  const result = arrangePointLists(source)
  assert.equal(result.groups, 1)
  assert.ok(result.text.includes('- 工具能做什么？\n\n- 成本是多少？'))
  assert.ok(!result.text.includes('- 继续叙述'))
  assert.ok(result.text.includes('```text\n这些问题：\n\n要改变吗？'))
  assert.equal(arrangePointLists(result.text).text, result.text)
  assert.equal(arrangePointLists('问题：\n\n## 中间标题\n\n第一问？\n\n第二问？').groups, 0)
})

test('manual lists preserve point order and can be restored to paragraphs', () => {
  const source = '检查能力\n\n核查成本\n\n小规模验证'
  const numbered = formatSelection(source, 0, source.length, 'numbered')
  assert.equal(numbered, '1. 检查能力\n\n2. 核查成本\n\n3. 小规模验证')
  assert.equal(formatSelection(numbered, 0, numbered.length, 'numbered'), numbered)
  const bullets = formatSelection(numbered, 0, numbered.length, 'bullets')
  assert.equal(bullets, '- 检查能力\n\n- 核查成本\n\n- 小规模验证')
  assert.equal(formatSelection(bullets, 0, bullets.length, 'paragraph'), source)
})

test('section numbers and outline align, remain optional and never duplicate existing numbers', () => {
  const source = '# 标题\n\n导语。\n\n开场内容。\n\n## 01 第一节\n\n正文。\n\n## 成本与回报\n\n- 一个要点\n- 另一个要点'
  const doc = new DOMParser().parseFromString(renderMarkdownLayout(source, 'tech', 16, 1.9, { lead: true, outline: true, numberedSections: true }), 'text/html')
  assert.equal(doc.querySelectorAll('h2')[0].textContent, '01 第一节')
  assert.equal(doc.querySelectorAll('h2')[1].textContent, '02 成本与回报')
  assert.ok(doc.body.firstElementChild.children[2].textContent.includes('本文脉络'))
  assert.ok(doc.body.firstElementChild.children[2].textContent.includes('02 成本与回报'))
  const plain = new DOMParser().parseFromString(renderMarkdownLayout(source, 'tech', 16, 1.9), 'text/html')
  assert.equal(plain.querySelectorAll('h2')[1].textContent, '成本与回报')
  assert.equal(plain.querySelectorAll('li').length, 2)
})

test('AI plan adds structure and all emphasis without rewriting paragraphs', () => {
  const source = '# 企业研究\n\n从真实任务开始，核对以下事项：\n\n核对预算。\n\n观察客户反馈。\n\n术语指最小可行产品。'
  const blocks = layoutBlocks(source)
  const [title, intro, a, b, term] = blocks
  const result = applyLayoutPlan(source, {
    sections: [{ before: a.id, title: '验证路径' }],
    lists: [{ ids: [a.id, b.id], ordered: true }],
    emphasis: [{ id: intro.id, text: '真实任务', style: 'highlight' }, { id: a.id, text: '预算', style: 'bold' }, { id: term.id, text: '最小可行产品', style: 'italic' }],
  })
  const doc = new DOMParser().parseFromString(renderMarkdownLayout(result.text, 'tech', 16, 1.9), 'text/html')
  assert.equal(doc.querySelector('h2').textContent, '验证路径')
  assert.equal(doc.querySelectorAll('ol li').length, 2)
  assert.equal(doc.querySelector('mark').textContent, '真实任务')
  assert.equal(doc.querySelector('strong').textContent, '预算')
  assert.equal(doc.querySelector('em').textContent, '最小可行产品')
  doc.querySelector('h2').remove()
  assert.equal(doc.body.textContent.replace(/\s/g, ''), blocks.map(block => block.text).join('').replace(/^# /, '').replace(/\s/g, ''))
})

test('AI cannot reorder, lose text, cross code or overlap emphasis', () => {
  const source = '开头重复重复。\n\n甲项。\n\n```js\nalert(1)\n```\n\n乙项。\n\n已有 **加粗**。'
  const [intro, a, b, styled] = layoutBlocks(source)
  const result = applyLayoutPlan(source, {
    lists: [{ ids: [a.id, b.id], ordered: false }, { ids: [b.id, a.id], ordered: true }],
    splits: [{ id: a.id, items: ['改写', '正文'] }],
    emphasis: [{ id: intro.id, text: '重复', style: 'bold' }, { id: styled.id, text: '加粗', style: 'italic' }, { id: a.id, text: '甲项', style: 'strike' }],
  })
  assert.equal(result.text, source)
  assert.equal(result.changes, 0)
  const valid = applyLayoutPlan('第一，核对成本。第二，验证需求。', { splits: [{ id: 0, items: ['第一，核对成本。', '第二，验证需求。'] }], emphasis: [{ id: 0, text: '核对成本', style: 'bold' }, { id: 0, text: '成本', style: 'highlight' }] })
  assert.equal(valid.text, '- 第一，**核对成本**。\n- 第二，验证需求。')
})

test('bold, highlight, italic and strike render, export and toggle', () => {
  for (const [format, tag, marker] of [['bold','strong','**'], ['highlight','mark','=='], ['italic','em','*'], ['strike','s','~~']]) {
    const text = formatSelection('重点', 0, 2, format)
    assert.equal(text, marker + '重点' + marker)
    assert.equal(formatSelection(text, 0, text.length, format), '重点')
    assert.equal(prepareArticleLayout(text), text)
    for (const theme of ['tech', 'blue', 'green', 'editorial', 'technical']) {
      const doc = new DOMParser().parseFromString(layoutDocument('测试', renderMarkdownLayout(text, theme, 16, 1.9)), 'text/html')
      assert.equal(doc.querySelector(tag).textContent, '重点')
      assert.ok(doc.querySelector(tag).getAttribute('style'))
    }
  }
})

test('Word import preserves bold, highlight, italic and strikethrough', async () => {
  const bytes = await readFile(new URL('./fixtures/auto-layout.docx', import.meta.url))
  const result = await importWord({ arrayBuffer: async () => bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) })
  const doc = new DOMParser().parseFromString(renderMarkdownLayout(result.markdown, 'tech', 16, 1.9), 'text/html')
  for (const [tag, text] of [['strong','保留加粗'], ['mark','保留高亮'], ['em','保留斜体'], ['s','保留删除线']]) assert.equal(doc.querySelector(tag)?.textContent, text)
})

test('unstyled Word title is recognized even with inline formatting later', () => {
  const source = 'AI 工具出海研究\n\n先验证 **需求**，再扩大投入。'
  assert.equal(prepareArticleLayout(source), '# ' + source)
})

test('narrative explanations are not turned into arbitrary bullet points', () => {
  const source = '这里说的迎合，不只是夸你聪明。\n\n它还可能接受问题里的前提。\n\n当然，赞同也可能有依据。'
  const blocks = layoutBlocks(source)
  const result = applyLayoutPlan(source, { lists: [{ ids: blocks.slice(1).map(b => b.id), ordered: false }] })
  assert.equal(result.text, source)
  assert.equal(prepareArticleLayout('_原有斜体_'), '_原有斜体_')
})
