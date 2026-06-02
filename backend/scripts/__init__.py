"""Operator-facing scripts.

Run inside the backend container, e.g.:

    docker compose -f infra/docker-compose.yml exec backend \\
        python -m scripts.drive_engagement --scope acme.com --prompt "..."
"""
