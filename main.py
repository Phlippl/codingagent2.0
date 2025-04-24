import os
import hmac
import hashlib
import json
from typing import Optional, Dict, Any, List, Set
from fastapi import FastAPI, HTTPException, Header, BackgroundTasks, Request, Depends, Query
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from app.rag_manager import RAGManager
from app.preprocessor import Preprocessor
from app.embedding_manager import EmbeddingManager
from app.local_scan_manager import LocalScanManager
from fastapi.staticfiles import StaticFiles
from fastapi.openapi.utils import get_openapi
import logging
from datetime import datetime, timedelta

# Configuration
LOCAL_PROJECT_PATH = os.getenv("LOCAL_PROJECT_PATH", "D:\\DevEnv\\DevProjects\\codingagent")
if not os.path.exists(LOCAL_PROJECT_PATH):
    raise ValueError(f"LOCAL_PROJECT_PATH '{LOCAL_PROJECT_PATH}' does not exist")

IGNORE_DIRS = os.getenv("IGNORE_DIRS", "").split(",") if os.getenv("IGNORE_DIRS") else None
TRACK_EXTENSIONS = os.getenv("TRACK_EXTENSIONS", "").split(",") if os.getenv("TRACK_EXTENSIONS") else None

API_KEY = os.getenv("API_KEY", "")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("app.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Initialize RAG manager
rag = RAGManager(
    local_path=LOCAL_PROJECT_PATH,
    auto_sync=True,
    sync_interval=int(os.getenv("SYNC_INTERVAL", "300")),  # 300 seconds default
    ignore_dirs=IGNORE_DIRS,
    track_extensions=TRACK_EXTENSIONS
)

app = FastAPI(title="CodeContextAI API", version="1.0.0")

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Restrict for production!
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

try:
    app.mount(
        "/.well-known",
        StaticFiles(directory=".well-known", html=False),
        name="well-known",
    )
except:
    logger.warning("/.well-known directory not found, skipping mount")

# API models
class QueryRequest(BaseModel):
    query: str = Field(..., description="The natural language query")
    top_k: int = Field(5, description="Number of results to return", ge=1, le=50)
    include_dependencies: bool = Field(True, description="If true, related files will be included")


class PromptRequest(BaseModel):
    query: str = Field(..., description="The natural language query")
    top_k: int = Field(5, description="Number of most relevant results to include", ge=1, le=20)
    max_context_length: int = Field(8000, description="Maximum length of context in characters", ge=1000, le=100000)


class LocalDirectoryUpdateRequest(BaseModel):
    local_path: Optional[str] = Field(None, description="Path to the local directory to scan")
    force_rebuild: bool = Field(False, description="If true, the index will be completely rebuilt")


class StatusResponse(BaseModel):
    status: str
    timestamp: str
    local_path: Optional[str] = None
    last_sync: Optional[str] = None
    auto_sync: Optional[bool] = None
    sync_interval: Optional[int] = None


class FileRequest(BaseModel):
    file_path: str = Field(..., description="Relative path to the file in the local directory")


# Authentication middleware
async def verify_api_key(x_api_key: str = Header(None)):
    if API_KEY and (not x_api_key or x_api_key != API_KEY):
        raise HTTPException(status_code=401, detail="Invalid API key")
    return True


@app.on_event("startup")
def startup_event():
    """Build the index on application start if needed."""
    try:
        logger.info("Application started, checking index and dependency graph...")
        # Check if FAISS index exists
        if not os.path.exists("index.faiss") or not os.path.exists("metadata.pkl"):
            logger.info("Index not found, building new index...")
            rag.build_index()
            logger.info("Index successfully created")
        
        # Check if dependency graph exists
        if not os.path.exists("dependency_graph.json"):
            logger.info("Dependency graph not found, building...")
            rag.graph_builder.build_graph()
            logger.info("Dependency graph successfully created")
    except Exception as e:
        logger.error(f"Error during startup: {str(e)}")


@app.get("/", response_model=StatusResponse)
async def read_root():
    """API base endpoint with status information."""
    status = {
        "status": "online",
        "timestamp": datetime.now().isoformat(),
        "local_path": rag.local_path,
        "last_sync": rag.last_sync_time.isoformat() if rag.last_sync_time else None,
        "auto_sync": rag.auto_sync,
        "sync_interval": rag.sync_interval
    }
    return status


@app.post("/retrieve", dependencies=[Depends(verify_api_key)])
async def retrieve(req: QueryRequest):
    """
    Retrieves the most relevant code chunks for a natural language query.
    """
    try:
        # Update context if needed
        rag.update_context_on_change()
        matches = rag.retrieve(req.query, top_k=req.top_k, include_dependencies=req.include_dependencies)
        return {"matches": matches}
    except Exception as e:
        logger.error(f"Error during retrieve operation: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/prompt", dependencies=[Depends(verify_api_key)])
async def generate_prompt(req: PromptRequest):
    """
    Creates a comprehensive prompt with retrieved context and the query.
    """
    try:
        # Update context if needed
        rag.update_context_on_change()
        prompt_text = rag.build_prompt(
            req.query, 
            top_k=req.top_k, 
            max_context_length=req.max_context_length
        )
        return {"prompt": prompt_text}
    except Exception as e:
        logger.error(f"Error during prompt generation: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/sync", dependencies=[Depends(verify_api_key)])
async def sync_directory(
    background_tasks: BackgroundTasks,
    wait: bool = Query(False, description="If true, executes synchronously instead of in the background"),
    force_rescan: bool = Query(False, description="If true, rescans all files regardless of changes")
):
    """
    Manually synchronizes the local directory and updates the index.
    """
    try:
        if wait:
            # Synchronous execution
            result = rag.sync_directory(force_rescan)
            return result
        else:
            # Asynchronous execution in the background
            background_tasks.add_task(rag.sync_directory, force_rescan)
            return {
                "status": "started", 
                "message": "Synchronization started", 
                "timestamp": datetime.now().isoformat()
            }
    except Exception as e:
        logger.error(f"Error during directory synchronization: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/rebuild", dependencies=[Depends(verify_api_key)])
async def rebuild_index(
    background_tasks: BackgroundTasks,
    request: LocalDirectoryUpdateRequest = None,
    wait: bool = Query(False, description="If true, executes synchronously instead of in the background")
):
    """
    Completely rebuilds the index. Optionally a different local directory can be used.
    """
    global rag  # Define global before use
    
    try:
        if request and request.local_path:
            # Check if the new path exists
            if not os.path.exists(request.local_path):
                raise HTTPException(status_code=400, detail=f"Directory not found: {request.local_path}")
                
            # Update the local path
            old_path = rag.local_path
            
            # Create a new RAG manager with the new path
            rag = RAGManager(
                local_path=request.local_path,
                auto_sync=rag.auto_sync,
                sync_interval=rag.sync_interval
            )
            logger.info(f"Local directory path changed from {old_path} to {request.local_path}")
        
        if wait:
            # Synchronous execution
            result = rag.build_index()
            return result
        else:
            # Asynchronous execution in the background
            background_tasks.add_task(rag.build_index)
            return {
                "status": "started", 
                "message": f"Index rebuild for {rag.local_path} started", 
                "timestamp": datetime.now().isoformat()
            }
    except Exception as e:
        logger.error(f"Error during index rebuild: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/auto_sync", dependencies=[Depends(verify_api_key)])
async def get_auto_sync():
    """
    Returns the current status of automatic synchronization.
    """
    return {
        "auto_sync": rag.auto_sync,
        "sync_interval": rag.sync_interval,
        "last_sync": rag.last_sync_time.isoformat() if rag.last_sync_time else None
    }


@app.post("/auto_sync", dependencies=[Depends(verify_api_key)])
async def set_auto_sync(
    enabled: bool = Query(..., description="Enables/disables automatic synchronization"),
    interval: int = Query(None, description="Synchronization interval in seconds")
):
    """
    Enables or disables automatic local directory synchronization.
    """
    try:
        if enabled and not rag.auto_sync:
            # Enable automatic synchronization
            rag.auto_sync = True
            if interval is not None:
                rag.sync_interval = max(30, interval)  # Minimum interval: 30 seconds
            rag.start_auto_sync()
            return {
                "status": "success", 
                "message": f"Automatic synchronization enabled (interval: {rag.sync_interval}s)",
                "auto_sync": True,
                "sync_interval": rag.sync_interval
            }
        elif not enabled and rag.auto_sync:
            # Disable automatic synchronization
            rag.stop_auto_sync()
            rag.auto_sync = False
            if interval is not None:
                rag.sync_interval = max(30, interval)
            return {
                "status": "success", 
                "message": "Automatic synchronization disabled",
                "auto_sync": False,
                "sync_interval": rag.sync_interval
            }
        else:
            # Only change interval
            if interval is not None:
                old_interval = rag.sync_interval
                rag.sync_interval = max(30, interval)
                return {
                    "status": "success", 
                    "message": f"Synchronization interval changed from {old_interval}s to {rag.sync_interval}s",
                    "auto_sync": rag.auto_sync,
                    "sync_interval": rag.sync_interval
                }
            else:
                return {
                    "status": "info", 
                    "message": "No changes made",
                    "auto_sync": rag.auto_sync,
                    "sync_interval": rag.sync_interval
                }
    except Exception as e:
        logger.error(f"Error in auto-sync configuration: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/files", dependencies=[Depends(verify_api_key)])
async def get_file_structure(
    force_refresh: bool = Query(False, description="If true, cache is ignored and rebuilt")
):
    """
    Returns the hierarchical file structure of the local directory.
    """
    try:
        structure = rag.get_file_structure(force_refresh=force_refresh)
        return structure
    except Exception as e:
        logger.error(f"Error retrieving file structure: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/file", dependencies=[Depends(verify_api_key)])
async def get_file_content(request: FileRequest):
    """
    Retrieves the content of a specific file.
    """
    try:
        content = rag.get_file_content(request.file_path)
        return content
    except Exception as e:
        logger.error(f"Error retrieving file content: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/dependencies", dependencies=[Depends(verify_api_key)])
async def get_dependencies():
    """
    Returns the dependency graph of the local directory.
    """
    try:
        graph = rag.get_dependency_graph()
        return graph
    except Exception as e:
        logger.error(f"Error retrieving dependency graph: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


def custom_openapi():
    """Creates a custom OpenAPI specification."""
    if app.openapi_schema:
        return app.openapi_schema
        
    openapi_schema = get_openapi(
        title="CodeContextAI API",
        version="1.0.0",
        description=(
            "This API enables integration of an intelligent code agent "
            "that prepares a local code directory for AI assistants. "
            "The agent uses techniques like chunking, embedding, and vector databases "
            "to enable context search, dependency analysis, and semantic understanding."
        ),
        routes=app.routes,
    )
    
    # Additional information
    openapi_schema["info"]["contact"] = {
        "name": "CodeContextAI Team",
        "email": "support@codecontext-ai.example.com"
    }
    
    # Define security schemas
    openapi_schema["components"]["securitySchemes"] = {
        "ApiKeyHeader": {
            "type": "apiKey",
            "in": "header",
            "name": "X-API-Key"
        }
    }
    
    # Global security requirement
    openapi_schema["security"] = [{"ApiKeyHeader": []}]
    
    app.openapi_schema = openapi_schema
    return app.openapi_schema


# Register custom OpenAPI specification
app.openapi = custom_openapi