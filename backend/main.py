"""
YouTube RAG backend. This is the notebook code, wrapped in a FastAPI server.
Changes from the notebook: Groq instead of OpenAI, local embeddings
(Groq has no embeddings API), and the key is read from .env.
"""
import logging
import os
import re
import threading
from contextlib import asynccontextmanager
from typing import Dict, List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from youtube_transcript_api import YouTubeTranscriptApi, TranscriptsDisabled, NoTranscriptFound
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_groq import ChatGroq
from langchain_community.vectorstores import FAISS
from langchain_core.embeddings import Embeddings
from langchain_core.prompts import PromptTemplate
from langchain_core.runnables import RunnableParallel, RunnablePassthrough, RunnableLambda
from langchain_core.output_parsers import StrOutputParser

load_dotenv()
log = logging.getLogger("uvicorn.error")

if not os.getenv("GROQ_API_KEY"):
    raise RuntimeError("GROQ_API_KEY is missing. Copy .env.example to .env and add your key.")

# NOTE: llama-3.3-70b-versatile was shut down by Groq on 2026-08-16 (free/developer tiers).
# openai/gpt-oss-120b is Groq's recommended replacement. Override with GROQ_MODEL in .env.
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
EMBED_MODEL = os.getenv("EMBED_MODEL", "BAAI/bge-small-en-v1.5")
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models_cache")


# ---------- Local embeddings (fastembed) ----------
class LocalEmbeddings(Embeddings):
    """Loads the model lazily so the server always starts, even if the download fails."""

    def __init__(self, model_name: str):
        self.model_name = model_name
        self._model = None
        self._lock = threading.Lock()
        self.error: Optional[str] = None

    @property
    def ready(self) -> bool:
        return self._model is not None

    def load(self):
        with self._lock:
            if self._model is None:
                from fastembed import TextEmbedding
                try:
                    self._model = TextEmbedding(model_name=self.model_name, cache_dir=CACHE_DIR)
                    self.error = None
                except Exception as e:
                    self.error = f"{type(e).__name__}: {e}"
                    raise
        return self._model

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        return [v.tolist() for v in self.load().embed(texts)]

    def embed_query(self, text: str) -> List[float]:
        return next(iter(self.load().query_embed(text))).tolist()


embeddings = LocalEmbeddings(EMBED_MODEL)


def _warmup():
    log.info("Loading embedding model '%s' (first run downloads ~130MB, please wait)...", EMBED_MODEL)
    try:
        embeddings.load()
        log.info("Embedding model ready.")
    except Exception as e:
        log.error("Embedding model failed to load: %s", e)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    threading.Thread(target=_warmup, daemon=True).start()
    yield


app = FastAPI(title="YT RAG Chat", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ---------- Step 3 - Augmentation (same prompt as the notebook) ----------
prompt = PromptTemplate(
    template="""
      You are a helpful assistant.
      Answer ONLY from the provided transcript context.
      If the context is insufficient, just say you don't know.

      {context}
      Question: {question}
    """,
    input_variables=["context", "question"],
)

llm = ChatGroq(model=GROQ_MODEL, temperature=0.2)
parser = StrOutputParser()


def format_docs(retrieved_docs):
    return "\n\n".join(doc.page_content for doc in retrieved_docs)


# ---------- Step 1a - Indexing (Document Ingestion) ----------
def get_transcript(video_id: str) -> str:
    api = YouTubeTranscriptApi()
    try:
        fetched = api.fetch(video_id, languages=["en"])
    except NoTranscriptFound:
        # No English captions: use any available transcript (translated to English if possible).
        available = list(api.list(video_id))
        if not available:
            raise
        fetched = None
        for t in available:
            if t.is_translatable:
                try:
                    fetched = t.translate("en").fetch()
                    break
                except Exception:
                    pass
        if fetched is None:
            fetched = available[0].fetch()
    return " ".join(chunk.text for chunk in fetched)


# ---------- Build the chain for one video (Steps 1b, 1c/1d, 2 and the chain) ----------
_chains: Dict[str, object] = {}
_chunk_counts: Dict[str, int] = {}


def get_chain(video_id: str):
    if video_id in _chains:
        return _chains[video_id], True

    try:
        transcript = get_transcript(video_id)
    except TranscriptsDisabled:
        raise HTTPException(422, "This video has no captions, so it can't be indexed.")
    except Exception as e:
        log.exception("Transcript fetch failed")
        raise HTTPException(502, f"Could not fetch transcript: {e}")

    # Step 1b - Text Splitting
    splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)
    chunks = splitter.create_documents([transcript])

    # Step 1c & 1d - Embeddings + vector store
    try:
        vector_store = FAISS.from_documents(chunks, embeddings)
    except Exception as e:
        log.exception("Embedding failed")
        raise HTTPException(
            503,
            f"Embedding model could not be loaded ({e}). The first run needs internet to download "
            f"~130MB; check your connection and the server console.",
        )

    # Step 2 - Retrieval
    retriever = vector_store.as_retriever(search_type="similarity", search_kwargs={"k": 4})

    # Building a Chain
    parallel_chain = RunnableParallel({
        "context": retriever | RunnableLambda(format_docs),
        "question": RunnablePassthrough(),
    })
    main_chain = parallel_chain | prompt | llm | parser

    if len(_chains) >= 20:  # keep memory bounded
        oldest = next(iter(_chains))
        _chains.pop(oldest)
        _chunk_counts.pop(oldest, None)
    _chains[video_id] = main_chain
    _chunk_counts[video_id] = len(chunks)
    return main_chain, False


# ---------- API ----------
VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


class IndexRequest(BaseModel):
    video_id: str


class ChatRequest(BaseModel):
    video_id: str
    question: str


def check_id(video_id: str):
    if not VIDEO_ID_RE.match(video_id):
        raise HTTPException(400, "Invalid YouTube video id.")


@app.get("/health")
def health():
    return {
        "status": "ok",
        "model": GROQ_MODEL,
        "embeddings_ready": embeddings.ready,
        "embeddings_error": embeddings.error,
        "cached_videos": len(_chains),
    }


@app.post("/index")
def index_video(req: IndexRequest):
    check_id(req.video_id)
    _, cached = get_chain(req.video_id)
    return {"video_id": req.video_id, "chunks": _chunk_counts[req.video_id], "cached": cached}


@app.post("/chat")
def chat(req: ChatRequest):
    check_id(req.video_id)
    question = req.question.strip()
    if not question:
        raise HTTPException(400, "Question is empty.")
    main_chain, _ = get_chain(req.video_id)
    try:
        answer = main_chain.invoke(question)
    except Exception as e:
        log.exception("LLM call failed")
        msg = str(e)
        if "model_decommissioned" in msg or "model_not_found" in msg or "decommissioned" in msg:
            msg = (f"Groq model '{GROQ_MODEL}' is no longer available. Set GROQ_MODEL in backend/.env "
                   f"to a current model (see console.groq.com/docs/models). Details: {msg}")
        raise HTTPException(500, f"LLM error: {msg}")
    return {"answer": answer, "sources": []}
