# The app runs only in Docker (compose.yaml). install/test are the dev toolchain for tests before a deploy.
.PHONY: install test deploy deploy-quick logs backup-setup install-backup backup-now backup-status fetch-isracard isracard-login hapoalim-login install-fetcher fetcher-status fetcher-logs
install:
	git config core.hooksPath .githooks
	python3 -m venv .venv
	.venv/bin/pip install -e '.[test]'
	cd frontend && npm install
test:
	.venv/bin/python -c "import fastapi, sqlalchemy, alembic, pandas, openpyxl, pymupdf, pdfplumber, cryptography"
	.venv/bin/pytest
	cd frontend && npm test && npx tsc --noEmit -p .
deploy: test deploy-quick
deploy-quick:
	docker compose up --build -d
	@id=$$(docker compose ps -q budget); for i in $$(seq 1 30); do \
	  s=$$(docker inspect -f '{{.State.Health.Status}}' $$id); \
	  [ "$$s" = healthy ] && echo "deployed: healthy" && exit 0; sleep 2; \
	done; echo "container not healthy: $$s"; docker compose logs --tail 30 budget; exit 1
logs:
	docker compose logs --tail 50 budget
fetch-isracard:
	@docker exec family-budget-fetcher node -e "fetch('http://127.0.0.1:8080/run',{method:'POST'}).then(r=>r.text()).then(console.log)" \
	  || echo "fetcher is not running: sudo make isracard-login / hapoalim-login (first time) or systemctl start family-budget-fetcher"
fetcher-status:
	@systemctl --no-pager --lines=0 status family-budget-fetcher || true
	@docker exec family-budget-fetcher node -e "fetch('http://127.0.0.1:8080/status').then(r=>r.text()).then(console.log)" 2>/dev/null || true
fetcher-logs:
	journalctl -u family-budget-fetcher -n 50 --no-pager
install-fetcher:
	@ls /etc/credstore.encrypted/family-budget-*.cred >/dev/null 2>&1 || { echo "no sealed login: sudo make isracard-login or sudo make hapoalim-login"; exit 1; }
	install -d -o 10002 -g 10002 -m 700 $${FAMILY_BUDGET_FETCHER_STATE:-/srv/family-budget-fetcher-state}
	docker compose --profile fetch build fetcher
	sed "s|__REPO__|$(CURDIR)|" deploy/family-budget-fetcher.service > /etc/systemd/system/family-budget-fetcher.service
	install -d /etc/systemd/system/family-budget-fetcher.service.d
	{ echo "[Service]"; for f in /etc/credstore.encrypted/family-budget-*.cred; do n=$${f##*/family-budget-}; n=$${n%.cred}; echo "LoadCredentialEncrypted=$$n.json:$$f"; done; } \
	  > /etc/systemd/system/family-budget-fetcher.service.d/credentials.conf
	systemctl daemon-reload
	systemctl enable family-budget-fetcher
	systemctl restart family-budget-fetcher
isracard-login:
	@bash scripts/fetch-login.sh isracard
hapoalim-login:
	@bash scripts/fetch-login.sh hapoalim
backup-setup:
	@bash scripts/backup-setup.sh
install-backup:
	@ls /etc/credstore.encrypted/family-budget-backup.cred >/dev/null 2>&1 || { echo "no sealed passphrase: sudo make backup-setup"; exit 1; }
	for u in service timer; do sed "s|__REPO__|$(CURDIR)|" deploy/family-budget-backup.$$u > /etc/systemd/system/family-budget-backup.$$u; done
	systemctl daemon-reload
	systemctl enable --now family-budget-backup.timer
backup-now:
	systemctl start family-budget-backup.service
	@journalctl -u family-budget-backup -n 5 --no-pager -o cat
backup-status:
	@systemctl list-timers family-budget-backup.timer --no-pager
	@journalctl -u family-budget-backup -n 3 --no-pager -o cat
	@docker compose exec -T budget ls -lh /data/backups 2>/dev/null || true
