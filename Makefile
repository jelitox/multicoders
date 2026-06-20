.PHONY: install test run clean release demo tools

install:
	pip install -e .

test:
	python3 -m unittest discover -s tests -v

demo:
	python3 multicoders/demo.py

tools:
	python3 multicoders/tools_integration.py

run:
	python3 -m multicoders arena --dry-run "Crear un decorador para medir tiempo de ejecución"

release:
	@echo "Ejecutando tests antes de release..."
	python3 -m unittest discover -s tests -v
	@echo "Preparando commit quirúrgico..."
	git add .github/ .gitignore .gitmodules Makefile README.md pyproject.toml multicoders/ tests/ CONTRIBUTING.md .env.example
	git commit -m "feat: production-ready Multicoders MVP with Parrot integration and AST lens"
	@echo "Creando repositorio en GitHub (trocglobal/multicoders)..."
	gh repo create trocglobal/multicoders --private --source=. --remote=origin --push

clean:
	rm -rf build/ dist/ *.egg-info .pytest_cache
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
	rm -rf /tmp/multicoders-runs/*
