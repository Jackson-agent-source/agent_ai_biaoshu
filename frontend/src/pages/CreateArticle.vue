<script setup lang="ts">
/**
 * 创建文章页：填写写作要求 -> 点「开始生成」-> 等 Agent 跑完 -> 跳到文章详情。
 *
 * 重要：后端的生成接口是「同步」的，会一直卡到 Agent 全部跑完才返回，
 * 可能要 30~120 秒。所以这里必须：
 *   1. 显示「生成中」状态；
 *   2. 把按钮禁用掉，避免用户狂点导致重复生成。
 */

import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox, type FormInstance, type FormRules } from 'element-plus'

import AgentProgress from '@/components/AgentProgress.vue'
import { ApiError } from '@/api/http'
import { useAgentStore } from '@/stores/agent'
import type { GenerateArticleRequest } from '@/types/article'

const router = useRouter()
const agentStore = useAgentStore()

const formRef = ref<FormInstance>()
/** 是否正在生成（用来禁用按钮、显示进度） */
const submitting = ref(false)
/** 生成成功后拿到的运行 id，交给 AgentProgress 轮询用 */
const runId = ref('')

/** 篇幅选项 */
const LENGTH_OPTIONS: { label: string; value: 'short' | 'medium' | 'long' }[] = [
  { label: '短篇（约 800 字）', value: 'short' },
  { label: '中篇（约 1500 字）', value: 'medium' },
  { label: '长篇（约 2500 字）', value: 'long' },
]

const form = reactive<Required<Pick<GenerateArticleRequest, 'topic' | 'audience' | 'goal' | 'tone' | 'length' | 'research_mode' | 'extra_requirements'>>>({
  topic: '',
  audience: '普通互联网用户',
  goal: '知识科普',
  tone: '轻松易懂',
  length: 'medium',
  research_mode: true,
  extra_requirements: '',
})

const rules: FormRules = {
  topic: [
    { required: true, message: '请输入文章主题', trigger: 'blur' },
    { min: 1, max: 500, message: '主题长度需在 1 到 500 个字符之间', trigger: 'blur' },
  ],
}

/** 提交生成 */
async function handleSubmit(): Promise<void> {
  // 双保险：即使按钮被绕过，也不允许重复提交
  if (submitting.value) {
    return
  }

  const instance = formRef.value
  if (!instance) {
    return
  }

  const valid = await instance.validate().catch(() => false)
  if (!valid) {
    return
  }

  submitting.value = true
  try {
    const payload: GenerateArticleRequest = { ...form }
    const result = await agentStore.generate(payload)
    runId.value = result.run_id
    ElMessage.success('文章生成完成')

    // 问一句要不要现在配图。
    //
    // ★ 这里用**两个** try/catch 是有原因的，不是啰嗦：
    //   ElMessageBox 用 reject 表示"用户点了取消"，这和真的出错走的是同一条
    //   catch 分支。只写一个 try/catch 的话，用户点"先不看"会被当成生成失败，
    //   弹出一句莫名其妙的错误提示。第一个 catch 只干一件事：把取消吃掉。
    let wantImages = false
    try {
      await ElMessageBox.confirm(
        '文章已经生成好了。要现在去生成配图吗？公众号推送要求草稿必须有配图。',
        '生成配图',
        {
          confirmButtonText: '去生成配图',
          cancelButtonText: '先不看',
          type: 'success',
        },
      )
      wantImages = true
    } catch {
      wantImages = false
    }

    await router.push({
      name: 'article-detail',
      params: { id: result.article_id },
      // 让详情页直接停在「配图」tab 上，省掉用户自己去找的那一下
      query: wantImages ? { tab: 'images' } : {},
    })
  } catch (error) {
    // 后端返回的中文提示会通过 ApiError 带过来
    ElMessage.error(error instanceof ApiError ? error.message : '生成失败，请稍后重试')
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div class="create-page">
    <div class="page-header">
      <h2 class="page-title">创建文章</h2>
      <p class="page-desc">
        填好下面的要求后点「开始生成」，Agent 会先去网上搜资料，再写出初稿。
        整个过程通常需要 30~120 秒，请耐心等待，不要重复点击。
      </p>
    </div>

    <el-card shadow="never">
      <el-form ref="formRef" :model="form" :rules="rules" label-width="110px">
        <el-form-item label="文章主题" prop="topic">
          <el-input
            v-model="form.topic"
            type="textarea"
            :rows="2"
            maxlength="500"
            show-word-limit
            placeholder="例如：为什么秋天的树叶会变黄？"
          />
        </el-form-item>

        <el-form-item label="目标读者">
          <el-input v-model="form.audience" maxlength="100" placeholder="默认：普通互联网用户" />
        </el-form-item>

        <el-form-item label="写作目标">
          <el-input v-model="form.goal" maxlength="100" placeholder="例如：知识科普" />
        </el-form-item>

        <el-form-item label="语气风格">
          <el-input v-model="form.tone" maxlength="100" placeholder="例如：轻松易懂" />
        </el-form-item>

        <el-form-item label="篇幅">
          <el-select v-model="form.length" class="length-select">
            <el-option
              v-for="option in LENGTH_OPTIONS"
              :key="option.value"
              :label="option.label"
              :value="option.value"
            />
          </el-select>
        </el-form-item>

        <el-form-item label="联网搜资料">
          <el-switch v-model="form.research_mode" />
          <span class="form-hint">开启后 Agent 会先搜索资料再写作，内容更准确</span>
        </el-form-item>

        <el-form-item label="额外要求">
          <el-input
            v-model="form.extra_requirements"
            type="textarea"
            :rows="4"
            maxlength="2000"
            show-word-limit
            placeholder="还有什么特别要求？例如：多用例子、结尾加一句互动提问"
          />
        </el-form-item>

        <el-form-item>
          <el-button
            type="primary"
            size="large"
            :loading="submitting"
            :disabled="submitting"
            @click="handleSubmit"
          >
            {{ submitting ? '生成中，请稍候…' : '开始生成' }}
          </el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <!-- 生成过程中显示进度时间线 -->
    <AgentProgress v-if="submitting || runId" :run-id="runId" :active="submitting" />
  </div>
</template>

<style scoped>
.page-header {
  margin-bottom: 16px;
}

.page-title {
  margin: 0;
  font-size: 18px;
  color: #303133;
}

.page-desc {
  margin: 8px 0 0;
  font-size: 13px;
  color: #909399;
}

.length-select {
  width: 240px;
}

.form-hint {
  margin-left: 12px;
  font-size: 12px;
  color: #909399;
}
</style>
