#!/usr/bin/env bash
# Deletes HW_ALG's old artifacts and outputs that the database may not know about, except the milestones' commits.
# They used to be the qaboard_clean_untracked_hwalg_* commands of the backend, with these folders hard-coded.
# WARNING: don't run this unless you know what you are doing.
#   sirc/scripts/clean-hwalg.sh artifacts [--use-cache]
#   sirc/scripts/clean-hwalg.sh outputs <user> [--use-cache]
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
# Run from the qaboard checkout of the production host
compose() { deployments/deploy.py "$here/production.env" compose "$@"; }

common=(--project CDE-Users/HW_ALG --exclude CDE-Users/HW_ALG/ALG_GEN)
case "${1:-}" in
  artifacts)
    shift
    compose exec -T backend qaboard_clean_untracked_artifacts "${common[@]}" \
      --root /algo/CIS_artifacts/CDE-Users/HW_ALG \
      --root /algo/PSP_2x_artifacts/CDE-Users/HW_ALG \
      --root /algo/KITT_ISP_artifacts/CDE-Users/HW_ALG "$@"
    ;;
  outputs)
    user="$2"; shift 2
    compose exec -T backend qaboard_clean_untracked_outputs "${common[@]}" \
      --root "/algo/CIS/outputs/$user/CDE-Users/HW_ALG" \
      --root "/algo/PSP_2x/outputs/$user/CDE-Users/HW_ALG" \
      --root "/algo/KITT_ISP/outputs/$user/CDE-Users/HW_ALG" "$@"
    ;;
  *)
    sed -n '2,6p' "$0"; exit 2 ;;
esac
