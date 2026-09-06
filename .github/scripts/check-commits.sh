#!/usr/bin/env bash
# Valida que los mensajes de commit sigan el estandar del curso:
#   tipo(alcance): descripcion
# Uso: check-commits.sh <base> <head>
set -uo pipefail

BASE="${1:-origin/main}"
CABEZA="${2:-HEAD}"

TIPOS='feat|fix|docs|refactor|test|chore'
ALCANCES=" storage index external catalog parser txn planner api frontend bench docs build ci "
PATRON="^(${TIPOS})(\([a-z0-9-]+\))?: [a-z].*[^.]$"

fallos=0
total=0

while IFS=$'\t' read -r sha msg; do
  [ -z "${sha:-}" ] && continue
  total=$((total + 1))
  corto="${sha:0:7}"

  if ! printf '%s' "$msg" | grep -Eq "$PATRON"; then
    echo "::error::[$corto] no sigue 'tipo(alcance): descripcion' en minusculas y sin punto final -> $msg"
    fallos=$((fallos + 1))
    continue
  fi

  if [ "${#msg}" -gt 72 ]; then
    echo "::error::[$corto] tiene ${#msg} caracteres (maximo 72) -> $msg"
    fallos=$((fallos + 1))
    continue
  fi

  if printf '%s' "$msg" | grep -Eq '^[a-z]+\('; then
    alcance="$(printf '%s' "$msg" | sed -E 's/^[a-z]+\(([a-z0-9-]+)\).*/\1/')"
    case "$ALCANCES" in
      *" $alcance "*) ;;
      *)
        echo "::error::[$corto] usa el alcance '$alcance', que no esta en la lista oficial:${ALCANCES}-> $msg"
        fallos=$((fallos + 1))
        ;;
    esac
  fi
done < <(git log --no-merges --format='%H%x09%s' "${BASE}..${CABEZA}")

echo "Commits revisados: ${total}. Con problemas: ${fallos}."
[ "$fallos" -eq 0 ]
