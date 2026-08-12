import requests

from config import get_logger

log = get_logger()


class OllamaClient:
    def __init__(self, base_url: str, model: str, session=None, timeout: int = 180):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.session = session or requests.Session()
        self.timeout = timeout

    def generate(self, prompt: str, system: str | None = None) -> str:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        body = {"model": self.model, "messages": messages, "stream": False}
        try:
            resp = self.session.post(f"{self.base_url}/api/chat", json=body,
                                     timeout=self.timeout)
            data = resp.json()
            return data["message"]["content"]
        except Exception as exc:
            log.info("LLM | generate failed: %s", exc)
            return ""
