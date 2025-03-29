from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
import requests
from bs4 import BeautifulSoup
from typing import Optional, Dict, List
import logging
import json

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

# FastAPI app instance
from fastapi.middleware.cors import CORSMiddleware
app = FastAPI()

# Input schema for the /parse_and_post endpoint
class ParseAndPostPayload(BaseModel):
    url: str
    api_token: str
    target_node_id: str


# Middleware to allow CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins
    allow_credentials=True,
    allow_methods=["*"],  # Allow all methods
    allow_headers=["*"],  # Allow all headers
)

@app.post("/parse_and_post")
async def parse_and_post(request: Request):
    try:
        data = await request.json()

        # If Tana sends the body as a stringified JSON, decode it
        if isinstance(data, str):
            import json
            data = json.loads(data)

        url = data["url"]
        api_token = data["api_token"]
        target_node_id = data["target_node_id"]

    except Exception as e:
        logger.error(f"Failed to parse incoming request: {e}")
        raise HTTPException(status_code=422, detail="Invalid request format")

    # Now pass url, token, and node ID to your existing logic
    return parse_and_post_internal(url, api_token, target_node_id)


def parse_and_post_internal(url: str, api_token: str, target_node_id: str):
    # your existing fetch → parse → format → post to Tana logic
    logger.info(f"Received request to parse and post: {payload.url}")

    # Step 1: Fetch the webpage
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/119.0.0.0 Safari/537.36"
        )
    }
    try:
        response = requests.get(payload.url, headers=headers, timeout=10)
        response.raise_for_status()
        logger.info(f"Fetched content from {payload.url} successfully")
    except requests.RequestException as e:
        logger.error(f"Error fetching URL '{payload.url}': {e}")
        raise HTTPException(status_code=400, detail="Failed to fetch the given URL.")

    soup = BeautifulSoup(response.text, "html.parser")

    # Step 2: Extract content
    title = soup.title.string.strip() if soup.title else payload.url

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

    if not semantic_content:
        logger.warning(f"No semantic content found in {payload.url}")

    # Step 3: Build Tana payload
    tana_headers = {
        "Authorization": f"Bearer {payload.api_token}",
        "Content-Type": "application/json"
    }

    tana_node = {
        "name": title,
        "description": payload.url,
        "children": []
    }

    # og:image as "Image" field
    if "og:image" in og_tags:
        tana_node["children"].append({
            "name": "Image",
            "description": og_tags["og:image"]
        })
        del og_tags["og:image"]

    # Semantic content if exists
    if semantic_content:
        tana_node["children"].append({
            "name": "Semantic Content",
            "description": semantic_content
        })

    # Add all meta + OG (excluding og:image)
    for key, value in {**meta_tags, **og_tags}.items():
        tana_node["children"].append({
            "name": key,
            "description": value
        })

    # tana_request = {
    #     "targetNodeId": payload.target_node_id,
    #     "nodes": [tana_node]
    # }

    tana_request = {
        "targetNodeId": "INBOX",
        "nodes": [tana_node]
    }

    logger.info(f"Tana request payload:\n{json.dumps(tana_request, indent=2)}")
    logger.info(f"Sending data to Tana node {payload.target_node_id}")
    logger.debug(f"Request headers: {tana_headers}")
    logger.debug(f"Request body: {json.dumps(tana_request, indent=2)}")

    # Step 4: POST to Tana Input API
    try:
        logger.info(f"Sending extracted content to Tana node {payload.target_node_id}")
        tana_response = requests.post(
            "https://europe-west1-tagr-prod.cloudfunctions.net/addToNodeV2",
            headers=tana_headers,
            json=tana_request
        )
        tana_response.raise_for_status()
        logger.info(f"Tana Input API responded with status {tana_response.status_code}")
    except requests.RequestException as e:
        if 'tana_response' in locals():
            logger.error(f"Tana API returned error: {tana_response.status_code} {tana_response.text}")
            raise HTTPException(status_code=502, detail="Failed to post data to Tana.")
        logger.debug(f"Tana response body: {tana_response.text if 'tana_response' in locals() else 'No response'}")
        raise HTTPException(status_code=502, detail="Failed to post data to Tana.")

    return {"message": "Content extracted and sent to Tana successfully."}

if __name__ == "__main__":
    # uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
    app
