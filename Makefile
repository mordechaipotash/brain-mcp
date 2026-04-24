# brain-mcp — Build targets
#
# Usage:
#   make setup      Create venv and install package in dev mode
#   make ingest     Import conversations from configured sources
#   make embed      Create vector embeddings for semantic search
#   make summarize  Generate structured conversation summaries
#   make serve      Start the MCP server
#   make test       Run basic tests
#   make clean      Remove generated data (keeps config)
#   make lint       Check code quality

PYTHON ?= python3
VENV := venv
PIP := $(VENV)/bin/pip
PY := $(VENV)/bin/python

.PHONY: setup ingest embed summarize serve test clean lint verify-skills help

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

setup: ## Create venv, install package in dev mode
	@echo "Setting up brain-mcp..."
	@test -d $(VENV) || $(PYTHON) -m venv $(VENV)
	@$(PIP) install --upgrade pip -q
	@$(PIP) install -e ".[dev]" -q
	@mkdir -p data vectors logs
	@test -f brain.yaml || cp brain.yaml.example brain.yaml
	@echo "Setup complete. Edit brain.yaml to configure your sources."

ingest: ## Import conversations from configured sources
	@echo "Ingesting conversations..."
	@$(PY) -c "from brain_mcp.config import load_config, set_config; from brain_mcp.ingest import run_all_ingesters; cfg = set_config(load_config()) or __import__('brain_mcp.config', fromlist=['get_config']).get_config(); run_all_ingesters(cfg)"

embed: ## Create vector embeddings for semantic search
	@echo "Running embedding pipeline..."
	@$(PY) -m brain_mcp.embed.embed

embed-full: ## Re-embed everything from scratch
	@$(PY) -m brain_mcp.embed.embed full

embed-stats: ## Show embedding statistics
	@$(PY) -m brain_mcp.embed.embed stats

summarize: ## Generate structured conversation summaries (requires LLM API)
	@echo "Running summarize pipeline..."
	@$(PY) -m brain_mcp.summarize.summarize

summarize-stats: ## Show summary statistics
	@$(PY) -m brain_mcp.summarize.summarize stats

serve: ## Start the MCP server (stdio transport)
	@$(PY) -m brain_mcp.server

test: ## Run basic tests
	@$(PY) -m pytest tests/ -v

lint: ## Check code quality with basic checks
	@echo "Checking for syntax errors..."
	@$(PY) -m py_compile brain_mcp/config.py
	@$(PY) -m py_compile brain_mcp/server/server.py
	@$(PY) -m py_compile brain_mcp/server/db.py
	@$(PY) -m py_compile brain_mcp/ingest/schema.py
	@$(PY) -m py_compile brain_mcp/ingest/noise_filter.py
	@echo "No syntax errors found."

verify-skills: ## Validate SHELET skill manifests against the MCP tool surface
	@$(PY) scripts/verify_skills.py

clean: ## Remove generated data (keeps config and sources)
	@echo "Cleaning generated data..."
	rm -rf data/all_conversations.parquet
	rm -rf data/brain_summaries_v6.parquet
	rm -rf data/brain_summaries_v6.jsonl
	rm -rf vectors/
	@echo "Cleaned. Run 'make ingest' to regenerate."

clean-all: ## Remove everything including venv
	@echo "Full clean..."
	rm -rf $(VENV)
	rm -rf data/ vectors/ logs/
	rm -rf __pycache__ */__pycache__ */*/__pycache__
	rm -rf .pytest_cache tests/__pycache__
	rm -rf *.egg-info
	@echo "Full clean complete. Run 'make setup' to start over."
