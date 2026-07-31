#!/bin/bash
set -euo pipefail
case "${1:-all}" in
  conversation|conversation-sandbox|interactions)
    echo "Conversation Interactions Sandbox: https://charenix.com/lobster/dashboard/conversation-sandbox"
    ;;
  dashboard|home)
    echo "Lobster Dashboard: https://charenix.com/lobster/dashboard/"
    ;;
  api|conversation-api)
    echo "Conversation Interactions API: https://charenix.com/api/lobster-conversation-interactions-dashboard?limit=140"
    ;;
  all|*)
    echo "Lobster Dashboard: https://charenix.com/lobster/dashboard/"
    echo "Conversation Interactions Sandbox: https://charenix.com/lobster/dashboard/conversation-sandbox"
    echo "Conversation Interactions API: https://charenix.com/api/lobster-conversation-interactions-dashboard?limit=140"
    echo "Raid Boss Sandbox: https://charenix.com/lobster/dashboard/raid-boss-sandbox"
    echo "Cognitive Sandbox: https://charenix.com/lobster/dashboard/cognitive-sandbox"
    echo "Integrated Cognitive Sandbox: https://charenix.com/lobster/dashboard/integrated-cognitive-sandbox"
    echo "Lounge: https://charenix.com/lounge"
    ;;
esac
