<script setup lang="ts">
/**
 * 文章编辑器。
 * 左边改内容，右边实时预览（Markdown 渲染 / HTML 源码）。
 */

import { computed, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import MarkdownIt from 'markdown-it'
import DOMPurify from 'dompurify'

import { updateArticle } from '@/api/articles'
import { ApiError } from '@/api/http'
import type { Article } from '@/types/article'

const props = defineProps<{
  /** 要编辑的文章 */
  article: Article
}>()

const emit = defineEmits<{
  /** 保存成功后把最新的文章抛给父组件 */
  (e: 'saved', article: Article): void
}>()

/** 编辑器里可改的字段 */
interface EditorForm {
  title: string
  subtitle: string
  summary: string
  content_markdown: string
}

/** 从文章对象里取出要编辑的字段（顺便复制一份，避免直接改动父组件的数据） */
function fromArticle(article: Article): EditorForm {
  return {
    title: article.title,
    subtitle: article.subtitle,
    summary: article.summary,
    content_markdown: article.content_markdown,
  }
}

const form = reactive<EditorForm>(fromArticle(props.article))
const saving = ref(false)
const activeTab = ref<'preview' | 'html'>('preview')

// 切换到另一篇文章时，重新填充表单
watch(
  () => props.article.id,
  () => {
    Object.assign(form, fromArticle(props.article))
  },
)

/** markdown-it 实例：html 关掉，避免正文里直接写 HTML 标签 */
const md = new MarkdownIt({
  html: false,
  linkify: true,
  breaks: true,
})

/** Markdown -> HTML */
const renderedHtml = computed(() => md.render(form.content_markdown || ''))

/**
 * 预览用的 HTML。
 *
 * 【为什么要用 DOMPurify 净化】
 * 正文是 AI 生成的、还混入了从网上抓回来的内容，里面完全可能藏有
 * <script>、<img onerror=...> 这类恶意代码。而 Vue 的 v-html 会把这些
 * 字符串「原样当成 HTML 运行」，一旦渲染就会造成 XSS 攻击（比如盗走
 * 登录 token）。所以渲染前必须先用 DOMPurify 把危险标签和属性过滤掉。
 */
const safeHtml = computed(() => DOMPurify.sanitize(renderedHtml.value))

/** 保存 */
async function handleSave(): Promise<void> {
  if (!form.title.trim()) {
    ElMessage.warning('标题不能为空')
    return
  }

  saving.value = true
  try {
    const updated = await updateArticle(props.article.id, {
      title: form.title,
      subtitle: form.subtitle,
      summary: form.summary,
      content_markdown: form.content_markdown,
    })
    emit('saved', updated)
    ElMessage.success('已保存')
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : '保存失败，请稍后重试')
  } finally {
    saving.value = false
  }
}

/** 重置：丢弃当前修改，回到传入时的内容 */
function handleReset(): void {
  Object.assign(form, fromArticle(props.article))
  ElMessage.info('已重置为上次保存的内容')
}
</script>

<template>
  <el-card class="article-editor" shadow="never">
    <template #header>
      <div class="editor-header">
        <span class="editor-title">内容编辑</span>
        <div class="editor-actions">
          <el-button :disabled="saving" @click="handleReset">重置</el-button>
          <el-button type="primary" :loading="saving" @click="handleSave">保存</el-button>
        </div>
      </div>
    </template>

    <el-row :gutter="16">
      <!-- 左：编辑区 -->
      <el-col :xs="24" :md="12">
        <el-form label-position="top">
          <el-form-item label="标题">
            <el-input v-model="form.title" maxlength="200" show-word-limit placeholder="请输入标题" />
          </el-form-item>

          <el-form-item label="副标题">
            <el-input v-model="form.subtitle" maxlength="200" placeholder="可选" />
          </el-form-item>

          <el-form-item label="摘要">
            <el-input
              v-model="form.summary"
              type="textarea"
              :rows="3"
              maxlength="500"
              show-word-limit
              placeholder="可选"
            />
          </el-form-item>

          <el-form-item label="正文（Markdown）">
            <el-input
              v-model="form.content_markdown"
              type="textarea"
              :rows="22"
              placeholder="在这里编辑 Markdown 正文"
            />
          </el-form-item>
        </el-form>
      </el-col>

      <!-- 右：预览区 -->
      <el-col :xs="24" :md="12">
        <el-tabs v-model="activeTab">
          <el-tab-pane label="预览" name="preview">
            <!-- safeHtml 已经过 DOMPurify 净化，可以安全地用 v-html 渲染 -->
            <div class="markdown-body" v-html="safeHtml"></div>
          </el-tab-pane>
          <el-tab-pane label="HTML 源码" name="html">
            <pre class="html-source">{{ safeHtml }}</pre>
          </el-tab-pane>
        </el-tabs>
      </el-col>
    </el-row>
  </el-card>
</template>

<style scoped>
.editor-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.editor-title {
  font-weight: 600;
  color: #303133;
}

.editor-actions {
  display: flex;
  gap: 8px;
}

/* 预览区样式：让 Markdown 渲染出来好看一点 */
.markdown-body {
  min-height: 320px;
  max-height: 640px;
  overflow-y: auto;
  padding: 12px 16px;
  border: 1px solid #e4e7ed;
  border-radius: 4px;
  background-color: #ffffff;
  line-height: 1.8;
  color: #303133;
  font-size: 14px;
}

.markdown-body :deep(h1),
.markdown-body :deep(h2),
.markdown-body :deep(h3) {
  margin: 16px 0 10px;
  font-weight: 600;
}

.markdown-body :deep(p) {
  margin: 10px 0;
}

.markdown-body :deep(code) {
  background-color: #f5f7fa;
  padding: 2px 5px;
  border-radius: 3px;
  font-size: 13px;
}

.markdown-body :deep(pre) {
  background-color: #f5f7fa;
  padding: 12px;
  border-radius: 4px;
  overflow-x: auto;
}

.markdown-body :deep(blockquote) {
  margin: 10px 0;
  padding-left: 12px;
  border-left: 4px solid #dcdfe6;
  color: #606266;
}

.markdown-body :deep(img) {
  max-width: 100%;
}

.html-source {
  min-height: 320px;
  max-height: 640px;
  overflow: auto;
  margin: 0;
  padding: 12px 16px;
  border: 1px solid #e4e7ed;
  border-radius: 4px;
  background-color: #f5f7fa;
  font-size: 12px;
  line-height: 1.7;
  color: #606266;
  white-space: pre-wrap;
  word-break: break-all;
}
</style>
