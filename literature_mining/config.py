import os
from pathlib import Path
from typing import Dict


# Default location of API.txt is alongside this LLM_RAG_V3 package,
# so that the folder can be copied to another machine and still work.
_ROOT = Path(__file__).resolve().parents[1]
API_TXT_PATH = os.environ.get("API_TXT_PATH", str(_ROOT / "API.txt"))

# Contact email used in User-Agent
CONTACT_EMAIL = "qtle@connect.ust.hk"

# Default User-Agent for publisher TDM requests and LLM calls
USER_AGENT = f"Retrosynthesis search (mailto:{CONTACT_EMAIL})"

# Default SiliconFlow OpenAI-compatible base URL
#SILICONFLOW_BASE_URL = os.environ.get(
#    "SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1"
#SILICONFLOW_BASE_URL = "https://api.siliconflow.cn/v1"
#)


def load_api_keys(path: str = API_TXT_PATH) -> Dict[str, str]:
    """
    Load API keys from a simple key:value text file.

    Expected format (one per line):
        elsevier:YOUR_ELSEVIER_KEY
        spring nature:YOUR_SPRINGER_NATURE_KEY
        wiley:YOUR_WILEY_KEY
        rsc:YOUR_RSC_API_KEY
        siliconflow:YOUR_SILICONFLOW_API_KEY
    """
    keys: Dict[str, str] = {}
    if not os.path.exists(path):
        return keys

    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or ":" not in line:
                continue
            name, value = line.split(":", 1)
            keys[name.strip()] = value.strip()
    return keys


#def get_siliconflow_api_key(keys: Dict[str, str]) -> str:
#    """
#    Pick the SiliconFlow API key from the loaded key dict or environment.
#    """
#    key = keys.get("siliconflow") or os.environ.get("SILICONFLOW_API_KEY", "")
#    if not key:
#        raise RuntimeError(
#            "SiliconFlow API key not found. "
#            "Add a line 'siliconflow:YOUR_SILICONFLOW_API_KEY' to API.txt "
#            "or set the SILICONFLOW_API_KEY environment variable."
#        )
#    return key
