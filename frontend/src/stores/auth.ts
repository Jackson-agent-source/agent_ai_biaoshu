/**
 * 登录状态仓库。
 *
 * 保存 token 和当前用户信息。token 同时写入 localStorage，
 * 这样刷新页面（甚至关掉浏览器再打开）依然是登录状态。
 */

import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import * as authApi from '@/api/auth'
import { TOKEN_KEY, clearToken, setToken } from '@/api/http'
import type { LoginRequest, LoginResponse, RegisterRequest, User } from '@/types/api'

export const useAuthStore = defineStore('auth', () => {
  // 初始化时先从 localStorage 里读一次 token
  const token = ref<string>(localStorage.getItem(TOKEN_KEY) ?? '')
  const user = ref<User | null>(null)
  const loading = ref(false)

  /** 是否已登录 */
  const isLoggedIn = computed(() => token.value.length > 0)
  /** 当前用户名，未登录时为空字符串 */
  const username = computed(() => user.value?.username ?? '')

  /** 保存 token（内存 + localStorage） */
  function saveToken(value: string): void {
    token.value = value
    setToken(value)
  }

  /** 清空登录状态 */
  function reset(): void {
    token.value = ''
    user.value = null
    clearToken()
  }

  /** 登录 */
  async function login(payload: LoginRequest): Promise<LoginResponse> {
    loading.value = true
    try {
      const data = await authApi.login(payload)
      saveToken(data.token)
      user.value = data.user
      return data
    } finally {
      loading.value = false
    }
  }

  /** 注册（后端注册成功后直接返回 token，所以等同于登录） */
  async function register(payload: RegisterRequest): Promise<LoginResponse> {
    loading.value = true
    try {
      const data = await authApi.register(payload)
      saveToken(data.token)
      user.value = data.user
      return data
    } finally {
      loading.value = false
    }
  }

  /**
   * 拉取当前用户信息。
   * 用于刷新页面后恢复「用户名」等显示内容。
   */
  async function fetchMe(): Promise<User | null> {
    if (!token.value) {
      return null
    }
    try {
      const me = await authApi.fetchMe()
      user.value = me
      return me
    } catch (error) {
      // token 失效了就当作未登录处理
      reset()
      throw error
    }
  }

  /** 退出登录 */
  async function logout(): Promise<void> {
    try {
      if (token.value) {
        await authApi.logout()
      }
    } catch {
      // 退出接口失败也无所谓，本地状态照样清掉
    } finally {
      reset()
    }
  }

  return {
    token,
    user,
    loading,
    isLoggedIn,
    username,
    login,
    register,
    fetchMe,
    logout,
    reset,
  }
})
