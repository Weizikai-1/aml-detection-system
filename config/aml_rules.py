"""
业务戒律配置
"""
AML_RULES = {
    "mandatory": [
        {
            "id": "M1",
            "name": "必须使用真实数据",
            "description": "所有分析判断必须基于真实交易数据，禁止臆测、假设或编造不存在的交易信息、账户信息、金额等。",
            "enforcement": "hard",
        },
        {
            "id": "M2",
            "name": "必须标注可疑理由",
            "description": "每笔判定为可疑的交易必须标注具体违规行为和证据，说明触发了哪条规则、为什么可疑。",
            "enforcement": "hard",
        },
        {
            "id": "M3",
            "name": "风险评分范围 0-100",
            "description": "风险评分必须在 0-100 分范围内，分值越高表示可疑程度越高，输出时必须附带具体分数。",
            "enforcement": "hard",
        },
        {
            "id": "M4",
            "name": "证据链完整可追溯",
            "description": "所有可疑判定必须有完整证据链，从交易数据到规则命中到结论，每一步都可追溯和验证。",
            "enforcement": "hard",
        },
    ],
    "prohibited": [
        {
            "id": "P1",
            "name": "禁止遗漏高风险交易",
            "description": "规则引擎已命中的高风险交易（评分≥70分）禁止无理由降级或排除，如排除必须给出明确的排除依据。",
            "enforcement": "hard",
        },
        {
            "id": "P2",
            "name": "禁止误报正常交易",
            "description": "缺乏充分证据时不得随意将正常交易标记为可疑，严禁为了提高召回率而牺牲准确率。",
            "enforcement": "hard",
        },
        {
            "id": "P3",
            "name": "禁止无证据判定可疑",
            "description": "没有真实交易数据支撑、没有规则命中证据、没有合理业务解释的，不得判定为可疑交易。",
            "enforcement": "hard",
        },
        {
            "id": "P4",
            "name": "禁止主观臆断",
            "description": "不得基于账户名称、交易对手等表面信息做主观推断，所有结论必须有数据和规则支撑。",
            "enforcement": "soft",
        },
    ],
    "risk_score_scale": {
        "min": 0,
        "max": 100,
        "unit": "分",
        "description": "0分表示完全正常，100分表示确定可疑，分值越高可疑程度越高",
    },
}
