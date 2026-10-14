"""
Central configuration. Every tunable lives here and is read from the
environment (or .env), so experiments can be reproduced by recording env vars.
"""
import os
from dotenv import load_dotenv
from sqlalchemy.engine import URL

load_dotenv()

# Database
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", "5434"))
DB_NAME = os.getenv("DB_NAME", "genosql")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASS = os.getenv("DB_PASS", "")

# Least-privilege login used to execute LLM-generated SQL.
# Created by scripts/setup_db.py with SELECT-only grants.
READER_USER = os.getenv("READER_USER", "genosql_reader")
READER_PASS = os.getenv("READER_PASS", "")

# LLM
LLM_BACKEND = os.getenv("LLM_BACKEND", "ollama")          # "ollama" | "openai"
LLM_MODEL = os.getenv("LLM_MODEL", "qwen2.5-coder:7b")
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "")        # any OpenAI-compatible server
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

# RAG
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
RAG_K = int(os.getenv("RAG_K", "3"))

# Query execution
MAX_ROWS = int(os.getenv("MAX_ROWS", "200"))
STATEMENT_TIMEOUT_MS = int(os.getenv("STATEMENT_TIMEOUT_MS", "15000"))
MAX_REPAIR_ATTEMPTS = int(os.getenv("MAX_REPAIR_ATTEMPTS", "2"))


def admin_url() -> URL:
    return URL.create("postgresql+psycopg2", DB_USER, DB_PASS, DB_HOST, DB_PORT, DB_NAME)


def reader_url() -> URL:
    return URL.create("postgresql+psycopg2", READER_USER, READER_PASS, DB_HOST, DB_PORT, DB_NAME)
