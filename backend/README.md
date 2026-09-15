# 后端 —— 微信公众号 AI Content Agent

Django 5.2 + DRF + LangChain 1.x。

**完整的项目说明请看仓库根目录的 [`../README.md`](../README.md)**，
这里只放后端自己的东西。

---

## 快速开始

```bash
cd backend

python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS / Linux

pip install -r requirements.txt

copy .env.example .env          # Windows
# cp .env.example .env          # macOS / Linux
# ★ 然后打开 .env，填上 LLM_API_KEY 和 LLM_MODEL

python manage.py migrate
python manage.py createsuperuser
python manage.py runserver 8000
```

验证：<http://127.0.0.1:8000/health/>

---

## 常用命令

```bash
python manage.py check                    # 检查配置有没有问题
python manage.py makemigrations --check   # 确认模型和迁移文件一致（改完 models.py 跑一下）
python manage.py migrate                  # 建表 / 更新表结构
python manage.py shell                    # 交互式调试
python manage.py runserver 8000

.venv/Scripts/python.exe -m pytest        # 跑测试（Windows）
.venv/Scripts/python.exe -m pytest apps/agent/tests/test_tools.py -v   # 只跑某个文件
```

---

## 代码分层（改代码前先看这个）

```
views.py          很薄：收请求 → Serializer 校验 → 调用 Service → 返回
   ↓
serializers.py    输入校验 + 输出格式
   ↓
services/         业务逻辑，所有数据库写操作都在这里
   ↓
models.py         只定义数据结构，不写业务逻辑
```

**三条硬规则**（违反了会让项目变得难以维护）：

1. **Agent 不直接操作 Django Model。** Agent 层（`agents/`）只接收输入、返回
   `ArticleDraft`，落库由 `services/agent_service.py` 统一负责。
   这样 Agent 层可以脱离 Django 单独测试。
2. **Tool 不直接写数据库。** 工具是纯函数：输入 → 输出。
   `web_search` / `read_webpage` 只往 `SourceRegistry` 登记，不碰 Model。
3. **只有 `services/model_factory.py` 能读 API Key。** 其他文件一律
   `model = get_chat_model()`。

---

## 几个关键文件

| 文件 | 作用 | 改它的时机 |
| --- | --- | --- |
| `apps/agent/agents/content_agent.py` | 用 `create_agent` 搭 Agent、挂中间件 | 要加工具、改限制策略 |
| `apps/agent/prompts/content_agent_prompt.py` | System Prompt | 想调整写作风格、搜索策略 |
| `apps/agent/tools/web_search.py` | 搜索 Provider（tavily / google） | 要接 Exa、Bing，或改搜索参数 |
| `apps/agent/agents/tool_budget_middleware.py` | 工具调用预算 | 改超限后的行为 |
| `apps/agent/tools/url_safety.py` | SSRF 防护 | **除非你很清楚在做什么，否则别动** |
| `apps/agent/services/source_registry.py` | 防编造来源 | **同上，这是核心安全机制** |
| `apps/agent/services/agent_service.py` | 主流程编排（16 个步骤） | 要改生成流程 |
| `apps/agent/schemas/article.py` | 结构化输出定义 | 想加字段（记得同步改 Article 模型和迁移） |
| `config/settings.py` | 所有配置从环境变量读 | 加新配置项 |

---

## 加一个新工具（比如查天气）

1. 在 `apps/agent/tools/` 下新建 `weather.py`：

```python
from langchain_core.tools import tool

@tool
def get_weather(city: str) -> dict:
    """查询某个城市的天气。

    Args:
        city: 城市名，例如"北京"
    """
    return {"success": True, "city": city, "weather": "晴"}
```

2. 在 `apps/agent/tools/__init__.py` 里导出
3. 在 `content_agent.py` 的 `tools=[...]` 列表里加上它
4. 在 System Prompt 里告诉模型「什么时候该用它」
5. 补测试（参考 `tests/test_tools.py`）

**注意**：工具返回的 dict 里请带 `success` 字段 —— 审计中间件靠它判断这次调用成功还是失败。
如果这个工具会访问外部地址，**必须先用 `url_safety.validate_public_url()` 校验**。

---

## 加一个新接口

1. `views.py` 里加一个 `APIView`，**保持很薄**
2. 数据库操作写进 `services.py`
3. `urls.py` 里注册路由
4. 补测试（参考 `tests/test_api.py`）

⚠️ 路由顺序有坑：`path("stats/", ...)` 必须写在 `path("<uuid:article_id>", ...)` 前面，
否则 `stats` 会被当成 uuid 去匹配。

---

## 测试

```
175 passed in ~4s
```

- **完全离线**，不需要 API Key，不花钱
- 用 `ScriptedChatModel` 假模型 + 假搜索服务，但**工具调用、中间件、
  来源校验、数据库写入全是真的**
- 有安全网：忘记打桩的测试会立刻失败，不会偷偷去调付费大模型

```bash
.venv/Scripts/python.exe -m pytest -v                       # 全部
.venv/Scripts/python.exe -m pytest apps/agent/tests/test_api.py   # 单个文件
.venv/Scripts/python.exe -m pytest -k "source" -v           # 按名字筛选
```

---

## 目录速查

```
apps/
  common/         统一异常、响应信封、request_id、Markdown 渲染、分页
  accounts/       登录注册（复用 Django 自带 User）
  conversations/  Conversation / Message
  agent/          ★ Agent 核心：模型工厂、工具、登记簿、编排服务、中间件
  articles/       Article 模型与业务层
config/           settings / urls / wsgi / asgi
```
