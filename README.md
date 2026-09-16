# 微信公众号 AI Content Agent（V1）

一个**真的会用工具**的公众号内容创作助手。

你告诉它「写一篇关于 2026 年 AI Agent 最新发展的公众号文章」，
它会自己判断要不要查资料 → 自己调用搜索工具 → 自己决定打开哪个网页读原文 →
综合资料写出一篇结构完整的文章 → 把用到的来源交给你核对。
文章生成之后，你还可以让它**生成配图**，再一键**推到公众号草稿箱**。

**它不会自动发布。** 生成的是草稿，推到公众号也是草稿 ——
发布这一下永远由你亲自点。

---

## 目录

- [一、这个项目能做什么](#一这个项目能做什么)
- [二、它是怎么工作的](#二它是怎么工作的)
- [三、技术栈](#三技术栈)
- [四、目录结构](#四目录结构)
- [五、快速开始](#五快速开始)
- [六、环境变量说明](#六环境变量说明)
- [七、数据库表](#七数据库表)
- [八、接口文档](#八接口文档)
- [九、两个核心工具](#九两个核心工具)
- [十、安全设计](#十安全设计)
- [十一、跑测试](#十一跑测试)
- [十二、部署到云服务器](#十二部署到云服务器)
- [十三、常见问题](#十三常见问题)
- [十四、V1 没做的事](#十四v1-没做的事)

---

## 一、这个项目能做什么

| 能力 | 说明 |
| --- | --- |
| 自主研究 | Agent 自己决定搜索什么、搜几次、读哪个页面，不需要你写流程 |
| 真实来源 | 文章引用的每个链接，都必须是它**本次真的检索到或读过**的，编造的一律删除 |
| 结构化产出 | 标题、摘要、开头钩子、大纲、正文、配图建议，一次给全 |
| 过程可见 | 前端能看到「搜索了什么 → 拿到几个来源 → 读了哪篇」，不是黑盒 |
| 风险提示 | 资料不足、工具失败、达到调用上限，都会明确写出来，而不是假装一切正常 |
| 人工编辑 | 生成的是草稿，你可以改标题、改正文、标记状态 |
| AI 配图 | 按文章内容自动给出配图建议，**你决定要不要生成**、生成哪几张 |
| 推送到公众号 | 一键把文章（含配图）推进公众号**草稿箱**，你在手机上确认后再发 |

---

## 二、它是怎么工作的

### 主流程

```
用户填需求（主题 / 读者 / 目标 / 风格 / 篇幅）
        ↓
Django 校验参数，创建 AgentRun 记录
        ↓
★ Agent 自己做决定（这一步是循环的，不是固定流程）
        ↓
   大模型看到需求 ──→ 判断信息够不够
        ↓ 不够
   调用 web_search  ──→ 拿到一批搜索结果（自动登记来源）
        ↓
   大模型看搜索结果 ──→ 判断哪几篇值得细读
        ↓
   调用 read_webpage ──→ 读到网页正文（自动登记来源）
        ↓ 够了
   调用结构化输出工具，返回 ArticleDraft
        ↓
系统校验：文章里的来源 URL 真的出现过吗？
        ↓ 没出现过 → 删掉，并记一条风险提示
Markdown 转安全 HTML（防 XSS）
        ↓
写库：Article（草稿）+ ToolCall + Source + Message
        ↓
前端展示，你人工确认
        ↓
★ 到这里 Agent 的活就干完了。**下面这两步是你点的，不是模型自己决定的**：
        ↓
【可选】按 article.image_suggestions 生成配图（封面 + 正文插图）
        ↓
【可选】推送到公众号草稿箱 → 你在手机上确认 → 手动点发布
```

**关键点：第 4~8 步的循环次数和顺序，是模型自己决定的，不是代码写死的。**
代码只负责给它工具和规矩。这就是 Agent 和「固定工作流」的区别。

> **配图和推送为什么不交给模型自己决定？**
> 因为它们**要花钱**（生图按张计费），而且一旦接进 Agent 循环，
> 就多了一整类「模型在循环里反复画图把额度烧光」的失控可能。
> 加上这两步天然需要人看一眼才知道好不好 —— 交给按钮是最合适的。
> 它们在这张图里是**并列的一条支线**，不在 Agent 的循环体内。

### 三道防失控的闸门

AI 自己决定调用工具，就存在「搜起来没完」的风险。所以有三层保护：

| 闸门 | 配置项 | 默认值 | 超了会怎样 |
| --- | --- | --- | --- |
| 工具调用次数 | `MAX_TOOL_CALLS` | 8 | 停止研究，用现有资料把文章写完，并加风险提示 |
| 单次搜索结果数 | `MAX_SEARCH_RESULTS` | 5（硬上限 10） | 夹到上限 |
| 思考步数 | `AGENT_RECURSION_LIMIT` | 30 | LangGraph 强制中断，返回明确错误 |

**注意：达到上限时不会失败，而是「尽力交付 + 如实告知」。** 这比直接报错有用得多。

> **踩坑记录：这件事比看上去难。**
>
> 第一版我们用的是 LangChain 自带的 `ToolCallLimitMiddleware`，结果发现
> **搜索一旦正常工作，生成反而会失败**：它拦截时注入的话是
> `"Do not make additional tool calls."`，而模型把这句话理解成「那我什么都别做了」——
> 可对模型来说，**「返回文章」本身也是一次工具调用**（结构化输出工具 `ArticleDraft`）。
> 于是它回一条空消息、循环结束、拿不到结构化结果，整次生成判定失败。
>
> 更麻烦的是这个 bug 只在研究量大时才触发，恰恰是最需要它的场景；
> 而假模型测试抓不到它，因为剧本是硬编码的，模型不会「被吓住」。
>
> 现在换成自己的 `ToolBudgetMiddleware`（`apps/agent/agents/tool_budget_middleware.py`）：
> 靠 `wrap_tool_call` 钩子**只对真实工具触发、不对结构化输出工具触发**这一点，
> 精准做到「拦住检索、放行出稿」，并且拦截话术明确告诉模型
> **下一步该做什么**（"基于已有资料直接输出最终文章"），而不只是"不许做什么"。
>
> 好消息是，模型对这句话的服从度很高——它接下来不仅立刻出稿，还会主动把
> "哪几个来源没来得及核实"一条条写进风险提示里。

### 模型忘了调用工具怎么办

真实模型偶尔会这样：**文章其实已经写完了，但它写在了普通消息正文里，
没有调用 `ArticleDraft` 工具。** 这条消息里没有 tool_calls，Agent 循环正常结束，
但拿不到结构化结果 —— 用户白等 30 多秒，最后只看到一句"生成失败"。

这是模型侧的随机行为，我们控制不了，所以改成兜住它：
检测到这种情况时，把已有的对话历史原样喂回去，再补一句

> 请**不要重新搜索、也不要重新写一遍**，直接把你上一条回复里的内容，
> 用 `ArticleDraft` 工具按字段返回一次即可。

模型照做，文章就回来了。多花一次模型调用，但比让用户白等一场好得多。
代价是这种情况会多一次请求，所以只在**已经要失败**的时候才触发。

---

## 三、技术栈

**后端**：Python 3.11+、Django 5.2、Django REST Framework、LangChain 1.x（`create_agent`）、
Pydantic、httpx、BeautifulSoup4、trafilatura、Pillow（图片压缩）、MySQL 8 / SQLite

**前端**：Vue 3、Vite、TypeScript、Vue Router、Pinia、Axios、Element Plus、markdown-it

> **关于 LangGraph**：它已经作为依赖装好了，V1 不直接用。
> `create_agent` 本身就是用 LangGraph 实现的，以后要做「大纲人工确认 → 写作 → 事实核查」
> 这种固定流程时，可以平滑换成 LangGraph 的 `StateGraph`，不用重写业务代码。

---

## 四、目录结构

```
agent_ai_biaoshu/
├── .gitignore                    # ★ 第一条规则就是忽略 .env
├── .gitattributes                # ★ 强制 .sh 用 LF，防止容器里 ^M 报错
├── README.md                     # 你正在看的这份
├── docker-compose.yml            # ★ 部署编排：db + backend + web
├── .env.deploy.example           # ★ 部署配置模板（可提交；真正的 .env 不要提交）
│
├── backend/                      # Django 后端
│   ├── manage.py
│   ├── requirements.txt
│   ├── pytest.ini                # 测试配置（强制用内存 SQLite）
│   ├── conftest.py               # 全局测试 fixture
│   ├── Dockerfile                # ★ 生产镜像（gunicorn）
│   ├── entrypoint.sh             # ★ 自检 → 等库 → migrate → 收集静态 → gunicorn
│   ├── .dockerignore             # ★ 把 .env 挡在镜像外（镜像层能被解开！）
│   ├── .env.example              # ★ 环境变量模板，复制成 .env 再填
│   ├── .env                      # 你的真实密钥（已被 git 忽略，不要提交）
│   │
│   ├── config/                   # 项目配置
│   │   ├── settings.py           # 所有配置都从环境变量读
│   │   ├── settings_test.py      # 测试专用配置
│   │   └── urls.py               # 根路由
│   │
│   └── apps/
│       ├── common/               # 公共设施
│       │   ├── exceptions.py     # 统一业务异常 + 全局异常处理器
│       │   ├── responses.py      # 统一返回信封
│       │   ├── middleware.py     # request_id 中间件
│       │   ├── markdown_renderer.py  # Markdown → 安全 HTML
│       │   ├── pagination.py
│       │   └── utils.py
│       │
│       ├── accounts/             # 登录注册（复用 Django 自带 User）
│       │
│       ├── conversations/        # 会话与消息
│       │   └── models.py         # Conversation / Message
│       │
│       ├── agent/                # ★ Agent 核心
│       │   ├── models.py         # AgentRun / ToolCall / Source
│       │   ├── serializers.py
│       │   ├── views.py          # 接口（很薄，只做校验和转发）
│       │   ├── urls.py
│       │   ├── agents/
│       │   │   ├── content_agent.py           # ★ 用 create_agent 搭 Agent
│       │   │   ├── audit_middleware.py        # 记录每次工具调用
│       │   │   └── tool_budget_middleware.py  # ★ 研究预算：超限就收手，但照样出稿
│       │   ├── services/
│       │   │   ├── model_factory.py    # ★ 全项目唯一读 API Key 的地方
│       │   │   ├── source_registry.py  # ★ 防 AI 编造来源
│       │   │   └── agent_service.py    # ★ 主流程编排
│       │   ├── tools/
│       │   │   ├── url_safety.py       # ★ 防 SSRF
│       │   │   ├── web_search.py       # 工具 1：联网搜索
│       │   │   └── read_webpage.py     # 工具 2：读网页正文
│       │   ├── prompts/
│       │   │   └── content_agent_prompt.py  # System Prompt
│       │   ├── schemas/
│       │   │   └── article.py        # ArticleDraft 结构化输出定义
│       │   └── tests/                # 175 个测试
│       │
│       ├── articles/             # 文章
│       │   ├── models.py         # Article
│       │   ├── services.py       # 所有写操作都在这里
│       │   ├── serializers.py
│       │   └── views.py
│       │
│       ├── imaging/              # ★ AI 配图
│       │   ├── models.py         # GeneratedImage（图片存文件系统，不存库）
│       │   ├── providers.py      # 生图服务，OpenAI 兼容（中转站 / 官方都能接）
│       │   ├── image_utils.py    # ★ 微信硬性尺寸限制：正文图 <1MB、封面 900x500 约 64KB
│       │   ├── services.py       # 额度账本、插入位置计算、封面选择
│       │   ├── views.py / urls.py / serializers.py / admin.py
│       │   └── tests/            # 66 个测试（含落盘、路径安全、压缩）
│       │
│       └── wechat/               # ★ 推送到公众号草稿箱
│           ├── client.py         # ★ stable_token + errcode 中文翻译
│           ├── html_builder.py   # Markdown + 配图 → 公众号能吃的内联 HTML
│           ├── services.py       # 建/更新草稿、图片压缩上传、失败留痕
│           ├── views.py / urls.py / serializers.py / admin.py
│           └── tests/            # 30 个测试
│
└── frontend/                     # Vue 前端
    ├── package.json
    ├── vite.config.ts
    ├── Dockerfile                # ★ 多阶段：node 构建 → nginx 托管
    ├── nginx.conf                # ★ 对外唯一入口，同时反代 /api/ 和 /media/
    ├── .dockerignore             # ★ 挡住本地 .env，否则会写死 localhost
    ├── .env.example
    ├── README.md                 # 前端自己的说明
    └── src/
        ├── api/                  # 接口封装（http.ts 负责拆信封 + 错误翻译）
        ├── stores/               # Pinia 状态
        ├── pages/                # 页面
        ├── components/           # 组件（含 ArticleImagePanel.vue 配图面板）
        ├── types/                # TypeScript 类型
        └── utils/
```

> **注意后端**没有** `media/` 目录**（那是我本地跑出来的，已被 `.gitignore` 忽略）。
> 生产环境里它由 Docker 命名卷 `uploads` 提供，见 12.1 和 12.9。

---

## 五、快速开始

### 0. 先确认环境

```bash
python --version     # 需要 3.11 或更高
node -v              # 需要 18 或更高
```

### 1. 启动后端

```bash
cd backend

# 创建并激活虚拟环境
python -m venv .venv

# Windows:
.venv\Scripts\activate
# macOS / Linux:
# source .venv/bin/activate

# 安装依赖
pip install -r requirements.txt

# 复制环境变量模板
# Windows:
copy .env.example .env
# macOS / Linux:
# cp .env.example .env

# ★ 现在打开 .env，把 LLM_API_KEY 和 LLM_MODEL 填上（见第六节）
#   想用配图和推送公众号，再把 IMAGE_* / WECHAT_* 那两组填上 ——
#   不填也不影响生成文章，配图那里只会显示一句「未配置」的中文提示。

# 建表
python manage.py migrate

# 创建管理员账号（可以登录 /admin 后台看数据）
python manage.py createsuperuser

# 启动
python manage.py runserver 8000
```

看到 `Starting development server at http://127.0.0.1:8000/` 就成功了。
浏览器打开 <http://127.0.0.1:8000/health/>，应该看到：

```json
{"success": true, "data": {"status": "ok"}, "message": ""}
```

### 2. 启动前端

**另开一个终端**（后端要保持运行）：

```bash
cd frontend

npm install

# 如果后端不是 8000 端口，改一下 .env 里的 VITE_API_BASE_URL
# copy .env.example .env

npm run dev
```

浏览器打开终端里提示的地址（默认 <http://localhost:5173>）。

### 3. 生成第一篇文章

1. 打开前端页面，先**注册一个账号**（或直接登录）
2. 点「创建文章」
3. 主题填：`2026 年 AI Agent 最新发展`
4. 其他项可以用默认值，点「开始生成」
5. **耐心等待 30~120 秒** —— 它真的在联网搜索和读网页
6. 生成完会**弹一句「要现在去生成配图吗？」** —— 点「去生成配图」会直接跳到详情页的配图面板；
   点「先不看」就正常进详情页（这一步只影响停在哪个 tab，不影响生成结果）
7. 生成完会自动跳到文章详情页，你能看到正文、引用来源、风险提示

**接下来还能做两件事：**

| 想做什么 | 去哪儿 | 说明 |
| --- | --- | --- |
| 生成配图 | 详情页右侧的「配图」面板 | 按模型的配图建议**逐条**生成（一张 30~90 秒），可以设封面、改图注、改插入位置、删掉重来 |
| 推到公众号 | 详情页页头的「推送到公众号草稿箱」 | **必须先有至少一张配图**（公众号草稿必须有封面），没有时按钮是灰的并会告诉你为什么 |

推完之后去**公众号后台 → 草稿箱**，能看到这篇文章（含正文插图和封面）。
**在手机上确认没问题后再手动点发布** —— 系统永远不会替你发。

如果想看 Agent 到底做了什么，回到首页看「最近的运行」，
里面能看到每一次工具调用的参数和结果预览。

> 本地开发时，图片由 Django 自己托管（`DEBUG=True` 时的 `/media/` 兜底路由），
> 不需要 nginx，也不需要跑 Docker。

---

## 六、环境变量说明

所有配置都在 `backend/.env` 里。**这个文件已经被 `.gitignore` 忽略，不会被提交。**

### 必须填的（不填就跑不起来）

| 变量 | 说明 | 例子 |
| --- | --- | --- |
| `LLM_API_KEY` | 大模型的 API Key | `sk-xxxxxxxx` |
| `LLM_MODEL` | 模型名 | `gpt-4o-mini` / `deepseek-chat` / `qwen-plus` |

### 按需填的

| 变量 | 说明 |
| --- | --- |
| `LLM_PROVIDER` | 默认 `openai`。只要你的服务兼容 OpenAI 接口就填 `openai` |
| `LLM_BASE_URL` | 用国内兼容服务时填。DeepSeek 填 `https://api.deepseek.com/v1` |
| `SEARCH_PROVIDER` | `tavily`（推荐）或 `google`（正在下线，见下）。不填或 `SEARCH_API_KEY` 为空时 Agent 拿不到外部资料，会在风险提示里如实说明 |
| `SEARCH_API_KEY` | 搜索服务密钥 |
| `SEARCH_ENGINE_ID` | **只有存量 Google 引擎需要**，见下面的「关于联网搜索」 |
| `SEARCH_LANGUAGE` | 结果语言限制。留空 = 不限制（推荐，prompt 已要求英文为主） |
| `SEARCH_BASE_URL` | 一般留空，自动用各家默认地址。**切换 provider 时记得清空**，否则会把请求打到上一家的地址上（代码里有防呆，配错会忽略并打日志） |
| `DB_ENGINE` | `mysql` 或 `sqlite`，见下 |
| `DJANGO_SECRET_KEY` | 生产环境必须换成随机字符串 |

### AI 配图（不填不影响生成文章，只影响配图）

**文生图走的是 OpenAI 兼容接口**，所以任何兼容 OpenAI 的中转站 / 官方 API 都能用。

| 变量 | 说明 |
| --- | --- |
| `IMAGE_API_KEY` | 生图服务的密钥 |
| `IMAGE_BASE_URL` | 生图服务的地址，例如 `https://<中转站域名>/v1`。**代码刻意不回退到 OpenAI 官方地址** —— 中转站和官方的模型名、计费都不一样，猜错了只会让你收到一个看不懂的 404 |
| `IMAGE_MODEL` | 模型名，照中转站实际提供的名字填（例如 `gpt-image-1`、`dall-e-3`） |
| `IMAGE_SIZE` | 出图尺寸，默认 `1024x1536`（竖图，适合公众号） |
| `IMAGE_QUALITY` | `low` / `medium` / `high`，默认 `medium` |
| `IMAGE_OUTPUT_FORMAT` | `jpeg` 或 `png`，默认 `jpeg` |
| `IMAGE_TIMEOUT` | 单张图的超时秒数。生图比生文慢得多，默认 `300`（前端也是 300 秒，正好对齐） |
| `IMAGE_STYLE_PROMPT` | **统一画风提示词**。会拼在每条配图建议前面，用来让整篇文章的图风格一致 |
| `IMAGE_MAX_PER_ARTICLE` | 单篇文章最多几张配图，默认 `8` |
| `IMAGE_DAILY_QUOTA_PER_USER` | 每人每天最多生成几张，`0` = 不限制 |
| `IMAGE_DAILY_QUOTA_GLOBAL` | 全站每天最多生成几张，`0` = 不限制 |

> **上面三个 `IMAGE_*` 只要缺一个，配图功能会退化成一个「未配置」的中文提示，
> 而不会让服务崩掉** —— 生成文章这条主链路完全不依赖配图。
> 但**每日额度建议一定要设**：生图按张计费，比生文贵得多（见 12.5）。

### 推送到公众号（不填 = 推送按钮报「未配置」）

| 变量 | 说明 |
| --- | --- |
| `WECHAT_APP_ID` | 公众号后台 → 开发 → 基本配置里的 AppID |
| `WECHAT_APP_SECRET` | 同一页的 AppSecret。**这个泄露等于公众号被人接管**，只放服务器上 |
| `WECHAT_TIMEOUT` | 调微信接口的超时秒数，默认 `30`（上传图片比取 token 慢） |
| `WECHAT_AUTHOR` | 草稿里的「作者」一栏，一般填你的公众号名 |
| `THROTTLE_WECHAT_PUBLISH` | 推送的频率上限，默认 `30/hour`。**只作用于推送**，查推送历史不受影响 |

> ★ 还需要在公众号后台做两件**微信侧**的配置，代码这边无能为力：
> **① 把服务器公网 IP 加进「IP 白名单」**（否则微信返回 `40164`）；
> **② 确认接口权限里有「草稿箱」**（已认证订阅号默认就有）。
> 详见 12.11 的排查表。

### 复制到公众号（**不需要任何公众号配置**）

详情页的「复制到公众号」把正文 + 配图拼成一份富文本放进剪贴板，
你粘进公众号编辑器就是排好版的内容，然后手动填标题、手动发布。

> ★ 这条路的全部意义就是**不依赖上面那组 `WECHAT_*` 配置**：
> 没有 AppSecret、IP 没进白名单、号没有草稿权限（`48001`），全都不影响。
> 它也不需要文章有配图 —— 只复制文字是完全正当的用法。

只有一个变量，**不填也能用**（只是没有图）：

| 变量 | 说明 |
| --- | --- |
| `MEDIA_PUBLIC_BASE_URL` | 配图的对外基地址，就是网站本身的地址，例如 `https://你的域名`。**要和网站同源，别带端口号** |

> ★ 为什么必须显式配：复制出去的 HTML 会被粘到 `mp.weixin.qq.com` 上，
> 相对路径在那里会被解析成 `mp.weixin.qq.com/media/...`，图必裂。
> 而地址**不能**从请求头猜 —— nginx 的 `proxy_set_header Host $host` 拿到的是
> 请求方给的值、而且不带端口，猜出来的地址既不可信也不完整。
> 症状和"图片被浏览器拦掉"一模一样，极难分辨。
>
> ★ 没填时后端**会跳过所有配图**并在界面上提示你去填，正文照常复制 ——
> 宁可少图，也不要给你一墙裂图。
>
> ★ **上了 HTTPS 之后这一项必须跟着变成 `https://`**：页面是 https 而图片是 http 的话，
> 浏览器会按**混合内容**把它们全部拦掉。这正是 12.10 要解决的问题之一。
> 改这一项只需要 `docker compose up -d backend`，**前端一个字都不用动**。

> ★★ **图片能不能粘过去，取决于公众号编辑器**：如果它在粘贴时把图**重新上传**
> 到微信自己的服务器（秀米/135 那些排版工具就是这么工作的），那纯 HTTP 也能用；
> 如果它只在浏览器里渲染外链图，那 `https://` 页面里的 `http://` 图片会被浏览器
> 按混合内容拦掉。**正文文字一定没问题**，图片请以实际粘贴结果为准。

### 关于联网搜索

**推荐 Tavily，不要选 Google。** 原因见下面的「为什么不用 Google」。

**方案 A：Tavily（推荐）**

去 <https://tavily.com> 注册，免费额度够个人开发用，只要**一个**凭据。

```bash
SEARCH_PROVIDER=tavily
SEARCH_API_KEY=你的API Key
SEARCH_BASE_URL=          # 留空即可
```

> 注意别把控制台网页地址 `https://app.tavily.com` 填进 `SEARCH_BASE_URL`，
> 那是网页不是 API。代码里对这一条有专门的检测和提示。

**方案 B：Google（新项目走不通，别试）**

> **⚠️ 这不是配置问题，是 Google 单方面关闭了。**（2026-09 实测确认）
>
> **现象**：API 已启用、计费已开通、Key 有效、cx 正确，调用仍然返回
> ```
> 403 PERMISSION_DENIED
> "This project does not have the access to Custom Search JSON API."
> ```
>
> **原因**：Custom Search JSON API 自 2026 年起**对新客户关闭**
> （Google 文档原文：*closed to new customers / not available for new customers*），
> 2027-01-01 彻底停用。叠加的另一条是：自 **2026-01-20** 起，
> **新建的搜索引擎不能再选「搜索整个网络」**，只能用「Sites to search」，最多 50 个域名。
>
> **关键：用户端无法解决。** 论坛里有人试过重新启用 API、等待 24 小时、
> 改用 OAuth、换项目、换 Key，全部得到同一个 403。改配置、重新申请都是白费功夫 ——
> 这不是你漏了哪一步。
>
> 只有**当年就配成整网搜索的存量账号**还能继续用，但也只到 2027-01-01。
>
> 官方说明：<https://support.google.com/programmable-search/answer/12397162>
> · 同类问题讨论：<https://discuss.google.dev/t/347093>

代码里对这条有专门识别（`search_provider_no_entitlement`），
提示会直接告诉你"改配置没用、请换服务"，不会把你引到改 API 限制的死路上绕圈。

如果你**确实**持有当年的整网引擎，仍然可以这样配：

```bash
SEARCH_PROVIDER=google
SEARCH_API_KEY=你的API Key      # console.cloud.google.com/apis/credentials
SEARCH_ENGINE_ID=你的cx
SEARCH_BASE_URL=                # ★ 一定要清空这行
```

> 免费额度 100 次查询/天，用完返回 403；系统会把这个错误翻译成
> 「每日免费额度已用完」而不是让你对着一个 `403` 猜。

**英文优先是怎么实现的？**

主要靠 **Prompt 层**：System Prompt 里有专门一节要求
「先发 2~3 个英文查询，再按需补 1 个中文查询」，
工具描述里也写了同样的引导。搜索结果用什么语言不影响写作 ——
读英文资料，用中文成文，sources 里保留原始英文标题。

另外还有两个可选参数：

| 变量 | 作用 |
| --- | --- |
| `SEARCH_LANGUAGE` | 填 `lang_en` 就是硬性只要英文网页（只对 Google 生效）。**一般不建议** —— 那样中文补充查询也会被过滤掉 |
| `SEARCH_BASE_URL` | 一般留空。**切换 provider 时记得清空**，否则会把请求打到上一家的地址上（代码里有防呆，配错会忽略并打日志） |

### 关于数据库

| 方案 | 什么时候用 | 怎么配 |
| --- | --- | --- |
| **SQLite** | 本地开发、想立刻跑起来 | `DB_ENGINE=sqlite`，其余不用管 |
| **MySQL** | 正式使用 | `DB_ENGINE=mysql`，然后填 `DB_NAME` / `DB_USER` / `DB_PASSWORD` / `DB_HOST` / `DB_PORT` |

> **⚠️ MySQL 版本要求：Django 5.2 需要 MySQL 8.0.11 或更高。**
> 如果你本机装的是 MySQL 5.7，会因为版本过低无法连接（这是 Django 的硬性要求，不是配置问题）。
> 两个选择：升级到 MySQL 8，或者先用 `DB_ENGINE=sqlite` 把项目跑起来。
>
> 建库语句：
> ```sql
> CREATE DATABASE ai_content_agent CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
> ```
> 必须是 `utf8mb4`，否则存中文和 emoji 会出问题。

---

## 七、数据库表

用 `python manage.py migrate` 自动创建。

### `agent_run` —— 一次生成任务

| 字段 | 说明 |
| --- | --- |
| `id` | UUID 主键，对外叫 `run_id` |
| `conversation_id` | 属于哪个会话 |
| `status` | `pending` / `running` / `completed` / `failed` / `cancelled` |
| `input_text` | 实际发给模型的完整输入（方便复盘） |
| `model_name` | 这次用的模型 |
| `tool_call_count` | 调用了几次工具 |
| `error_message` | 失败时的中文提示（**不含堆栈和密钥**） |
| `started_at` / `completed_at` | 耗时统计 |

### `tool_call` —— 每一次工具调用

| 字段 | 说明 |
| --- | --- |
| `agent_run_id` | 属于哪次运行 |
| `tool_name` | `web_search` / `read_webpage` |
| `arguments` | 调用参数（JSON） |
| `status` | `running` / `success` / `failed` / `blocked` |
| `result_preview` | **只存摘要，最多 500 字**，不存网页全文 |

> `blocked` 表示"模型申请调用、但被研究预算拦下、**根本没执行**"。
> 单独一个状态而不是记成 `failed`，是因为两者含义不同：混在一起会让风险提示
> 同时冒出"有 N 次调用失败"和"达到调用上限"两条，看起来像工具坏了。
> 前端时间线上它也照样显示，用户能看出研究是在哪一步被截断的。
| `duration_ms` / `sequence` | 耗时、顺序 |

### `source` —— 检索到的来源

| 字段 | 说明 |
| --- | --- |
| `agent_run_id` | 属于哪次运行 |
| `url` / `url_hash` | 原始地址和它的 sha256（唯一约束用哈希，因为 URL 太长建不了索引） |
| `domain` | 域名 |
| `snippet` | **摘要，最多 500 字** |
| `source_type` | `search`（搜到的）/ `webpage`（真的读过全文的） |
| `is_cited` / `usage` | 文章有没有引用它、用来支撑什么观点 |

### `article` —— 文章

| 字段 | 说明 |
| --- | --- |
| `title` / `subtitle` / `summary` / `hook` / `outline` | 标题、副标题、摘要、开头钩子、大纲 |
| `content_markdown` | 正文原始 Markdown |
| `content_html` | 渲染并**清洗过**的 HTML（可以直接粘进公众号后台） |
| `image_suggestions` | 配图建议 |
| `source_ids` | 引用的来源 id 列表 |
| `risk_notes` | 风险提示（模型给的 + 系统检测到的） |
| `status` | `draft` / `review` / `approved` / `archived` |
| `version` | 每次编辑 +1 |
| `model_name` / `tool_call_count` | 溯源信息：这篇是怎么生成的 |

### `conversation` / `message` —— 会话与消息

记录每一轮对话。`Message.message_type` 区分 `text` / `tool_call` / `tool_result` / `article`。

### `imaging_generatedimage` —— 一张 AI 配图

| 字段 | 说明 |
| --- | --- |
| `id` | UUID 主键。**这个 id 本身就是一条访问凭据**，见下 |
| `article_id` / `user_id` | 属于哪篇文章、哪个人（冗余一份 user 是为了每日额度统计不用 join） |
| `suggestion_index` / `suggestion` | 来自文章的第几条配图建议、建议原文 |
| `prompt` / `revised_prompt` | **实际**发给生图模型的提示词、模型改写后的版本 |
| `status` | `pending` / `running` / `succeeded` / `failed` |
| `image_file` | 图片在 `MEDIA_ROOT` 下的相对路径，形如 `images/2026/09/<32位uuid4>.jpg` |
| `content_type` / `width` / `height` / `byte_size` | 图片元信息（宽高读不出来时是 NULL，不阻断流程） |
| `caption` | 图注 |
| `anchor_index` / `sort_order` | 插在第几个 `## ` 小节之后、同位置的先后顺序 |
| `is_cover` | 是否作为公众号封面（**每篇最多一张**，由服务层保证） |
| `provider` / `model_name` / `duration_ms` | 排查用 |
| `wechat_url` | 上传到微信后拿到的 `mmbiz.qpic.cn` 地址。★ 微信**不允许正文图外链**，所以传过一次就存下来复用 |
| `error_message` | 失败时的中文提示 |

> **图片不在数据库里，在磁盘上。** 具体落在 Docker 命名卷 `uploads`，
> 生产由 nginx 直接发 `/media/`。**这意味着备份要连卷一起打包**，见 12.9。
>
> ★ **取图路径的安全性**：文件名里是一段**独立于主键**的 uuid4。
> 这不是随手加的随机数，而是这条链路上**唯一的访问控制** ——
> 浏览器的 `<img src>` 带不了 `Authorization` 头，取图接口只能是公开的，
> 所以「路径猜不出来」就是全部的安全性。刻意不把记录 id 编进路径。

### `wechat_wechatpublish` —— 一次「推送到公众号草稿箱」

| 字段 | 说明 |
| --- | --- |
| `id` | UUID 主键 |
| `article_id` / `user_id` | 推的哪篇、谁推的 |
| `status` | `running` / `succeeded` / `failed` |
| `action` | `add`（新建草稿）/ `update`（更新草稿） |
| `media_id` | 微信草稿 id。**只有成功的那次才写** —— 下次推送靠它走 `draft/update` |
| `content_html` | 本次**真正发出去的那份** HTML 快照 |
| `title` / `image_count` | 标题、这次带了几张正文图 |
| `error_code` / `error_message` | 微信原始的 errcode 和中文说明（`40164` 白名单、`48001` 没权限…） |

> **为什么要一条条存，而不是在 `article` 上放两个字段？**
>
> 1. 用户会**反复推**（改一版推一次），只存最后一次就查不到「上次到底推没推成功」；
> 2. 微信的错误信息很有价值，存下来才能在界面上直接告诉用户上次为什么失败；
> 3. 存了 `media_id` 才能做到「再推一次是**更新同一份草稿**，不是又新建一份」。
>
> `content_html` 存快照是因为**公众号的渲染和浏览器差得很远**。
> 用户说「排版乱了」时，必须能拿到当时真正发出去的那份 HTML ——
> 用现在的文章内容重新渲染一遍是没用的，文章可能已经被改过了。

---

## 八、接口文档

统一前缀 `/api/v1`，统一认证方式 `Authorization: Token <你的token>`。

### 返回格式

成功：
```json
{ "success": true, "data": { ... }, "message": "" }
```

失败：
```json
{
  "success": false,
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "请填写文章主题。",
    "request_id": "1c4021fe"
  }
}
```

> 报错时把 `request_id` 给后端，能直接在日志里定位到这一次请求。
> **失败响应里永远不会有堆栈、文件路径、API Key。**

### 接口列表

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/auth/register` | 注册，直接返回 token |
| POST | `/auth/login` | 登录，返回 token |
| POST | `/auth/logout` | 退出（删掉 token） |
| GET | `/auth/me` | 当前用户信息 |
| **POST** | **`/agent/runs`** | **★ 创建生成任务（同步，会等 30~120 秒）** |
| GET | `/agent/runs` | 我的历史运行记录 |
| GET | `/agent/runs/{run_id}` | 某次运行的状态 |
| GET | `/agent/runs/{run_id}/tool-calls` | 工具调用过程（前端时间线） |
| GET | `/agent/runs/{run_id}/sources` | 这次检索到的所有来源 |
| GET | `/agent/conversations` | 我的会话列表 |
| GET | `/agent/conversations/{id}/messages` | 会话里的消息 |
| GET | `/articles` | 文章列表（支持 `?status=` `?keyword=` `?page=`） |
| GET | `/articles/stats/` | 首页统计 |
| GET | `/articles/{id}` | 文章详情 |
| PATCH | `/articles/{id}` | 编辑文章 |
| DELETE | `/articles/{id}` | 归档（软删除） |
| GET | `/articles/{id}/sources` | 这篇文章引用的来源 |
| GET | `/articles/{id}/images` | 这篇文章的配图列表（含 `section_count`：正文有几个 `## ` 小节，前端靠它画插入位置） |
| **POST** | **`/articles/{id}/images`** | **★ 生成一张配图（同步，一张 30~90 秒）** |
| PATCH | `/images/{image_id}` | 改图注 / 改插入位置 / 设为封面 |
| DELETE | `/images/{image_id}` | 删掉这张配图（**磁盘上的文件一起删**） |
| GET | `/images/{image_id}/file` | 兜底取图（正常图片走 `/media/` 由 nginx 直发，这条只用于排查） |
| **POST** | **`/articles/{id}/wechat-publishes`** | **★ 推送到公众号草稿箱（同步，含上传图片）** |
| GET | `/articles/{id}/wechat-publishes` | 推送历史（**不含** `content_html`，最新在前） |
| GET | `/wechat-publishes/{publish_id}` | 单次推送详情（**含** `content_html` 快照） |
| GET | `/articles/{id}/wechat-html` | 「复制到公众号」用的正文 HTML（**不碰微信接口、没有任何前置条件**，详见下文） |

**配图接口的三个要点：**

1. **POST 是同步的**，一张图 30~90 秒。前端给这个接口单独设了 **5 分钟**超时 ——
   用默认的 30 秒必定被掐断，而图其实还在生成。
2. **PATCH `/images/{id}` 里 `is_cover=true` 会自动把同文章的其他图取消封面**，
   不用前端先遍历一遍把别人置 false。
3. **`anchor_index` 超出范围直接 400**（合法范围 `0 ~ section_count`），
   而不是被悄悄夹到边界上 —— 静默纠正会让「图跑到别的小节下面」变成一个查不出来的 bug。

**推送接口的两个要点：**

1. **这篇文章必须有至少一张 `succeeded` 的配图，否则 400。**
   微信的 `draft/add` 要求 `thumb_media_id`（封面）必填，没有封面图**根本推不出去**。
   与其让它到微信那边失败、返回一个看不懂的错误码，不如在这里就说清楚要做什么。
   → 前端在没有配图时应该**直接禁用按钮并说明原因**，而不是等报错。
2. **重复推送默认是「更新同一份草稿」**，草稿箱里始终只有一份 ——
   用户「改一版推一次」不会攒出一堆重复草稿。
   上一次的草稿如果被你在后台删了（微信返回 `40007`），会自动退回新建一份。

### 常用错误码

| code | HTTP | 含义 |
| --- | --- | --- |
| `VALIDATION_ERROR` | 400 | 参数不对，`message` 里有中文说明 |
| `UNAUTHENTICATED` | 401 | 没登录或 token 过期（前端会自动跳登录页） |
| `NOT_FOUND` | 404 | 资源不存在**或不属于你**（故意不区分，避免泄露 id 是否存在） |
| `CONVERSATION_NOT_FOUND` | 400 | 指定的会话不存在或不属于你 |
| `AGENT_EXECUTION_FAILED` | 502 | Agent 执行失败 |
| `MODEL_PROVIDER_ERROR` | 502 | 大模型调用失败（Key 不对 / 余额不足 / 网络不通） |
| `STRUCTURED_OUTPUT_ERROR` | 502 | 模型没按格式返回文章 |
| `AGENT_LIMIT_EXCEEDED` | 429 | 超过调用限制 |
| `IMAGE_PROVIDER_ERROR` | 502 | 生图服务调用失败（没填配置 / Key 不对 / 余额不足 / 内容审核拒绝） |
| `IMAGE_LIMIT_EXCEEDED` | 400 | 超过单篇文章的配图上限 |
| `IMAGE_QUOTA_EXCEEDED` | 429 | 超过每日配图额度 |
| `WECHAT_NOT_CONFIGURED` | 400 | 没填 `WECHAT_APP_ID` / `WECHAT_APP_SECRET` |
| `WECHAT_PRECONDITION_FAILED` | 400 | 前置条件不满足（例如还没有配图） |
| `WECHAT_PUBLISH_ERROR` | 502 | 微信返回了错误，`message` 里是**微信自己的中文说明**，原样透传 |

> **两条要特别说明的：**
>
> - **配图「没配置」和「调用失败」是同一个码 `IMAGE_PROVIDER_ERROR`（502）。**
>   这是刻意的：`IMAGE_API_KEY` / `IMAGE_BASE_URL` / `IMAGE_MODEL` 三个缺任何一个，
>   服务层会退化成 `NullImageProvider` —— 它不崩，只在 `message` 里**点名缺的是哪个变量**。
>   所以看到这条时先读 `message`，它通常已经告诉你答案了。
> - **`WECHAT_PUBLISH_ERROR` 里是微信自己的原话。**
>   微信的报错（`40164` IP 不在白名单、`48001` 没接口权限…）
>   都是你去公众号后台点两下就能解决的。糊成一句「推送失败」等于让你来问我们，
>   所以这里刻意原样透传。

---

## 九、两个核心工具

> **注意「工具」在这里是个专门的词**：它指的是**模型自己能决定要不要调用**的东西。
> 全项目只有下面这两个 —— 它们由 LLM 在推理过程中自主发起。
>
> **配图和推送公众号不是工具**，是普通的 REST 接口，由**你点按钮**触发。
> 这个区别是有意的：让模型自己决定「要不要花钱生成一张图」，
> 既不可控也不好排查，而且很容易在循环里把额度烧光。

### `web_search` —— 联网搜索

- 输入关键词，返回标题 + 链接 + 摘要
- **每一条结果都会自动登记进「来源登记簿」**，这是后面防伪造的基础
- 搜索服务挂了不会抛异常，而是返回错误码，Agent 会基于现有信息继续写作（场景 E）
- 结果条数被双重夹紧：`max(1, min(请求值, MAX_SEARCH_RESULTS, 10))`

### `read_webpage` —— 读取网页正文

处理顺序（每一步都有原因）：

```
URL 安全校验      → 防 SSRF，只允许 http/https，禁内网
发起请求          → 带超时、带 UA
检查 Content-Type → 只读文本，不下载 exe/zip/图片
限制响应体大小    → 边下边数，超过 2MB 就断开
提取正文          → trafilatura 为主，BeautifulSoup 兜底
清理噪音          → 去掉导航栏、脚本、菜单
长度截断          → 最多 2 万字符，防止撑爆模型上下文
登记来源          → 标记为「读过全文」
```

**绝不把原始 HTML 丢给模型，也绝不把网页全文存进数据库。**

---

## 十、安全设计

这一节是这个项目最重要的部分。

### 1. 密钥永远不出后端

- 全项目**只有 `apps/agent/services/model_factory.py` 一个文件**允许读 `LLM_API_KEY`
- 其他任何地方都只能 `model = get_chat_model()`
- 前端**不知道任何密钥**，也不直接访问任何大模型 API
- `.env` 被 `.gitignore` 忽略；`ToolCall` 记录里如果出现 `sk-` 会被自动打码

### 2. 防 AI 编造来源（本项目最有价值的设计）

问题：模型很擅长编一个「看起来很真」的链接，比如
`https://fake-example-123456.com/article`。

解法：**SourceRegistry（来源登记簿）**

```
工具每搜到一个结果、每读成功一个页面
        → 登记进本次运行的登记簿（只记 URL，不记全文）

文章生成完
        → 逐条核对 draft.sources 里的 URL
        → 登记簿里没有的，直接删除
        → 只要删过，就加一条风险提示告诉用户
        → 最终只有校验通过的来源才会写进 Article.source_ids
```

实测效果（真实链路验证）：

```
模型返回了 1 个伪造来源
→ 接口返回 risk_notes:
   ["模型返回了 1 个未经检索验证的来源，这些来源已被系统移除，相关引用请谨慎对待。"]
→ 文章的 sources = []
```

**而且：上一次运行搜到的 URL，在这一次运行时不算数**，必须重新检索到才有效。

### 3. 防 SSRF（让别人的服务器去访问我们内网）

`read_webpage` 是唯一会「主动访问外部地址」的地方，也是最大的风险点。三道防线：

1. **协议白名单** —— 只允许 `http` / `https`。`file://`、`ftp://`、`javascript:` 全部拒绝
2. **域名黑名单** —— `localhost`、`*.internal`、`*.local`、`metadata.google.internal` 等
3. **解析后再查 IP** —— ★ 光看域名是防不住的。一个叫 `evil-looking-domain.com` 的域名
   完全可以在 DNS 里解析到 `192.168.1.1`。所以必须在**真正解析出 IP 之后**再检查一遍，
   私有地址（10./172.16./192.168./127./169.254.）一律拒绝

**重定向也必须逐跳重新校验。** 否则攻击者可以用一个正常域名 302 跳转到
`http://169.254.169.254/`（云服务器的密钥接口）来绕过前面所有检查。
所以 `read_webpage` 没有用 httpx 的 `follow_redirects`，而是自己写了重定向循环，
**每一次跳转都重新过一遍安全校验**。

### 4. 前端拿不到敏感信息

- `error_message` 只存中文提示，完整堆栈只进服务器日志
- 全局异常处理器兜底：未知异常一律返回 `INTERNAL_SERVER_ERROR`，不泄露任何细节
- 日志里不记录 API Key、Authorization 头、用户密码

### 5. 不存第三方内容全文

- `ToolCall.result_preview` 最多 500 字
- `Source.snippet` 最多 500 字
- `read_webpage` 读到的全文**只存在于这一次 Agent 执行的内存里**，用完即弃

### 6. 防越权

所有查询强制带 `user=request.user`。访问别人的文章返回 **404 而不是 403** ——
连「这个 id 存不存在」都不告诉对方。

### 7. 防 XSS

模型生成的 Markdown 渲染成 HTML 后，用 `nh3` 清洗一遍，
`<script>`、`onclick` 之类全部去掉，外链自动加 `rel="noopener noreferrer"`。
前端再用 DOMPurify 洗第二遍（双保险）。

### 8. 绝不自动发布

`create_article_from_draft` 里写死了 `status=Article.Status.DRAFT`。
即使你说「帮我写文章并直接发布」，V1 也只会生成草稿然后回一句
**「文章已生成草稿，等待用户确认」**。这条是产品红线，不是配置项。

**推送到公众号沿用了同一条红线**：调用的接口是 `draft/add` / `draft/update`，
只**建草稿**，永远不碰 `freepublish`。草稿建好之后，
必须由你在公众号后台（手机上也行）人工确认后点发布。

> 这不是我们偷懒 —— 个人主体订阅号从 2025-07 起在 API 层面就拿不到
> `freepublish/submit` 的权限了。**产品红线和平台限制正好指向同一个做法。**

### 9. 配图的路径就是访问凭据

浏览器的 `<img src>` **带不了 `Authorization` 头**，所以取图接口只能是公开的。
这条链路上唯一的访问控制就是「路径猜不出来」：

- 文件名里是一段**独立于主键**的 uuid4（`images/2026/09/<32位>.jpg`）
- **刻意不把记录 id 编进路径** —— 那样一旦有人为了 admin 方便把主键换成
  自增或时间有序的 UUID，整个 media 目录就同时变成可枚举的，**而且不会有任何报错**，
  也不会有任何测试变红
- `/media/` 和网站**同源**，所以扩展名走白名单（`.svg` / `.html` 一律改写成 `.jpg`）——
  否则往那个目录里塞一个 `<script>` 就是一个存储型 XSS

### 10. AppSecret 和 API Key 一样对待

`WECHAT_APP_SECRET` 泄露等于公众号被人接管。所以它：

- 只在服务器 `.env` 里（`chmod 600`），**永远不进 Git**
- 只在后端使用，**前端拿不到**（前端只发一个「请推送」的请求）
- 不进日志、不进任何 API 响应

`access_token` 也一样：用 `/cgi-bin/stable_token` 在服务端取，
**绝不缓存到前端**，也绝不花钱买「中控服务器」——
多 worker 下老的 `/cgi-bin/token` 会互踢导致偶发 `40001`，
`stable_token` 就是微信为这个问题提供的正解。

### 11. 传输层必须是 HTTPS

上面 10 条防的都是「密钥/凭据别泄漏出去」，但**只要传输层是明文，全都白搭**：
用户的 token 存在浏览器 localStorage 里，每个请求都以
`Authorization: Token xxx` 明文发送，同一个 WiFi 下谁都能抓走并冒充登录。

所以 **HTTPS 不是「有空再做的优化」，是这个项目能拿出去用的前提**。
部署时必须走 12.10 那条路（域名 + Let's Encrypt + `USE_HTTPS=True`），
不能停在「只有 IP 的 8080」上。

> ★ 顺带一提，这也是「复制到公众号」的配图能不能显示的前提：
> `https://` 页面里的 `http://` 图片会被浏览器按混合内容拦掉。

---

## 十一、跑测试

```bash
cd backend
.venv/Scripts/python.exe -m pytest        # Windows
# .venv/bin/python -m pytest              # macOS / Linux
```

测试**完全离线**，不需要 API Key，不花钱，大约 10 秒跑完。

```
271 passed
```

覆盖内容：

| 文件 | 数量 | 测什么 |
| --- | --- | --- |
| `test_url_safety.py` | 37 | SSRF 防护：各种协议、内网 IP、DNS 解析后仍是内网、IPv6 绕过 |
| `test_tools.py` | 44 | 两个工具的成功/失败/超时/体积限制/重定向到内网/正文提取；两个搜索 Provider 的错误码翻译、语言参数、provider 选择与防呆 |
| `test_source_registry.py` | 22 | 防伪造来源、URL 归一化、去重 |
| `test_agent_service.py` | 27 | **端到端**：真实 LangChain 循环 + 假模型 + 假搜索；每日额度（单人 / 全站 / 跨零点重置） |
| `test_api.py`（agent） | 45 | 接口、参数边界、越权访问、错误信封；关闭注册、额度 429、限流文案是中文 |
| `imaging/test_storage.py` | 16 | **配图落盘**：文件真的写进 `MEDIA_ROOT`、路径里是独立 uuid4、扩展名白名单（`.svg`/`.html` 被打回 `.jpg`）、删记录和级联删文章都会删文件、文件丢了 `read_bytes()` 返回 `b""` 而不抛异常 |
| `imaging/test_api.py` | 32 | 配图接口：逐张生成、单篇上限、每日额度（**并断言额度挡住时生图服务一次都没被调用**）、设封面会取消其他封面、`anchor_index` 越界 400 |
| `imaging/test_image_utils.py` | 18 | 微信图片压缩：正文图压到 1MB 以内、封面裁成 900×500 的 JPG 且 ≤64KB、RGBA 透明区铺**白**底 |
| `wechat/test_api.py` | 30 | 推送草稿箱：未配置 / 没配图时的中文引导、正文图**上传一次后复用**、重复推送走 `draft/update`、草稿被删（`40007`）自动退回新建、微信原始错误码原样透传、历史列表**不含** `content_html` |

**怎么做到不花钱的？** 见 `apps/agent/tests/fakes.py`：
用 `ScriptedChatModel` 按剧本回放模型的回答，但**工具调用、中间件、来源校验、
数据库写入全部是真的**。所以测出来的可信度远高于「把所有东西都 mock 掉」。

配图这条链路同理：测试里把 `get_image_provider` 换成假 provider，
**一场测试都不许真的打中转站**；微信那边换成 `FakeWeChatClient`，
连 `draft/add` 的请求体长什么样都能断言。

而且有一个安全网：**任何测试如果忘了打桩，会立刻失败而不是偷偷去调付费大模型。**

> ★ 跑测试**不会**在你本地的 `backend/media/` 里留下任何图片：
> `settings_test.py` 把 `MEDIA_ROOT` 指到临时目录，每个用例还各自再隔离一层。
> 这个属性是**实测过的**（跑完 271 条之后 `backend/media/` 仍然不存在），
> 不是「应该没问题」。

其他检查：

```bash
python manage.py check                 # 配置检查
python manage.py makemigrations --check # 确认模型和迁移文件一致
cd ../frontend && npm run build         # 前端类型检查 + 打包
```

> **`makemigrations --check` 不能省。** Django 建测试库时会带 `run_syncdb=True`，
> 也就是说**即使没有迁移文件，测试也照样全绿** —— 而生产环境
> 跑的是 `migrate`，会真实地少一张表。这个差异只在部署那一刻才暴露。

---

## 十二、部署到云服务器

这一节假设：**Ubuntu / Debian，有域名，走 HTTPS，给一小群人用。**

> ★ 域名 + HTTPS 不是"锦上添花"，是**必需品**：只有 IP 的话登录凭证是明文在
> 公网上跑的（详见 12.10 开头的解释）。所以部署流程里签证书那一步不能跳。
>
> ★ 服务器在**境外**的话不需要 ICP 备案，80/443 直接用；**境内**服务器必须备案
> 之后 80/443 才通，没备案就只能先用 8080 这样的高位端口。
>
> ★ 本文档的部署拓扑是**两层**：前面的宝塔 nginx 终结 TLS、持有证书，
> 我们的容器只在 `127.0.0.1:8080` 上讲明文 HTTP，由宝塔反代进来。
> 12.10 有完整说明。**容器本身不碰证书，也不需要证书文件才能启动** ——
> 这是走宝塔这条路带来的一个实际好处：`docker compose up` 不会再因为
> 证书没签好而卡在 `Restarting`。
>
> ★ 确实没有域名、只能纯 HTTP 跑（临时测试）？把 `.env` 的 `USE_HTTPS`
> 设成 `False`、`ALLOWED_HOSTS` 加上 `<公网IP>`，浏览器直接开
> `http://<公网IP>:8080` 即可（`WEB_BIND` 这时要临时改成 `0.0.0.0`，
> 用完记得改回来 —— 理由见 12.10「一个容易埋雷的地方」）。12.4 里有说明。

### 12.1 部署后长什么样

```
   浏览器
     │
     │  https://<域名>   ← 证书在这一层
     ▼
┌──────────────────────────────────────────────────────────┐
│  宝塔 nginx（宿主机，独占 80 / 443）                        │
│    ├─ 你的域名          → 反代 http://127.0.0.1:8080      │
│    └─ 同一台机器上别的站  → 原样不动（见 12.10 开头）        │
└──────────────────────────┬───────────────────────────────┘
                           │ 明文 HTTP，只走回环地址
                           │ ★ 绑 127.0.0.1，公网碰不到，见 12.10「埋雷」
┌──────────────────────────▼───────────────────────────────┐
│  web 容器（nginx，只监听 80、只讲明文）                      │
│    ├─ /healthz             → 200（容器探活）               │
│    ├─ /                    → 前端静态文件（Vue 打包产物）    │
│    ├─ /api/ /admin/ /health/                              │
│    │        → 反代 http://backend:8000                    │
│    ├─ /static/             → 反代 backend                 │
│    └─ /media/              → 命名卷 uploads（只读直发）      │
└──────────────────────────┬───────────────────────────────┘
                           │ 容器内网，不映射到宿主机
┌──────────────────────────▼───────────────────────────────┐
│  backend 容器（gunicorn 3 worker × 4 线程）                 │
└──────────────────────────┬───────────────────────────────┘
┌──────────────────────────▼───────────────────────────────┐
│  db 容器（mysql:8.0）                                      │
└──────────────────────────────────────────────────────────┘

   ★ TLS 不在这套 compose 里 —— 由宿主机上的宝塔终结，
     连同证书的签发和自动续期一起归它管。见 12.10。
   命名卷：mysql_data（全部文章和账号）  uploads（全部配图）
   ★ 两个卷都不能丢，`docker compose down -v` 会同时删掉它们，见 12.9
```

**为什么 TLS 交给宝塔，而不是让 `web` 容器自己扛？**

因为**那台机器上还有别的网站在宝塔上**。两个 nginx 抢同一个 80/443，
要么容器起不来（`port is already allocated`），要么那个站被搞挂。
完整背景见 12.10 开头 —— 那一条不是"偏好"，是实测出来的约束。

**图片为什么要单独走一条路？**
配图是几百 KB 的二进制。如果让 Django 来发，**每张图都要占住一个 gunicorn
worker（只有 3×4=12 个）**，图上多一点就能把 API 请求饿死。
交给 nginx 直接从一个只读挂载的卷里发是零成本的 —— 它还会顺手做缓存和 Range 请求。

所以 `web` 容器把 `uploads` 卷以 **`:ro`（只读）** 挂到 `/var/www/media`，
只有 `backend` 能写。这样即使 nginx 被攻破，也改不了图片。

**为什么让 nginx 同时管前端和接口（同源）？**

前端构建时 `VITE_API_BASE_URL=/api/v1` 是**相对路径**，请求自动打到同一个域名。
于是 CORS 预检、跨域 Cookie 这一整类问题**根本不会发生**，
`.env` 里也不用填域名。将来换域名，只要改 nginx 和 `.env`，
**前端一个字都不用动、不用重新构建**。

**为什么 backend 和 db 用 `expose` 而不是 `ports`？**
`expose` 只在容器网络内可见，**不映射到宿主机**。这样即使防火墙配错了，
公网也摸不到 8000 和 3306。

---

### 12.2 服务器上先做这四件事

```bash
# 1. 装 Docker（官方脚本装的是 compose v2 插件）
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER      # ★ 执行完要退出 SSH 重新登录才生效
docker compose version             # 确认是 v2（有横杠的 docker-compose 是老的 v1）

# 2. 加 2G swap
#    前端构建要跑全量 TypeScript 类型检查，峰值内存 1GB+；
#    2 核 4G 机器上同时跑 MySQL + 3 个 gunicorn worker，不加 swap 容易被 OOM killer 干掉。
#    ★ 被 OOM 杀掉时 Node 只打印一句 "Killed"，完全看不出是内存问题。
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab

# 3. 防火墙：只开 22、80 和 443（★ 装了宝塔的话看下面的说明，别用 UFW）
sudo ufw allow 22/tcp && sudo ufw allow 80/tcp && sudo ufw allow 443/tcp && sudo ufw enable
# ★ 不要开 8000 / 3306 / 5173

#    ★ 80 和 443 两个都要开：80 负责「跳转到 HTTPS」和证书续期验证，
#      少开 80 的话证书 90 天后会续期失败。

# 4. ★★★ 去云厂商控制台的安全组里，放行 80 和 443 端口的入方向流量
#    安全组是独立于 UFW 的第二道墙。UFW 全配对了但网页打不开，99% 是这里没放行。
#    ★ 还有一点容易踩：Docker 是通过 iptables 直接下发端口映射的，**绕过 UFW** ——
#      所以"我 UFW 没开 80 但网页能打开"是正常的，真正说了算的是安全组。
```

> ### ★ 服务器上装了宝塔面板的话
>
> **① 不要用 UFW，改用宝塔的「安全」页面。**
> 宝塔自己在管 iptables / firewalld，再开一个 UFW 两套规则会互相打架，
> 而且宝塔会弹警告。放行端口请去 **宝塔 → 安全 → 添加端口规则**，
> 加 80 和 443。已经手贱开了 UFW 的话：`sudo ufw disable`。
>
> **② 先查清楚这台机器上还有没有别的网站在跑 —— 有的话，12.10 的每一步
> 都要顺带确认没影响到它。**
>
> ```bash
> ls -l /www/server/panel/vhost/nginx/
> grep -rhE '^[[:space:]]*(server_name|listen)' /www/server/panel/vhost/nginx/ 2>/dev/null \
>   | sed 's/^[[:space:]]*//' | sort -u
> ```
>
> | 看到什么 | 说明 |
> | --- | --- |
> | 只有 `0.default.conf` 之类的默认配置，没有别的域名 | 这台机器目前只有宝塔自己，动起来顾虑少 |
> | 有别的域名（`xxx.online.conf` 之类） | **这台机器上还有别的站在跑。** 部署时实测遇到的就是这种（`supermanapi.online`），别去停宝塔，也别改它的全局配置 |
>
> ★★ **无论哪种情况，容器都不去碰 80/443：宝塔继续管，容器退到 8080 由它反代。**
>
> 这一节最早写的是「机器上只有你一个站就把宝塔的 nginx 停掉，让容器独占
> 80/443」—— 那个做法**现在不成立了**：`frontend/nginx.conf` 已经改成
> 纯 HTTP 单 server 块，TLS 和 80/443 一律归宝塔。容器就算独占 80，
> 也没人给浏览器讲 HTTPS，站点照样打不开。
>
> 换句话说，「要不要停宝塔的 nginx」这个问题**已经不存在了**：
> 容器永远只监听 `127.0.0.1:8080`，和宝塔井水不犯河水。
> 唯一要做的是在宝塔里加一个站点反代过来（12.10）。

---

> **具体怎么做：在宝塔里新加一个站点，反代到 `http://127.0.0.1:8080`**
>
> 什么都不用停。宝塔继续独占 80/443、继续管它自己的站点和证书；
> 我们只是多挂一个站点上去，指到 `web` 容器。两个站互不影响。
>
> 完整步骤见 **12.10**。那里有两个必须手工配、漏了整站打不开的东西
> （`X-Forwarded-Proto` 和 `proxy_read_timeout`），照着 12.10 第 2 步做。
>
> > ★ 这一节绕了一圈：最早写的是「8080 + 宝塔一键 SSL」，后来改成
> > 「容器独占 80/443 + certbot」，最后又改回宝塔反代 ——
> > 因为部署时才发现**那台机器上还有别的网站在宝塔上**。
> > 前提变了，结论就得跟着变；这不是前后矛盾，是实测出来的约束。
>
> ★ **顺带记一个坑：宝塔的 nginx 不是 systemd 拉起来的。**
>
> ```bash
> sudo systemctl status nginx | head -3
> #   Active: inactive (dead)          ← 说"没在跑"
> sudo ss -lntp | grep -E ':(80|443)\s'
> #   LISTEN ... users:(("nginx",pid=60661,...))   ← 可它明明占着 80
> ```
>
> 两句话说的是两回事：systemd 只认它自己启动过的进程，而宝塔是**自己直接
> exec 起 nginx** 的（master 的父进程是 1，不经过 systemd）。
> 所以 **`sudo systemctl stop nginx` 在这台机器上是空操作** ——
> 不报错、什么也不做，你以为停掉了其实没有。真要停（**按上面的结论，
> 你不需要停**）得用 `/etc/init.d/nginx stop`。
>
> 记这条是因为：网上和旧版本文档里「停掉 nginx 腾出 80」的建议到处都是，
> 照着做会得到一个"明明停了却还是端口冲突"的诡异现象。

---

### 12.3 把代码传到服务器上

**这个项目现在还不是一个 git 仓库**（本地没有 `.git`），所以先选一条路：

**方案 A：私有 Git 仓库（推荐，之后更新最省事）**

```bash
# 本机：初始化并推到 GitHub / Gitee 的**私有**仓库
git init
git add .
git status                      # ★★★ 提交前一定要看一眼这个
git commit -m "初始版本"
git remote add origin <你的私有仓库地址>
git push -u origin main
```

> ### ★★★ `git status` 那一眼是必须的
> 确认输出的文件列表里**没有 `.env`、没有 `backend/.env`**。
> （`.gitignore` 已经挡住了，但你要亲眼确认一次 —— 这一步花 5 秒，
> 而漏了的代价是密钥永久留在 Git 历史里，只能去服务商后台吊销重发。）
>
> 仓库**必须设成私有**。这个项目的代码本身不含密钥，
> 但 `ADMIN_URL_PATH`、限流配置这些信息对攻击者是有用的。

然后在服务器上：

```bash
sudo apt update && sudo apt install -y git
git clone <你的私有仓库地址> agent_ai_biaoshu
cd agent_ai_biaoshu
```

**方案 B：直接 scp / rsync（不想用 Git 的话）**

```bash
# 在本机项目根目录的**上一级**执行
# ★ --exclude 那几项不能省：.venv 和 node_modules 有几百 MB，
#   而且里面是 Windows 版的二进制，传上去也没法用
rsync -av --progress \
  --exclude '.venv' --exclude 'node_modules' --exclude 'dist' \
  --exclude '__pycache__' --exclude '*.sqlite3' --exclude '.git' \
  ./agent_ai_biaoshu/ 用户@<服务器IP>:~/agent_ai_biaoshu/
```

> ★ 方案 B 的 `.env` **会被传上去** —— 也就是你本机开发用的那份
> （里面 `DB_ENGINE=sqlite`、`DB_HOST` 也不对）。
> 所以传完**必须**立刻按 12.4 覆盖成服务器版的 `.env`，
> 别指望它能直接跑起来。

**之后要更新代码**：方案 A 用 `git pull`，方案 B 重新 rsync 一次。
两种方案的后续步骤都一样，见 12.8。

---

### 12.4 配置 .env

```bash
cd /path/to/agent_ai_biaoshu
cp .env.deploy.example .env
chmod 600 .env                    # ★ 只有自己能读
nano .env                         # 按下面的说明逐项改
```

> ### ★ 服务器上**只有这一份**配置
> **不要在服务器上再建 `backend/.env`。** 容器里的 Django 只读根目录这一份 `.env`
> （通过 compose 的 `env_file`），`backend/.env` 在部署场景下不仅没用，
> 还会让你在两份文件之间反复看错。两处配置一定会写岔。
>
> （本机开发仍然是另一回事 —— 那时用的就是 `backend/.env`，见第五节。）

**必须改的九项：**

| 变量 | 怎么填 | 填错的症状 |
| --- | --- | --- |
| `DJANGO_SECRET_KEY` | `openssl rand -base64 48` | 容器启动时被 entrypoint 直接拒绝 |
| `ALLOWED_HOSTS` | `<你的域名>,127.0.0.1,localhost` | Django 返回 `400 Invalid HTTP_HOST header`，**而且不告诉你它期望什么** |
| `CSRF_TRUSTED_ORIGINS` | `https://<你的域名>` | 后台登录时 403（★ 必须带 `https://` 前缀，只写域名不生效且不报错） |
| `SITE_ORIGIN` | `https://<你的域名>` | 只影响根路径那句提示语 |
| `MEDIA_PUBLIC_BASE_URL` | `https://<你的域名>` | 「复制到公众号」粘出去图全是裂的 |
| `USE_HTTPS` | `True` | Cookie 不加密、不跳转、不发 HSTS —— 等于 HTTPS 白配了 |
| `WEB_PORT` / `WEB_BIND` | `8080` / `127.0.0.1` | ① 填 80 会撞上宝塔的 nginx；② 填 `0.0.0.0` 是**安全漏洞**，见 12.10「埋雷」 |
| `DB_PASSWORD` / `DB_ROOT_PASSWORD` | `openssl rand -base64 32 \| tr -d '/+='` | 数据库连不上 |
| `LLM_API_KEY` | 你的 DeepSeek Key | 生成时报「调用大模型失败」 |

> ★ `WEB_PORT` 和宝塔那边反向代理的目标必须**对得上** ——
> 宝塔填 `http://127.0.0.1:8080`，这里就得是 `8080`。
> 改了一边忘了另一边，症状是 502 Bad Gateway。


**要用搜索和推送公众号，再补这些**（不填不影响生成文章，只影响对应功能）：

| 变量 | 怎么填 | 填错的症状 |
| --- | --- | --- |
| `SEARCH_API_KEY` | 你的 Tavily Key | 能生成，但会说「未能获取到任何外部资料」 |
| `IMAGE_API_KEY` | 生图服务的 Key | 配图报「配图生成服务暂时不可用」 |
| `IMAGE_BASE_URL` | 中转站地址，**必须带 `/v1` 结尾** | 同上（`message` 里会点名缺哪个变量） |
| `IMAGE_MODEL` | 照中转站**实际提供**的模型名填 | 生图返回 404 / 模型不存在 |
| `IMAGE_DAILY_QUOTA_PER_USER` | 例如 `20`。**留 0 = 不限制 = 钱包不设防** | 见下面的 12.5 |
| `IMAGE_DAILY_QUOTA_GLOBAL` | 例如 `200`。同上 | 同上 |
| `WECHAT_APP_ID` | 公众号后台 → 开发 → 基本配置 | 推送报「还没有配置微信公众号」 |
| `WECHAT_APP_SECRET` | 同一页。**泄露 = 公众号被接管** | 同上 |
| `WECHAT_AUTHOR` | 你的公众号名 | 草稿的「作者」一栏是空的（不影响推送） |

> ★ **`IMAGE_BASE_URL` 结尾有没有 `/v1` 是最常见的坑。**
> 代码用的是 OpenAI 兼容协议，路径是 `<BASE_URL>/images/generations`。
> 少了 `/v1` 就会打到 `https://xxx/images/generations`，得到一个 404。
> 代码**刻意不回退到 OpenAI 官方地址** —— 中转站和官方的模型名、计费都不一样，
> 猜错了只会让你收到一个看不懂的错。

> ★ **推送公众号还有一件微信侧的配置，代码这边做不到：**
> 去公众号后台把**服务器的公网 IP 加进「IP 白名单」**。
> 不加的话微信会返回 `40164`，而这个报错会原样显示给你看（见 12.11）。

**三个最容易踩的坑，每个都单独说一遍：**

**① `ALLOWED_HOSTS` 里的 `127.0.0.1` 不能删。**
容器健康检查（`backend` 自己的、以及 12.10 里新增的那条）都要走本机地址。
删了它，容器会永远是 `unhealthy` 状态 —— 而 `docker compose ps` 只会显示一个状态，
**不会告诉你为什么**。

**② `CSRF_TRUSTED_ORIGINS` 的协议前缀必须和实际访问方式一致。**
上了 HTTPS 就写 `https://<你的域名>`。写 `https://` 却用 `http://` 访问、
或者反过来，Django **不会报错、只会默默拒绝**，表现是后台登录 403。
（写域名却不带协议前缀也是不生效的，同样不报错。）

**③ 数据库密码里避开 `$` 符号。**
docker compose 会对 `.env` 做变量展开，`P@ss$word` 里的 `$word` 会被
当成变量替换成空字符串 —— 密码悄悄变了，然后你看到一个莫名其妙的
`Access denied`，而 `.env` 里明明写对了。`openssl rand ... | tr -d '/+='`
生成出来的字符集是安全的。

---

### 12.5 防烧钱：三层，各挡各的

这是部署到公网**最要紧**的一节。一次生成要调十几次大模型 + 联网搜索，
没有任何防护的话，一个人写个循环就能把额度刷光。

| 层 | 挡什么 | 配哪个变量 | 跨进程准不准 |
| --- | --- | --- | --- |
| ① 频率限流 | 短时间狂点按钮 | `THROTTLE_GENERATE` / `THROTTLE_LOGIN` / `THROTTLE_WECHAT_PUBLISH` | 用 `locmem` 时每个 worker 各算各的 |
| ② **单用户每日额度** | 一个人正常用但用很多 | `AGENT_DAILY_QUOTA_PER_USER` | ✅ 天然准确 |
| ③ **全站每日总额度** | 一群人各刷各的 / 注册小号 | `AGENT_DAILY_QUOTA_GLOBAL` | ✅ 天然准确 |
| ④ **配图每日额度**（另一本账） | 生图按张计费，比生文贵 | `IMAGE_DAILY_QUOTA_PER_USER` / `_GLOBAL` | ✅ 天然准确，见下 |

> **推送为什么要限流（而配图不用）？** 因为**微信侧有调用量上限**。
> `draft/add` + `media/uploadimg` 每一张图都是一次真实调用，
> 微信对接口有 45009（接口调用超过限制）。限流是替微信挡在前面，
> 而不是替我们省钱。默认 `30/hour` 对正常使用完全够 ——
> 它只在「写个循环反复推」时才生效。

**★ 关键认识：限流只能限制「频率」，限制不住「总量」。**

真正把额度烧光的姿势不是疯狂点按钮（那会被第①层挡住），
而是**每天正常用，但每次都跑满 8 次工具调用 + 十几次模型调用** ——
频率完全正常，第①层一点都拦不住。**只有自然日总量账本挡得住。**

而第②③层是**硬账本**：直接数数据库里 `agent_run` 表的真实行数。
清缓存、重启容器、换 worker 全都绕不过去，注册小号也没用（③是算所有人的）。

**它们统计「所有」运行记录，不管成功失败** —— 失败的那次照样真实调用了大模型、
照样花钱。按成功计费等于给失败留了个免费刷的洞。

```
AGENT_DAILY_QUOTA_PER_USER=20    # 单人每天最多几篇
AGENT_DAILY_QUOTA_GLOBAL=200     # ★ 全站每天总上限 = 你的最坏情况止损线
```

**这两个数字就是你的钱包保险丝，按自己的心理止损线来设。**
`0` 表示不限制。

### ★ 配图是**另一本账**，必须单独设

**生图比生文贵得多，而且是按张计费的。** 一篇文章 8 张图，
每张都可能比一次文章生成还贵 —— 更别说「重新生成」是可以无限点的。

配图的额度完全独立于上面那本账，数的是 `imaging_generatedimage` 表的行数：

```
IMAGE_DAILY_QUOTA_PER_USER=20    # 单人每天最多生成几张图
IMAGE_DAILY_QUOTA_GLOBAL=200     # ★ 全站每天总上限
IMAGE_MAX_PER_ARTICLE=8          # 单篇文章最多几张
```

**★ 这三个变量的默认值是 `0` = 不限制 = 钱包不设防。**
它是刻意留成这样的（本地开发不该被额度烦），
但**部署到公网时一定要设** —— 否则一个循环就能把生图额度刷光，
而且和文章那本账是分开的，你在文章额度上设的止损线拦不住它。

> 配图接口**没有加频率限流**，这是想过之后的决定：配图面板的设计就是
> 「按配图建议逐条生成」，8 条建议 = 8 次请求，几分钟内发完。
> 加一个按小时算的限流只会把正常用户挡在外面，
> 而**真正能止损的是上面那本硬账本**（数数据库行数，清缓存重启都绕不过去）。
>
> 另外：额度挡住时是**先检查、再生图**，不会出现「钱花了但告诉你超额了」。

**同时把注册关掉：**

```
ALLOW_REGISTRATION=False
```

开着的话，公网上的陌生人注册之后就能用**你的** `LLM_API_KEY` 生成文章、
用**你的** `IMAGE_API_KEY` 生成图，这钱是你出的。
关掉之后账号由管理员创建（见 12.6）。

---

### 12.6 构建、启动、建管理员

> ★ **容器不依赖证书，所以这一节可以先做完，再去配宝塔。**
> 证书和 443 都在宝塔那一层（12.10），`web` 容器只监听 80、只讲明文。
>
> ★ 但**顺序仍然重要**：容器起来之后，浏览器打不开域名是正常的 ——
> 那说明宝塔那边的站点和反向代理还没配（12.10 第 1~2 步）。
> 别在这个状态下反复重启容器，问题不在容器里。

```bash
docker compose build              # ★ 先构建（前端构建最慢，3~5 分钟）
docker compose up -d              # 再启动
docker compose ps                 # db / backend / web 三个都应该是 healthy
```

**看到 `Restarting` 就立刻看日志：**

```bash
docker compose logs backend
```

> 为什么会一直重启？`entrypoint.sh` 里有 `set -e`，任何一步失败容器就退出，
> 然后 `restart: unless-stopped` 又把它拉起来，再失败……
> 常见原因：`.env` 没填、`DJANGO_SECRET_KEY` 还是模板值、
> `DB_PASSWORD` 里有 `$`、数据库密码和 volume 里已有的不一致。

**建管理员账号：**

```bash
docker compose exec backend python manage.py createsuperuser
```

> ★ 不要图省事用 `--noinput` + 环境变量自动化。那样密码会留在
> shell history 和 `docker inspect` 里，等于没有密码。

**再往后台加人**：登录 `<你的后台路径>` → 认证和授权 → 用户 → 增加。
建完之后把用户加入需要的组（或者直接给 `is_staff`），
新用户就能登录前端了。

**后台地址**是 `.env` 里的 `ADMIN_URL_PATH`（模板里给了个随机串）。
`/admin/` 是所有扫描器的第一个探测目标，换掉之后它们连门都找不到。
改了记得用新地址访问。

> ★ 改这个值**不需要动 nginx**。`nginx.conf` 其实是个模板，
> 容器启动时会把 `${ADMIN_URL_PATH}` 替换成你的实际值
> （compose 已经把这个变量传给了 web 容器）。
> 但改完要重建才生效：`docker compose up -d --build web`。

---

### 12.7 验证清单（照着做一遍）

```bash
curl -i http://<域名>/health/             # 期望 301（证书链路的跳转是好的）
curl -i https://<域名>/health/            # 期望 200 + {"success":true}
curl -i http://<域名>/healthz             # 期望 200 + "ok"（web 容器自己的探活）
curl -i http://<IP>:8000/                 # 期望「连接被拒」—— 这是正确结果
docker compose logs backend | grep -i traceback    # 期望没有任何输出
docker compose exec backend ls /app/media            # ★ 卷挂上了没（空目录也正常，说明挂着）
docker compose exec web ls /var/www/media            # ★ nginx 那一侧只读挂载也得有
```

> ★ 第 1、2 条一起看才说明问题：只有 301 **且** https 那侧 200，
> 才证明「证书能用 + 跳转生效」。
>
> ★ 12.10 里还有一份**专门针对 HTTPS 的**验证清单（证书有效期、静态文件不跳转、
> 自动续期配置），上线时两份都要过。

然后**必须用浏览器**（不能用 curl 代替，要验证的是 nginx + 浏览器的真实行为）：

| 验证项 | 期望结果 | 失败时先查 |
| --- | --- | --- |
| 打开 `https://<域名>/` | 登录页正常，**地址栏是绿锁**，F12 无 CORS 报错 | 12.10 的证书那几条 |
| **在文章详情页按 F5** | 正常显示，不是 404 | `nginx.conf` 的 `try_files` 那行 |
| 生成一篇文章，掐表 | **2 分钟内出结果，无 504** | **两层 nginx 的 `proxy_read_timeout`**（宝塔那层默认只有 60s）/ LLM Key |
| 连点生成按钮 | 出现**中文**「操作太频繁了，请稍后再试」 | 限流配置 |
| 跑到单用户额度上限 | 出现「达到每日上限（20 篇）」 | 额度配置 |
| **生成一张配图** | 图能显示出来（**不是裂图**） | 见下面那条「配图专项」 |
| **点一次「复制到公众号」** | 提示已复制；粘进公众号编辑器后**文字和配图都在** | 12.10 的「复制到公众号」那条 |
| **推送到公众号草稿箱** | 提示成功，去公众号后台草稿箱能看到文章和封面 | 12.11 的 `40164` / `48001` 两行 |
| 打开 `https://<域名>/admin/` | **404**（因为已经换路径了） | — |
| 打开 `https://<域名>/<你的后台路径>` | 后台能登录，**CSS 正常显示** | `collectstatic` / whitenoise / `/static/` 的 `X-Forwarded-Proto`（见 12.10） |

**最后三条特别值得做：**

- 后台 CSS 正常 = 静态文件链路（whitenoise + `/static/` 反代 + `collectstatic`）整条是通的。
  ★ 上了 HTTPS 之后这一条还多一层含义：它同时证明 `/static/` 那个 location 带上了
  `X-Forwarded-Proto`。少了它，Django 会把每个 CSS 请求 301 到 https 再 301 回来，
  症状恰好就是「后台能登录但没样式」，和 `collectstatic` 没跑是同一个表现 —— 见 12.10。
- **配图能显示 = `uploads` 卷 + nginx 的 `/media/` 反代整条是通的。**
  这一条要专门验证，因为它跨了三个组件（backend 写入 → 命名卷 → nginx 只读挂载），
  而且**坏掉时的表现是「图片 404」而不是报错** —— 前端只会显示一个裂图，
  页面上其它一切正常，非常容易被当成「图生成坏了」而去查生图服务。
  F12 → Network 里看那个图片请求：**404 说明是卷或 nginx 的问题**，
  200 但图是破的才是生图的问题。
- **「复制到公众号」要单独实测**，因为它依赖的浏览器能力在 HTTP 和 HTTPS 下**不一样**：
  只有 HTTPS（安全上下文）下 `navigator.clipboard` 才存在，代码会走这条路；
  HTTP 下走的是 `execCommand` 兜底。**上线前后是两条不同的代码路径**，
  不能因为「上线前能用」就跳过这条。

**配图专项排查（一条命令定位是哪一层断的）：**

```bash
# 1. 后端到底写进去没有？（看得到文件说明 backend + 卷是好的）
docker compose exec backend ls -R /app/media | head -20
# 2. nginx 那边看得到吗？（看不到 = 卷挂载写错了）
docker compose exec web ls -R /var/www/media | head -20
# 3. nginx 认得这条路由吗？（期望不是 404）
curl -I https://<域名>/media/<随便一个真实文件名>
```

> 第 3 步**必须用文件名真的存在的那张图**，随便编一个路径得到 404 是正常的 ——
> 这条只能证明「nginx 把 /media/ 交给了卷」而不是「交给了 SPA 回退」
> （回退的话会返回 `200` + 一堆 HTML）。

---

### 12.8 更新流程

```bash
git pull                          # 方案 B 的话改成重新 rsync 一次
docker compose up -d --build
docker compose restart web        # ★★★ 这一行不能省
```

> ★ 服务名是 **`web`**，不是 `nginx` —— 写 `docker compose restart nginx`
> 会直接报 `no such service: nginx`（早期版本的 README 里就是这么写错的）。
> 三个服务叫 `web` / `backend` / `db`。

> **为什么必须重启 nginx？**
> nginx 在启动时解析 `proxy_pass http://backend:8000` 里的主机名，
> 把 **IP 缓存下来**。重新构建 backend 后容器 IP 会变，
> 而 nginx 还在往旧 IP 发请求 → 整个站 502，而 backend 本身完全正常。
> 这个现象非常难查，因为两个容器看起来都好端端的。

改了前端代码时，`--build` 会重新跑一遍 Vite 构建，这一步在 2 核机器上要几分钟，
是正常的。

> **`up -d --build` 不会碰任何命名卷。** `mysql_data`（文章、账号）
> 和 `uploads`（配图）都会原样保留，数据库迁移由容器的 entrypoint
> 自动跑（`migrate` 在 gunicorn 启动之前）—— **不需要你手工敲 migrate**。
>
> **只有 `down -v` 会删卷。** 平时更新永远只用 `down`，不加 `-v`。

**只改了 `.env` 的时候**，不用 `--build`（`--build` 是重建镜像，和配置无关）：

```bash
nano .env
docker compose up -d backend      # ★ 这一句才会让新配置生效（重建容器）
```

> ★ 改了哪一项就重建哪个服务 —— `up -d` 后面**不带服务名**就是全部重建，
> 拿不准时这么写最省事：
>
> | 改的变量 | 要重建 |
> | --- | --- |
> | `WEB_PORT` / `WEB_BIND` / `ADMIN_URL_PATH` | `web` |
> | `ALLOWED_HOSTS` / `CSRF_TRUSTED_ORIGINS` / `USE_HTTPS` / `SECURE_HSTS_*` / `SITE_ORIGIN` / `MEDIA_PUBLIC_BASE_URL` | `backend` |
> | `DB_*` | `backend`（`db` 自己的密码改不了，见 12.11 的 `Access denied` 那行） |
>
> 改完 `WEB_PORT` 尤其别忘了 —— 它决定宿主机哪个端口通到容器的 80。
> 不重建的话新端口根本没被监听，而**宝塔那边的反向代理还指着老端口**，
> 症状是 502 Bad Gateway（12.10 第 2 步）。
>
> ★ `WEB_BIND` 改了**必须重建**才生效，这一点比 `WEB_PORT` 更要紧 ——
> 它决定这个端口是不是对公网敞开，而它敞开就等于敞开了请求头伪造（12.10「埋雷」）。

> ★ **改 `.env` 之后不 `up -d` 是没用的。** 环境变量是在**容器创建时**
> 注入进去的，改文件不会影响已经在跑的容器 —— 你会对着一个明明改对了的
> `.env` 反复怀疑自己。确认新配置真的进去了（**只打印变量名，不打印值**，
> 免得把 Key 刷到终端和你的 shell 历史里）：
>
> ```bash
> docker compose exec backend printenv | grep -oE '^(IMAGE|WECHAT)_[A-Z_]+'
> ```

---

### 12.9 备份

**★★★ 备份现在是两件事，不是一件。** 只做下面第一条会**静默丢图**：
数据库完好无损地恢复了，所有配图却全是 404 —— 而且不会有任何报错，
你要等到打开某篇文章、发现图全裂了才会知道。

#### ① 数据库（文章、账号、推送记录）

```bash
docker compose exec -T db mysqldump -u root -p"$DB_ROOT_PASSWORD" \
  --single-transaction --default-character-set=utf8mb4 ai_content_agent \
  | gzip > ~/backup-db-$(date +%F).sql.gz
```

`--single-transaction` 保证导出的是一致性快照，**不锁表**，线上可以随时跑。

#### ② 配图（`uploads` 卷）

```bash
# ★ 用 docker run 起一个临时容器把卷打包出来，比去 /var/lib/docker/volumes/
#   里翻路径靠谱得多 —— 那个路径在不同发行版和不同 Docker 版本上并不一致。
docker run --rm \
  -v agent_ai_biaoshu_uploads:/data:ro \
  -v "$HOME:/backup" \
  alpine tar czf /backup/backup-media-$(date +%F).tar.gz -C /data .
```

> 卷名默认是 `<compose 项目名>_uploads`。项目名就是目录名，
> 用 `docker volume ls | grep uploads` 确认一下，**名字不对 tar 会打出一个空包
> 而且退出码还是 0** —— 这是最阴的一种失败。

#### 两个一起放进 crontab

```cron
0 3 * * *  cd /path/to/agent_ai_biaoshu && docker compose exec -T db mysqldump ... | gzip > ~/backup-db-$(date +\%F).sql.gz
30 3 * * * docker run --rm -v agent_ai_biaoshu_uploads:/data:ro -v "$HOME:/backup" alpine tar czf /backup/backup-media-$(date +\%F).tar.gz -C /data .
```

**并且把两份都留一份到另一台机器或对象存储** ——
备份和原始数据放在同一块盘上，等于没备份（服务器被删库 / 挂盘，两份一起没）。

#### 恢复（真的演练一次，别等到出事才第一次用）

```bash
gunzip -c ~/backup-db-2026-09-15.sql.gz | docker compose exec -T db mysql -u root -p"$DB_ROOT_PASSWORD" ai_content_agent
docker run --rm -v agent_ai_biaoshu_uploads:/data -v "$HOME:/backup" alpine tar xzf /backup/backup-media-2026-09-15.tar.gz -C /data
```

> ★ 恢复配图时**不要加 `:ro`**（要写进去）。
> 而且 tar 解包会**覆盖同名文件但不会删掉多余文件** ——
> 如果你是在一个已经跑过一段时间的卷上恢复，先清空再解包更干净。

> ★ **两个卷都不能丢：`mysql_data` 存着全部文章和账号，`uploads` 存着全部配图。**
> **`docker compose down -v` 会把它们一起删掉。** 平时更新只用 `down`，不要加 `-v`。
> 这条以前只写了一半（只提了 `mysql_data`），现在配图也在卷里了，代价翻了倍。

---

### 12.10 域名 + HTTPS：把那笔账结掉

#### 为什么这件事不能拖

用户的登录凭证存在浏览器 localStorage 里，每个请求都以
`Authorization: Token xxx` 的形式**明文**发送。只走 HTTP 就意味着：

> **同一个 WiFi 下的任何人，都能抓到它，然后冒充这个用户登录。**

还有第二个代价更隐蔽：**「复制到公众号」的配图能不能显示，一直是个赌。**
`https://` 页面里的 `http://` 图片会被浏览器按**混合内容**直接拦掉，
而粘出去的 HTML 是渲染在 `mp.weixin.qq.com` 上的 —— 那边是 https。
所以那批图能不能活下来，取决于公众号编辑器会不会重新上传，谁也没法保证。

上了 HTTPS，这两个问题一起消失。

> `sslip.io` / `nip.io` 这类「拿 IP 当域名」的服务理论上也能签证书，
> 但 Let's Encrypt 对这类共享域名有严格速率限制且不受你控制，
> **不建议依赖**。自己买个最便宜的域名（.xyz / .top，一年几十块）就行。

#### 这条路长什么样

```
浏览器
  │  https://jiesangai.online        ← 证书在这一层，443
  ▼
宝塔 nginx（宿主机，独占 80 / 443）
  │  同时还在服务同一台机器上的 supermanapi.online
  │  proxy_pass http://127.0.0.1:8080
  ▼
web 容器（nginx，只监听 80、只讲明文 HTTP）
  │    /api/  /admin/  /static/  →  proxy_pass http://backend:8000
  │    /media/                   →  直接从 uploads 卷发文件，不进 Django
  ▼
backend 容器（gunicorn / Django）
  ▼
db 容器（MySQL 8）
```

#### ★ 为什么让宝塔做 TLS，而不是让容器自己终结

**因为那台机器上已经有一个站在宝塔上。**

本文档实际部署时查到的：80 和 443 都被宝塔的 nginx 占着
（`/www/server/nginx/sbin/nginx`），上面挂着 `supermanapi.online`，
而且宝塔面板本身是活的（`BT-Panel` / `BT-Task` 两个进程都在跑）。

这种机器上让 `web` 容器去抢 80/443 会连环出事：

| 后果 | 说明 |
| --- | --- |
| 容器根本起不来 | `Bind for 0.0.0.0:80 failed: port is already allocated` |
| 另一个站会下线 | 停掉宝塔的 nginx = `supermanapi.online` 一起挂 |
| 而且未必停得住 | `BT-Task` 是个活着的守护进程，可能把 nginx 再拉回来，变成拉锯 |

所以正确做法是**顺着宝塔来**：它继续独占 80/443、继续管证书，
我们的容器退到一个不冲突的端口，由它反代进来。两个站互不影响。

代价是多了一层转发，于是多出两个**必须手工配、漏了整站打不开**的东西
（第 2 步）。下面第 2 步是全文最需要注意的一节。

#### 上 HTTPS 要动的东西（都在这个仓库里，已经改好了）

| 文件 | 改了什么 |
| --- | --- |
| `frontend/nginx.conf` | 改回**单 server 块、纯 HTTP**：删掉 443 块、TLS 配置、ACME 路径和 HTTP→HTTPS 跳转（这些归宝塔）；三个反代点的 `X-Forwarded-Proto` 从 `$scheme` 改成**透传** `$http_x_forwarded_proto` |
| `frontend/Dockerfile` | 去掉 `EXPOSE 443` 和 `PUBLIC_HOST`；健康检查打 `/healthz` |
| `docker-compose.yml` | 去掉证书卷挂载和 443 端口映射；`WEB_BIND` 默认值改成 `127.0.0.1` |
| `backend/config/settings.py` | HSTS 三个值改成可配；新增 `SECURE_REDIRECT_EXEMPT` 豁免健康检查（避免假 healthy） |
| `.env.deploy.example` | 删掉 `PUBLIC_HOST` / `HTTPS_PORT`；域名相关的项全部改成 `https://你的域名` |

**你要做的**是下面四步服务器操作。

---

#### 第 1 步：在宝塔里建站点

宝塔面板 → **网站** → **添加站点**：

| 项 | 填什么 |
| --- | --- |
| 域名 | 你的域名（不带 `www`，不带端口） |
| 根目录 | 随便填，**其实用不上** —— 我们只借它的反向代理 |
| FTP / 数据库 | 都不创建 |
| PHP 版本 | **纯静态** |

建完宝塔会在 `/www/server/panel/vhost/nginx/` 下生成一个 `<域名>.conf`。

> ★ **面板打不开的话**：先确认云安全组放行了面板端口（宝塔默认 8888）。
> 面板进程本身可能是活的 —— `ps aux | grep BT-Panel` 看一眼，
> 有进程就说明只是网络到不了，不是面板坏了。

#### 第 2 步：配反向代理 —— ★★★ 三个必须手改的地方

宝塔 → 网站 → 点你的域名 → **反向代理** → 添加反向代理：

| 项 | 填什么 |
| --- | --- |
| 代理名称 | 随便，比如 `biaoshu` |
| 目标 URL | `http://127.0.0.1:8080` |
| 发送域名 | `$host` |

保存之后，**宝塔生成的那段配置还不够用**，要手工补。
点该站点的 **配置文件**，找到 `location /` 里含
`proxy_pass http://127.0.0.1:8080;` 的那一段，确认有这四行：

```nginx
proxy_set_header Host $host;
proxy_set_header X-Real-IP $remote_addr;
proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
proxy_set_header X-Forwarded-Proto $scheme;     # ★★★ 最要命的一行
```

然后**再加两行超时**：

```nginx
proxy_read_timeout 600s;                        # ★★★ 不加，长文生成必然 504
proxy_send_timeout 600s;
```

改完保存，宝塔会自动 reload nginx。

---

**① 为什么 `X-Forwarded-Proto` 是必须的**

Django 打开 `USE_HTTPS` 之后有 `SECURE_SSL_REDIRECT`，它判断
"这个请求算不算 https"的**唯一依据**就是这个头
（`settings.py` 里的 `SECURE_PROXY_SSL_HEADER`）。

而请求链路是「浏览器 --https--> 宝塔 --**http**--> 容器」——
宝塔到容器那一段是**明文**。所以宝塔必须明确告诉下游
"原始请求是 https"，否则：

```
Django 认为请求是 http → 301 到 https://<域名><同一路径>
  → 浏览器照做 → 宝塔再转发进来（还是明文）
    → Django 又认为 http → 又 301 → …… 无限循环
```

浏览器最后报**「重定向次数过多」**，整站打不开。

★★ 这个故障极具迷惑性：`docker compose ps` 里每个容器都是
`healthy`，因为健康检查走的是容器内部、根本不经过这条路。
你看到的是"容器全好，但网站打不开"。

> ★ 容器里那个 nginx **不会**替你补上这个头 —— 它是**透传**宝塔给的值
> （`$http_x_forwarded_proto`），因为容器自己看到的 `$scheme` 恒等于 `http`。
> 两边必须接上，缺哪一边都是循环重定向。

**② 为什么超时必须调大**

一次文章生成是**同步**请求（联网搜索 + 读网页 + 多轮大模型），
耗时 30~120 秒，最坏情况更久。

容器里的 nginx 已经配了 600 秒，但**宝塔那层默认只有 60 秒** ——
不调大的话，请求在宝塔就被切断了，容器里配多长都没用。
用户在界面上看到的是"服务器错误"，而后端其实还在老老实实地跑
（`docker compose logs -f backend` 能看到它继续跑完）。

**③ 顺带确认一下 `Host`**

`ALLOWED_HOSTS` 校验的就是 `Host` 头。宝塔的"发送域名"填 `$host`
时会把原始域名透传下来，容器里的 nginx 再用 `proxy_set_header Host $host`
继续往后传 —— Django 最后看到的就是 `你的域名`，和 `ALLOWED_HOSTS` 对得上。

如果这一环断了，症状是 Django 返回
**400 `Invalid HTTP_HOST header`**，而且**不会告诉你它期望什么**。

---

#### 第 3 步：申请证书 + 强制 HTTPS

**先确认域名已经解析到这台机器**，否则申请必然失败：

```bash
dig +short <你的域名>       # 输出的应该就是这台服务器的公网 IP
```

没有输出、或者不是这台机器，先去域名商那边加一条 **A 记录**，
等生效再申请。`NOERROR` 但 Answer 为空 = 记录不存在，等多久都不会出现。

解析没问题后：宝塔 → 网站 → 你的域名 → **SSL** →
**Let's Encrypt** → 勾选域名 → 申请。

申请成功后，把 **「强制 HTTPS」** 开关打开。

> HTTP→HTTPS 的跳转**由宝塔做**，容器里那个 nginx **故意没做** ——
> 两边都做容易打架（跳转目标不一致时会互相踢成循环）。

> ★ **证书续期也归宝塔管**，它在到期前会自动续，你什么都不用做。
> 这是走宝塔这条路的一个附带好处：不用自己折腾 certbot 的续期钩子。

#### 第 4 步：填 `.env`、重建

```bash
cd /path/to/agent_ai_biaoshu
git pull
cp .env .env.bak-$(date +%F)     # 改坏了好回滚（.gitignore 会忽略它，不会误提交）
nano .env                        # 按 12.4 那张表改
```

最容易漏的几项：

```ini
WEB_PORT=8080                                # ★ 不要改成 80 —— 那是宝塔的
WEB_BIND=127.0.0.1                           # ★ 不要改成 0.0.0.0，见下面「埋雷」
ALLOWED_HOSTS=你的域名,127.0.0.1,localhost     # 127.0.0.1 不能删
CSRF_TRUSTED_ORIGINS=https://你的域名          # ★ 必须带 https://
SITE_ORIGIN=https://你的域名
MEDIA_PUBLIC_BASE_URL=https://你的域名         # 留着 http:// 的话粘出去的图会被拦
USE_HTTPS=True
SECURE_HSTS_SECONDS=300                      # ★ 先短后长，见下面「HSTS」
```

```bash
docker compose up -d --build      # --build 是必需的：nginx.conf 和 Dockerfile 都改了
docker compose ps                 # web / backend 都应该是 healthy
```

> ★ 改 `.env` 必须靠 `up -d` 才生效 —— `restart` **不会重新读 `env_file`**。
>
> ★ `http://<公网IP>:8080` 从现在起**从外面打不开了**，这是故意的
> （绑在 `127.0.0.1` 上）。本地要验证用 `curl -I http://127.0.0.1:8080/healthz`。

---

#### 验证清单（HTTPS 专项，和 12.7 那份一起做）

```bash
curl -I  http://<域名>                        # 期望 301 → https://<域名>/
curl -I  https://<域名>                       # 期望 200
curl -s  https://<域名>/healthz               # 期望 200 + "ok"
curl -I  https://<域名>/media/<一张真实存在的图>  # 期望 200 + Content-Type: image/jpeg

curl -I  http://127.0.0.1:8080/healthz        # 在服务器上跑，期望 200（容器直接可达）
```

**① ★★★ 静态文件那个死循环**（最容易漏，症状最误导人）

```bash
curl -sI https://<域名>/static/admin/css/base.css | head -3
```

期望 **200**。如果是 301 / 302 → **宝塔那边的 `X-Forwarded-Proto`
没配上**（第 2 步 ①）。症状是「后台能登录，但**完全没有样式**」，
和 `collectstatic` 没跑长得一模一样，很容易查错方向。

**② 长文生成不会被中途切断**（对应第 2 步 ②那两行 timeout）

在浏览器里真的生成一篇长文章，等它跑完。
如果 60 秒左右就报"服务器错误"、而后端日志显示它还在正常跑，
就是宝塔那层超时没改。

**③ 证书本身**

```bash
echo | openssl s_client -connect <域名>:443 -servername <域名> 2>/dev/null \
  | openssl x509 -noout -subject -dates -issuer
```

期望 `subject` 是你的域名、`issuer` 是 Let's Encrypt、`notAfter` 约 90 天后。

**④ ★ 宝塔上另一个站没被影响**

```bash
curl -I https://supermanapi.online      # 期望还是它原本的状态码（200）
```

这条是整条路线的**全部理由**，一定要确认一下。

**⑤ 浏览器**

1. 打开 `https://<域名>` —— 锁是绿的，没有「不安全」警告
2. 登录 → 打开一篇文章详情 → **后台（`/manage-8f3a91c7/`）也要开一次，
   确认有样式**（对应上面 ①）
3. F12 → Network → 点「复制到公众号」→ 看 `wechat-html` 的响应：
   `<img src>` 应该是 `https://<域名>/media/...`（https、无端口号）
4. **★ 重点：把复制出来的内容粘进公众号编辑器，看文字和图。**
   上 HTTPS 之后 `navigator.clipboard` 变成可用，代码会走**主路径**
   （HTTP 下走的是 `execCommand` 兜底）—— 这是一次**真实的行为切换**，
   上线前能用不代表上线后没问题，必须重测。
   如果图之前裂、现在好了，就确认是混合内容问题。
   如果弹出「浏览器不允许自动复制」，把现象告诉我，我们再调
   `frontend/src/utils/clipboard.ts`（大概率是取正文那个请求耗掉了用户手势）。

---

#### HSTS：先短后长

`.env` 里的 `SECURE_HSTS_SECONDS` **第一次填 300（5 分钟）**，
稳定跑一周之后再改成 `31536000`（1 年）。

为什么要绕这一下：HSTS 本身不是不可逆的（浏览器再看到 `max-age=0` 就解除），
**真正的风险是**一年 max-age 生效期间 HTTPS 一旦挂掉，
浏览器会拒绝回退到明文 —— 等于把一次配置事故放大成一年。

`SECURE_HSTS_INCLUDE_SUBDOMAINS` 默认填 `False`：
含子域会把你的域名下面**每一个**子域都锁进 HTTPS。
这台机器上还有别的站（比如那个 New API 网关），
将来想给别的子域跑点 http 服务会直接打不开，而且改回来要等 max-age 过期。

> ★ **忘了改成一年也不会有故障**，只是 HSTS 的保护弱一些。不用紧张。

---

#### 几个刻意没做的事

| 没做 | 为什么 |
| --- | --- |
| 容器里不做 TLS | 宝塔已经在做了。两边都做只会多一层要同步的东西，而且容器那份证书会被宝塔的续期甩在身后 |
| 容器里不做 HTTP→HTTPS 跳转 | 宝塔的「强制 HTTPS」在做。两边都跳、目标又对不齐时，会互相踢成循环 |
| 容器里不放 ACME 验证路径 | 证书归宝塔签和续，验证文件走的是宝塔自己那个 80 端口上的站点 |
| 容器里发 HSTS 头 | HSTS 由 Django 统一发。nginx 的 `add_header` 是**子块覆盖父块**：任何 location 里出现一个 `add_header`，server 级的所有 `add_header` 就全被丢掉，两处都发只会让规则更难推理 |
| HSTS preload | 进了浏览器内置列表想移除要等各个浏览器发版，以月计。没必要 |
| 给这个站单独开一个子域 | 那还要再签一张证书、多一个解析。主域名直接用更省事 |
| 证书进备份 | 现在证书由宝塔管，连备份都不用操心。**`.env` 相反，一定要留好**（它含全部密钥，别往网盘丢） |

#### 一个容易埋雷的地方

**`WEB_BIND` 必须是 `127.0.0.1`，不要改成 `0.0.0.0`。**

`SECURE_PROXY_SSL_HEADER` 一旦配上，Django 就**无条件信任**
`X-Forwarded-Proto` 头。而容器里的 nginx 是**原样透传**这个头的 ——
所以只要 8080 能从公网直连，任何人都能绕过宝塔、
自己伪造一个 `X-Forwarded-Proto: https`，让 Django 把一个明文请求
当成加密请求处理。这正是 `SECURE_PROXY_SSL_HEADER` 的经典用法错误。

绑在回环地址上，就只有宿主机上的宝塔能连到它。

> ★ 同理，**永远不要为了调试把 backend 的 8000 端口映射出来。**
> 容器里调试用 `docker compose exec backend ...`，不要开端口。
>
> ★ 这条也是 `8080` **不需要**在云安全组里放行的原因 ——
> 安全组里开的 80/443 是给宝塔用的，和这个容器无关。

---

### 12.11 出问题了怎么查

```bash
docker compose ps                    # 谁没起来
docker compose logs -f backend       # 后端实时日志（Ctrl+C 退出）
docker compose logs --tail=50 web    # nginx 最近 50 行
docker compose exec backend python manage.py check    # 配置自检
docker compose exec db mysql -u root -p -e "SHOW DATABASES;"   # 数据库通不通
```

| 症状 | 最可能的原因 |
| --- | --- |
| `docker compose ps` 一直 `Restarting` | `.env` 没配好 → `docker compose logs backend` 第一行会写清楚 |
| **浏览器报「重定向次数过多」，整站打不开** | 宝塔没发 `X-Forwarded-Proto`（12.10 第 2 步 ①）。★ 极具迷惑性：`docker compose ps` 里容器全是 `healthy` |
| **`web` 起不来，报 `port is already allocated`** | `WEB_PORT` 被改成了 `80` —— 那是宝塔的。改回 `8080`，见 12.10「埋雷」 |
| **`502 Bad Gateway`（宝塔的报错页）** | ① 容器没起来 → `docker compose ps`；② 宝塔反代的目标端口和 `WEB_PORT` 对不上（12.4 那张表） |
| 网页打不开，`curl 127.0.0.1:8080/healthz` 却正常 | 宝塔那层没通：站点没建、反代没配，或**云安全组**没放行 80/443 |
| **浏览器报「连接不安全」/ 证书错误** | 宝塔里证书没签成或没选中 → 网站 → SSL 看状态；也确认访问的是域名不是 IP |
| **`http://` 打不开、`https://` 正常** | 正常。宝塔的「强制 HTTPS」在跳转 —— 证书**续期要靠 80**，所以安全组 80 不能关 |
| **证书 90 天后过期了，没自动续上** | 宝塔里的自动续期没跑成 → 网站 → SSL → 续签 看提示。★ 这不是 certbot，别去 `certbot certificates` 里找 |
| 页面能打开，接口全部失败 | nginx 还指着旧 backend IP → `docker compose restart web` |
| 生成大文章必然 504 | **两层都有超时，都得看**：容器里 nginx 的 `proxy_read_timeout` 不是 `600s`，或**宝塔那层还是默认的 60s**（12.10 第 2 步 ②） |
| 60 秒左右报错，但 `docker compose logs -f backend` 显示它还在正常跑完 | 症状同上，而且**几乎肯定是宝塔那层** —— 容器里那层已经配好了 |
| 后台能登录但没样式 | ① 上线 HTTPS 之后：`location /static/` 少了 `X-Forwarded-Proto`（见 12.10 验证清单 ①）；② `collectstatic` 没跑成功 → 看 backend 启动日志 |
| **`backend` 容器一直 unhealthy，但功能正常** | `SECURE_REDIRECT_EXEMPT` 没生效：健康检查拿到 301，而 `curl -f` 把 3xx 也算成功 |
| `400 Invalid HTTP_HOST header` | `ALLOWED_HOSTS` 里少了你访问用的那个名字 |
| 后台登录 403 | `CSRF_TRUSTED_ORIGINS` 的协议/域名和实际访问方式对不上（上了 HTTPS 必须写 `https://`） |
| `Access denied for user` | 密码里有 `$` 被 compose 吃掉了 |
| 能生成但说「未能获取外部资料」 | `SEARCH_API_KEY` 没填 |
| 配图一张都生不出来，提示「服务暂时不可用」 | `IMAGE_API_KEY` / `IMAGE_BASE_URL` / `IMAGE_MODEL` 有一个是空的（`message` 里会点名是哪个） |
| 生图返回 404 或「模型不存在」 | `IMAGE_BASE_URL` 少了 `/v1` 结尾，或者 `IMAGE_MODEL` 不是中转站实际提供的名字 |
| 生图提示 401 / 余额不足 | `IMAGE_API_KEY` 不对，或者**中转站账户余额没了**（和 LLM 的余额是两个账户） |
| 配图生成很慢后超时 | `IMAGE_TIMEOUT` 太小（默认 300 秒）。生图比生文慢得多，一张 30~90 秒是常态；如果确实要调大，**前端的 `IMAGE_GENERATE_TIMEOUT` 和 nginx 的 `proxy_read_timeout` 也要一起调**，否则只是把超时点往后挪了一层 |
| **图片生成成功但页面上全是裂图** | ① `uploads` 卷没挂上（`docker compose exec backend ls /app/media`）；② nginx 没重启（`docker compose restart web`）；③ 浏览器缓存了旧的 404 —— 强刷一次 |
| **「复制到公众号」的文字过去了、图全是裂的** | ① `MEDIA_PUBLIC_BASE_URL` 写成 `http://` 而网站是 `https://` → 被**混合内容**拦掉（F12 Console 里能看到 Mixed Content 警告）；② 那一项干脆没填 → 后端会跳过所有图并给出中文提示 |
| 文章详情页按 F5 变 404 | `nginx.conf` 里 `/media/` 那条 `location` 没写，图片请求掉进了 SPA 的 `try_files ... /index.html` 回退 |
| **推送报 `40164`** | ★ **服务器公网 IP 不在公众号的「IP 白名单」里**。去公众号后台 → 开发 → 基本配置 → IP 白名单 加上。这是微信侧的硬性配置，代码改不了。报错原文会原样显示给你 |
| 推送报 `48001` / `api unauthorized` | 公众号没有「草稿箱」接口权限。已认证订阅号默认有；**未认证的个人号拿不到**，这就是推不出去的原因 |
| 推送报 `40001` / `invalid credential` | 多个进程在抢 `access_token`。本项目用的是 `/cgi-bin/stable_token`（就是微信为这个问题提供的），出现这条说明 `WECHAT_APP_SECRET` 可能填错了 |
| 推送报「还没有配图，无法推送」 | 不是 bug，是正常拦截 —— 公众号的草稿**必须有封面图**。先去配图面板生成至少一张 |

**推送相关的几个常见疑问：**

- **上一次的草稿被我在公众号后台删了怎么办？** 不用管。
  微信返回 `40007` 时系统会自动退回「新建一份草稿」，而不是报错。
- **重复推送会不会在草稿箱里攒出一堆重复草稿？** 不会。
  默认行为是**更新同一份**草稿。想强制新建，API 支持传 `force_new: true`
  （前端暂未暴露这个开关 —— 有了上面那条 `40007` 兜底，它基本用不上）。
- **删了配图记录，磁盘上的文件还在吗？** 会一起删掉，删文章级联删图也会。
  这是 `FileField` 的经典坑：**默认只删数据库记录，不删文件** ——
  不管的话 `media/` 会随着「重新生成 → 删掉不满意的」无限膨胀，
  而数据库里一条记录都没有，谁也不知道那些文件还有没有用。
  本项目用 `post_delete` 信号兜住了。

> **还有一个只在 Windows 上会遇到的坑**：如果你在 **Git Bash** 里手工执行
> `VITE_API_BASE_URL=/api/v1 npm run build`，MSYS 的路径转换会把 `/api/v1`
> 改写成 `C:/Program Files/Git/api/v1` 并**静默内联进 JS**。
> 用 `MSYS_NO_PATHCONV=1` 前缀可以关掉这个行为。
> （走 `docker compose build` 不受影响 —— 构建参数写在文件里，不经过 shell。）

---

## 十三、常见问题

**Q：生成很慢，一直转圈？**
A：正常。Agent 要真的联网搜索、打开网页、读完再写，30~120 秒是常态。
前端这个接口的超时单独设成了 5 分钟。

**Q：报错「还没有配置 LLM_MODEL，请在后端 .env 文件里填写模型名称」**
A：`.env` 里的 `LLM_MODEL` 是空的。填上模型名，比如 `gpt-4o-mini`、`deepseek-chat`。

**Q：报错「调用大模型失败」**
A：依次检查 `LLM_API_KEY` 对不对、`LLM_MODEL` 名字对不对、
`LLM_BASE_URL` 需不需要填（用官方 OpenAI 就留空）、账户余额够不够。

**Q：生成的文章里说「未能获取到任何外部资料」**
A：`SEARCH_API_KEY` 没配。去 <https://tavily.com> 注册一个免费账号填上就好了。
不配也能用，但它只能靠模型已有知识写，时效性没保障 —— 所以系统会如实提示你。

**Q：Google 的搜索 API 怎么配？照着老教程做了但报 403**
A：**配不出来，别再试了。** 不是你的配置问题 ——
Custom Search JSON API 已经**对新客户关闭**（2027-01-01 停用），
新项目调用会返回 `403 This project does not have the access to Custom Search JSON API`。
实测确认：API 已启用、计费已开通、Key 有效、cx 正确，依然是这条报错；
论坛里有人试过重新启用、等 24 小时、改用 OAuth、换项目、换 Key，全部无效。
用户端没有自助解决办法。请改用 Tavily，见上面的「关于联网搜索」。

**Q：切换 provider 后报了一个莫名其妙的错**
A：`SEARCH_BASE_URL` 忘了清空。它是所有 provider 共用的，
里面还留着上一家的地址。代码里有防呆会忽略它并打一条日志，
但最好的做法还是手动清空那一行。

**Q：MySQL 连不上 / 报版本错误**
A：Django 5.2 要求 MySQL 8.0.11+。本机是 5.7 的话，
先把 `DB_ENGINE` 改成 `sqlite` 跑起来，再考虑升级数据库。

**Q：端口 8000 被占用**
A：换个端口 `python manage.py runserver 8001`，
同时把 `frontend/.env` 里的 `VITE_API_BASE_URL` 改成 `http://localhost:8001/api/v1`。

**Q：前端提示「无法连接后端服务」**
A：后端没启动，或者端口对不上（见上一条），或者 `.env` 里的
`CORS_ALLOWED_ORIGINS` 没包含前端的地址。

**Q：Agent 只搜索不读网页？**
A：这是模型自己的判断。如果搜索结果里的摘要已经足够，它可能就不打开原文了 ——
这本身是合理行为，System Prompt 里也是这么教它的。想让它读得更勤，
可以在「补充要求」里写「请打开原文核实具体数据」。

**Q：配图一直转圈，是不是卡住了？**
A：正常。**生图比生文慢得多，一张 30~90 秒是常态**，比等文章还久。
前端给这个接口单独设了 5 分钟超时（默认的 30 秒必定被掐断）。
注意配图是**逐张串行**生成的，8 张就是好几分钟 —— 这是刻意的，
并发打中转站很容易触发限流，而且失败的图会白花钱。

**Q：配图报「配图生成服务暂时不可用」**
A：先读这句话后面的括号 —— **它会点名缺的是哪个变量**
（生图服务未配置时是这个提示，不是服务器坏了）。
三个变量缺任何一个都会这样：`IMAGE_API_KEY`、`IMAGE_BASE_URL`、`IMAGE_MODEL`。
都填了还报，就依次查 Key 对不对、中转站余额够不够、模型名是不是中转站真的提供。

**Q：生成的图和我想要的不一样 / 画风乱七八糟**
A：**`IMAGE_STYLE_PROMPT` 决定整体画风** —— 它是拼在每条配图建议前面的，
所以是让一篇文章里几张图风格统一的关键。这是目前唯一能在界面外调的旋钮。
（后端接口支持传自定义 `prompt` 覆盖建议原文，但**前端面板还没暴露这个输入框**。）
另外生图模型的提示词理解能力差异很大，同一个词在不同模型上出图可能完全不同 ——
换个模型试试往往比反复调词有效。

**Q：图片生成成功了，但页面上是裂图**
A：**几乎都是部署环境的问题，不是生图的问题。**
F12 → Network 里看那个图片请求：
**404 = 图没到 nginx**（`uploads` 卷没挂上，或者 nginx 没重启）；
**200 但图是破的**才是生图本身的问题。
12.7 有专门的「配图专项排查」三条命令，照着跑一遍就能定位。

**Q：推送到公众号报 `40164`**
A：**服务器公网 IP 不在公众号的 IP 白名单里。**
去公众号后台 → 开发 → 基本配置 → IP 白名单，把服务器 IP 加上。
这是微信侧的硬性配置，代码改不了，也没有绕过的方法。
（我们会把微信的原话原样显示给你，就是为了让你一眼知道该去做什么。）

**Q：推送到公众号报 `48001` / `api unauthorized`**
A：公众号没有「草稿箱」这个接口的权限。**已认证的订阅号默认有**，
未认证的个人号拿不到 —— 这是平台限制，不是配置问题。

**Q：为什么推送按钮是灰的？**
A：因为这篇文章还没有配图。**公众号的草稿必须有封面图**，
微信的 `draft/add` 接口里 `thumb_media_id` 是必填的 ——
没有配图**根本推不出去**。先去配图面板生成至少一张，按钮就会变成可点。
鼠标停在灰按钮上会告诉你原因。

---

## 十四、V1 没做的事

按需求文档约定，以下**明确不在 V1 范围内**：

- ❌ 多 Agent 协作、MCP、RAG 向量库
- ❌ Redis / Celery / 微服务 / K8s（生成是同步的，链路最短最好调试）
- ❌ **自动发布到微信公众号** —— ★ 这一条**永远不会做**，不是「还没排上」。
  现在做的是**推送到草稿箱**，发布那一下必须由人在公众号后台点。
  这条是产品红线，也是平台限制（个人主体订阅号 2025-07 起拿不到
  `freepublish/submit` 权限），两者正好指向同一个做法。
- ❌ 视频生成、**文生图的「改图 / 重绘」**（现在只能重新生成一张，不能局部修改）
- ❌ 小红书 / 抖音等多平台分发
- ❌ 复杂权限、团队协作、付费系统

**已经做完的（原计划里没有，后来加的）：**

- ✅ **AI 配图** —— 封面图 + 正文插图，走 OpenAI 兼容中转站，生成与否由你决定
- ✅ **推送到公众号草稿箱** —— 正文图会自动压到 1MB 以内、封面裁成 900×500，
  重复推送是更新同一份草稿

**V1.1 可以考虑的方向**（按性价比排序）：

1. **SSE 流式推送** —— 现在 POST 要等 30~120 秒（配图一张还要再加 30~90 秒），
   改成流式能看到「正在搜索…正在读第 2 个网页…正在画第 3 张图…」
2. **异步任务化** —— 接 Celery 或 Django 的 background task，请求立刻返回，前端轮询/订阅进度。
   ★ 配图是**同步生成**的，一篇文章 8 张图就是 8 次串行请求，
   这是目前最该异步化的一处
3. **LangGraph 化** —— 把「大纲人工确认 → 写作 → 事实核查」做成固定流程图，让用户在中途介入
4. **多平台适配** —— 小红书/抖音的文案风格差异很大，可以在 Prompt 层加平台模板。
   `wechat_publishes` 这张表刻意叫了平台前缀，就是为了那天不用改表名
5. **来源可信度评分** —— 按域名给来源打分，优先引用权威来源
6. **配图的工作台化** —— 现在只能逐条按建议生成，将来可以支持自定义提示词、
   局部重绘、从图库选图

---

## 许可

仅供学习与内部使用。
