.PHONY: help install install-python-dev dev build preview typecheck frontend-test clean up up down docker-build refresh restart build-serve python-lint python-lint-fix python-format python-format-check python-typecheck python-dead-code python-checks python-test-lint python-test-lint-fix python-test-format python-test-format-check python-test-typecheck python-test-checks python-coverage prek-install

# Frontend directory
FRONTEND_DIR := src/frontend

# Docker compose file
COMPOSE_FILE := docker-compose.dev.yml

# Default target
help:
	@echo "Available targets:"
	@echo ""
	@echo "Frontend:"
	@echo "  install    - Install frontend dependencies"
	@echo "  dev        - Start development server"
	@echo "  build      - Build frontend for production"
	@echo "  build-serve - Build and serve via Flask (test prod build without Docker)"
	@echo "  preview    - Preview production build"
	@echo "  typecheck  - Run TypeScript type checking"
	@echo "  frontend-test - Run frontend unit tests"
	@echo "  install-python-dev - Sync Python runtime + dev tooling with uv"
	@echo "  python-lint - Run Ruff against Python backend code"
	@echo "  python-lint-fix - Run Ruff with safe auto-fixes"
	@echo "  python-format - Format Python backend code with Ruff"
	@echo "  python-format-check - Check Python backend formatting with Ruff"
	@echo "  python-typecheck - Run BasedPyright against Python backend code"
	@echo "  python-dead-code - Run Vulture against Python backend code"
	@echo "  python-checks - Run all Python static analysis checks"
	@echo "  python-test-lint - Run Ruff against Python tests with the relaxed tests profile"
	@echo "  python-test-lint-fix - Run Ruff with safe auto-fixes against Python tests"
	@echo "  python-test-format - Format Python tests with Ruff"
	@echo "  python-test-format-check - Check Python test formatting with Ruff"
	@echo "  python-test-typecheck - Run lightweight BasedPyright checks against Python tests"
	@echo "  python-test-checks - Run all relaxed Python test static analysis checks"
	@echo "  python-coverage - Run tests with coverage report"
	@echo "  prek-install - Install prek git hooks"
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

# Install Python development dependencies
install-python-dev:
	@echo "Syncing Python runtime and dev tooling with uv..."
	uv sync --locked --extra browser

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
typecheck:
	@echo "Running TypeScript type checking..."
	cd $(FRONTEND_DIR) && npm run typecheck

# Python linting
python-lint:
	@echo "Running Ruff..."
	uv run ruff check shelfmark

python-lint-fix:
	@echo "Running Ruff with safe auto-fixes..."
	uv run ruff check shelfmark --fix

python-format:
	@echo "Formatting Python backend code with Ruff..."
	uv run ruff format shelfmark

python-format-check:
	@echo "Checking Python backend formatting with Ruff..."
	uv run ruff format --check shelfmark

python-typecheck:
	@echo "Running BasedPyright..."
	uv run basedpyright

python-dead-code:
	@echo "Running Vulture..."
	uv run vulture shelfmark

python-checks: python-lint python-format-check python-typecheck python-dead-code

python-test-lint:
	@echo "Running Ruff against tests with the relaxed tests profile..."
	uv run ruff check tests

python-test-lint-fix:
	@echo "Running Ruff with safe auto-fixes against tests..."
	uv run ruff check tests --fix

python-test-format:
	@echo "Formatting Python tests with Ruff..."
	uv run ruff format tests

python-test-format-check:
	@echo "Checking Python test formatting with Ruff..."
	uv run ruff format --check tests

python-test-typecheck:
	@echo "Running lightweight BasedPyright checks against tests..."
	uv run basedpyright tests --skipunannotated

python-test-checks: python-test-lint python-test-format-check python-test-typecheck

python-coverage:
	@echo "Running tests with coverage..."
	uv run pytest tests/ -x --tb=short -m "not integration and not e2e" --cov --cov-report=term-missing

prek-install:
	@echo "Installing prek git hooks..."
	uv run prek install

# Run frontend unit tests
frontend-test:
	@echo "Running frontend unit tests..."
	cd $(FRONTEND_DIR) && npm run test:unit

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
