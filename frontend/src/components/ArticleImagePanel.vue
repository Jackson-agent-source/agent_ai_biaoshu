<script setup lang="ts">
/**
 * 配图面板：文章详情页右侧「配图」tab 的内容。
 *
 * 布局分三块：
 *   1. 操作条 —— 一键生成剩余的配图建议 / 停止 / 进度
 *   2. 配图建议列表 —— 每条一个「生成」按钮（已经出过图的显示「已生成」）
 *   3. 已生成的图 —— 缩略图 + 图注 + 插入位置 + 设封面 + 删除
 *
 * ★ 数据全部走 stores/image.ts，组件自己不持有列表。
 *   这样"能不能推送到公众号"的判断（要求至少一张成功配图）
 *   和这里显示的图是**同一份状态**，不会出现一个说有一张、另一个说没有。
 */

import { computed } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Picture } from '@element-plus/icons-vue'

import { ApiError } from '@/api/http'
import { useImageStore } from '@/stores/image'
import {
  IMAGE_STATUS_TAG_TYPES,
  type GeneratedImage,
} from '@/types/image'
import type { Article } from '@/types/article'

const props = defineProps<{
  /** 当前文章（只需要 id 和 image_suggestions） */
  article: Article
}>()

const imageStore = useImageStore()

/** 配图建议原文列表（没有就是空数组，模板里据此显示空状态） */
const suggestions = computed(() => props.article.image_suggestions ?? [])

/** 已经有图的建议序号，用来把按钮置灰 */
const usedIndexes = computed(() => imageStore.usedSuggestionIndexes)

/** 还剩几条建议没有出图 */
const pendingCount = computed(
  () => suggestions.value.filter((_, index) => !usedIndexes.value.has(index)).length,
)

/**
 * 「插入位置」下拉选项。
 * ★ 上限取 sectionCount 和现有图里最大 anchor_index 的较大值：
 *   文章被编辑过、删掉了几个小节之后，老图的 anchor_index 可能超出
 *   sectionCount —— 不把它列进选项的话，那个下拉框会显示成空白，
 *   看起来像是"位置丢了"，其实值还在。
 */
const anchorOptions = computed(() => {
  const maxAnchor = imageStore.images.reduce(
    (max, item) => Math.max(max, item.anchor_index),
    0,
  )
  const top = Math.max(imageStore.sectionCount, maxAnchor)

  const options: { label: string; value: number }[] = [{ label: '文章开头', value: 0 }]
  for (let i = 1; i <= top; i += 1) {
    options.push({ label: `第 ${i} 节之后`, value: i })
  }
  return options
})

/** 统一的错误提示 */
function showError(error: unknown, fallback: string): void {
  ElMessage.error(error instanceof ApiError ? error.message : fallback)
}

/** 生成单张 */
async function handleGenerateOne(index: number, suggestion: string): Promise<void> {
  try {
    await imageStore.generateOne(props.article.id, {
      suggestion_index: index,
      suggestion,
    })
    ElMessage.success('配图已生成')
  } catch (error) {
    showError(error, '生成配图失败')
  }
}

/** 生成剩余的全部建议（串行，可能要几分钟） */
async function handleGenerateAll(): Promise<void> {
  const count = pendingCount.value
  try {
    await ElMessageBox.confirm(
      `要给剩余 ${count} 条配图建议各生成一张图吗？` +
        `每张大约需要 30~90 秒，${count} 张总共可能要几分钟，期间请不要关闭页面。`,
      '批量生成配图',
      { confirmButtonText: '开始生成', cancelButtonText: '取消', type: 'info' },
    )
  } catch {
    // 用户点了取消。ElMessageBox 用 reject 表示取消，
    // 不吞掉的话控制台会多一条没人处理的报错。
    return
  }

  const succeeded = await imageStore.generateBatch(props.article.id, suggestions.value)
  if (succeeded > 0) {
    ElMessage.success(`已生成 ${succeeded} 张配图`)
  }
  if (imageStore.errorMessage) {
    ElMessage.warning(`有配图没能生成：${imageStore.errorMessage}`)
  }
}

/** 改图注（失焦或回车时提交） */
async function handleCaptionChange(image: GeneratedImage, value: string | number): Promise<void> {
  const caption = String(value)
  if (caption === image.caption) {
    return
  }
  try {
    await imageStore.updateOne(image.id, { caption })
  } catch (error) {
    showError(error, '保存图注失败')
  }
}

/** 改插入位置 */
async function handleAnchorChange(image: GeneratedImage, value: number): Promise<void> {
  try {
    await imageStore.updateOne(image.id, { anchor_index: Number(value) })
    ElMessage.success('插入位置已更新')
  } catch (error) {
    showError(error, '更新插入位置失败')
  }
}

/** 设为封面 / 取消封面 */
async function handleToggleCover(image: GeneratedImage): Promise<void> {
  try {
    await imageStore.updateOne(image.id, { is_cover: !image.is_cover })
    ElMessage.success(image.is_cover ? '已取消封面' : '已设为封面')
  } catch (error) {
    showError(error, '设置封面失败')
  }
}

/** 删除一张配图 */
async function handleDelete(image: GeneratedImage): Promise<void> {
  try {
    await ElMessageBox.confirm(
      '确定要删除这张配图吗？图片文件会一起删掉，无法恢复。',
      '提示',
      { confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning' },
    )
  } catch {
    return
  }

  try {
    await imageStore.removeImage(image.id)
    ElMessage.success('配图已删除')
  } catch (error) {
    showError(error, '删除配图失败')
  }
}

/** 重新拉列表（文章正文被改过之后小节数会变，需要刷新） */
async function handleRefresh(): Promise<void> {
  try {
    await imageStore.fetchImages(props.article.id)
    ElMessage.success('已刷新')
  } catch (error) {
    showError(error, '刷新配图失败')
  }
}
</script>

<template>
  <div v-loading="imageStore.listLoading" class="image-panel">
    <!-- ---------- 操作条 ---------- -->
    <div class="panel-toolbar">
      <el-button
        type="primary"
        size="small"
        :loading="imageStore.generating"
        :disabled="pendingCount === 0"
        @click="handleGenerateAll"
      >
        {{ pendingCount > 0 ? `生成剩余 ${pendingCount} 张` : '全部建议已出图' }}
      </el-button>
      <el-button v-if="imageStore.generating" size="small" plain @click="imageStore.stopBatch()">
        停止
      </el-button>
      <el-button size="small" text :disabled="imageStore.generating" @click="handleRefresh">
        刷新
      </el-button>
    </div>

    <!-- 批量生成中的进度。一张 30~90 秒，没有这个用户会以为卡死了 -->
    <div v-if="imageStore.generating" class="progress-line">
      <el-icon class="is-loading"><Picture /></el-icon>
      正在生成第 {{ imageStore.progress.currentIndex + 1 }} 条建议的配图…
      （已完成 {{ imageStore.progress.done }}/{{ imageStore.progress.total }} 张）
    </div>

    <el-alert
      v-if="imageStore.errorMessage"
      class="panel-alert"
      type="error"
      :closable="false"
      show-icon
      :title="imageStore.errorMessage"
    />

    <!-- ---------- 配图建议 ---------- -->
    <div class="section-title">配图建议</div>

    <el-empty
      v-if="suggestions.length === 0"
      description="这篇文章没有配图建议，可以直接点下面的「生成一张」自己描述画面"
      :image-size="60"
    />

    <div v-else class="suggestion-list">
      <div v-for="(text, index) in suggestions" :key="index" class="suggestion-row">
        <span class="suggestion-text">{{ index + 1 }}. {{ text }}</span>
        <el-button
          size="small"
          :type="usedIndexes.has(index) ? 'info' : 'primary'"
          plain
          :disabled="imageStore.generating || usedIndexes.has(index)"
          @click="handleGenerateOne(index, text)"
        >
          {{ usedIndexes.has(index) ? '已生成' : '生成' }}
        </el-button>
      </div>
    </div>

    <!-- ---------- 已生成的图 ---------- -->
    <div class="section-title">
      已有配图
      <span class="dim">（{{ imageStore.images.length }} 张，其中
        {{ imageStore.succeededImages.length }} 张可用）</span>
    </div>

    <el-empty
      v-if="imageStore.images.length === 0"
      description="还没有配图"
      :image-size="60"
    />

    <div v-for="image in imageStore.images" :key="image.id" class="image-row">
      <!-- 缩略图：点开可看大图 -->
      <el-image
        v-if="image.file_url"
        class="thumb"
        :src="image.file_url"
        :preview-src-list="[image.file_url]"
        :initial-index="0"
        preview-teleported
        hide-on-click-modal
        fit="cover"
      />
      <div v-else class="thumb thumb-empty">
        <el-icon><Picture /></el-icon>
      </div>

      <div class="image-body">
        <div class="image-meta">
          <el-tag size="small" :type="IMAGE_STATUS_TAG_TYPES[image.status]">
            {{ image.status_display }}
          </el-tag>
          <el-tag v-if="image.is_cover" size="small" type="warning">封面</el-tag>
          <span v-if="image.width && image.height" class="dim">
            {{ image.width }}×{{ image.height }}
          </span>
        </div>

        <!-- 失败原因：后端把生图服务的报错翻译成中文了，直接展示 -->
        <div v-if="image.error_message" class="image-error">{{ image.error_message }}</div>

        <el-input
          :model-value="image.caption"
          size="small"
          maxlength="200"
          placeholder="图注（会显示在公众号正文的图片下方）"
          :disabled="image.status !== 'succeeded'"
          @change="(value: string | number) => handleCaptionChange(image, value)"
        />

        <div class="image-actions">
          <el-select
            :model-value="image.anchor_index"
            size="small"
            class="anchor-select"
            :disabled="image.status !== 'succeeded'"
            @change="(value: number) => handleAnchorChange(image, value)"
          >
            <el-option
              v-for="option in anchorOptions"
              :key="option.value"
              :label="option.label"
              :value="option.value"
            />
          </el-select>

          <el-button
            size="small"
            plain
            :disabled="image.status !== 'succeeded'"
            @click="handleToggleCover(image)"
          >
            {{ image.is_cover ? '取消封面' : '设为封面' }}
          </el-button>

          <el-button size="small" type="danger" plain @click="handleDelete(image)">
            删除
          </el-button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.panel-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 10px;
}

.progress-line {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 10px;
  font-size: 12px;
  color: #409eff;
  line-height: 1.6;
}

.panel-alert {
  margin-bottom: 10px;
}

.section-title {
  margin: 14px 0 8px;
  font-size: 13px;
  font-weight: 600;
  color: #606266;
}

.suggestion-list {
  border: 1px solid #f0f2f5;
  border-radius: 4px;
  padding: 4px 10px;
}

.suggestion-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
  padding: 6px 0;
  border-bottom: 1px solid #f5f7fa;
}

.suggestion-row:last-child {
  border-bottom: none;
}

.suggestion-text {
  flex: 1;
  font-size: 13px;
  color: #606266;
  line-height: 1.6;
}

.image-row {
  display: flex;
  gap: 10px;
  padding: 10px 0;
  border-bottom: 1px solid #f5f7fa;
}

.image-row:last-child {
  border-bottom: none;
}

.thumb {
  width: 84px;
  height: 84px;
  flex: 0 0 84px;
  border-radius: 4px;
  overflow: hidden;
  background: #f5f7fa;
  cursor: zoom-in;
}

.thumb-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  color: #c0c4cc;
  font-size: 24px;
  cursor: default;
}

.image-body {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.image-meta {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.image-error {
  font-size: 12px;
  color: #f56c6c;
  line-height: 1.6;
  word-break: break-all;
}

.image-actions {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.anchor-select {
  width: 130px;
}

.dim {
  font-size: 12px;
  font-weight: 400;
  color: #909399;
}
</style>
