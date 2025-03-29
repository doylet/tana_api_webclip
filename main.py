from fastapi import FastAPI, Request, HTTPException
from pydantic import BaseModel
import requests
from bs4 import BeautifulSoup
from typing import Optional, Dict, List
import uvicorn
import logging
import json
import os
import re

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

app = FastAPI()

def clean_text(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    return re.sub(r"[\r\n]+", " ", text).strip()

@app.post("/parse_and_post")
async def parse_and_post(request: Request):
    try:
        raw_data = await request.json()

        # Handle stringified JSON body
        if isinstance(raw_data, str):
            logger.info("Detected stringified JSON in body. Parsing...")
            raw_data = json.loads(raw_data)

        logger.info(f"Incoming request body:\n{json.dumps(raw_data, indent=2)}")

        url = raw_data["url"]
        api_token = raw_data["api_token"]
        target_node_id = raw_data["target_node_id"]

    except Exception as e:
        logger.error(f"Failed to parse incoming request: {e}")
        raise HTTPException(status_code=422, detail="Invalid request format")

    return parse_and_post_internal(url, api_token, target_node_id)

def parse_and_post_internal(url: str, api_token: str, target_node_id: str):
    logger.info(f"Processing URL: {url}")

    # Step 1: Fetch the webpage
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
        logger.info(f"Successfully fetched {url}")
    except requests.RequestException as e:
        logger.error(f"Failed to fetch URL: {e}")
        raise HTTPException(status_code=400, detail="Failed to fetch URL.")

    soup = BeautifulSoup(response.text, "html.parser")

    # Step 2: Extract data
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

    # Step 3: Construct Tana node
    tana_node = {
        "name": title,
        "description": clean_text(url),
        "children": []
    }

    if "og:image" in og_tags:
        tana_node["children"].append({
            "name": "Image",
            "description": clean_text(og_tags["og:image"])
        })
        del og_tags["og:image"]

    if semantic_content:
        tana_node["children"].append({
            "name": "Semantic Content",
            "description": clean_text(semantic_content)
        })
    else:
        tana_node["children"].append({
            "name": "Semantic Content",
            "description": "No semantic content found."
        })

    # Add meta + OG tags
    for key, value in {**meta_tags, **og_tags}.items():
        clean_key = clean_text(key)
        clean_val = clean_text(value)
        if clean_key and clean_val:
            tana_node["children"].append({
                "name": clean_key,
                "description": clean_val
            })

    # Final Tana payload
    tana_request = {
        "targetNodeId": target_node_id,
        "nodes": [tana_node]
    }

    logger.info(f"Tana request payload:\n{json.dumps(tana_request, indent=2)}")

    # Step 4: Send to Tana
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
        logger.info(f"Successfully posted to Tana. Status: {tana_response.status_code}")

    except requests.RequestException as e:
        logger.error(f"Tana API error: {e}")
        logger.error(f"Tana response: {tana_response.text if 'tana_response' in locals() else 'No response'}")
        raise HTTPException(status_code=502, detail="Failed to post data to Tana.")

    return {"message": "Content extracted and sent to Tana successfully."}

if __name__ == "__main__":
    # uvicorn.run("main:app", host="0.0.0.0", port=10000)
    app