#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 admin@example.com[,other@example.com]" >&2
  exit 2
fi

aws cloudformation deploy \
  --region us-east-1 \
  --stack-name biologix-production \
  --template-file "$(dirname "$0")/cloudformation.yml" \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides "AdminEmails=$1" \
  --no-fail-on-empty-changeset

aws cloudformation describe-stacks \
  --region us-east-1 \
  --stack-name biologix-production \
  --query 'Stacks[0].Outputs' \
  --output table
