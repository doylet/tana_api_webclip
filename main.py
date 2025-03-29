from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
import requests
from bs4 import BeautifulSoup
from typing import Optional, Dict, List, Union
import uvicorn
import logging
import json
import re

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

# App definition
app = FastAPI(
    title="Tana Webclip API",
    description="Extracts web content and posts it to Tana Input API.",
    version="1.0.0"
)

# Redirect / → /docs
@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse(url="/docs")

# Pydantic model for request body
class ParseAndPostPayload(BaseModel):
    url: str
    api_token: str
    target_node_id: str

# Clean up HTML text
def clean_text(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    return re.sub(r"[\r\n]+", " ", text).strip()

@app.post("/parse_and_post", response_model=Dict[str, str])
async def parse_and_post(payload: Union[ParseAndPostPayload, str]):
    """
    Accepts a URL, API token, and Tana target node ID.
    Extracts page content and sends it to Tana.
    """
    try:
        if isinstance(payload, str):
            logger.info("Detected stringified JSON in body. Parsing...")
            data = json.loads(payload)
            payload = ParseAndPostPayload(**data)
        else:
            data = payload.dict()

        logger.info(f"Parsed payload:\n{json.dumps(data, indent=2)}")

    except Exception as e:
        logger.error(f"Failed to parse request body: {e}")
        raise HTTPException(status_code=422, detail="Invalid request format")

    return parse_and_post_internal(
        payload.url,
        payload.api_token,
        payload.target_node_id
    )

def parse_and_post_internal(url: str, api_token: str, target_node_id: str):
    logger.info(f"Processing URL: {url}")

    # Spoof browser headers to bypass bot protections
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Cache-Control": "max-age=0"
    }

    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        logger.info(f"Fetched {url} successfully")
    except requests.RequestException as e:
        logger.error(f"Failed to fetch URL: {e}")
        raise HTTPException(status_code=400, detail="Failed to fetch URL")

    soup = BeautifulSoup(response.text, "html.parser")

    # Extract content
    title = clean_text(soup.title.string if soup.title else url)
    og_tags = {}
    meta_tags = {}
    for tag in soup.find_all("meta"):
        if tag.get("property", "").startswith("og:"):
            og_tags[tag["property"]] = tag.get("content", "")
        elif tag.get("name"):
            meta_tags[tag["name"]] = tag.get("content", "")

    semantic_elements = soup.find_all(["main", "article", "section"])
    semantic_content = "\n\n".join(
        elem.get_text(separator="\n", strip=True) for elem in semantic_elements
    )

    # Build Tana node
    tana_node = {
        "name": title,
        "description": clean_text(url),
        "children": []
    }

    if "og:image" in og_tags:
        tana_node["children"].append({
            "name": "Image",
            "dataType": "file",
            "file": clean_text(og_tags["og:image"])
        })
        del og_tags["og:image"]

    if semantic_content:
        for paragraph in semantic_content.split("\n\n"):
            clean_paragraph = clean_text(paragraph)
            if clean_paragraph:
                tana_node["children"].append({
                    "name": clean_paragraph
                })
        else:
            tana_node["children"].append({
                "name": "No semantic content found"
            })

    for key, value in {**meta_tags, **og_tags}.items():
        clean_key = clean_text(key)
        clean_val = clean_text(value)
        if clean_key and clean_val:
            tana_node["children"].append({
                "name": clean_key,
                "description": clean_val
            })

    tana_request = {
        "targetNodeId": target_node_id,
        "nodes": [tana_node]
    }

    logger.info(f"Tana request payload:\n{json.dumps(tana_request, indent=2)}")

    # POST to Tana
    try:
        tana_headers = {
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json"
        }

        tana_response = requests.post(
            "https://europe-west1-tagr-prod.cloudfunctions.net/addToNodeV2",
            headers=tana_headers,
            json=tana_request
        )
        tana_response.raise_for_status()
        logger.info(f"Posted to Tana successfully: {tana_response.status_code}")

    except requests.RequestException as e:
        logger.error(f"Tana API error: {e}")
        logger.error(f"Tana response: {tana_response.text if 'tana_response' in locals() else 'No response'}")
        raise HTTPException(status_code=502, detail="Failed to post to Tana")

    return {"message": "Content extracted and sent to Tana successfully."}

if __name__ == "__main__":
    # uvicorn.run("main:app", host="0.0.0.0", port=10000)
    app