.PHONY: install test lint format run clean

install:
	pip install -r requirements.txt

test:
	python -m pytest -q

test-cov:
	python -m pytest --cov=. --cov-report=term-missing

lint:
	ruff check .

format:
	ruff format .

run:
	streamlit run app.py

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} +
	rm -rf .pytest_cache .coverage htmlcov
