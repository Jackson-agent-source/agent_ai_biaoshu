"""研究方向目录：展示名称与实际检索用词分离，可保留自定义关注词。"""
MAX_FOCUS_TERMS = 16

RESEARCH_PRESETS = [
    {"label": "Web3", "group": "主线", "description": "以太坊与L2、DeFi、RWA、稳定币及监管动态", "terms": ["Web3", "以太坊 Layer2", "DeFi RWA", "稳定币监管"]},
    {"label": "AI出海", "group": "主线", "description": "AI产品海外增长、定价、渠道、本地化与合规案例", "terms": ["AI出海", "AI产品海外", "AI startup global expansion", "AI SaaS海外"]},
    {"label": "AI工具研究", "group": "主线", "description": "工具能力、评测、价格与工作流对比", "terms": ["AI工具", "AI tools", "AI评测"]},
    {"label": "AI落地企业", "group": "主线", "description": "企业案例、实施成本、数据治理与ROI", "terms": ["企业AI落地", "enterprise AI adoption", "AI ROI"]},
    {"label": "AI赋能行业", "group": "主线", "description": "制造、医疗、教育、金融与零售应用", "terms": ["AI行业应用", "AI制造", "AI医疗", "AI教育"]},
    {"label": "投资", "group": "主线", "description": "资产配置、估值框架与风险管理", "terms": ["资产配置", "投资研究", "估值"]},
    {"label": "美股", "group": "主线", "description": "企业财报、现金流、竞争优势与估值", "terms": ["美股", "US stocks earnings", "美股财报"]},
    {"label": "BTC", "group": "主线", "description": "比特币ETF、链上数据、挖矿与监管", "terms": ["比特币", "Bitcoin ETF", "BTC"]},
    {"label": "纳指", "group": "主线", "description": "纳指100、综合指数、成分权重与ETF", "terms": ["纳斯达克指数", "Nasdaq 100", "纳指ETF"]},
    {"label": "港卡", "group": "主线", "description": "香港银行账户、官方要求、费用与跨境服务", "terms": ["香港银行开户", "香港银行卡", "香港银行账户"]},
    {"label": "AI Agent", "group": "拓展", "description": "多智能体、MCP、自动化工作流与可靠性", "terms": ["AI Agent", "MCP智能体", "多智能体"]},
    {"label": "AI商业化与SaaS", "group": "拓展", "description": "订阅收入、付费转化、单位经济与护城河", "terms": ["AI SaaS", "AI商业化", "AI订阅收入"]},
    {"label": "算力与半导体", "group": "拓展", "description": "GPU、数据中心、推理成本与能源约束", "terms": ["AI算力", "GPU数据中心", "半导体"]},
    {"label": "宏观与利率", "group": "拓展", "description": "美联储、通胀、美债与美元流动性", "terms": ["美联储利率", "美债收益率", "美元流动性"]},
    {"label": "稳定币与支付", "group": "拓展", "description": "稳定币、储备披露、支付场景与监管", "terms": ["稳定币监管", "stablecoin payments", "稳定币储备"]},
    {"label": "跨境金融", "group": "拓展", "description": "汇率、跨境支付、账户费用与规则变化", "terms": ["跨境支付", "香港银行费用", "外汇政策"]},
]


def search_terms(label: str) -> list[str]:
    return next((item["terms"] for item in RESEARCH_PRESETS if item["label"].casefold() == label.casefold()), [label])
