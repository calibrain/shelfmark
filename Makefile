.PHONY: help install install-ci install-python-dev dev build preview frontend-typecheck frontend-lint frontend-format frontend-format-fix frontend-checks frontend-test clean up down docker-build refresh restart build-serve python-lint python-lint-fix python-format python-format-fix python-typecheck python-dead-code python-checks python-test python-test-cov checks fix

# Frontend directory
FRONTEND_DIR := src/frontend

# Docker compose file
COMPOSE_FILE := docker-compose.dev.yml

# Default target
help:
	@echo "Available targets:"
	@echo ""
	@echo "Quality:"
	@echo "  checks     - Run ALL static analysis checks (frontend + Python)"
	@echo "  fix        - Auto-fix lint + format issues (frontend + Python)"
	@echo ""
	@echo "Frontend:"
	@echo "  install    - Install frontend dependencies"
	@echo "  dev        - Start development server"
	@echo "  build      - Build frontend for production"
	@echo "  build-serve - Build and serve via Flask (test prod build without Docker)"
	@echo "  preview    - Preview production build"
	@echo "  frontend-typecheck - Run TypeScript type checking"
	@echo "  frontend-lint - Run Oxlint against frontend code"
	@echo "  frontend-format - Check frontend formatting with Oxfmt"
	@echo "  frontend-format-fix - Format frontend code with Oxfmt"
	@echo "  frontend-checks - Run all frontend static analysis checks"
	@echo "  frontend-test - Run frontend unit tests"
	@echo ""
	@echo "Python:"
	@echo "  install-python-dev - Sync Python runtime + dev tooling with uv"
	@echo "  python-lint - Run Ruff against Python code (backend + tests)"
	@echo "  python-lint-fix - Run Ruff with safe auto-fixes"
	@echo "  python-format - Check Python formatting with Ruff"
	@echo "  python-format-fix - Format Python code with Ruff"
	@echo "  python-typecheck - Run BasedPyright against backend + tests"
	@echo "  python-dead-code - Run Vulture against backend code"
	@echo "  python-checks - Run all Python static analysis checks"
	@echo "  python-test - Run unit tests"
	@echo "  python-test-cov - Run unit tests with coverage report"
	@echo "  clean      - Remove node_modules and build artifacts"
	@echo ""
	@echo "Backend (Docker):"
	@echo "  up         - Start backend services"
	@echo "  down       - Stop backend services"
	@echo "  restart    - Restart backend services (no rebuild)"
	@echo "  docker-build - Build Docker image"
	@echo "  refresh    - Rebuild and restart backend services"

# Install dependencies
install:
	@echo "Installing frontend dependencies..."
	cd $(FRONTEND_DIR) && npm install

install-ci:
	@echo "Installing frontend dependencies (CI, lockfile-strict)..."
	cd $(FRONTEND_DIR) && npm ci

# Install Python development dependencies
install-python-dev:
	@echo "Syncing Python runtime and dev tooling with uv..."
	uv sync --locked --extra browser
	@echo "Installing prek git hooks..."
	uv run prek install

# Start development server
dev:
	@echo "Starting development server..."
	cd $(FRONTEND_DIR) && npm run dev

# Build for production
build:
	@echo "Building frontend for production..."
	cd $(FRONTEND_DIR) && npm run build

# Build frontend and sync to frontend-dist for the running container to serve
build-serve: build
	@echo "Syncing build to frontend-dist..."
	@mkdir -p frontend-dist
	rsync -a --delete $(FRONTEND_DIR)/dist/ frontend-dist/
	@echo "Done. Hit the Flask backend (port 8084) to test the production build."

# Preview production build
preview:
	@echo "Previewing production build..."
	cd $(FRONTEND_DIR) && npm run preview

# Type checking
frontend-typecheck:
	@echo "Running TypeScript type checking..."
	cd $(FRONTEND_DIR) && npm run typecheck

# Python linting (backend + tests)
python-lint:
	@echo "Running Ruff..."
	uv run ruff check shelfmark tests

python-lint-fix:
	@echo "Running Ruff with safe auto-fixes..."
	uv run ruff check shelfmark tests --fix

python-format:
	@echo "Checking Python formatting with Ruff..."
	uv run ruff format --check shelfmark tests

python-format-fix:
	@echo "Formatting Python code with Ruff..."
	uv run ruff format shelfmark tests

python-typecheck:
	@echo "Running BasedPyright..."
	uv run basedpyright
	@echo "Running BasedPyright against tests..."
	uv run basedpyright tests --skipunannotated

python-dead-code:
	@echo "Running Vulture..."
	uv run vulture shelfmark

python-checks: python-lint python-format python-typecheck python-dead-code

python-test:
	@echo "Running tests..."
	uv run pytest tests/ -x --tb=short -m "not integration and not e2e"

python-test-cov:
	@echo "Running tests with coverage..."
	uv run pytest tests/ -x --tb=short -m "not integration and not e2e" --cov --cov-report=term-missing

# Frontend linting
frontend-lint:
	@echo "Running Oxlint..."
	cd $(FRONTEND_DIR) && npm run lint

# Frontend formatting
frontend-format:
	@echo "Checking frontend formatting with Oxfmt..."
	cd $(FRONTEND_DIR) && npm run format:check

frontend-format-fix:
	@echo "Formatting frontend code with Oxfmt..."
	cd $(FRONTEND_DIR) && npm run format

# All frontend static analysis
frontend-checks: frontend-lint frontend-format frontend-typecheck

# Run frontend unit tests
frontend-test:
	@echo "Running frontend unit tests..."
	cd $(FRONTEND_DIR) && npm run test:unit

# All static analysis checks (frontend + Python)
checks: frontend-checks python-checks

# Auto-fix lint + format issues (frontend + Python)
fix: python-lint-fix python-format-fix frontend-format-fix

# Clean build artifacts and dependencies
clean:
	@echo "Cleaning build artifacts and dependencies..."
	rm -rf $(FRONTEND_DIR)/node_modules
	rm -rf $(FRONTEND_DIR)/dist

# Start backend services
up:
	@echo "Starting backend services..."
	docker compose -f $(COMPOSE_FILE) up -d

# Stop backend services
down:
	@echo "Stopping backend services..."
	docker compose -f $(COMPOSE_FILE) down

# Build Docker image
docker-build:
	@echo "Building Docker image..."
	docker compose -f $(COMPOSE_FILE) build

# Restart backend services (no rebuild)
restart:
	@echo "Restarting backend services..."
	docker compose -f $(COMPOSE_FILE) restart

# Rebuild and restart backend services
refresh:
	@echo "Rebuilding and restarting backend services..."
	docker compose -f $(COMPOSE_FILE) down
	docker compose -f $(COMPOSE_FILE) build
	docker compose -f $(COMPOSE_FILE) up -d
