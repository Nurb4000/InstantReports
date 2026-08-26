.PHONY: help test test-unit test-integration test-all lint format clean

help: ## Show this help message
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

test: test-all ## Run all tests

test-unit: ## Run unit tests
	pytest tests/unit/ -v

test-integration: ## Run integration tests
	pytest tests/integration/ -v

test-all: ## Run all tests
	pytest tests/ -v --tb=short

lint: ## Run linting
	ruff check app/ tests/

format: ## Format code
	ruff format app/ tests/

clean: ## Clean up generated files
	rm -rf __pycache__ .pytest_cache .mypy_cache
	rm -f test.db
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
