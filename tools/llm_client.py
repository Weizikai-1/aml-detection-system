"""
LLM 客户端封装
统一管理 DeepSeek API 调用
"""
from langchain_openai import ChatOpenAI
from config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL, LLM_CONFIG


def get_llm(temperature: float = None) -> ChatOpenAI:
    """
    获取 LLM 实例

    Args:
        temperature: 温度参数(为空则用默认值)

    Returns:
        ChatOpenAI 实例
    """
    return ChatOpenAI(
        model=DEEPSEEK_MODEL,
        api_key=DEEPSEEK_API_KEY,
        base_url=DEEPSEEK_BASE_URL,
        temperature=temperature or LLM_CONFIG["temperature"],
        max_tokens=LLM_CONFIG["max_tokens"],
        timeout=LLM_CONFIG["timeout"],
    )


def invoke_llm(prompt: str, system_prompt: str = "") -> str:
    """
    调用 LLM

    Args:
        prompt: 用户提示词
        system_prompt: 系统提示词

    Returns:
        LLM 响应文本
    """
    llm = get_llm()
    messages = []
    if system_prompt:
        messages.append(("system", system_prompt))
    messages.append(("human", prompt))

    for attempt in range(LLM_CONFIG["retry_times"]):
        try:
            response = llm.invoke(messages)
            return response.content
        except Exception as e:
            print(f"LLM 调用失败(第{attempt+1}次): {e}")
            if attempt == LLM_CONFIG["retry_times"] - 1:
                raise
    return ""
