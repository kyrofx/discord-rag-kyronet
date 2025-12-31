#!/bin/bash
# This is a multi-service Discord RAG project
# Each service (api, bot, scheduler) is built and deployed from its own subdirectory
# See railway.json or railway.toml for service configurations

echo "This is a monorepo containing multiple services."
echo "Services are defined in railway.json and should be deployed separately:"
echo "  - api: production/api"
echo "  - bot: bot"
echo "  - scheduler: scheduler"
echo "  - MongoDB: mongo (Docker image)"
echo "  - Redis: redis (Docker image)"
echo ""
echo "Please deploy services individually using Railway service configuration."
exit 0
