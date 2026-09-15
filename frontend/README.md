# 微信公众号 AI Content Agent —— 前端

这是「微信公众号 AI Content Agent」项目的前端工程，基于 **Vue 3 + Vite + TypeScript**。

前端负责界面展示和调用后端接口，真正的 AI 生成逻辑、API Key 都放在后端。

---

## 一、环境要求

- Node.js >= 18（推荐 20 或更高）
- npm >= 9

查看版本：

```bash
node -v
npm -v
```

---

## 二、快速开始

### 1. 先启动后端

**前端必须依赖后端才能正常使用**，所以请先把后端跑起来。

后端默认地址是 `http://localhost:8000`，接口前缀是 `/api/v1`。
如果后端的端口不是 8000，请修改下面的 `VITE_API_BASE_URL`。

### 2. 安装依赖

```bash
cd frontend
npm install
```

### 3. 配置环境变量

项目里已经有一份可以直接用的 `.env`，内容和 `.env.example` 一样：

```
VITE_API_BASE_URL=http://localhost:8000/api/v1
```

| 变量名 | 说明 | 默认值 |
| --- | --- | --- |
| `VITE_API_BASE_URL` | 后端 API 的基础地址 | `http://localhost:8000/api/v1` |

> 注意：Vite 只会读取以 `VITE_` 开头的变量；修改 `.env` 后需要**重启** `npm run dev` 才会生效。

> ★ **这个变量是构建时静态内联进 JS 的**，不是运行期读取的。
> 也就是说改它必须**重新构建**，光改服务器上的环境变量、重启容器都没有用。
> 生产部署走的是 `VITE_API_BASE_URL=/api/v1`（相对路径），
> 由 `frontend/Dockerfile` 的 `ARG` 传入 —— 详见根目录 README 的
> 「十二、部署到云服务器」。

> ★ 代码里的兜底值是 `/api/v1`（相对路径），见 `src/api/http.ts`。
> 故意不用 `http://localhost:8000` —— 万一构建参数因为某种原因没生效，
> 相对路径仍然是对的，而写死 localhost 会让**每个访客的请求都打到他自己的电脑上**，
> 页面能打开但所有接口静默失败，报错完全指不出原因。

### 4. 启动开发服务器

```bash
npm run dev
```

启动后浏览器访问 <http://localhost:5173>。

**登录账号**：直接用 Django 的超级管理员账号登录即可
（就是执行 `python manage.py createsuperuser` 时设置的用户名和密码）。
也可以点击登录页下方的「去注册」自己注册一个新账号。

---

## 三、常用命令

| 命令 | 说明 |
| --- | --- |
| `npm run dev` | 启动开发服务器（端口 5173，改代码会自动刷新） |
| `npm run build` | 类型检查 + 打包，产物在 `dist/` 目录 |
| `npm run preview` | 本地预览打包后的产物 |
| `npm run type-check` | 只做 TypeScript 类型检查，不打包 |

---

## 四、目录结构

```
src/
  api/          调用后端接口的地方
    http.ts       axios 实例：自动带 token、统一错误处理
    auth.ts       登录 / 注册 / 退出
    agent.ts      Agent 生成、运行进度
    articles.ts   文章增删改查
  components/   可复用的小组件
    AgentProgress.vue   Agent 执行进度时间线
    ArticleEditor.vue   文章编辑器（Markdown 编辑 + 预览）
    SourceList.vue      资料来源列表
    RunStatus.vue       运行状态标签
  layouts/
    MainLayout.vue   左侧菜单 + 顶栏的整体布局
  pages/        一个个页面
    Login.vue           登录 / 注册
    Dashboard.vue       仪表盘（统计 + 最近文章）
    CreateArticle.vue   创建文章
    ArticleDetail.vue   文章详情（编辑 + 资料 + 风险提示）
    ArticleHistory.vue  文章历史（搜索 + 筛选 + 分页）
    Settings.vue        设置
  router/       路由配置（含登录拦截）
  stores/       Pinia 状态管理
  types/        与后端约定的数据类型
  utils/        小工具函数
```

---

## 五、几个需要知道的地方

1. **接口返回值的处理**
   后端统一返回 `{ success, data, message }` 这样的「信封」。
   `src/api/http.ts` 里的响应拦截器已经帮你把信封拆开了，
   所以在页面里写 `const article = await getArticle(id)` 拿到的**直接就是 data**，
   不需要再写 `res.data.data`。

2. **出错时怎么办**
   所有错误都会被转换成 `ApiError`，它的 `message` 是中文的，可以直接弹给用户看：

   ```ts
   try {
     await doSomething()
   } catch (error) {
     ElMessage.error(error instanceof ApiError ? error.message : '操作失败')
   }
   ```

3. **登录状态**
   登录成功后 token 会存到 `localStorage`（key 是 `ai_content_agent_token`），
   之后每个请求都会自动带上 `Authorization: Token <token>` 请求头。
   token 失效（后端返回 401）时会自动清除并跳回登录页。

4. **生成文章为什么要等很久**
   `POST /agent/runs` 是**同步**接口，后端会等 Agent 全部跑完才返回，
   可能要 30~120 秒。所以这个请求单独把超时时间设成了 5 分钟，
   并且生成期间「开始生成」按钮会被禁用，避免重复提交。

5. **Markdown 预览为什么安全**
   预览用 `markdown-it` 把 Markdown 转成 HTML，再用 `DOMPurify` 净化之后才用 `v-html` 渲染。
   AI 生成的内容里可能混有恶意脚本，净化这一步是必须的，不要删掉。

---

## 六、打包部署

```bash
npm run build
```

打包产物在 `frontend/dist/` 目录，把它交给 Nginx 之类的静态服务器托管即可。

如果前端和后端不在同一个域名下，记得在后端开启 CORS，
或者用 Nginx 把 `/api` 反向代理到后端。
