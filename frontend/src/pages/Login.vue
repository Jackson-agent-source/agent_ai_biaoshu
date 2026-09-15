<script setup lang="ts">
/**
 * 登录 / 注册页。
 * 两种模式用同一个表单，点下面的链接切换。
 */

import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, type FormInstance, type FormRules } from 'element-plus'

import { ApiError } from '@/api/http'
import { useAuthStore } from '@/stores/auth'

type Mode = 'login' | 'register'

const auth = useAuthStore()
const router = useRouter()
const route = useRoute()

const mode = ref<Mode>('login')
const formRef = ref<FormInstance>()
const submitting = ref(false)

const form = reactive({
  username: '',
  password: '',
  email: '',
})

/** 表单校验规则 */
const rules: FormRules = {
  username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
  password: [
    { required: true, message: '请输入密码', trigger: 'blur' },
    { min: 6, message: '密码至少 6 位', trigger: 'blur' },
  ],
  email: [{ type: 'email', message: '邮箱格式不正确', trigger: 'blur' }],
}

/** 切换登录 / 注册 */
function toggleMode(): void {
  mode.value = mode.value === 'login' ? 'register' : 'login'
  formRef.value?.clearValidate()
}

/** 提交表单 */
async function handleSubmit(): Promise<void> {
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
    if (mode.value === 'login') {
      await auth.login({ username: form.username, password: form.password })
    } else {
      await auth.register({
        username: form.username,
        password: form.password,
        // 邮箱是可选的，为空就不传这个字段
        ...(form.email ? { email: form.email } : {}),
      })
    }

    ElMessage.success(mode.value === 'login' ? '登录成功' : '注册成功')

    // 登录前如果被拦在某个页面，登录后回到那里
    const redirect = typeof route.query.redirect === 'string' ? route.query.redirect : '/'
    await router.push(redirect)
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : '操作失败，请稍后重试')
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div class="login-page">
    <el-card class="login-card" shadow="always">
      <div class="login-header">
        <h1 class="login-title">微信公众号 AI Content Agent</h1>
        <p class="login-subtitle">
          {{ mode === 'login' ? '登录后开始生成文章' : '创建一个新账号' }}
        </p>
      </div>

      <el-form
        ref="formRef"
        :model="form"
        :rules="rules"
        label-position="top"
        @submit.prevent="handleSubmit"
      >
        <el-form-item label="用户名" prop="username">
          <el-input v-model="form.username" placeholder="请输入用户名" size="large" />
        </el-form-item>

        <el-form-item label="密码" prop="password">
          <el-input
            v-model="form.password"
            type="password"
            placeholder="请输入密码"
            size="large"
            show-password
          />
        </el-form-item>

        <!-- 只有注册模式才需要邮箱 -->
        <el-form-item v-if="mode === 'register'" label="邮箱（可选）" prop="email">
          <el-input v-model="form.email" placeholder="请输入邮箱，可以不填" size="large" />
        </el-form-item>

        <el-button
          type="primary"
          size="large"
          class="login-button"
          :loading="submitting"
          @click="handleSubmit"
        >
          {{ mode === 'login' ? '登录' : '注册' }}
        </el-button>
      </el-form>

      <div class="login-footer">
        <el-button link type="primary" @click="toggleMode">
          {{ mode === 'login' ? '还没有账号？去注册' : '已有账号？去登录' }}
        </el-button>
      </div>

      <el-alert type="info" :closable="false" class="login-tip">
        <template #title>
          提示：可以直接用 Django 的超级管理员账号登录（就是执行
          <code>createsuperuser</code> 时设置的那个用户名和密码）。
        </template>
      </el-alert>
    </el-card>
  </div>
</template>

<style scoped>
.login-page {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100vh;
  background-color: #f5f7fa;
}

.login-card {
  width: 420px;
  padding: 8px 12px;
}

.login-header {
  text-align: center;
  margin-bottom: 20px;
}

.login-title {
  margin: 0;
  font-size: 19px;
  color: #303133;
}

.login-subtitle {
  margin: 8px 0 0;
  font-size: 13px;
  color: #909399;
}

.login-button {
  width: 100%;
  margin-top: 4px;
}

.login-footer {
  margin-top: 12px;
  text-align: center;
}

.login-tip {
  margin-top: 16px;
}

.login-tip code {
  background-color: #f5f7fa;
  padding: 1px 4px;
  border-radius: 3px;
}
</style>
