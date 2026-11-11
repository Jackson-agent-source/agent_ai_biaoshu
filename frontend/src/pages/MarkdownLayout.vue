<script setup lang="ts">
import { computed, nextTick, onMounted, onBeforeUnmount, ref, watch } from 'vue'
import { api } from '@/api/http'
import { layoutBlocks, applyLayoutPlan, type LayoutPlan } from '@/utils/autoLayout'
import { prepareArticleLayout, arrangePointLists, formatSelection, type SelectionFormat } from '@/utils/layoutEditing'
import { recognizePlainText } from '@/utils/documentStructure'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { useAuthStore } from '@/stores/auth'
import { getArticle } from '@/api/articles'
import { layoutThemes, paperScenes, paperBackgrounds, layoutDocument, renderMarkdownLayout, type LayoutTheme, type PaperBackground } from '@/utils/markdownLayout'

const sample = '# 从想法到落地：一份 AI 研究笔记\n\n好的排版，让观点更容易被读懂。把你的 Markdown 粘贴到左侧，选择一种适合内容的风格。\n\n## 先说结论\n\n研究一个工具，既要看**能力边界**，也要看成本与实际工作流。\n\n> 先用一个真实任务验证，再决定是否扩大使用。\n\n## 值得追踪的维度\n\n- 工具能解决什么具体问题\n- 企业落地的投入与回报\n- 海外市场的需求和本地化\n\n| 维度 | 需要核查的证据 |\n| --- | --- |\n| 能力 | 可复现的测试 |\n| 成本 | 官方价格与使用量 |\n\n### 一个简单示例\n\n```python\nprint("从小规模验证开始")\n```\n\n---\n\n把信息整理为自己的判断，而不只是收集更多信息。'
const auth = useAuthStore()
const route = useRoute()
const title = ref('我的研究笔记')
const markdown = ref(sample)
const theme = ref<LayoutTheme>('tech')
const fontSize = ref(16)
const lineHeight = ref(1.9)
const width = ref('desktop')
const lead = ref(true)
const outline = ref(false)
const numberedSections = ref(false)
const paragraphGap = ref(22)
const paperBackground = ref<PaperBackground>('grid')
const decorativeCover = ref(true)
const contentWidth = ref(600)
const chapterPanels = ref(true)
const displayTitle = ref('')
const reading = ref(false)
const editor = ref<HTMLTextAreaElement>()
const autoArrange = ref(true)
const arranging = ref(false)
const originalLayout = ref<string | null>(null)
let layoutJob = 0
let layoutController: AbortController | undefined
function cancelLayout(): void { layoutJob++; layoutController?.abort(); arranging.value = false }
watch(markdown, () => {
  if (arranging.value) importNotice.value = '正文已修改，本次自动整理已取消；可随时重新整理。'
  cancelLayout()
}, { flush: 'sync' })
watch(autoArrange, enabled => { if (!enabled) cancelLayout() })
onBeforeUnmount(cancelLayout)

async function autoFormat(): Promise<void> {
  if (!markdown.value.trim()) return
  cancelLayout()
  originalLayout.value = markdown.value
  markdown.value = arrangePointLists(prepareArticleLayout(markdown.value)).text
  numberedSections.value = true; outline.value = true
  const source = markdown.value
  const blocks = layoutBlocks(source)
  if (!blocks.length || blocks.length > 200 || blocks.some(block => block.text.length > 5000)
    || blocks.reduce((sum, block) => sum + block.text.length, 0) > 20000) {
    importNotice.value = '已完成基础排版，全文保留。AI 整理支持 2 万字符、200 段、单段 5000 字以内的正文，当前文章未发送给模型。'
    return
  }
  const job = ++layoutJob
  layoutController = new AbortController()
  arranging.value = true
  importNotice.value = '正在自动梳理小标题、分点和重点；你可以继续编辑，修改正文会取消本次自动整理。'
  try {
    const plan = await api.post<LayoutPlan>('/agent/layout', {
      blocks: blocks.map(({ id, text, kind }) => ({ id, text, kind })),
    }, { timeout: 180000, signal: layoutController.signal })
    if (job !== layoutJob || markdown.value !== source) return
    const result = applyLayoutPlan(source, plan)
    if (result.text.length > 500000) throw new Error('排版结果超过长度限制，已保留原文。')
    markdown.value = result.text
    importNotice.value = result.changes
      ? `已自动完成 ${result.changes} 处层次与重点调整。新增小标题由 AI 提炼，正文未改写；可撤销自动整理。`
      : '已完成基础排版，未发现适合继续添加的层次或重点，保留现有结构。'
  } catch (error) {
    if (job !== layoutJob) return
    importNotice.value = `${error instanceof Error ? error.message : 'AI 整理暂未完成'} 已保留全文和基础排版，可点击“自动整理全文”重试。`
  } finally { if (job === layoutJob) arranging.value = false }
}
function undoAutoFormat(): void {
  cancelLayout()
  if (originalLayout.value !== null) markdown.value = originalLayout.value
  originalLayout.value = null
  importNotice.value = '已恢复自动整理前的正文。'
}
async function onPaste(event: ClipboardEvent): Promise<void> {
  const text = event.clipboardData?.getData('text/plain') || ''
  if (!autoArrange.value || (!text.includes('\n') && text.length < 120)) return
  const input = editor.value
  if (!input) return
  event.preventDefault()
  const pasted = markdown.value.slice(0, input.selectionStart) + text + markdown.value.slice(input.selectionEnd)
  if (pasted.length > 500000) { ElMessage.warning('内容超过 50 万字符，请分篇导入'); return }
  replaceDocument(pasted)
  await nextTick()
  await autoFormat()
}
function selectTheme(id: LayoutTheme): void { theme.value = id }
function refineLayout(style: LayoutTheme = 'tech'): void {
  if (!markdown.value.trim()) return
  replaceDocument(prepareArticleLayout(markdown.value))
  theme.value = style; lead.value = true
  importNotice.value = `已对全文应用${layoutThemes.find(item => item.id === style)?.name}版式，整理标题、段落与独立引文。正文可继续编辑，导出与复制使用同一份排版。`
}
function organizeStructure(): void {
  if (!markdown.value.trim()) { ElMessage.info('请先粘贴或导入正文'); return }
  const result = arrangePointLists(prepareArticleLayout(markdown.value))
  if (result.text.length > 500000) { ElMessage.warning('内容超过长度限制'); return }
  if (result.text !== markdown.value) replaceDocument(result.text)
  numberedSections.value = true; outline.value = true
  importNotice.value = result.groups
    ? `已整理 ${result.groups} 组问题清单，并开启文章脉络与章节编号。普通段落保持原样；选中多段文字还可手动分点。`
    : '已开启文章脉络与章节编号。没有发现明确的问题清单；选中需要分点的多段文字，点击“分点列表”或“编号步骤”。'
}
async function applyFormat(format: SelectionFormat): Promise<void> {
  const input = editor.value
  if (!input) return
  const start = input.selectionStart; const end = input.selectionEnd
  if (start === end) { ElMessage.info('请先在左侧选中要调整的文字'); return }
  const updated = formatSelection(markdown.value, start, end, format)
  if (updated.length > 500000) { ElMessage.warning('内容超过长度限制'); return }
  replaceDocument(updated)
  await nextTick(); input.focus(); input.setSelectionRange(start, Math.min(updated.length, end))
}
const importing = ref(false)
const copying = ref(false)
const previousDocument = ref<{ text: string; title: string; displayTitle: string } | null>(null)
const importNotice = ref('')
function replaceDocument(text: string, name = title.value): void {
  cancelLayout(); originalLayout.value = null
  previousDocument.value = { text: markdown.value, title: title.value, displayTitle: displayTitle.value }
  markdown.value = text; title.value = name
}
function recognize(): void {
  if (!markdown.value.trim()) { ElMessage.warning('请先粘贴普通文字'); return }
  if (/^(?:#{1,6}\s|```|~~~)/m.test(markdown.value)) { ElMessage.info('内容已有 Markdown 结构，可直接选择风格，无需重复识别'); return }
  replaceDocument(recognizePlainText(markdown.value))
  importNotice.value = '已按普通文字识别标题、段落和列表，请在右侧核对；识别不准确时可撤销或在左侧修改。'
}
function undoImport(): void {
  cancelLayout(); originalLayout.value = null
  if (!previousDocument.value) return
  markdown.value = previousDocument.value.text; title.value = previousDocument.value.title
  displayTitle.value = previousDocument.value.displayTitle
  previousDocument.value = null; importNotice.value = ''
}
const fileInput = ref<HTMLInputElement>()
const storageError = ref(false)
const ready = ref(false)
const html = computed(() => renderMarkdownLayout(markdown.value, theme.value, fontSize.value, lineHeight.value, { lead: lead.value, paragraphGap: paragraphGap.value, displayTitle: displayTitle.value, outline: outline.value, numberedSections: numberedSections.value, paperBackground: paperBackground.value, contentWidth: contentWidth.value, chapterPanels: chapterPanels.value, decorativeCover: decorativeCover.value }))
const wordCount = computed(() => markdown.value.replace(/\s/g, '').length)
const draftKey = computed(() => auth.user?.id ? `markdown-layout-draft:${auth.user.id}` : '')

watch(draftKey, key => {
  if (!key || ready.value) return
  try {
    const raw = localStorage.getItem(key)
    if (raw) {
      const draft = JSON.parse(raw)
      if (typeof draft.markdown === 'string') markdown.value = draft.markdown.slice(0, 500000)
      if (typeof draft.title === 'string') title.value = draft.title
      if (typeof draft.displayTitle === 'string') displayTitle.value = draft.displayTitle.slice(0, 120)
      if (typeof draft.lead === 'boolean') lead.value = draft.lead
      if (typeof draft.autoArrange === 'boolean') autoArrange.value = draft.autoArrange
      if (typeof draft.originalLayout === 'string') originalLayout.value = draft.originalLayout.slice(0, 500000)
      if (typeof draft.outline === 'boolean') outline.value = draft.outline
      if (typeof draft.numberedSections === 'boolean') numberedSections.value = draft.numberedSections
      if (typeof draft.paragraphGap === 'number') paragraphGap.value = Math.max(12, Math.min(36, draft.paragraphGap))
      if (paperBackgrounds.some(item => item.id === draft.paperBackground)) paperBackground.value = draft.paperBackground
      if ([520, 600, 680].includes(draft.contentWidth)) contentWidth.value = draft.contentWidth
      if (typeof draft.chapterPanels === 'boolean') chapterPanels.value = draft.chapterPanels
      if (typeof draft.decorativeCover === 'boolean') decorativeCover.value = draft.decorativeCover
      if (layoutThemes.some(item => item.id === draft.theme)) theme.value = draft.theme
      if (typeof draft.fontSize === 'number') fontSize.value = Math.max(12, Math.min(22, draft.fontSize))
      if (typeof draft.lineHeight === 'number') lineHeight.value = Math.max(1.4, Math.min(2.4, draft.lineHeight))
    }
  } catch { storageError.value = true }
  ready.value = true
}, { immediate: true })
watch([title, markdown, theme, fontSize, lineHeight, displayTitle, lead, paragraphGap, outline, numberedSections, autoArrange, originalLayout, paperBackground, contentWidth, chapterPanels, decorativeCover], () => {
  if (!ready.value || !draftKey.value) return
  try {
    localStorage.setItem(draftKey.value, JSON.stringify({ title: title.value, markdown: markdown.value, theme: theme.value, fontSize: fontSize.value, lineHeight: lineHeight.value, displayTitle: displayTitle.value, lead: lead.value, paragraphGap: paragraphGap.value, outline: outline.value, numberedSections: numberedSections.value, autoArrange: autoArrange.value, originalLayout: originalLayout.value, paperBackground: paperBackground.value, contentWidth: contentWidth.value, chapterPanels: chapterPanels.value, decorativeCover: decorativeCover.value }))
    storageError.value = false
  } catch { storageError.value = true }
})

async function importFile(event: Event): Promise<void> {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  importing.value = true
  try {
    if (!/\.(docx|md|markdown|txt)$/i.test(file.name)) throw new Error('支持 .docx、.md、.markdown 和 .txt；旧版 .doc 请先另存为 .docx')
    const isWord = /\.docx$/i.test(file.name)
    if (file.size > (isWord ? 10000000 : 1000000)) throw new Error(isWord ? 'Word 文档请控制在 10 MB 以内' : '文本文件请控制在 1 MB 以内')
    let text: string
    let notice = ''
    if (isWord) {
      const { importWord } = await import('@/utils/importWord')
      const result = await importWord(file)
      text = result.markdown; notice = result.warning
    } else {
      text = await file.text()
    }
    if (!text.trim()) throw new Error('文档中没有可识别的正文，请确认不是图片或扫描件')
    if (text.length > 500000) throw new Error('文档过长，请控制在 50 万字符以内')
    replaceDocument(text, file.name.replace(/\.[^.]+$/, ''))
    displayTitle.value = ''
    importNotice.value = notice
    if (autoArrange.value) {
      importing.value = false
      await autoFormat()
      if (notice) importNotice.value += ' ' + notice
    }
    ElMessage.success('已导入并排版，可继续选择风格')
  } catch (error) { ElMessage.error(error instanceof Error ? error.message : '导入失败，请检查文件是否损坏或加密') }
  finally { input.value = ''; importing.value = false }
}

function download(format: 'html' | 'md'): void {
  const content = format === 'html' ? layoutDocument(title.value, html.value) : markdown.value
  const url = URL.createObjectURL(new Blob([content], { type: format === 'html' ? 'text/html;charset=utf-8' : 'text/markdown;charset=utf-8' }))
  const link = document.createElement('a'); link.href = url
  link.download = `${title.value.replace(/[<>:"/\\|?*\u0000-\u001f]/g, '_').slice(0, 100) || '文档'}.${format}`
  link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000)
}

async function copyLayout(): Promise<void> {
  copying.value = true
  try {
    if (!navigator.clipboard?.write || typeof ClipboardItem === 'undefined') throw new Error('当前浏览器不支持富文本复制，请使用“导出 HTML”')
    const plain = new DOMParser().parseFromString(html.value, 'text/html').body.textContent || ''
    await navigator.clipboard.write([new ClipboardItem({ 'text/html': new Blob([html.value], { type: 'text/html' }), 'text/plain': new Blob([plain], { type: 'text/plain' }) })])
    ElMessage.success('已复制排版，可粘贴到公众号或支持富文本的编辑器')
  } catch (error) { ElMessage.error(error instanceof Error ? error.message : '复制失败，请导出 HTML') }
  finally { copying.value = false }
}

onMounted(async () => {
  if (!auth.user) {
    try { await auth.fetchMe() } catch { return }
  }
  if (typeof route.query.article !== 'string') return
  importing.value = true
  try {
    const article = await getArticle(route.query.article)
    replaceDocument(article.content_markdown, article.title)
    displayTitle.value = article.title
    if (autoArrange.value) { importing.value = false; await autoFormat() }
    ElMessage.success('已载入文章的已保存正文，排版不会修改原文章')
  } catch { ElMessage.error('文章载入失败，仍可粘贴或导入 Markdown') }
  finally { importing.value = false }
})
</script>

<template>
  <div :class="['layout-studio', { 'tech-studio': theme === 'tech' }]" v-loading="importing">
    <header class="studio-head"><div><h2>文章排版工作台</h2><p>导入文章，自动分点、提炼小标题、突出重点，让全文层次清晰。</p></div>
      <div class="actions"><el-button @click="fileInput?.click()">导入文档</el-button><el-button @click="download('md')">导出 MD</el-button><el-button @click="download('html')">导出 HTML</el-button><el-button type="primary" :loading="copying" @click="copyLayout">复制排版</el-button></div>
      <input ref="fileInput" hidden type="file" accept=".docx,.md,.markdown,.txt" @change="importFile" />
    </header>
    <div class="auto-layout-bar"><div><el-checkbox v-model="autoArrange">导入 / 粘贴后自动整理</el-checkbox><p>开启后会将正文发送给已配置的 AI 模型，可能产生 API 费用；自动整理小标题、分点、加粗、高亮与少量斜体。删除线保留原有标记，或手动添加。</p></div><el-button type="primary" :loading="arranging" @click="autoFormat">{{ arranging ? '正在梳理全文…' : '自动整理全文' }}</el-button><el-button :disabled="originalLayout === null" @click="undoAutoFormat">撤销自动整理</el-button></div>
    <div class="recognition-bar"><el-button type="primary" @click="refineLayout('tech')">全文清透排版</el-button><el-button type="primary" plain @click="organizeStructure">梳理层次与分点</el-button><el-button type="primary" plain @click="recognize">普通文字识别并排版</el-button><el-button :disabled="!previousDocument" @click="undoImport">撤销内容调整</el-button><span>Word 自动提取结构；已有 Markdown 无需识别。支持 DOCX / TXT / MD。</span></div>
    <el-alert v-if="importNotice" :title="importNotice" type="info" :closable="false" show-icon class="import-notice" />
    <div class="theme-picker" aria-label="排版风格">
      <button v-for="item in layoutThemes" :key="item.id" type="button" :class="['theme-option', { selected: theme === item.id }]" :aria-pressed="theme === item.id" @click="selectTheme(item.id)">
        <span :class="['theme-sample', item.id]" :style="{ color: item.color }"><b>文章标题</b><i></i><i></i><em>引文与思考</em></span><span><strong>{{ item.name }}</strong><small>{{ item.description }}</small></span>
      </button>
    </div>
    <div class="format-bar"><label>文档名称 <el-input v-model="title" maxlength="100" aria-label="文档名称" /></label><label>字号 <el-input-number v-model="fontSize" :min="12" :max="22" size="small" aria-label="字号" /></label><label>行距 <el-input-number v-model="lineHeight" :min="1.4" :max="2.4" :step="0.1" :precision="1" size="small" aria-label="行距" /></label>
      <el-radio-group v-model="width" size="small" aria-label="预览宽度"><el-radio-button value="desktop">宽屏预览</el-radio-button><el-radio-button value="mobile">手机预览</el-radio-button></el-radio-group>
    </div>
    <div class="scene-picker" aria-label="背景版式"><button v-for="scene in paperScenes" :key="scene.id" type="button" :aria-pressed="paperBackground === scene.id" :class="['scene-option', { active: paperBackground === scene.id }]" @click="paperBackground = scene.id; decorativeCover = true"><span class="scene-art" :style="scene.preview"><span class="scene-orbit"></span><b>文章的另一种打开方式</b><i></i><i></i></span><strong>{{ scene.name }}</strong><small>{{ scene.description }}</small></button></div>
    <div class="paper-controls"><label>文章背景 <select v-model="paperBackground" aria-label="文章背景"><option v-for="item in paperBackgrounds" :key="item.id" :value="item.id">{{ item.name }}</option></select></label><label>正文宽度 <select v-model.number="contentWidth" aria-label="正文宽度"><option :value="520">紧凑 · 520</option><option :value="600">舒适 · 600</option><option :value="680">宽版 · 680</option></select></label><el-checkbox v-model="chapterPanels">章节与引文底色</el-checkbox><el-checkbox v-model="decorativeCover">装饰封面</el-checkbox><span>封面使用正文大标题或版面标题；HTML 保留完整效果，部分编辑器可能简化纹理。</span></div>
    <div class="format-bar editorial-controls"><label>版面标题 <el-input v-model="displayTitle" maxlength="120" placeholder="可选；正文已有大标题时不重复添加" aria-label="版面标题" /></label><el-checkbox v-model="lead">首段作为导语</el-checkbox><el-checkbox v-model="outline">显示文章脉络</el-checkbox><el-checkbox v-model="numberedSections">章节编号</el-checkbox><label>段间距 <el-input-number v-model="paragraphGap" :min="12" :max="36" :step="2" size="small" aria-label="段间距" /></label><el-button @click="reading = !reading">{{ reading ? '返回编辑' : '放大预览' }}</el-button></div>
    <div :class="['workbench', { reading }]" @keydown.esc="reading = false"><section class="editor-panel"><div class="panel-label"><strong>Markdown 原文</strong><span>{{ wordCount.toLocaleString() }} 字符</span></div><div class="editing-tools" aria-label="文字排版工具"><span>选中文字后</span><button @mousedown.prevent @click="applyFormat('heading')">小标题</button><button @mousedown.prevent @click="applyFormat('bullets')">分点列表</button><button @mousedown.prevent @click="applyFormat('numbered')">编号步骤</button><button @mousedown.prevent @click="applyFormat('bold')"><b>加粗</b></button><button @mousedown.prevent @click="applyFormat('highlight')">高亮</button><button @mousedown.prevent @click="applyFormat('italic')"><i>斜体</i></button><button @mousedown.prevent @click="applyFormat('strike')"><s>删除线</s></button><button @mousedown.prevent @click="applyFormat('quote')">设为引文</button><button @mousedown.prevent @click="applyFormat('pullquote')">重点摘句</button><button @mousedown.prevent @click="applyFormat('paragraph')">恢复正文</button></div><textarea ref="editor" v-model="markdown" @paste="onPaste" aria-label="Markdown 原文" spellcheck="false" maxlength="500000" placeholder="粘贴整篇文章即可自动整理，也可直接编辑 Markdown…" /></section>
      <section class="preview-panel"><div class="panel-label"><strong>排版预览</strong><span>{{ layoutThemes.find(item => item.id === theme)?.name }}</span><div v-if="reading" class="reading-actions"><el-button size="small" @click="width = width === 'mobile' ? 'desktop' : 'mobile'">{{ width === 'mobile' ? '宽屏预览' : '手机预览' }}</el-button><el-button size="small" @click="reading = false">返回编辑</el-button></div></div><div class="preview-scroll"><div :class="['preview-paper', { mobile: width === 'mobile' }]" v-html="html" /></div></section></div>
    <p class="draft-note">{{ storageError ? '浏览器草稿保存失败，请及时导出文件。' : '草稿保存在当前浏览器；自动整理调用已配置的 AI，切换风格不调用模型。' }} HTML 和富文本保留视觉风格；MD 文件保留排版标记（高亮使用 ==文字==，需编辑器支持）。不同编辑器粘贴效果可能有差异。</p>
  </div>
</template>

<style scoped>
.scene-picker { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:14px; margin:0 0 16px; }
.scene-option { padding:10px; background:#fff; border:1px solid #dce2ed; border-radius:10px; text-align:left; cursor:pointer; color:#39475d; }
.scene-option.active { border-color:#5c6c9a; box-shadow:0 0 0 1px #5c6c9a; }
.scene-option:focus-visible { outline:3px solid #a8caec; outline-offset:3px; }
.scene-art { display:block; padding:16px; border-radius:6px; min-height:110px; box-sizing:border-box; }
.scene-orbit { display:block; width:68px; height:30px; border:1px solid currentColor; opacity:.4; border-radius:50%; margin-bottom:16px; }
.scene-art b { display:block; font-size:13px; line-height:1.6; } .scene-art i { display:block; height:2px; width:75%; background:currentColor; opacity:.18; margin-top:8px; }
.scene-option > strong { display:block; margin:12px 2px 6px; font-size:14px; } .scene-option > small { color:#7b8191; font-size:12px; }
@media(max-width:760px) { .scene-picker { grid-template-columns:1fr; } .scene-option { display:grid; grid-template-columns:115px 1fr; column-gap:14px; } .scene-art { grid-row:span 2; min-height:100px; padding:12px; } .scene-art b { font-size:11px; } .scene-orbit { height:20px; margin-bottom:8px; } .scene-option > strong { align-self:end; } }

.paper-controls { display:flex; align-items:center; flex-wrap:wrap; gap:16px; padding:14px 18px; margin-bottom:18px; background:#f3f4fa; border-radius:8px; }
.paper-controls label { display:flex; align-items:center; gap:10px; font-size:13px; }
.paper-controls select { padding:7px 12px; border:1px solid #d8dbea; border-radius:5px; background:white; color:#454d81; font:inherit; }
.paper-controls select:focus-visible { outline:2px solid #8c8fc1; outline-offset:2px; }
.paper-controls > span { color:#73768a; font-size:12px; }

.auto-layout-bar { display:flex; gap:12px; align-items:center; flex-wrap:wrap; padding:16px 20px; margin-bottom:16px; border:1px solid #dedff0; border-radius:8px; background:#f7f7fc; }
.auto-layout-bar > div { flex:1; min-width:240px; } .auto-layout-bar p { font-size:12px; color:#73768a; line-height:1.8; margin:4px 0 0; }

.recognition-bar { display:flex; align-items:center; flex-wrap:wrap; gap:10px; margin-bottom:16px; } .recognition-bar span { font-size:13px; color:#738194; } .import-notice { margin-bottom:16px; }
.tech-studio .studio-head h2 { color:#353b59; } .tech-studio .preview-scroll { background:#f0f1f6; } .tech-studio .panel-label { background:#f7f7fb; color:#626780; }
.layout-studio { max-width: 1500px; margin: auto; color: #263445; }
.studio-head { display: flex; justify-content: space-between; align-items: center; gap: 20px; margin-bottom: 22px; }
h2 { margin: 0 0 7px; font-size: 25px; } .studio-head p, .draft-note { color: #738194; margin: 0; font-size: 13px; }
.actions { display: flex; flex-wrap: wrap; gap: 8px; } .actions .el-button { margin-left: 0; }
.theme-picker { display: grid; grid-template-columns: repeat(auto-fit,minmax(180px,1fr)); gap: 12px; margin-bottom: 20px; }
.theme-option { display: flex; align-items: center; gap: 16px; padding: 17px; border: 1px solid #dce3eb; background: #fff; border-radius: 8px; cursor: pointer; text-align: left; color: inherit; }
.theme-option.selected { border-color: #8c8fc1; box-shadow: inset 0 0 0 1px #8c8fc1; background:#f8f8fd; } .theme-option:focus-visible { outline: 3px solid #a8caec; outline-offset: 2px; }
.theme-sample { display:block; flex:0 0 76px; height:76px; padding:10px 8px; background:#fff; border:1px solid #e4e7ec; box-sizing:border-box; } .theme-sample b { display:block; font-size:10px; margin-bottom:8px; } .theme-sample i { display:block; background:#dce0e5; height:2px; margin:5px 0; } .theme-sample em { display:block; font:7px/1.4 sans-serif; border-left:2px solid; padding-left:4px; } .theme-sample.tech { background:white; } .theme-sample.tech b { color:#454d81; text-align:center; padding:4px 0; } .theme-sample.tech em { color:#8d8ca9; border-color:#deddf0; padding-left:4px; } .theme-sample.editorial b { font-family:Georgia,SimSun,serif; font-size:12px; color:#25272b; } .theme-sample.editorial em { border-left:0; border-top:1px solid; padding:3px 0; } .theme-sample.green b { border-left:3px solid; padding-left:4px; } .theme-sample.technical b { border-bottom:1px solid; padding-bottom:3px; } .theme-option strong { display: block; margin-bottom: 5px; font-size: 14px; } .theme-option small { color: #738194; font-size: 12px; }
.format-bar { display: flex; flex-wrap: wrap; align-items: center; gap: 18px; margin: 0 0 18px; } .format-bar label { display: flex; align-items: center; gap: 9px; font-size: 13px; } .format-bar .el-input { width: 220px; }
.editing-tools { display:flex; flex-wrap:wrap; align-items:center; gap:6px; padding:10px 14px; border-bottom:1px solid #e4eaf0; } .editing-tools span { color:#738194; font-size:12px; margin-right:4px; } .editing-tools button { border:1px solid #dce3eb; background:white; color:#344256; border-radius:4px; padding:5px 8px; cursor:pointer; } .editing-tools button:hover { border-color:#71364c; color:#71364c; } .editorial-controls .el-input { width:300px; } .workbench.reading { position:fixed; inset:74px 24px 20px 224px; z-index:1000; display:flex; flex-direction:column; box-shadow:0 12px 50px #26344525; } .reading-actions { display:flex; gap:6px; } .workbench.reading .preview-panel { display:flex; flex-direction:column; min-height:0; flex:1; } .workbench.reading .panel-label { flex-shrink:0; } .workbench.reading .editor-panel { display:none; } .workbench.reading .preview-scroll { height:auto; flex:1; padding:28px; }
.workbench { display: grid; grid-template-columns: minmax(0,1fr) minmax(0,1fr); border: 1px solid #dce3eb; border-radius: 8px; overflow: hidden; background: #fff; }
.panel-label { height: 45px; display: flex; justify-content: space-between; align-items: center; padding: 0 18px; background: #f8fafc; border-bottom: 1px solid #e4eaf0; font-size: 13px; } .panel-label span { color: #8492a3; }
.editor-panel { border-right: 1px solid #dce3eb; } textarea { box-sizing: border-box; display: block; width: 100%; height: 610px; resize: vertical; padding: 24px; border: 0; outline: none; background: #fff; color: #344256; font: 14px/1.9 Consolas,'Microsoft YaHei',monospace; } textarea:focus { box-shadow: inset 0 0 0 2px #a8caec; }
.preview-scroll { height: 660px; overflow: auto; background: #eaf0f5; padding: 20px; box-sizing: border-box; } .preview-paper { background: white; min-height: 100%; margin: auto; } .preview-paper.mobile { max-width: 375px; } .draft-note { margin-top: 14px; line-height: 1.8; }
@media (max-width: 1050px) { .theme-picker { grid-template-columns: repeat(2,minmax(0,1fr)); } .studio-head { align-items: flex-start; flex-direction: column; } }
@media (max-width: 760px) { .workbench.reading { inset:68px 8px 8px; } .workbench.reading .preview-scroll { padding:8px; } .workbench { grid-template-columns: 1fr; } .editor-panel { border-right: 0; } textarea { height: 350px; } .preview-scroll { height: 550px; padding: 8px; } .theme-option { padding: 12px; gap: 8px; } .theme-mark { font-size: 19px; } }
</style>
