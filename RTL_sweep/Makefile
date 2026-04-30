.PHONY: install test run run-mock sweep clean

install:
	pip install -r requirements.txt

test:
	python -m pytest tests/ -v

run:
	python main.py

run-mock:
	python main.py --mock

sweep:
	python run_sweep.py --config examples/example_config.json --mock

clean:
	rm -rf __pycache__ .pytest_cache build dist *.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
