from qfactor.agent.llm import LLMClient

c = LLMClient()
print("enabled", bool(c.api_key), "model", c.model, "base", c.base_url, flush=True)
print(c.chat_json("Reply with a JSON object only.", 'Return {"ping": true}'), flush=True)
