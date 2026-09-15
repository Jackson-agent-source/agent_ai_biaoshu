# 微信公众号 AI Content Agent（V1）

一个**真的会用工具**的公众号内容创作助手。

你告诉它「写一篇关于 2026 年 AI Agent 最新发展的公众号文章」，
它会自己判断要不要查资料 → 自己调用搜索工具 → 自己决定打开哪个网页读原文 →
综合资料写出一篇结构完整的文章 → 把用到的来源交给你核对。

**它不会自动发布。** V1 只生成草稿，发布这一下永远由你亲自点。

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
```

**关键点：第 4~8 步的循环次数和顺序，是模型自己决定的，不是代码写死的。**
代码只负责给它工具和规矩。这就是 Agent 和「固定工作流」的区别。

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
Pydantic、httpx、BeautifulSoup4、trafilatura、MySQL 8 / SQLite

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
│       └── articles/             # 文章
│           ├── models.py         # Article
│           ├── services.py       # 所有写操作都在这里
│           ├── serializers.py
│           └── views.py
│
└── frontend/                     # Vue 前端
    ├── package.json
    ├── vite.config.ts
    ├── Dockerfile                # ★ 多阶段：node 构建 → nginx 托管
    ├── nginx.conf                # ★ 对外唯一入口，同时反代 /api/
    ├── .dockerignore             # ★ 挡住本地 .env，否则会写死 localhost
    ├── .env.example
    ├── README.md                 # 前端自己的说明
    └── src/
        ├── api/                  # 接口封装（http.ts 负责拆信封 + 错误翻译）
        ├── stores/               # Pinia 状态
        ├── pages/                # 页面
        ├── components/           # 组件
        ├── types/                # TypeScript 类型
        └── utils/
```

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
6. 生成完会自动跳到文章详情页，你能看到正文、引用来源、风险提示

如果想看 Agent 到底做了什么，回到首页看「最近的运行」，
里面能看到每一次工具调用的参数和结果预览。

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

---

## 九、两个核心工具

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

---

## 十一、跑测试

```bash
cd backend
.venv/Scripts/python.exe -m pytest        # Windows
# .venv/bin/python -m pytest              # macOS / Linux
```

测试**完全离线**，不需要 API Key，不花钱，大约 4 秒跑完。

```
175 passed
```

覆盖内容：

| 文件 | 数量 | 测什么 |
| --- | --- | --- |
| `test_url_safety.py` | 37 | SSRF 防护：各种协议、内网 IP、DNS 解析后仍是内网、IPv6 绕过 |
| `test_tools.py` | 44 | 两个工具的成功/失败/超时/体积限制/重定向到内网/正文提取；两个搜索 Provider 的错误码翻译、语言参数、provider 选择与防呆 |
| `test_source_registry.py` | 22 | 防伪造来源、URL 归一化、去重 |
| `test_agent_service.py` | 27 | **端到端**：真实 LangChain 循环 + 假模型 + 假搜索；每日额度（单人 / 全站 / 跨零点重置） |
| `test_api.py` | 45 | 接口、参数边界、越权访问、错误信封；关闭注册、额度 429、限流文案是中文 |

**怎么做到不花钱的？** 见 `apps/agent/tests/fakes.py`：
用 `ScriptedChatModel` 按剧本回放模型的回答，但**工具调用、中间件、来源校验、
数据库写入全部是真的**。所以测出来的可信度远高于「把所有东西都 mock 掉」。

而且有一个安全网：**任何测试如果忘了打桩，会立刻失败而不是偷偷去调付费大模型。**

其他检查：

```bash
python manage.py check                 # 配置检查
python manage.py makemigrations --check # 确认模型和迁移文件一致
cd ../frontend && npm run build         # 前端类型检查 + 打包
```

---

## 十二、部署到云服务器

这一节假设：**Ubuntu / Debian，只有公网 IP、没有域名，给一小群人用。**

### 12.1 部署后长什么样

```
                        ┌─────────────────────────────────────────┐
   浏览器 ──── 80 ───▶  │  web 容器（nginx）                        │
   http://<IP>          │   ├─ /                    → 前端静态文件   │
                        │   ├─ /api/ /admin/ /health/               │
                        │   │        → 反代 http://backend:8000     │
                        │   └─ /static/             → 反代 backend   │
                        └─────────────────────────────────────────┘
                                     │ 容器内网，不映射到宿主机
                        ┌────────────▼────────────────────────────┐
                        │  backend 容器（gunicorn 3 worker × 4 线程）│
                        └────────────┬────────────────────────────┘
                        ┌────────────▼────────────────────────────┐
                        │  db 容器（mysql:8.0）                     │
                        └─────────────────────────────────────────┘
```

**为什么让 nginx 同时管前端和接口（同源）？**

前端构建时 `VITE_API_BASE_URL=/api/v1` 是**相对路径**，请求自动打到同一个 `IP:80`。
于是 CORS 预检、跨域 Cookie 这一整类问题**根本不会发生**，
`.env` 里也不用填 IP。将来买了域名，只要改 nginx，**前端一个字都不用动、不用重新构建**。

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

# 3. 防火墙：只开 22 和 80（★ 装了宝塔的话看下面的说明，别用 UFW）
sudo ufw allow 22/tcp && sudo ufw allow 80/tcp && sudo ufw enable
# ★ 不要开 8000 / 3306 / 5173

# 4. ★★★ 去云厂商控制台的安全组里，放行 80 端口的入方向流量
#    安全组是独立于 UFW 的第二道墙。UFW 全配对了但网页打不开，99% 是这里没放行。
```

> ### ★ 服务器上装了宝塔面板的话，这一节要改两处
>
> **① 不要用 UFW，改用宝塔的「安全」页面。**
> 宝塔自己在管 iptables / firewalld，再开一个 UFW 两套规则会互相打架，
> 而且宝塔会弹警告。放行端口请去 **宝塔 → 安全 → 添加端口规则**。
> 已经手贱开了 UFW 的话：`sudo ufw disable`。
>
> **② 端口从 80 改成 8080。**
> 宝塔自带一个 nginx 占着 80。所以 `.env` 里用默认的 `WEB_PORT=8080`，
> 浏览器访问 `http://<IP>:8080`，**宝塔那边一个字都不用动**。
> 用 80 的话 `docker compose up` 会直接报：
> `Bind for 0.0.0.0:80 failed: port is already allocated`
>
> 跑通之后如果想让地址干净一点（不带端口号），再去
> **宝塔 → 网站 → 添加站点 → 反向代理**，目标填 `http://127.0.0.1:8080`，
> 同时把 `.env` 里的 `WEB_BIND` 改成 `127.0.0.1`。
> 好处是将来买了域名可以用宝塔一键上 HTTPS，见 12.10。

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

**必须改的六项：**

| 变量 | 怎么填 | 填错的症状 |
| --- | --- | --- |
| `DJANGO_SECRET_KEY` | `openssl rand -base64 48` | 容器启动时被 entrypoint 直接拒绝 |
| `ALLOWED_HOSTS` | `<你的公网IP>,127.0.0.1,localhost` | Django 返回 `400 Invalid HTTP_HOST header`，**而且不告诉你它期望什么** |
| `CSRF_TRUSTED_ORIGINS` | `http://<你的公网IP>` | `/admin/` 登录时 403 |
| `DB_PASSWORD` / `DB_ROOT_PASSWORD` | `openssl rand -base64 32 \| tr -d '/+='` | 数据库连不上 |
| `LLM_API_KEY` | 你的 DeepSeek Key | 生成时报「调用大模型失败」 |
| `SEARCH_API_KEY` | 你的 Tavily Key | 能生成，但会说「未能获取到任何外部资料」 |

**三个最容易踩的坑，每个都单独说一遍：**

**① `ALLOWED_HOSTS` 里的 `127.0.0.1` 不能删。**
容器健康检查请求的是 `http://127.0.0.1:8000/health/`。删了它，
容器会永远是 `unhealthy` 状态 —— 而 `docker compose ps` 只会显示一个状态，
**不会告诉你为什么**。

**② `CSRF_TRUSTED_ORIGINS` 必须带 `http://` 前缀。**
写 `203.0.113.10` 是**不生效**的，而且 Django **不会报错、只会默默拒绝**。
必须写 `http://203.0.113.10`。

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
| ① 频率限流 | 短时间狂点按钮 | `THROTTLE_GENERATE` / `THROTTLE_LOGIN` | 用 `locmem` 时每个 worker 各算各的 |
| ② **单用户每日额度** | 一个人正常用但用很多 | `AGENT_DAILY_QUOTA_PER_USER` | ✅ 天然准确 |
| ③ **全站每日总额度** | 一群人各刷各的 / 注册小号 | `AGENT_DAILY_QUOTA_GLOBAL` | ✅ 天然准确 |

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

**同时把注册关掉：**

```
ALLOW_REGISTRATION=False
```

开着的话，公网上的陌生人注册之后就能用**你的** `LLM_API_KEY` 生成文章，
这钱是你出的。关掉之后账号由管理员创建（见 12.6）。

---

### 12.6 构建、启动、建管理员

```bash
docker compose build              # ★ 先构建（前端构建最慢，3~5 分钟）
docker compose up -d              # 再启动
docker compose ps                 # db / backend 应该是 healthy，web 是 Up
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
curl -i http://localhost/health/          # 期望 200 + {"success":true}
curl -i http://<IP>:8000/                 # 期望「连接被拒」—— 这是正确结果
docker compose logs backend | grep -i traceback    # 期望没有任何输出
```

然后**必须用浏览器**（不能用 curl 代替，要验证的是 nginx + 浏览器的真实行为）：

| 验证项 | 期望结果 | 失败时先查 |
| --- | --- | --- |
| 打开 `http://<IP>/` | 登录页正常，**F12 无 CORS 报错** | nginx 日志 |
| **在文章详情页按 F5** | 正常显示，不是 404 | `nginx.conf` 的 `try_files` 那行 |
| 生成一篇文章，掐表 | **2 分钟内出结果，无 504** | nginx `proxy_read_timeout` / LLM Key |
| 连点生成按钮 | 出现**中文**「操作太频繁了，请稍后再试」 | 限流配置 |
| 跑到单用户额度上限 | 出现「达到每日上限（20 篇）」 | 额度配置 |
| 打开 `http://<IP>/admin/` | **404**（因为已经换路径了） | — |
| 打开 `http://<IP>/<你的后台路径>` | 后台能登录，**CSS 正常显示** | `collectstatic` / whitenoise |

最后一条特别值得做：后台 CSS 正常 = 静态文件链路（whitenoise +
`/static/` 反代 + `collectstatic`）整条是通的。

---

### 12.8 更新流程

```bash
git pull                          # 方案 B 的话改成重新 rsync 一次
docker compose up -d --build
docker compose restart nginx      # ★★★ 这一行不能省
```

> **为什么必须重启 nginx？**
> nginx 在启动时解析 `proxy_pass http://backend:8000` 里的主机名，
> 把 **IP 缓存下来**。重新构建 backend 后容器 IP 会变，
> 而 nginx 还在往旧 IP 发请求 → 整个站 502，而 backend 本身完全正常。
> 这个现象非常难查，因为两个容器看起来都好端端的。

改了前端代码时，`--build` 会重新跑一遍 Vite 构建，这一步在 2 核机器上要几分钟，
是正常的。

---

### 12.9 备份

```bash
docker compose exec -T db mysqldump -u root -p"$DB_ROOT_PASSWORD" \
  --single-transaction --default-character-set=utf8mb4 ai_content_agent \
  | gzip > ~/backup-$(date +%F).sql.gz
```

`--single-transaction` 保证导出的是一致性快照，**不锁表**，线上可以随时跑。

放进 crontab 每天凌晨 3 点自动跑。**并且留一份到另一台机器或对象存储** ——
备份和数据库放在同一块盘上，等于没备份。

> ★ **`mysql_data` 这个 volume 里存着全部文章和账号。**
> `docker compose down -v` 会删掉它。平时更新只用 `down` 不要加 `-v`。

---

### 12.10 ⚠️ 必须知道的：明文 HTTP 的代价

**这是「只有 IP、没有域名」绕不过去的缺陷，请务必读完再决定。**

用户的登录凭证存在浏览器 localStorage 里，每个请求都以
`Authorization: Token xxx` 的形式**明文**发送。这意味着：

> **同一个 WiFi 下的任何人，都能抓到它，然后冒充这个用户登录。**

**如果这一小群人都在可信网络里**（公司内网、家里），风险可以接受。

**如果不能接受，解法只有 HTTPS，而 HTTPS 需要域名。**
但成本比想象中低得多，而且**代码已经为这一天准备好了**：

```
1. 买个最便宜的域名（.xyz / .top，一年几十块）→ A 记录指向服务器 IP
2. certbot 签一张 Let's Encrypt 证书（免费，自动续期）
3. nginx 里加 443 的 server 块 + 证书路径
4. .env 里把 USE_HTTPS 改成 True，重启后台
```

第 4 步会一次性生效下面这些（`settings.py` 里已经写好了）：
`SECURE_SSL_REDIRECT`（HTTP 自动跳 HTTPS）、Cookie Secure、
HSTS、`X-Forwarded-Proto` 信任。

**改动量接近零，不需要改任何业务代码。**

> `sslip.io` / `nip.io` 这类「拿 IP 当域名」的服务理论上也能签证书，
> 但 Let's Encrypt 对这类共享域名有严格速率限制且不受你控制，
> **不建议依赖**。

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
| 网页打不开，`curl localhost` 却正常 | **云厂商安全组**没放行 80（不是 UFW） |
| 页面能打开，接口全部失败 | nginx 还指着旧 backend IP → `docker compose restart nginx` |
| 生成大文章必然 504 | nginx `proxy_read_timeout` 不是 `600s` |
| 后台能登录但没样式 | `collectstatic` 没跑成功 → 看 backend 启动日志 |
| `400 Invalid HTTP_HOST header` | `ALLOWED_HOSTS` 里少了你访问用的那个名字 |
| 后台登录 403 | `CSRF_TRUSTED_ORIGINS` 少了 `http://` 前缀 |
| `Access denied for user` | 密码里有 `$` 被 compose 吃掉了 |
| 能生成但说「未能获取外部资料」 | `SEARCH_API_KEY` 没填 |

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

---

## 十四、V1 没做的事

按需求文档约定，以下**明确不在 V1 范围内**：

- ❌ 多 Agent 协作、MCP、RAG 向量库
- ❌ Redis / Celery / 微服务 / K8s（生成是同步的，链路最短最好调试）
- ❌ **自动发布到微信公众号**（这需要公众号的开发者权限，且风险太高，永远应该人工确认）
- ❌ AI 配图 / 视频生成
- ❌ 小红书 / 抖音等多平台分发
- ❌ 复杂权限、团队协作、付费系统

**V1.1 可以考虑的方向**（按性价比排序）：

1. **SSE 流式推送** —— 现在 POST 要等 30~120 秒，改成流式能看到「正在搜索…正在读第 2 个网页…」
2. **异步任务化** —— 接 Celery 或 Django 的 background task，请求立刻返回，前端轮询/订阅进度
3. **LangGraph 化** —— 把「大纲人工确认 → 写作 → 事实核查」做成固定流程图，让用户在中途介入
4. **多平台适配** —— 小红书/抖音的文案风格差异很大，可以在 Prompt 层加平台模板
5. **来源可信度评分** —— 按域名给来源打分，优先引用权威来源

---

## 许可

仅供学习与内部使用。
