<script setup lang="ts">
import { onMounted, onBeforeUnmount, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api, ApiError } from '@/api/http'
import { useAuthStore } from '@/stores/auth'

type Kind = 'text' | 'image'
interface Connection { kind: Kind; enabled: boolean; base_url: string; model_name: string; has_key: boolean; default_available: boolean; default_model: string }
interface Form extends Connection { api_key: string; busy: boolean; testing: boolean; result: string; error: boolean; saved: boolean }
const auth = useAuthStore()
const loading = ref(true)
const loadError = ref('')
const types = [
  { id: 'text' as Kind, name: '文字模型', description: '用于文章生成、热点选题和文章自动排版。', sample: 'deepseek-chat', address: 'https://api.deepseek.com/v1' },
  { id: 'image' as Kind, name: '图片模型', description: '用于文章详情中的 AI 配图。排版页的网格、光晕等背景仍为本地版式。', sample: '服务商提供的图片模型名称', address: 'https://你的服务商地址/v1' },
]
const empty = (kind: Kind): Form => ({ kind, enabled: false, base_url: '', model_name: '', has_key: false, default_available: false, default_model: '', api_key: '', busy: false, testing: false, result: '', error: false, saved: false })
const forms = reactive<Record<Kind, Form>>({ text: empty('text'), image: empty('image') })
let mounted = true
onBeforeUnmount(() => { mounted = false; forms.text.api_key = ''; forms.image.api_key = '' })
async function load(): Promise<void> {
  loading.value = true; loadError.value = ''
  try {
    const data = await api.get<Record<Kind, Connection>>('/auth/api-settings')
    if (!mounted) return
    for (const kind of ['text', 'image'] as Kind[]) Object.assign(forms[kind], data[kind], { api_key: '', saved: data[kind].enabled })
  } catch (error) { loadError.value = error instanceof Error ? error.message : '设置读取失败，请重试' }
  finally { loading.value = false }
}
function payload(kind: Kind) {
  const form = forms[kind]
  return { enabled: form.enabled, base_url: form.base_url.trim(), model_name: form.model_name.trim(), ...(form.api_key ? { api_key: form.api_key.trim() } : {}) }
}
function valid(kind: Kind): boolean {
  const form = forms[kind]
  if (form.enabled && (!form.base_url.trim() || !form.model_name.trim() || (!form.api_key.trim() && !form.has_key))) {
    form.result = '请填写 API 地址、模型名称和密钥。'; form.error = true; return false
  }
  return true
}
async function save(kind: Kind): Promise<void> {
  if (!valid(kind)) return
  const form = forms[kind]; form.busy = true; form.result = ''
  try {
    const data = await api.put<Connection>(`/auth/api-settings/${kind}`, payload(kind))
    if (!mounted) return
    Object.assign(form, data, { api_key: '', saved: data.enabled })
    form.error = false; form.result = data.enabled ? '已保存，下次调用将使用你的 API，无需重启。' : '已切换为服务器默认配置，个人密钥仍保留。'
    ElMessage.success('API 设置已保存')
  } catch (error) { form.error = true; form.result = error instanceof Error ? error.message : '保存失败' }
  finally { form.busy = false }
}
async function test(kind: Kind): Promise<void> {
  if (!valid(kind)) return
  const form = forms[kind]; form.testing = true; form.result = ''
  try {
    const result = await api.post<{ message: string }>(`/auth/api-settings/${kind}/test`, payload(kind), { timeout: 45000 })
    if (!mounted) return
    form.error = false; form.result = result.message + ' 配置尚需点击“保存并生效”。'
  } catch (error) {
    form.error = true
    form.result = error instanceof ApiError ? error.message : '连接测试失败，请核对配置。'
  } finally { form.testing = false }
}
async function remove(kind: Kind): Promise<void> {
  try { await ElMessageBox.confirm('删除此账号保存的 API 地址、模型和密钥，并恢复服务器默认配置。', '删除个人 API 配置', { confirmButtonText: '删除配置', cancelButtonText: '取消', type: 'warning' }) } catch { return }
  const form = forms[kind]; form.busy = true
  try {
    const data = await api.delete<Connection>(`/auth/api-settings/${kind}`)
    if (!mounted) return
    Object.assign(form, data, { api_key: '', saved: false, error: false, result: '个人配置已删除。' })
  } catch (error) { form.error = true; form.result = error instanceof Error ? error.message : '删除失败' }
  finally { form.busy = false }
}
function preset(kind: Kind): void {
  const form = forms[kind]
  form.enabled = true; form.base_url = 'https://api.deepseek.com/v1'; form.model_name = 'deepseek-chat'; form.result = ''
}
onMounted(load)
</script>

<template>
  <div class="settings-page" v-loading="loading">
    <header class="settings-heading"><div><h2>设置</h2><p>连接你的模型服务，让文章与图片使用你自己的 API。</p></div><span class="account">当前账号 · {{ auth.user?.username || '—' }}</span></header>
    <el-alert v-if="loadError" :title="loadError" type="error" :closable="false" show-icon /><el-button v-if="loadError" @click="load">重新加载</el-button>
    <section v-for="item in types" :key="item.id" class="connection-card" :aria-label="item.name + ' API 配置'">
      <header><div><h3>{{ item.name }}</h3><p>{{ item.description }}</p></div><el-tag :type="forms[item.id].saved ? 'success' : 'info'">{{ forms[item.id].saved ? '当前使用个人 API' : '当前使用服务器默认' }}</el-tag></header>
      <p class="default-info">服务器默认：{{ forms[item.id].default_available ? forms[item.id].default_model : '尚未配置' }}</p>
      <el-checkbox v-model="forms[item.id].enabled" :disabled="forms[item.id].busy || forms[item.id].testing || !!loadError" :aria-label="item.name + '使用个人 API'">使用我自己的 API</el-checkbox>
      <div v-if="forms[item.id].enabled" class="connection-fields">
        <div class="preset-row"><span>支持 OpenAI 兼容接口</span><el-button v-if="item.id === 'text'" size="small" plain @click="preset(item.id)" :disabled="forms[item.id].busy || forms[item.id].testing">填入 DeepSeek 示例</el-button></div>
        <label>API 地址<el-input v-model="forms[item.id].base_url" :aria-label="item.name + ' API 地址'" :placeholder="item.address" maxlength="500" :disabled="forms[item.id].busy || forms[item.id].testing" /></label>
        <p class="field-note">填写服务商提供的 HTTPS 基地址，通常以 /v1 结尾；不要加 /chat/completions 或 /images/generations。</p>
        <label>模型名称<el-input v-model="forms[item.id].model_name" :aria-label="item.name + '模型名称'" :placeholder="item.sample" maxlength="150" :disabled="forms[item.id].busy || forms[item.id].testing" /></label>
        <label>API Key <el-tag v-if="forms[item.id].has_key" size="small" type="success">已保存</el-tag><el-input v-model="forms[item.id].api_key" :aria-label="item.name + ' API Key'" type="password" show-password autocomplete="new-password" :placeholder="forms[item.id].has_key ? '留空沿用已保存密钥；填写新值可替换' : '粘贴服务商提供的 API Key'" maxlength="4096" :disabled="forms[item.id].busy || forms[item.id].testing" /></label>
        <p class="field-note">密钥只提交到本站后端加密保存，不回显、不写入浏览器草稿。更换地址时需重新填写密钥。</p>
      </div>
      <div class="connection-actions"><el-button type="primary" :loading="forms[item.id].busy" :disabled="loading || !!loadError || forms[item.id].testing" @click="save(item.id)">保存并生效</el-button><el-button v-if="forms[item.id].enabled" :loading="forms[item.id].testing" :disabled="forms[item.id].busy || !!loadError" @click="test(item.id)">测试连接</el-button><el-button v-if="forms[item.id].has_key" type="danger" text :disabled="forms[item.id].busy || forms[item.id].testing" @click="remove(item.id)">删除个人配置</el-button></div>
      <p v-if="forms[item.id].enabled" class="test-note">{{ item.id === 'text' ? '测试会发送一条短消息，产生少量文字 API 费用；文章研究需要模型支持工具调用。' : '测试只读取模型列表，不生成图片、不验证最终生图能力；部分服务不提供模型列表。' }}</p>
      <el-alert v-if="forms[item.id].result" :title="forms[item.id].result" :type="forms[item.id].error ? 'error' : 'success'" :closable="false" show-icon />
    </section>
    <p class="settings-footnote">配置仅对当前账号生效，包括该账号的定时选题和文章生成。启用后，相应请求会发送至你填写的服务商，并使用该 API 的额度。文字 API 与图片 API 可来自不同服务商。</p>
  </div>
</template>

<style scoped>
.settings-page { max-width:1000px; margin:0 auto; color:#303c50; }
.settings-heading { display:flex; justify-content:space-between; align-items:center; gap:16px; margin-bottom:24px; }
h2 { font-size:26px; margin:0 0 8px; } .settings-heading p,.connection-card header p { color:#748095; font-size:14px; line-height:1.7; margin:0; }
.account { color:#69758b; font-size:13px; white-space:nowrap; }
.connection-card { padding:26px; border:1px solid #dee4ee; border-radius:12px; background:white; margin-bottom:22px; }
.connection-card header { display:flex; justify-content:space-between; align-items:flex-start; gap:20px; } h3 { font-size:19px; margin:0 0 9px; }
.default-info { padding:12px 14px; background:#f4f6fb; border-radius:6px; color:#69758b; font-size:13px; }
.connection-fields { max-width:700px; margin:16px 0; } .connection-fields label { display:block; font-size:14px; margin-top:18px; }
.connection-fields label .el-input { display:block; margin-top:8px; } .field-note,.test-note { color:#7a8596; font-size:12px; line-height:1.8; margin:7px 0 0; }
.preset-row { display:flex; justify-content:space-between; align-items:center; color:#69758b; font-size:13px; gap:12px; }
.connection-actions { display:flex; flex-wrap:wrap; gap:8px; margin-top:20px; } .connection-actions .el-button { margin-left:0; }
.connection-card .el-alert { margin-top:16px; } .settings-footnote { color:#7a8596; line-height:1.9; font-size:13px; }
@media(max-width:700px) { .settings-heading { align-items:flex-start; flex-direction:column; } .connection-card { padding:18px; } .connection-card header { flex-direction:column; gap:12px; } }
</style>
