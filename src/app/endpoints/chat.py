# src/app/endpoints/chat.py
import time
import base64
import os
import tempfile
import mimetypes
import httpx
from urllib.parse import urlparse
from fastapi import APIRouter, HTTPException
from app.logger import logger
from schemas.request import GeminiRequest, OpenAIChatRequest
from app.services.gemini_client import get_gemini_client, GeminiClientNotInitializedError
from app.services.session_manager import get_translate_session_manager

router = APIRouter()

@router.post("/translate")
async def translate_chat(request: GeminiRequest):
    try:
        gemini_client = get_gemini_client()
    except GeminiClientNotInitializedError as e:
        raise HTTPException(status_code=503, detail=str(e))

    session_manager = get_translate_session_manager()
    if not session_manager:
        raise HTTPException(status_code=503, detail="Session manager is not initialized.")
    try:
        # This call now correctly uses the fixed session manager
        response = await session_manager.get_response(request.model, request.message, request.files)
        return {"response": response.text}
    except Exception as e:
        logger.error(f"Error in /translate endpoint: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error during translation: {str(e)}")

def convert_to_openai_format(response_text: str, model: str, stream: bool = False):
    return {
        "id": f"chatcmpl-{int(time.time())}",
        "object": "chat.completion.chunk" if stream else "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": response_text,
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        },
    }

async def process_media_url(media_url: str) -> str:
    """
    Download or decode media (image/video) and save to a temporary file.
    Returns the path to the temporary file.
    """
    logger.info(f"Processing media URL: {media_url[:50]}...")
    content = None
    ext = ""

    if media_url.startswith("data:"):
        try:
            header, base64_data = media_url.split(",", 1)
            mime_type = header.split(":")[1].split(";")[0]
            ext = mimetypes.guess_extension(mime_type)
            if not ext:
                if "video" in mime_type:
                    ext = ".mp4"
                else:
                    ext = ".png"
            content = base64.b64decode(base64_data)
        except Exception as e:
            logger.error(f"Failed to decode base64 media: {e}")
            raise HTTPException(status_code=400, detail="Invalid base64 media data")
    elif media_url.startswith("http"):
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(media_url, timeout=60.0)
                if resp.status_code == 200:
                    content = resp.content
                    parsed = urlparse(media_url)
                    path = parsed.path
                    ext = os.path.splitext(path)[1]
                    if not ext:
                         # Try to guess from content-type
                        content_type = resp.headers.get("content-type")
                        if content_type:
                            ext = mimetypes.guess_extension(content_type)
                    
                    if not ext:
                        ext = ".png"
                else:
                     logger.error(f"Failed to download media: status {resp.status_code}")
                     raise HTTPException(status_code=400, detail=f"Failed to download media from URL: {resp.status_code}")
        except Exception as e:
            logger.error(f"Error downloading media: {e}")
            raise HTTPException(status_code=400, detail=f"Error downloading media: {str(e)}")
    else:
        raise HTTPException(status_code=400, detail="Unsupported media URL format")

    if content:
        with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
            tmp.write(content)
            logger.info(f"Saved media to temporary file: {tmp.name}")
            return tmp.name
    
    logger.error("Failed to process media: No content retrieved")
    raise HTTPException(status_code=400, detail="Failed to process media")

@router.post("/v1/chat/completions")
async def chat_completions(request: OpenAIChatRequest):
    try:
        gemini_client = get_gemini_client()
    except GeminiClientNotInitializedError as e:
        raise HTTPException(status_code=503, detail=str(e))

    is_stream = request.stream if request.stream is not None else False

    if not request.messages:
        raise HTTPException(status_code=400, detail="No messages provided.")

    conversation_parts = []
    files_to_upload = []
    temp_files = []

    try:
        for msg in request.messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            
            if not content:
                continue

            text_content = ""
            
            if isinstance(content, str):
                text_content = content
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict):
                        if part.get("type") == "text":
                            text_content += part.get("text", "")
                        elif part.get("type") == "image_url":
                            image_url_obj = part.get("image_url", {})
                            url = image_url_obj.get("url") if isinstance(image_url_obj, dict) else image_url_obj
                            if url:
                                try:
                                    temp_file_path = await process_media_url(url)
                                    temp_files.append(temp_file_path)
                                    files_to_upload.append(temp_file_path)
                                    text_content += "\n[Image uploaded]"
                                except Exception as e:
                                    logger.error(f"Error processing image: {e}")
                        elif part.get("type") == "video_url":
                            video_url_obj = part.get("video_url", {})
                            url = video_url_obj.get("url") if isinstance(video_url_obj, dict) else video_url_obj
                            if url:
                                try:
                                    temp_file_path = await process_media_url(url)
                                    temp_files.append(temp_file_path)
                                    files_to_upload.append(temp_file_path)
                                    text_content += "\n[Video uploaded]"
                                except Exception as e:
                                    logger.error(f"Error processing video: {e}")
            
            if not text_content and not files_to_upload:
                continue

            if role == "system":
                conversation_parts.append(f"System: {text_content}")
            elif role == "user":
                conversation_parts.append(f"User: {text_content}")
            elif role == "assistant":
                conversation_parts.append(f"Assistant: {text_content}")

        if not conversation_parts and not files_to_upload:
            raise HTTPException(status_code=400, detail="No valid messages found.")

        # Join all parts with newlines
        final_prompt = "\n\n".join(conversation_parts)

        if request.model:
            try:
                # Map 'banana' model to a real model (e.g., gemini-3.0-flash)
                model_name = request.model.value
                if model_name == "banana":
                    model_name = "gemini-3.0-flash"
                    logger.info(f"Mapped model 'banana' to '{model_name}'")

                # Pass the list of temporary file paths to gemini_client
                logger.info(f"Generating content with model: {model_name}, prompt length: {len(final_prompt)}, files: {len(files_to_upload) if files_to_upload else 0}")
                response = await gemini_client.generate_content(
                    message=final_prompt, 
                    model=model_name, 
                    files=files_to_upload if files_to_upload else None
                )

                # Append images to response text if available
                response_text = response.text
                if hasattr(response, "images") and response.images:
                    for img in response.images:
                        response_text += f"\n\n![{img.alt}]({img.url})"

                return convert_to_openai_format(response_text, request.model.value, is_stream)
            except Exception as e:
                logger.error(f"Error in /v1/chat/completions endpoint: {e}", exc_info=True)
                raise HTTPException(status_code=500, detail=f"Error processing chat completion: {str(e)}")
        else:
            raise HTTPException(status_code=400, detail="Model not specified in the request.")
            
    finally:
        # Clean up temporary files
        for f in temp_files:
            try:
                if os.path.exists(f):
                    os.remove(f)
            except Exception as e:
                logger.error(f"Failed to delete temp file {f}: {e}")
