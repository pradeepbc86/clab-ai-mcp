.PHONY: deploy destroy agent mcp-server validate

deploy:
	cd topology && containerlab deploy -t lab.clab.yml

destroy:
	cd topology && containerlab destroy -t lab.clab.yml --cleanup

agent:
	python3 agent.py

mcp-server:
	python3 mcp_server.py

validate:
	python3 -m pytest tests/ -v
