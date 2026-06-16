# ---
# file: Makefile
# project: solanatrilly
# purpose: Developer convenience targets — lint gate, hook installer
# story: US-33 AC-33.2
# ---

.PHONY: lint lint-fix install-hooks

## Run ruff lint check inside the web container (mirrors CI lint step).
lint:
	docker compose run --rm web ruff check .

## Run ruff with auto-fix inside the web container.
lint-fix:
	docker compose run --rm web ruff check --fix .

## Install the pre-commit hook into .git/hooks/pre-commit.
install-hooks:
	bash scripts/install-hooks.sh
