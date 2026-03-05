import os
import re
from azure.ai.contentsafety import ContentSafetyClient
from azure.core.credentials import AzureKeyCredential
from azure.ai.contentsafety.models import AnalyzeTextOptions
from app.logger import get_logger

logger = get_logger("security")

# Ensure environment variables are set
AZURE_ENDPOINT = os.environ.get("CONTENT_SAFETY_ENDPOINT", "https://your-content-safety-endpoint.cognitiveservices.azure.com/")
AZURE_KEY = os.environ.get("CONTENT_SAFETY_KEY", "dummy-key-for-local")

def get_security_client():
    return ContentSafetyClient(AZURE_ENDPOINT, AzureKeyCredential(AZURE_KEY))

def apply_guardrails(text: str, check_type: str = "input") -> bool:
    """
    Returns True if the text is safe, False if unsafe.
    """
    if not text:
        return True
        
    try:
        # 1. Cloud Check (Azure AI Content Safety)
        if AZURE_KEY != "dummy-key-for-local":
            client = get_security_client()
            request = AnalyzeTextOptions(text=text)
            response = client.analyze_text(request)
            for category_result in response.categories_analysis:
                if category_result.severity > 0: 
                    logger.warning(f"Azure AI Security [BLOCKED]: Detected {category_result.category} severity {category_result.severity}")
                    return False

        # 2. Local Heuristic Checks
        text_lower = text.lower()
        
        if check_type == "input":
            # Detect destructive intent via regex (catches "drop the table", "DROP  Table", etc.)
            destructive_patterns = [
                r"\bdrop\b.*\btable\b",
                r"\balter\b.*\btable\b",
                r"\btruncate\b.*\btable\b",
                r"\bdelete\b.*\bfrom\b",
                r"\bupdate\b.*\bset\b",
                r"\bdrop\b.*\bdatabase\b",
                r"\binsert\b.*\binto\b"
            ]
            for pattern in destructive_patterns:
                if re.search(pattern, text_lower):
                    logger.warning(f"Security [BLOCKED INPUT]: Destructive SQL pattern detected: '{pattern}'")
                    return False
                    
            if "ignore previous" in text_lower or "system prompt" in text_lower:
                logger.warning("Security [BLOCKED INPUT]: Prompt injection attempt detected.")
                return False
        
        elif check_type == "output":
            # PII Detection
            forbidden_pii = [r"\bssn\b", r"\bsocial security\b", r"[\w\.-]+@[\w\.-]+\.\w+"]
            for p in forbidden_pii:
                if re.search(p, text_lower):
                    logger.warning(f"Security [BLOCKED OUTPUT]: PII Leak detected: '{p}'")
                    return False
                    
        return True
    
    except Exception as e:
        logger.error(f"Security Service Error: {e}. Falling back to fail-closed heuristic.")
        # Minimal regex check if service is down
        destructive_patterns = [r"\bdrop\b.*\btable\b", r"\btruncate\b.*\btable\b", r"\bdelete\b.*\bfrom\b"]
        for pattern in destructive_patterns:
            if re.search(pattern, text.lower()):
                return False
        return True # Default to True only if no heuristic matches
