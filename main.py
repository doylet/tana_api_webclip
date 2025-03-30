from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import RedirectResponse, JSONResponse
from pydantic import BaseModel
import requests
from bs4 import BeautifulSoup
from bs4.element import Tag
from typing import Optional, Dict, List, Union
import uvicorn
import logging
import json
import re
import mimetypes
from urllib.parse import urlparse
from base64 import b64encode

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    # handlers=[
    #     logging.FileHandler("tana_webclip.log", mode="a", encoding="utf-8"),
    # ]
)
logger = logging.getLogger(__name__)


# App definition
app = FastAPI(
    title="Tana Webclip API",
    description="Extracts web content and posts it to Tana Input API.",
    version="1.0.0"
)

# Redirect / to /docs
@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse(url="/docs")

# Pydantic model
class TanaResponse(BaseModel):
    message: str
    status_code: str
    tana_error: Optional[str] = None

class ParseAndPostPayload(BaseModel):
    url: str
    api_token: str
    target_node_id: str

# Helpers
def clean_text(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    text = text.replace("\u00a0", " ")  # non-breaking space to space
    text = re.sub(r'[\r\n\t]+', ' ', text)
    text = re.sub(r'\s+', ' ', text)
    text = text.strip()
    if text == '"undefined"' or text.lower() == 'undefined':
        return None
    return text

def extract_rich_text(element: Tag) -> Optional[str]:
    parts = []

    for child in element.descendants:
        if isinstance(child, Tag):
            if child.name == "a" and child.get("href"):
                text = clean_text(child.get_text())
                href = child.get("href")
                if text and href:
                    parts.append(f"[{text}]({href})")
            # elif child.name == "code":
            #     code = clean_text(child.get_text())
            #     if code:
            #         parts.append(f"`{code}`")
        else:
            text = clean_text(str(child))
            if text:
                parts.append(text)

    joined = " ".join(parts)
    return clean_text(joined)


def extract_structured_content(soup: BeautifulSoup) -> List[Dict[str, Union[str, List[Dict[str, str]]]]]:
    body = soup.body
    if not body:
        return []

    structured = []
    current_section = {
        "name": "",
        "children": []
    }

    def flush_section():
        if current_section["children"]:
            structured.append(current_section.copy())

    for element in body.find_all(recursive=True):
        if isinstance(element, Tag):
            tag = element.name.lower()
            if tag in ["nav", "summary", "details"]:
                continue
            if tag in ["h1", "h2", "h3"]:
                flush_section()
                current_section = {
                    "name": clean_text(element.get_text()),
                    "children": []
                }
            
            elif tag in ["p", "li"]:
                rich_text = extract_rich_text(element)
                if rich_text:
                    current_section["children"].append({"name": rich_text})

    flush_section()
    return structured


@app.post("/parse_and_post", response_model=TanaResponse)
async def parse_and_post(payload: Union[ParseAndPostPayload, str]):
    try:
        if isinstance(payload, str):
            data = json.loads(payload)
            payload = ParseAndPostPayload(**data)
        else:
            data = payload.dict()
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

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
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
    logger.info(f"Response content type: {response.headers.get('Content-Type')}")
    title = clean_text(soup.title.string) if soup.title and soup.title.string else None
    if not title:
        parsed_url = urlparse(url)
        title = parsed_url.netloc + parsed_url.path
    logger.info(f"Extracted title: {title}")

    og_tags = {}
    meta_tags = {}
    for tag in soup.find_all("meta"):
        if tag.get("property", "").startswith("og:"):
            og_tags[tag["property"]] = tag.get("content", "")
        elif tag.get("name"):
            meta_tags[tag["name"]] = tag.get("content", "")

    tana_node = {
        "name": title or url,
        "description": None,
        "children": []
    }

    # Structured content extraction
    structured_sections = extract_structured_content(soup)
    logger.info(f"Extracted {len(structured_sections)} sections from the page.")
    MAX_SECTIONS = 100
    for i, section in enumerate(structured_sections):
        if i >= MAX_SECTIONS:
            tana_node["children"].append({
                "name": "⚠️ Content clipped",
                "description": "Only the first 100 sections were included."
            })
            break
        if section.get("name") and ("children" not in section or section["children"]):
            tana_node["children"].append(section)
    
    # Add meta + OG tags
    for key, value in {**meta_tags, **og_tags}.items():
        # safe_key = key.replace(":", " - ")
        k, v = clean_text(key), clean_text(value)
        if k and v:
            tana_node["children"].append({
                "name": "Meta: " + k,
                "children": [
                    {
                        "type": "field",
                        "attributeId": "nodeID",
                        "children": [
                            {
                                "name": v
                            }
                        ]
                    }
                ]
            })

    tana_request = {
        "targetNodeId": target_node_id,
        "nodes": [tana_node]
    }

    logger.info("Constructed Tana request:")
    logger.info(json.dumps(tana_request, indent=2))

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
        if tana_response.status_code != 200:
            logger.error(f"Tana API returned error: {tana_response.status_code} {tana_response.text}")
            raise requests.RequestException(response=tana_response)

        logger.info(f"Posted to Tana successfully: {tana_response.status_code}")
    except requests.RequestException as e:
        logger.error("Tana API failed — debugging nodes individually...")

        # Debug each node one at a time
        for i, child in enumerate(tana_node["children"]):
            test_node = {
                "targetNodeId": target_node_id,
                "nodes": [{
                    "name": tana_node["name"],
                    "children": [child]
                }]
            }
            try:
                test_resp = requests.post(
                    "https://europe-west1-tagr-prod.cloudfunctions.net/addToNodeV2",
                    headers=tana_headers,
                    json=test_node
                )
                test_resp.raise_for_status()
                logger.info(f"Node {i} succeeded")
            except requests.RequestException as single_error:
                logger.error(f"Node {i} failed: {json.dumps(child, indent=2)}")
                logger.error(f"Tana response: {single_error.response.text if single_error.response else 'No response'}")
    
        raise HTTPException(status_code=502, detail="Failed to post to Tana")

    return TanaResponse(
        message="Content extracted and sent to Tana successfully.",
        status_code="200"
    )

if __name__ == "__main__":
    app
