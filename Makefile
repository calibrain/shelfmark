.PHONY: help install install-python-dev dev build preview typecheck frontend-test clean up up down docker-build refresh restart build-serve python-lint python-lint-fix

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
	@echo "  install-python-dev - Install Python dev tooling"
	@echo "  python-lint - Run Ruff against Python backend code"
	@echo "  python-lint-fix - Run Ruff with safe auto-fixes"
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
	@echo "Installing Python dev dependencies..."
	python3 -m pip install -r requirements-dev.txt

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
	python3 -m ruff check shelfmark

python-lint-fix:
	@echo "Running Ruff with safe auto-fixes..."
	python3 -m ruff check shelfmark --fix

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
