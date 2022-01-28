run-dev:
	docker compose -f docker-compose.dev.yml up

run-prod:
	docker compose -f docker-compose.yml up -d --build
