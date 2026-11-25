# house-monitor — run it as a systemd user service. Tests and the rest are in CLAUDE.md's
# Commands.

PYTHON   ?= $(shell command -v python3)
UNIT_DIR  = $(or $(XDG_CONFIG_HOME),$(HOME)/.config)/systemd/user

.DEFAULT_GOAL := help

.PHONY: help
help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

.PHONY: install-service
install-service: ## Run house-monitor from this clone as a systemd user service
	@test -n "$(PYTHON)" || { echo "python3 not found on PATH; set PYTHON=/path/to/python3"; exit 1; }
	mkdir -p $(UNIT_DIR)
	sed -e 's|@DIR@|$(CURDIR)|' -e 's|@PYTHON@|$(PYTHON)|' contrib/systemd/house-monitor.service > $(UNIT_DIR)/house-monitor.service
	systemctl --user daemon-reload
	systemctl --user enable house-monitor.service
	systemctl --user restart house-monitor.service

.PHONY: uninstall-service
uninstall-service: ## Stop and remove the systemd user service
	-systemctl --user disable --now house-monitor.service
	rm -f $(UNIT_DIR)/house-monitor.service
	systemctl --user daemon-reload
