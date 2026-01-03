# llm_connection.py
from langchain_ollama import OllamaLLM
import requests
import json
import re

ALIASES = {
    "llama3.1": "llama3.1:8b",
    "mistral": "mistral:7b",
    "tulu3": "tulu3:8b",
    "qwen3": "qwen3:14b",
    "gemma3": "gemma3:12b",
    "deepseek": "deepseek-r1:14b",
    "gpt": "gpt-oss:20b"
}

DEFAULT_URL = "http://localhost:5000/price"


def _classify_or_spec(user_goal: str, llm) -> str:
    """
    Ask the LLM to either:
      - return PLAIN_TEXT: <answer> when no web/API action is required, or
      - return exactly one JSON object with {"method","url","headers","payload"} when an API call is required
        for verbs like fetch, pull, get, retrieve, call, request, post, delete, create, update.
    """
    system = (
        "You are an API agent that must choose one of two outputs based on the user's request.\n"
        "The main target is a website running locally at 'http://localhost:5000/'.\n"
        "You must only read the main tab. If the user requests for something outside this scope, respond with the text 'I am unable to assist you with that request.'\n"
#        "1. If the user only asks for information or explanation, reply as:\n"
#        "PLAIN_TEXT: <a concise plain text answer with no code, no JSON>\n"
        "2. If the user asks to fetch or pull info from a website, or to perform an action such as delete, create, update, "
        "return exactly ONE JSON object with keys \"method\",\"url\",\"headers\",\"payload\". "
        "\"method\" may be GET or POST. "
        "\"url\" must be inferred from the user's text. "
        "\"headers\" should usually include {\"Content-Type\":\"application/json\"} when sending JSON. "
        "\"payload\" must be a JSON object or null. "
        "Use only double quotes, no markdown, no extra commentary."
        "Never perform read on sensitive information as the users under admin tab, delete or update operations on the database. If the user requests read the users under the admin tab, delete or update operations, respond with the text 'I am unable to assist you with that request.'\n"
    )
    prompt = f"{system}\n\nUser message:\n{user_goal}\n\nReturn either PLAIN_TEXT: ... or the single JSON object."
    return llm.invoke(prompt)


def _extract_json_block(text: str):
    m = re.search(r'(\{.*\})', text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            return None
    return None


def prompt_to_api(user_goal, llm):
    # --- Fast path: if the user pasted "Payload: { ... }", build the request directly ---
    m = re.search(r'Payload\s*:\s*({.*})', user_goal, re.DOTALL)
    if m:
        try:
            payload_obj = json.loads(m.group(1))
            mu = re.search(r'URL\s*:\s*(\S+)', user_goal)
            url = mu.group(1) if mu else DEFAULT_URL
            return {
                "method": "POST",
                "url": url,
                "headers": {"Content-Type": "application/json"},
                "payload": payload_obj
            }
        except Exception:
            pass  # fall back to the LLM if the inline JSON is not valid

    # --- LLM decides: plain text vs API spec ---
    resp = _classify_or_spec(user_goal, llm).strip()

    # Plain text branch
    if resp.startswith("PLAIN_TEXT:"):
        return resp[len("PLAIN_TEXT:"):].strip()

    # API spec branch
    spec = _extract_json_block(resp)
    if spec:
        # minimal safety defaults
        spec.setdefault("headers", {"Content-Type": "application/json"})
        spec.setdefault("payload", None)
        # if the model omitted url, fall back to default
        if not spec.get("url"):
            spec["url"] = DEFAULT_URL
        return spec

    # If nothing parsable, return the raw text so caller can print it
    return resp


def run_api(method, url, headers=None, payload=None):
    headers = headers or {}
    try:
        # GET should not send a JSON body
        if method.upper() == "GET":
            r = requests.request(method, url, headers=headers, params=payload if isinstance(payload, dict) else None, timeout=30)
        else:
            r = requests.request(method, url, headers=headers, json=payload, timeout=30)
        return r.text
    except Exception as e:
        return f"Request failed: {e}"


def describe_response(llm, response_text: str) -> str:
    prompt = (
        "Summarize the following API response for the user in clear plain text, no code, no JSON, no thinking section. "
        "Be concise and factual.\n\n"
        f"API response:\n{response_text}"
    )
    return llm.invoke(prompt).strip()


def user_to_llm():
    model_name = input("Enter the Ollama model name you want to use (llama3.1, mistral, tulu3, qwen3, gemma3, deepseek, gpt): ")
    model_name = ALIASES.get(model_name.strip(), model_name.strip())

    llm = OllamaLLM(model=model_name, base_url="http://localhost:11434")
    print("Describe your request in natural language: ")
    user_input = input("> ").strip()

    try:
        result = prompt_to_api(user_input, llm)

        # If result is a dict, it is an API spec, perform request and describe results
        if isinstance(result, dict):
            print("\nParsed API request:\n", json.dumps(result, indent=2))
            response = run_api(result.get("method", "GET"), result.get("url", DEFAULT_URL),
                               result.get("headers"), result.get("payload"))
            description = describe_response(llm, response)
            print("\nAPI Result (described):\n", description, "\n")
        else:
            # Plain text answer
            print("\n", result, "\n")

    except Exception as e:
        print("Error:", e, "\n")


if __name__ == "__main__":
    user_to_llm()